import json
from decimal import Decimal

from django.core import mail
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.tests import make_user
from apps.cart.models import CartItem
from apps.cart.tests import make_coupon
from apps.orders.models import CouponRedemption, Order
from apps.orders.services import flow
from apps.orders.signals import order_paid
from apps.orders.tests import CheckoutBase
from apps.payments.models import Payment, WebhookEvent
from apps.payments.services import gateway, payment as svc
from apps.payments.testing import FakeGateway, patch_gateway, razorpay_settings, sign, webhook_request, WEBHOOK_SECRET
from apps.products.models import Product, StockMovement


class SignatureTests(TestCase):
    @razorpay_settings
    def test_checkout_signature(self):
        good = sign("order_1", "pay_1")
        self.assertTrue(gateway.verify_checkout_signature("order_1", "pay_1", good))
        self.assertFalse(gateway.verify_checkout_signature("order_1", "pay_2", good))  # payment swapped
        self.assertFalse(gateway.verify_checkout_signature("order_2", "pay_1", good))  # order swapped
        self.assertFalse(gateway.verify_checkout_signature("order_1", "pay_1", good[:-1] + "0"))
        self.assertFalse(gateway.verify_checkout_signature("order_1", "pay_1", ""))
        self.assertFalse(gateway.verify_checkout_signature("", "", ""))

    def test_missing_secret_never_verifies(self):
        # With no secret configured, even a "signature" computed from an empty key must fail.
        self.assertFalse(gateway.verify_checkout_signature("order_1", "pay_1", sign("order_1", "pay_1", secret="")))
        self.assertFalse(gateway.verify_webhook_signature(b"{}", "anything"))

    @razorpay_settings
    def test_webhook_signature(self):
        body, signature = webhook_request("payment.captured", "order_1", "pay_1")
        self.assertTrue(gateway.verify_webhook_signature(body, signature))
        self.assertFalse(gateway.verify_webhook_signature(body + b" ", signature))
        self.assertFalse(gateway.verify_webhook_signature(body, ""))

    def test_to_paise(self):
        self.assertEqual(gateway.to_paise(Decimal("1499.50")), 149950)
        self.assertEqual(gateway.to_paise(Decimal("0.10")), 10)


@razorpay_settings
class PaymentBase(CheckoutBase):
    def setUp(self):
        super().setUp()
        self.gw = FakeGateway()
        patcher = patch_gateway(self.gw)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.put(self.black, 2)
        self.order = self.create()
        Order.objects.filter(pk=self.order.pk).update(otp_verified_at=timezone.now())
        self.order.refresh_from_db()

    # helpers
    def start(self):
        return svc.start_payment(self.order)

    def callback(self, payment, payment_id="pay_ok1", signature=None, client=None):
        return (client or self.client).post(reverse("payments:callback"), {
            "razorpay_order_id": payment.razorpay_order_id, "razorpay_payment_id": payment_id,
            "razorpay_signature": signature if signature is not None else sign(payment.razorpay_order_id, payment_id),
        })

    def webhook(self, event, payment, payment_id="pay_ok1", **kw):
        body, signature = webhook_request(event, payment.razorpay_order_id, payment_id, **kw)
        headers = {"HTTP_X_RAZORPAY_SIGNATURE": signature, "HTTP_X_RAZORPAY_EVENT_ID": kw.pop("event_id", None) or f"evt_{event}_{payment_id}_{len(body)}"}
        return self.client.post(reverse("payments:webhook"), data=body, content_type="application/json", **headers)

    def stock(self, variant=None):
        variant = variant or self.black
        variant.refresh_from_db()
        return variant.stock


class StartPaymentTests(PaymentBase):
    def test_creates_razorpay_order_for_exact_amount(self):
        payment = self.start()
        self.assertEqual(self.gw.orders, [{"amount": gateway.to_paise(self.order.total), "receipt": self.order.order_number,
                                           "notes": {"order_number": self.order.order_number}}])
        self.assertEqual((payment.status, payment.amount, payment.razorpay_order_id), ("pending", self.order.total, "order_T0001"))
        self.assertEqual(self.order.payments.count(), 1)

    def test_is_idempotent(self):
        first, second = self.start(), self.start()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(len(self.gw.orders), 1)

    def test_failed_attempt_is_retried_on_same_razorpay_order(self):
        payment = self.start()
        svc.mark_failed(razorpay_order_id=payment.razorpay_order_id, reason="card declined")
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.FAILED)
        retry = self.start()
        self.assertEqual((retry.pk, retry.status), (payment.pk, "pending"))
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, Order.PaymentStatus.PENDING)

    def test_unverified_order_cannot_start(self):
        Order.objects.filter(pk=self.order.pk).update(otp_verified_at=None)
        self.order.refresh_from_db()
        with self.assertRaises(svc.PaymentError):
            self.start()
        self.assertEqual(self.gw.orders, [])

    def test_sold_out_since_checkout_blocks_payment(self):
        type(self.black).objects.filter(pk=self.black.pk).update(stock=1)
        with self.assertRaisesMessage(svc.PaymentError, "sold out"):
            self.start()
        self.assertEqual(self.gw.orders, [])

    def test_gateway_failure_leaves_no_payment(self):
        self.gw.fail_create = True
        with self.assertRaises(svc.PaymentError):
            self.start()
        self.assertEqual(Payment.objects.count(), 0)

    def test_cancelled_order_cannot_start(self):
        Order.objects.filter(pk=self.order.pk).update(status=Order.Status.CANCELLED)
        self.order.refresh_from_db()
        with self.assertRaises(svc.PaymentError):
            self.start()


class PaymentPageTests(PaymentBase):
    def url(self):
        return reverse("payments:start", args=[self.order.order_number])

    def test_page_exposes_public_key_only(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url())
        self.assertContains(response, "rzp_test_key")
        self.assertContains(response, "order_T0001")
        self.assertNotContains(response, "test_key_secret")
        self.assertNotContains(response, WEBHOOK_SECRET)
        self.assertEqual(response.context["config"]["amount"], gateway.to_paise(self.order.total))

    def test_unconfigured_gateway_shows_unavailable(self):
        from django.test import override_settings
        self.client.force_login(self.user)
        with override_settings(RAZORPAY_KEY_ID="", RAZORPAY_KEY_SECRET=""):
            self.assertEqual(self.client.get(self.url()).status_code, 503)

    def test_other_users_order_404(self):
        self.client.force_login(make_user(username="x", email="x@example.com", mobile="9444444444"))
        self.assertEqual(self.client.get(self.url()).status_code, 404)

    def test_requires_login(self):
        self.assertEqual(self.client.get(self.url()).status_code, 302)

    def test_paid_order_redirects_to_success(self):
        payment = self.start()
        svc.mark_paid(razorpay_order_id=payment.razorpay_order_id, razorpay_payment_id="pay_1")
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(self.url()), reverse("orders:success", args=[self.order.order_number]))


class CallbackTests(PaymentBase):
    def test_valid_callback_confirms_the_order(self):
        coupon = make_coupon(value="10")
        Order.objects.filter(pk=self.order.pk).update(coupon=coupon, coupon_code="SAVE10")
        self.cart.coupon = coupon
        self.cart.save()
        payment = self.start()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.callback(payment)
        self.assertRedirects(response, reverse("orders:success", args=[self.order.order_number]), fetch_redirect_response=False)

        self.order.refresh_from_db()
        payment.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ("paid", "paid"))
        self.assertIsNotNone(self.order.paid_at)
        self.assertEqual((payment.status, payment.razorpay_payment_id), ("paid", "pay_ok1"))
        self.assertEqual(self.order.payment_id, "pay_ok1")
        self.assertEqual(self.stock(), 3)  # 5 - 2
        self.assertTrue(self.order.inventory_reduced)
        self.assertEqual(StockMovement.objects.filter(order=self.order, reason="order_paid").count(), 1)
        self.assertTrue(CouponRedemption.objects.filter(order=self.order).exists())
        self.assertEqual(CartItem.objects.count(), 0)
        self.cart.refresh_from_db()
        self.assertIsNone(self.cart.coupon)
        self.assertEqual(self.order.history.filter(note="Payment received").count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.popularity, 2)

    def test_success_page_after_callback(self):
        payment = self.start()
        self.callback(payment)
        self.client.force_login(self.user)
        response = self.client.get(reverse("orders:success", args=[self.order.order_number]))
        self.assertContains(response, "is confirmed")
        self.assertNotContains(response, 'http-equiv="refresh"')

    def test_invalid_signature_changes_nothing(self):
        payment = self.start()
        response = self.callback(payment, signature="0" * 64)
        self.assertEqual(response.status_code, 400)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ("payment_pending", "pending"))
        self.assertEqual(self.stock(), 5)

    def test_signature_for_a_different_payment_is_rejected(self):
        payment = self.start()
        stolen = sign(payment.razorpay_order_id, "pay_someone_elses")
        self.assertEqual(self.callback(payment, payment_id="pay_mine", signature=stolen).status_code, 400)
        self.assertEqual(self.stock(), 5)

    def test_missing_fields_rejected(self):
        self.assertEqual(self.client.post(reverse("payments:callback"), {}).status_code, 400)

    def test_unknown_razorpay_order_404(self):
        response = self.client.post(reverse("payments:callback"), {
            "razorpay_order_id": "order_nope", "razorpay_payment_id": "pay_1", "razorpay_signature": sign("order_nope", "pay_1")})
        self.assertEqual(response.status_code, 404)

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(reverse("payments:callback")).status_code, 405)

    def test_replayed_callback_is_idempotent(self):
        payment = self.start()
        for _ in range(3):
            self.callback(payment)
        self.assertEqual(self.stock(), 3)
        self.assertEqual(StockMovement.objects.filter(order=self.order).count(), 1)
        self.assertEqual(self.order.history.filter(note="Payment received").count(), 1)
        self.assertEqual(Payment.objects.filter(status="paid").count(), 1)

    def test_works_without_a_session(self):
        """Razorpay's redirect flow arrives cross-site, with no cookies."""
        payment = self.start()
        from django.test import Client
        response = self.callback(payment, client=Client(enforce_csrf_checks=True))
        self.assertEqual(response.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")

    def test_failure_reporting_is_login_scoped_and_never_downgrades_paid(self):
        payment = self.start()
        url = reverse("payments:failed")
        self.assertEqual(self.client.post(url, {"razorpay_order_id": payment.razorpay_order_id}).status_code, 302)  # anonymous
        self.client.force_login(make_user(username="x", email="x@example.com", mobile="9444444444"))
        self.assertEqual(self.client.post(url, {"razorpay_order_id": payment.razorpay_order_id}).json(), {"recorded": False})
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(url, {"razorpay_order_id": payment.razorpay_order_id, "reason": "declined"}).json(), {"recorded": True})
        self.callback(payment)  # customer retries and pays
        self.assertEqual(self.client.post(url, {"razorpay_order_id": payment.razorpay_order_id}).json(), {"recorded": False})
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")


class WebhookTests(PaymentBase):
    def setUp(self):
        super().setUp()
        self.payment = self.start()

    def test_bad_or_missing_signature_rejected(self):
        body, _ = webhook_request("payment.captured", self.payment.razorpay_order_id, "pay_1")
        for headers in ({}, {"HTTP_X_RAZORPAY_SIGNATURE": "bad"}):
            response = self.client.post(reverse("payments:webhook"), data=body, content_type="application/json", **headers)
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.stock(), 5)

    def test_captured_marks_paid(self):
        response = self.webhook("payment.captured", self.payment, amount=gateway.to_paise(self.order.total))
        self.assertEqual(response.json(), {"result": "paid"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "paid")
        self.assertEqual(self.stock(), 3)

    def test_same_event_delivered_twice(self):
        for _ in range(2):
            body, signature = webhook_request("payment.captured", self.payment.razorpay_order_id, "pay_1")
            response = self.client.post(reverse("payments:webhook"), data=body, content_type="application/json",
                                        HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID="evt_same")
        self.assertEqual(response.json(), {"result": "duplicate_event"})
        self.assertEqual(self.stock(), 3)
        self.assertEqual(WebhookEvent.objects.filter(event_id="evt_same", processed=True).count(), 1)

    def test_callback_and_webhook_together_confirm_once(self):
        self.callback(self.payment)
        response = self.webhook("payment.captured", self.payment)
        self.assertEqual(response.json(), {"result": "already_paid"})
        self.assertEqual(self.stock(), 3)
        self.assertEqual(self.order.history.filter(note="Payment received").count(), 1)

    def test_webhook_then_callback_confirm_once(self):
        self.webhook("payment.captured", self.payment)
        self.callback(self.payment)
        self.assertEqual(self.stock(), 3)

    def test_wrong_amount_is_rejected(self):
        response = self.webhook("payment.captured", self.payment, amount=100)
        self.assertEqual(response.json(), {"result": "mismatch"})
        self.order.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual((self.order.payment_status, self.payment.status), ("pending", "failed"))
        self.assertEqual(self.stock(), 5)

    def test_failed_then_successful_retry(self):
        self.webhook("payment.failed", self.payment, payment_id="pay_bad", status="failed", error="Insufficient funds")
        self.order.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual((self.order.payment_status, self.payment.status), ("failed", "failed"))
        self.assertIn("Insufficient funds", self.payment.failure_reason)
        self.webhook("payment.captured", self.payment, payment_id="pay_good")
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ("paid", "paid"))

    def test_late_failure_event_cannot_undo_a_paid_order(self):
        self.callback(self.payment)
        response = self.webhook("payment.failed", self.payment, payment_id="pay_other", status="failed")
        self.assertEqual(response.json(), {"result": "ignored"})
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, "paid")

    def test_unknown_order_acknowledged(self):
        body, signature = webhook_request("payment.captured", "order_not_ours", "pay_1")
        response = self.client.post(reverse("payments:webhook"), data=body, content_type="application/json",
                                    HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID="evt_x")
        self.assertEqual((response.status_code, response.json()), (200, {"result": "unknown_order"}))

    def test_authorized_but_uncaptured_is_ignored(self):
        self.assertEqual(self.webhook("payment.captured", self.payment, status="authorized").json(), {"result": "ignored"})
        self.assertEqual(self.stock(), 5)

    def test_unhandled_event_type_ignored(self):
        self.assertEqual(self.webhook("refund.processed", self.payment).json(), {"result": "ignored"})

    def test_malformed_body_rejected(self):
        import hashlib, hmac
        body = b"not json"
        signature = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        response = self.client.post(reverse("payments:webhook"), data=body, content_type="application/json", HTTP_X_RAZORPAY_SIGNATURE=signature)
        self.assertEqual(response.status_code, 400)

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(reverse("payments:webhook")).status_code, 405)


class FulfilmentFailureTests(PaymentBase):
    """Money arrives but the order can't be fulfilled: refund automatically."""

    def setUp(self):
        super().setUp()
        self.payment = self.start()

    def test_last_unit_sold_elsewhere_triggers_automatic_refund(self):
        type(self.black).objects.filter(pk=self.black.pk).update(stock=1)  # someone else bought it
        coupon = make_coupon()
        Order.objects.filter(pk=self.order.pk).update(coupon=coupon)
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            response = self.callback(self.payment)
        self.assertEqual(callbacks, [])  # no "order paid" notification for an order that isn't happening
        self.assertEqual(response.status_code, 302)
        self.order.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status, self.payment.status), ("refunded", "refunded", "refunded"))
        self.assertEqual(self.gw.refunds, [("pay_ok1", gateway.to_paise(self.order.total))])
        self.assertEqual(self.stock(), 1)  # untouched
        self.assertFalse(CouponRedemption.objects.exists())
        self.assertEqual(CartItem.objects.count(), 1)  # customer keeps their cart
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("orders:success", args=[self.order.order_number])), "has been refunded")

    def test_failed_refund_is_flagged_then_retried_on_replay(self):
        type(self.black).objects.filter(pk=self.black.pk).update(stock=0)
        self.gw.fail_refund = True
        self.callback(self.payment)
        self.order.refresh_from_db()
        self.payment.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status, self.payment.status), ("cancelled", "paid", "paid"))
        self.assertEqual(self.payment.failure_reason, svc.REFUND_PENDING)
        self.assertTrue(self.order.history.filter(note__contains="Refund this payment manually").exists())

        self.gw.fail_refund = False  # Razorpay redelivers the webhook later
        self.webhook("payment.captured", self.payment)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ("refunded", "refunded"))
        self.assertEqual(len(self.gw.refunds), 1)

    def test_payment_on_an_order_cancelled_meanwhile_is_refunded(self):
        flow_order = Order.objects.get(pk=self.order.pk)
        Order.objects.filter(pk=flow_order.pk).update(status=Order.Status.CANCELLED)
        self.callback(self.payment)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ("refunded", "refunded"))
        self.assertEqual(self.stock(), 5)

    def test_double_payment_on_two_razorpay_orders_refunds_the_extra(self):
        second = Payment.objects.create(order=self.order, amount=self.order.total, razorpay_order_id="order_SECOND")
        self.callback(self.payment, payment_id="pay_first")
        self.callback(second, payment_id="pay_second")
        self.order.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ("paid", "paid"))  # real order untouched
        self.assertEqual(second.status, "refunded")
        self.assertEqual(self.gw.refunds, [("pay_second", gateway.to_paise(self.order.total))])
        self.assertEqual(self.stock(), 3)

    def test_manually_cancelled_paid_order_is_not_auto_refunded_on_replay(self):
        self.callback(self.payment)
        Order.objects.filter(pk=self.order.pk).update(status=Order.Status.CANCELLED)  # admin cancels, refunds by hand later
        self.webhook("payment.captured", self.payment)
        self.assertEqual(self.gw.refunds, [])
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "cancelled")


class NotificationIsolationTests(PaymentBase):
    def test_failing_listener_never_breaks_a_paid_order(self):
        calls = []

        def broken_listener(sender, order, **kwargs):
            calls.append(order.order_number)
            raise RuntimeError("WhatsApp is down")

        order_paid.connect(broken_listener)
        self.addCleanup(order_paid.disconnect, broken_listener)
        payment = self.start()
        with self.captureOnCommitCallbacks(execute=True):
            response = self.callback(payment)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(calls, [self.order.order_number])
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ("paid", "paid"))

    def test_signal_fires_once_per_paid_order(self):
        calls = []
        listener = lambda sender, order, **kw: calls.append(order.pk)  # noqa: E731
        order_paid.connect(listener, weak=False)
        self.addCleanup(order_paid.disconnect, listener)
        payment = self.start()
        with self.captureOnCommitCallbacks(execute=True):
            self.callback(payment)
            self.callback(payment)
        self.assertEqual(calls, [self.order.pk])


class ConstraintAndSuccessPageTests(PaymentBase):
    def test_database_forbids_two_paid_payments_for_one_order(self):
        Payment.objects.create(order=self.order, amount=self.order.total, razorpay_order_id="order_A", status="paid", razorpay_payment_id="pay_A")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.create(order=self.order, amount=self.order.total, razorpay_order_id="order_B", status="paid", razorpay_payment_id="pay_B")

    def test_payment_id_cannot_be_claimed_twice(self):
        Payment.objects.create(order=self.order, amount=1, razorpay_order_id="order_A", razorpay_payment_id="pay_A")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.create(order=self.order, amount=1, razorpay_order_id="order_B", razorpay_payment_id="pay_A")

    def test_pending_order_shows_confirming_and_refreshes(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("orders:success", args=[self.order.order_number]))
        self.assertContains(response, "Confirming your payment")
        self.assertContains(response, 'http-equiv="refresh"')

    def test_success_page_is_private(self):
        self.client.force_login(make_user(username="x", email="x@example.com", mobile="9444444444"))
        self.assertEqual(self.client.get(reverse("orders:success", args=[self.order.order_number])).status_code, 404)

    def test_exactly_one_gateway_order_after_many_page_loads(self):
        self.client.force_login(self.user)
        for _ in range(4):
            self.client.get(reverse("payments:start", args=[self.order.order_number]))
        self.assertEqual(len(self.gw.orders), 1)
