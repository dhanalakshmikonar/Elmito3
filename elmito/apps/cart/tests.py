from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.tests import make_user
from apps.cart.models import Cart, CartItem
from apps.cart.services import cart as svc
from apps.orders.models import Coupon, CouponRedemption
from apps.orders.services.coupons import CouponError, validate_coupon
from apps.products.models import Product
from apps.products.tests import make_order, make_product


def make_coupon(code="SAVE10", kind="percent", value="10", **kw):
    now = timezone.now()
    defaults = dict(starts_at=now - timedelta(days=1), expires_at=now + timedelta(days=1), per_user_limit=1)
    defaults.update(kw)
    return Coupon.objects.create(code=code, discount_type=kind, value=Decimal(value), **defaults)


class CartTestCase(TestCase):
    def setUp(self):
        self.product = make_product(price="1000", variants=(("Black", "M", 5), ("Black", "L", 0), ("White", "M", 20)))
        variants = self.product.variants
        self.black_m, self.black_l, self.white_m = (
            variants.get(color="Black", size="M"), variants.get(color="Black", size="L"), variants.get(color="White", size="M"))

    def add(self, variant=None, quantity=1, **extra):
        data = {"product": self.product.pk, "variant": (variant or self.black_m).pk, "quantity": quantity}
        data.update(extra)
        return self.client.post(reverse("cart:add"), data)

    def cart_items(self):
        return {(i.variant.color, i.variant.size): i.quantity for i in CartItem.objects.select_related("variant")}


class AddToCartTests(CartTestCase):
    def test_add_creates_session_cart(self):
        response = self.add(quantity=2)
        self.assertRedirects(response, reverse("cart:detail"))
        self.assertEqual(self.cart_items(), {("Black", "M"): 2})
        self.assertIsNone(Cart.objects.get().user)

    def test_adding_same_variant_merges_lines(self):
        self.add(quantity=2)
        self.add(quantity=1)
        self.assertEqual(self.cart_items(), {("Black", "M"): 3})

    def test_resolve_by_colour_and_size_without_variant_id(self):
        self.client.post(reverse("cart:add"), {"product": self.product.pk, "color": "white", "size": "m", "quantity": 1})
        self.assertEqual(self.cart_items(), {("White", "M"): 1})

    def test_missing_size_rejected(self):
        response = self.client.post(reverse("cart:add"), {"product": self.product.pk, "color": "Black", "quantity": 1}, follow=True)
        self.assertContains(response, "select a size")
        self.assertEqual(CartItem.objects.count(), 0)

    def test_out_of_stock_variant_rejected(self):
        response = self.client.post(
            reverse("cart:add"), {"product": self.product.pk, "variant": self.black_l.pk, "quantity": 1}, follow=True)
        self.assertContains(response, "sold out")
        self.assertEqual(CartItem.objects.count(), 0)

    def test_cannot_exceed_stock_across_adds(self):
        self.add(quantity=4)
        response = self.client.post(reverse("cart:add"), {"product": self.product.pk, "variant": self.black_m.pk, "quantity": 2}, follow=True)
        self.assertContains(response, "Only 5 left")
        self.assertEqual(self.cart_items(), {("Black", "M"): 4})

    def test_per_item_cap(self):
        response = self.client.post(reverse("cart:add"), {"product": self.product.pk, "variant": self.white_m.pk, "quantity": 11}, follow=True)
        self.assertContains(response, "up to 10")
        self.assertEqual(CartItem.objects.count(), 0)

    def test_invalid_quantities_rejected(self):
        for bad in ("0", "-1", "abc", ""):
            self.client.post(reverse("cart:add"), {"product": self.product.pk, "variant": self.black_m.pk, "quantity": bad})
        self.assertEqual(CartItem.objects.count(), 0)

    def test_variant_from_another_product_rejected(self):
        other = make_product("Other", "OTH", variants=(("Black", "M", 5),))
        self.client.post(reverse("cart:add"), {"product": self.product.pk, "variant": other.variants.get().pk, "quantity": 1})
        self.assertEqual(CartItem.objects.count(), 0)

    def test_mismatched_colour_and_variant_rejected(self):
        self.add(color="White")  # variant is Black/M
        self.assertEqual(CartItem.objects.count(), 0)

    def test_draft_product_cannot_be_added(self):
        Product.objects.filter(pk=self.product.pk).update(status=Product.Status.DRAFT)
        self.add()
        self.assertEqual(CartItem.objects.count(), 0)

    def test_buy_now_goes_to_checkout(self):
        response = self.add(action="buy_now")
        self.assertRedirects(response, reverse("orders:checkout"), fetch_redirect_response=False)
        self.assertEqual(self.cart_items(), {("Black", "M"): 1})

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(reverse("cart:add")).status_code, 405)


class CartPricingTests(CartTestCase):
    def test_prices_come_from_database_not_request(self):
        self.add(price="1", unit_price="1", total="1")
        summary = svc.summarize(Cart.objects.get())
        self.assertEqual(summary.subtotal, Decimal("1000.00"))

    def test_discount_price_used(self):
        Product.objects.filter(pk=self.product.pk).update(discount_price=Decimal("800"))
        self.add(quantity=2)
        self.assertEqual(svc.summarize(Cart.objects.get()).subtotal, Decimal("1600.00"))

    @override_settings(FREE_SHIPPING_THRESHOLD=2000, FLAT_SHIPPING_CHARGE=99)
    def test_shipping_threshold_boundary(self):
        self.add(self.white_m, quantity=1)
        self.assertEqual(svc.summarize(Cart.objects.get()).shipping, Decimal("99.00"))
        self.add(self.white_m, quantity=1)  # subtotal exactly 2000
        summary = svc.summarize(Cart.objects.get())
        self.assertEqual((summary.shipping, summary.total), (Decimal("0.00"), Decimal("2000.00")))

    @override_settings(FREE_SHIPPING_THRESHOLD=2000, FLAT_SHIPPING_CHARGE=99)
    def test_totals_with_coupon_and_shipping(self):
        make_coupon(value="10")
        self.add(self.white_m, quantity=2)  # 2000
        cart = Cart.objects.get()
        svc.apply_coupon(cart, "save10")
        summary = svc.summarize(cart)
        # 10% off => 1800, which drops below the free-shipping threshold
        self.assertEqual((summary.subtotal, summary.discount, summary.shipping, summary.total),
                         (Decimal("2000.00"), Decimal("200.00"), Decimal("99.00"), Decimal("1899.00")))

    def test_stock_drop_flags_line_problem(self):
        self.add(quantity=4)
        self.black_m.__class__.objects.filter(pk=self.black_m.pk).update(stock=2)
        summary = svc.summarize(Cart.objects.get())
        self.assertTrue(summary.has_problems)
        self.assertEqual(summary.lines[0].problem, "Only 2 left.")
        response = self.client.get(reverse("cart:detail"))
        self.assertContains(response, "Only 2 left.")

    def test_empty_cart_has_no_shipping(self):
        summary = svc.summarize(None)
        self.assertEqual((summary.total, summary.shipping), (Decimal("0.00"), Decimal("0.00")))


class CartEditingTests(CartTestCase):
    def test_update_and_remove(self):
        self.add(quantity=2)
        item = CartItem.objects.get()
        self.client.post(reverse("cart:update", args=[item.pk]), {"quantity": 4})
        item.refresh_from_db()
        self.assertEqual(item.quantity, 4)
        self.client.post(reverse("cart:update", args=[item.pk]), {"quantity": 0})
        self.assertEqual(CartItem.objects.count(), 0)

    def test_cannot_raise_above_stock_but_can_lower(self):
        self.add(quantity=4)
        item = CartItem.objects.get()
        self.client.post(reverse("cart:update", args=[item.pk]), {"quantity": 9})
        item.refresh_from_db()
        self.assertEqual(item.quantity, 4)
        type(self.black_m).objects.filter(pk=self.black_m.pk).update(stock=2)  # now over-stocked in cart
        self.client.post(reverse("cart:update", args=[item.pk]), {"quantity": 3})
        item.refresh_from_db()
        self.assertEqual(item.quantity, 3)

    def test_cannot_touch_someone_elses_cart_item(self):
        victim = make_user()
        victim_cart = Cart.objects.create(user=victim)
        item = CartItem.objects.create(cart=victim_cart, variant=self.white_m, quantity=2)
        self.client.post(reverse("cart:remove", args=[item.pk]))
        self.client.post(reverse("cart:update", args=[item.pk]), {"quantity": 9})
        item.refresh_from_db()
        self.assertEqual(item.quantity, 2)

    def test_header_badge_counts_items(self):
        self.add(quantity=3)
        self.assertContains(self.client.get(reverse("core:home")), 'class="badge">3<')


class CartMergeOnLoginTests(CartTestCase):
    def login(self):
        return self.client.post(reverse("accounts:login"), {"identifier": "asha_rao", "password": "Sturdy#Pass9271"})

    def test_anonymous_cart_becomes_user_cart(self):
        make_user()
        self.add(quantity=2)
        self.login()
        cart = Cart.objects.get()
        self.assertEqual(cart.user.username, "asha_rao")
        self.assertIsNone(cart.session_key)
        self.assertEqual(self.cart_items(), {("Black", "M"): 2})

    def test_merge_sums_quantities_and_caps_at_stock(self):
        user = make_user()
        CartItem.objects.create(cart=Cart.objects.create(user=user), variant=self.black_m, quantity=3)
        self.add(quantity=4)
        self.login()
        self.assertEqual(Cart.objects.count(), 1)
        self.assertEqual(self.cart_items(), {("Black", "M"): 5})  # 3 + 4 capped at stock 5

    def test_cart_survives_logout(self):
        make_user()
        self.login()
        self.add(quantity=1)
        self.client.post(reverse("accounts:logout"))
        self.assertEqual(self.client.get(reverse("cart:detail")).context["summary"].lines, [])
        self.login()
        self.assertEqual(self.cart_items(), {("Black", "M"): 1})


class CouponTests(TestCase):
    def test_percent_with_cap(self):
        make_coupon(value="50", max_discount=Decimal("300"))
        self.assertEqual(validate_coupon("save10", subtotal=Decimal("2000")).discount, Decimal("300.00"))

    def test_fixed_never_exceeds_subtotal(self):
        make_coupon(kind="fixed", value="500")
        self.assertEqual(validate_coupon("SAVE10", subtotal=Decimal("300")).discount, Decimal("300.00"))

    def test_code_is_case_insensitive(self):
        make_coupon()
        self.assertEqual(validate_coupon(" save10 ", subtotal=Decimal("1000")).discount, Decimal("100.00"))

    def test_minimum_order_value(self):
        make_coupon(min_order_value=Decimal("1500"))
        with self.assertRaisesMessage(CouponError, "minimum order"):
            validate_coupon("SAVE10", subtotal=Decimal("1000"))

    def test_expired_not_started_inactive_unknown_share_one_message(self):
        now = timezone.now()
        make_coupon("OLD", expires_at=now - timedelta(hours=1), starts_at=now - timedelta(days=2))
        make_coupon("SOON", starts_at=now + timedelta(hours=1), expires_at=now + timedelta(days=2))
        make_coupon("OFF", is_active=False)
        for code in ("OLD", "SOON", "OFF", "NOPE"):
            with self.assertRaisesMessage(CouponError, "isn't valid"):
                validate_coupon(code, subtotal=Decimal("1000"))

    def test_total_usage_limit(self):
        coupon = make_coupon(usage_limit=1)
        order = make_order([(make_product().variants.get(), 1)])
        CouponRedemption.objects.create(coupon=coupon, user=order.user, order=order)
        with self.assertRaisesMessage(CouponError, "fully redeemed"):
            validate_coupon("SAVE10", subtotal=Decimal("1000"))

    def test_per_user_limit(self):
        coupon = make_coupon(per_user_limit=1)
        order = make_order([(make_product().variants.get(), 1)])
        CouponRedemption.objects.create(coupon=coupon, user=order.user, order=order)
        with self.assertRaisesMessage(CouponError, "already used"):
            validate_coupon("SAVE10", subtotal=Decimal("1000"), user=order.user)
        other = User.objects.create_user("other", "o@example.com", "x", mobile="9111111111", full_name="O")
        self.assertTrue(validate_coupon("SAVE10", subtotal=Decimal("1000"), user=other))

    def test_coupon_removed_when_it_stops_being_valid(self):
        product = make_product(price="1000")
        coupon = make_coupon(min_order_value=Decimal("900"))
        cart = Cart.objects.create(session_key="tok", coupon=coupon)
        item = CartItem.objects.create(cart=cart, variant=product.variants.get(), quantity=1)
        self.assertEqual(svc.refresh(cart).discount, Decimal("100.00"))
        Product.objects.filter(pk=product.pk).update(price=Decimal("500"))
        summary = svc.refresh(cart)
        cart.refresh_from_db()
        self.assertEqual((summary.discount, bool(summary.coupon_error), cart.coupon), (Decimal("0.00"), True, None))

    def test_apply_coupon_view_and_empty_cart(self):
        make_coupon()
        response = self.client.post(reverse("cart:apply_coupon"), {"code": "SAVE10"}, follow=True)
        self.assertContains(response, "cart is empty")
