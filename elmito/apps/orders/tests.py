import re
from decimal import Decimal

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import Address
from apps.accounts.services.otp import OTPInvalid
from apps.accounts.tests import make_user
from apps.cart.models import Cart, CartItem
from apps.orders.models import Order
from apps.orders.services import flow
from apps.orders.services.checkout import CheckoutError, create_order_from_cart
from apps.payments.testing import FakeGateway, patch_gateway, razorpay_settings
from apps.products.models import Product
from apps.products.tests import make_product

ADDRESS = dict(full_name="Asha Rao", phone="9876543210", line1="12 Marine Drive, Colaba", city="Mumbai",
               state="Maharashtra", pincode="400001", landmark="Near the clock tower")


def make_address(user, **overrides):
    return Address.objects.create(user=user, **{**ADDRESS, **overrides})


class CheckoutBase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.address = make_address(self.user, is_default=True)
        self.product = make_product(price="1000", variants=(("Black", "M", 5), ("White", "L", 3)))
        self.black = self.product.variants.get(color="Black")
        self.white = self.product.variants.get(color="White")
        self.cart = Cart.objects.create(user=self.user)

    def put(self, variant, qty):
        CartItem.objects.update_or_create(cart=self.cart, variant=variant, defaults={"quantity": qty})

    def create(self):
        return create_order_from_cart(user=self.user, cart=self.cart, address=self.address)


class OrderCreationTests(CheckoutBase):
    def test_order_snapshots_prices_items_and_address(self):
        self.put(self.black, 2)
        self.put(self.white, 1)
        order = self.create()
        self.assertEqual(order.subtotal, Decimal("3000.00"))
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(order.payment_status, Order.PaymentStatus.PENDING)
        self.assertEqual(order.items.count(), 2)
        item = order.items.get(color="Black")
        self.assertEqual((item.quantity, item.unit_price, item.line_total, item.sku), (2, Decimal("1000.00"), Decimal("2000.00"), self.black.sku))
        self.assertEqual((order.ship_name, order.ship_city, order.ship_pincode), ("Asha Rao", "Mumbai", "400001"))
        self.assertRegex(order.order_number, r"^ELM\d{6}[0-9A-F]{6}$")
        self.assertEqual(order.history.count(), 1)

    @override_settings(FREE_SHIPPING_THRESHOLD=5000, FLAT_SHIPPING_CHARGE=99)
    def test_total_includes_shipping(self):
        self.put(self.black, 1)
        order = self.create()
        self.assertEqual((order.subtotal, order.shipping_charge, order.total), (Decimal("1000.00"), Decimal("99.00"), Decimal("1099.00")))

    def test_snapshot_survives_address_and_price_edits(self):
        self.put(self.black, 1)
        order = self.create()
        Address.objects.filter(pk=self.address.pk).update(city="Pune")
        Product.objects.filter(pk=self.product.pk).update(price=Decimal("9999"))
        order.refresh_from_db()
        self.assertEqual(order.ship_city, "Mumbai")
        self.assertEqual(order.items.get().unit_price, Decimal("1000.00"))

    def test_creating_twice_returns_the_same_order(self):
        self.put(self.black, 1)
        first, second = self.create(), self.create()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Order.objects.count(), 1)

    def test_changed_cart_makes_new_order_and_supersedes_old_draft(self):
        self.put(self.black, 1)
        first = self.create()
        self.put(self.black, 2)
        second = self.create()
        first.refresh_from_db()
        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(first.status, Order.Status.CANCELLED)
        self.assertEqual(second.status, Order.Status.PENDING)

    def test_order_awaiting_payment_is_not_cancelled_by_new_draft(self):
        self.put(self.black, 1)
        first = self.create()
        flow.prepare_for_payment(first)
        self.put(self.black, 2)
        self.create()
        first.refresh_from_db()
        self.assertEqual(first.status, Order.Status.PAYMENT_PENDING)

    def test_empty_cart_rejected(self):
        with self.assertRaisesMessage(CheckoutError, "empty"):
            self.create()

    def test_unavailable_item_blocks_order(self):
        self.put(self.black, 4)
        type(self.black).objects.filter(pk=self.black.pk).update(stock=1)
        with self.assertRaisesMessage(CheckoutError, "no longer available"):
            self.create()
        self.assertEqual(Order.objects.count(), 0)

    def test_someone_elses_address_rejected(self):
        other = make_user(username="other", email="o@example.com", mobile="9111111111")
        stranger_address = make_address(other)
        self.put(self.black, 1)
        with self.assertRaises(CheckoutError):
            create_order_from_cart(user=self.user, cart=self.cart, address=stranger_address)

    def test_coupon_discount_recorded_on_order(self):
        from apps.cart.tests import make_coupon
        self.put(self.black, 2)
        self.cart.coupon = make_coupon(value="10")
        self.cart.save()
        order = self.create()
        self.assertEqual((order.discount_total, order.coupon_code), (Decimal("200.00"), "SAVE10"))
        self.assertEqual(order.total, order.subtotal - order.discount_total + order.shipping_charge)

    def test_invalid_coupon_blocks_order_and_is_dropped(self):
        from apps.cart.tests import make_coupon
        self.put(self.black, 1)
        self.cart.coupon = make_coupon(is_active=False)
        self.cart.save()
        with self.assertRaises(CheckoutError):
            self.create()
        self.cart.refresh_from_db()
        self.assertIsNone(self.cart.coupon)


class OrderOtpFlowTests(CheckoutBase):
    def setUp(self):
        super().setUp()
        self.put(self.black, 1)
        self.order = self.create()

    def code(self):
        return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)

    def test_default_stage_requires_otp(self):
        self.assertEqual(flow.next_step(self.order), "otp")

    @override_settings(ORDER_OTP_STAGE="none")
    def test_stage_none_skips_otp(self):
        self.assertEqual(flow.next_step(self.order), "payment")

    def test_correct_code_unlocks_payment(self):
        flow.start_otp(self.order)
        flow.verify_order_otp(self.order, self.code())
        self.order.refresh_from_db()
        self.assertIsNotNone(self.order.otp_verified_at)
        self.assertEqual(self.order.status, Order.Status.PAYMENT_PENDING)
        self.assertEqual(flow.next_step(self.order), "payment")

    def test_wrong_code_does_not_unlock(self):
        flow.start_otp(self.order)
        wrong = "000000" if self.code() != "000000" else "111111"
        with self.assertRaises(OTPInvalid):
            flow.verify_order_otp(self.order, wrong)
        self.order.refresh_from_db()
        self.assertIsNone(self.order.otp_verified_at)

    def test_code_for_another_order_is_rejected(self):
        flow.start_otp(self.order)
        stolen = self.code()
        self.put(self.black, 2)
        other = self.create()
        with self.assertRaises(OTPInvalid):
            flow.verify_order_otp(other, stolen)

    def test_code_cannot_be_reused(self):
        flow.start_otp(self.order)
        code = self.code()
        flow.verify_order_otp(self.order, code)
        # A second order cannot piggyback on the consumed code.
        self.put(self.black, 3)
        other = self.create()
        with self.assertRaises(OTPInvalid):
            flow.verify_order_otp(other, code)

    def test_verifying_is_idempotent(self):
        flow.start_otp(self.order)
        code = self.code()
        flow.verify_order_otp(self.order, code)
        flow.verify_order_otp(self.order, code)  # second call is a harmless no-op
        self.assertEqual(self.order.history.filter(note="Ready for payment").count(), 1)


class CheckoutViewTests(CheckoutBase):
    def setUp(self):
        super().setUp()
        self.put(self.black, 1)
        self.client.force_login(self.user)

    def test_login_required(self):
        self.client.logout()
        for name in ("checkout", "review"):
            response = self.client.get(reverse(f"orders:{name}"))
            self.assertEqual(response.status_code, 302)
            self.assertIn("/account/login/", response.url)
        self.assertEqual(self.client.post(reverse("orders:confirm")).status_code, 302)

    def test_empty_cart_redirects_to_cart(self):
        self.cart.items.all().delete()
        self.assertRedirects(self.client.get(reverse("orders:checkout")), reverse("cart:detail"))

    def test_checkout_preselects_default_address(self):
        response = self.client.get(reverse("orders:checkout"))
        self.assertEqual(response.context["selected"], self.address)

    def test_cannot_choose_another_users_address(self):
        other = make_user(username="other", email="o@example.com", mobile="9111111111")
        stranger = make_address(other)
        response = self.client.post(reverse("orders:checkout"), {"address": stranger.pk})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("checkout_address_id", self.client.session)

    def test_review_requires_address(self):
        self.assertRedirects(self.client.get(reverse("orders:review")), reverse("orders:checkout"))

    @razorpay_settings
    def test_full_flow_to_payment(self):
        self.client.post(reverse("orders:checkout"), {"address": self.address.pk})
        self.assertContains(self.client.get(reverse("orders:review")), "Confirm order")

        response = self.client.post(reverse("orders:confirm"))
        order = Order.objects.get()
        self.assertRedirects(response, reverse("orders:verify_otp", args=[order.order_number]))
        self.assertEqual(len(mail.outbox), 1)

        # Payment page is gated until the OTP is verified.
        self.assertRedirects(self.client.get(reverse("payments:start", args=[order.order_number])),
                             reverse("orders:verify_otp", args=[order.order_number]))

        code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
        response = self.client.post(reverse("orders:verify_otp", args=[order.order_number]), {"code": code})
        self.assertRedirects(response, reverse("payments:start", args=[order.order_number]), fetch_redirect_response=False)
        with patch_gateway(FakeGateway()):
            self.assertEqual(self.client.get(reverse("payments:start", args=[order.order_number])).status_code, 200)

    def test_double_confirm_creates_one_order_and_one_email(self):
        self.client.post(reverse("orders:checkout"), {"address": self.address.pk})
        self.client.post(reverse("orders:confirm"))
        self.client.post(reverse("orders:confirm"))
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(ORDER_OTP_STAGE="none")
    def test_stage_none_goes_straight_to_payment(self):
        self.client.post(reverse("orders:checkout"), {"address": self.address.pk})
        response = self.client.post(reverse("orders:confirm"))
        order = Order.objects.get()
        self.assertRedirects(response, reverse("payments:start", args=[order.order_number]), fetch_redirect_response=False)
        self.assertEqual(order.status, Order.Status.PAYMENT_PENDING)
        self.assertEqual(len(mail.outbox), 0)

    def test_wrong_otp_shows_error(self):
        self.client.post(reverse("orders:checkout"), {"address": self.address.pk})
        self.client.post(reverse("orders:confirm"))
        order = Order.objects.get()
        code = re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)
        wrong = "000000" if code != "000000" else "111111"
        response = self.client.post(reverse("orders:verify_otp", args=[order.order_number]), {"code": wrong})
        self.assertContains(response, "incorrect")

    def test_resend_respects_cooldown(self):
        self.client.post(reverse("orders:checkout"), {"address": self.address.pk})
        self.client.post(reverse("orders:confirm"))
        order = Order.objects.get()
        response = self.client.post(reverse("orders:resend_otp", args=[order.order_number]), follow=True)
        self.assertContains(response, "Please wait")
        self.assertEqual(len(mail.outbox), 1)

    def test_other_users_order_is_404(self):
        self.client.post(reverse("orders:checkout"), {"address": self.address.pk})
        self.client.post(reverse("orders:confirm"))
        order = Order.objects.get()
        intruder = make_user(username="intruder", email="i@example.com", mobile="9222222222")
        self.client.force_login(intruder)
        self.assertEqual(self.client.get(reverse("orders:verify_otp", args=[order.order_number])).status_code, 404)
        self.assertEqual(self.client.get(reverse("payments:start", args=[order.order_number])).status_code, 404)

    def test_cancelled_order_cannot_be_verified(self):
        self.client.post(reverse("orders:checkout"), {"address": self.address.pk})
        self.client.post(reverse("orders:confirm"))
        order = Order.objects.get()
        Order.objects.filter(pk=order.pk).update(status=Order.Status.CANCELLED)
        response = self.client.get(reverse("orders:verify_otp", args=[order.order_number]))
        self.assertRedirects(response, reverse("core:home"))


class AddressTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client.force_login(self.user)
        self.url = reverse("accounts:address_create")

    def test_first_address_becomes_default(self):
        self.client.post(self.url, ADDRESS)
        self.assertTrue(Address.objects.get().is_default)

    def test_only_one_default(self):
        self.client.post(self.url, ADDRESS)
        self.client.post(self.url, {**ADDRESS, "line1": "99 Another Road, Bandra", "is_default": "on"})
        self.assertEqual(list(Address.objects.filter(is_default=True).values_list("line1", flat=True)), ["99 Another Road, Bandra"])

    def test_validation(self):
        for field, bad in (("pincode", "000000"), ("pincode", "40001"), ("phone", "12345"), ("state", "Atlantis"), ("city", "12"), ("line1", "x")):
            self.client.post(self.url, {**ADDRESS, field: bad})
        self.assertEqual(Address.objects.count(), 0)

    def test_phone_normalised(self):
        self.client.post(self.url, {**ADDRESS, "phone": "+91 98765-43210"})
        self.assertEqual(Address.objects.get().phone, "9876543210")

    def test_cannot_edit_or_delete_others_address(self):
        other = make_user(username="other", email="o@example.com", mobile="9111111111")
        theirs = make_address(other)
        self.assertEqual(self.client.get(reverse("accounts:address_edit", args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("accounts:address_delete", args=[theirs.pk])).status_code, 404)
        self.assertTrue(Address.objects.filter(pk=theirs.pk).exists())

    def test_deleting_default_promotes_another(self):
        self.client.post(self.url, ADDRESS)
        self.client.post(self.url, {**ADDRESS, "line1": "99 Another Road, Bandra"})
        default = Address.objects.get(is_default=True)
        self.client.post(reverse("accounts:address_delete", args=[default.pk]))
        self.assertTrue(Address.objects.get().is_default)

    def test_editing_default_keeps_it_default(self):
        self.client.post(self.url, ADDRESS)
        address = Address.objects.get()
        data = {**ADDRESS, "city": "Thane"}
        self.client.post(reverse("accounts:address_edit", args=[address.pk]), data)  # is_default omitted
        address.refresh_from_db()
        self.assertEqual((address.city, address.is_default), ("Thane", True))

    def test_address_limit(self):
        for i in range(10):
            make_address(self.user, line1=f"{i} Long Street Name")
        self.client.post(self.url, ADDRESS)
        self.assertEqual(Address.objects.count(), 10)

    def test_next_redirect_is_validated(self):
        response = self.client.post(self.url, {**ADDRESS, "next": "https://evil.example/"})
        self.assertRedirects(response, reverse("accounts:addresses"))
        response = self.client.post(self.url, {**ADDRESS, "line1": "77 Safe Road, Worli", "next": reverse("orders:checkout")}, follow=False)
        self.assertEqual(response.url, reverse("orders:checkout"))
