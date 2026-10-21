import csv
import io

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.accounts.tests import make_user
from apps.cart.tests import make_coupon
from apps.orders.models import Order
from apps.orders.services import status as svc
from apps.orders.signals import order_status_changed
from apps.payments.models import Payment
from apps.payments.services import payment as payments
from apps.payments.testing import FakeGateway, patch_gateway, razorpay_settings
from apps.orders.tests import CheckoutBase
from apps.products.models import StockMovement

S = Order.Status


@razorpay_settings
class ManagementBase(CheckoutBase):
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

    def pay(self):
        payment = payments.start_payment(self.order)
        payments.mark_paid(razorpay_order_id=payment.razorpay_order_id, razorpay_payment_id="pay_1")
        self.order.refresh_from_db()
        return payment

    def stock(self):
        self.black.refresh_from_db()
        return self.black.stock


class TransitionTests(ManagementBase):
    def test_happy_path_to_delivered(self):
        self.pay()
        for step in (S.PROCESSING, S.PACKED, S.SHIPPED, S.OUT_FOR_DELIVERY, S.DELIVERED):
            self.order = svc.change_status(self.order, step, by=self.user)
        self.assertEqual(self.order.status, S.DELIVERED)
        self.assertEqual(self.order.history.filter(status=S.DELIVERED).count(), 1)

    def test_invalid_jumps_rejected(self):
        self.pay()
        for bad in (S.DELIVERED, S.SHIPPED, S.RETURNED, S.PENDING):
            with self.assertRaises(svc.TransitionError):
                svc.change_status(self.order, bad, by=self.user)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, S.PAID)

    def test_same_status_rejected(self):
        self.pay()
        with self.assertRaises(svc.TransitionError):
            svc.change_status(self.order, S.PAID)

    def test_staff_cannot_mark_an_unpaid_order_paid(self):
        payments.start_payment(self.order)
        self.order.refresh_from_db()
        with self.assertRaisesMessage(svc.TransitionError, "automatically"):
            svc.change_status(self.order, S.PAID, by=self.user)
        self.assertFalse(self.order.is_paid)

    def test_terminal_states_are_final(self):
        self.pay()
        self.order = svc.change_status(self.order, S.REFUNDED, by=self.user)
        self.assertEqual(svc.next_statuses(self.order), [])

    def test_next_statuses_exclude_system_only(self):
        self.pay()
        self.assertNotIn(S.PAID, svc.next_statuses(self.order))
        self.assertIn(S.PROCESSING, svc.next_statuses(self.order))

    def test_status_change_is_announced_after_commit(self):
        seen = []
        listener = lambda sender, order, old_status, new_status, **kw: seen.append((old_status, new_status))  # noqa: E731
        order_status_changed.connect(listener, weak=False)
        self.addCleanup(order_status_changed.disconnect, listener)
        self.pay()
        with self.captureOnCommitCallbacks(execute=True):
            svc.change_status(self.order, S.PROCESSING, by=self.user)
        self.assertEqual(seen, [("paid", "processing")])

    def test_failing_listener_does_not_block_status_change(self):
        def broken(sender, **kw):
            raise RuntimeError("email down")
        order_status_changed.connect(broken)
        self.addCleanup(order_status_changed.disconnect, broken)
        self.pay()
        with self.captureOnCommitCallbacks(execute=True):
            svc.change_status(self.order, S.PROCESSING, by=self.user)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, S.PROCESSING)


class CancellationTests(ManagementBase):
    def test_cancel_unpaid_order(self):
        self.order = svc.cancel_by_customer(self.order, by=self.user)
        self.assertEqual(self.order.status, S.CANCELLED)
        self.assertEqual(self.gw.refunds, [])

    def test_cancel_paid_order_refunds_and_restocks(self):
        self.pay()
        self.assertEqual(self.stock(), 3)
        order = svc.cancel_by_customer(self.order, by=self.user)
        payment = Payment.objects.get()
        self.assertEqual((order.status, order.payment_status, payment.status), ("cancelled", "refunded", "refunded"))
        self.assertEqual(self.gw.refunds, [("pay_1", int(self.order.total * 100))])
        self.assertEqual(self.stock(), 5)
        self.assertEqual(StockMovement.objects.filter(reason="order_restock").count(), 1)

    def test_cancel_while_processing_allowed_but_not_after_packing(self):
        self.pay()
        svc.change_status(self.order, S.PROCESSING, by=self.user)
        self.order.refresh_from_db()
        self.assertTrue(svc.can_customer_cancel(self.order))
        svc.change_status(self.order, S.PACKED, by=self.user)
        self.order.refresh_from_db()
        with self.assertRaisesMessage(svc.TransitionError, "already been packed"):
            svc.cancel_by_customer(self.order, by=self.user)

    def test_failed_refund_leaves_order_untouched(self):
        self.pay()
        self.gw.fail_refund = True
        with self.assertRaisesMessage(svc.TransitionError, "refund could not be processed"):
            svc.cancel_by_customer(self.order, by=self.user)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.payment_status), ("paid", "paid"))
        self.assertEqual(self.stock(), 3)  # not restocked: nothing changed
        self.assertEqual(Payment.objects.get().status, "paid")

    def test_cannot_cancel_someone_elses_order(self):
        other = make_user(username="x", email="x@example.com", mobile="9444444444")
        with self.assertRaises(svc.TransitionError):
            svc.cancel_by_customer(self.order, by=other)

    def test_cancelling_twice_is_rejected(self):
        self.pay()
        svc.cancel_by_customer(self.order, by=self.user)
        self.order.refresh_from_db()
        with self.assertRaises(svc.TransitionError):
            svc.cancel_by_customer(self.order, by=self.user)
        self.assertEqual(len(self.gw.refunds), 1)
        self.assertEqual(self.stock(), 5)

    def test_admin_refund_of_delivered_order(self):
        self.pay()
        for step in (S.PROCESSING, S.PACKED, S.SHIPPED, S.DELIVERED):
            self.order = svc.change_status(self.order, step, by=self.user)
        self.order = svc.change_status(self.order, S.REFUNDED, by=self.user)
        self.assertEqual((self.order.status, self.order.payment_status), ("refunded", "refunded"))
        self.assertEqual(len(self.gw.refunds), 1)

    def test_refunding_an_already_refunded_payment_does_not_call_gateway_again(self):
        """Out-of-stock auto-refund leaves payment_status=refunded; a later status change must not double-refund."""
        self.pay()
        svc.cancel_by_customer(self.order, by=self.user)
        self.order.refresh_from_db()
        svc.change_status(self.order, S.REFUNDED, by=self.user)
        self.assertEqual(len(self.gw.refunds), 1)


class CustomerOrderViewTests(ManagementBase):
    def test_list_and_detail(self):
        self.pay()
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("orders:list")), self.order.order_number)
        response = self.client.get(reverse("orders:detail", args=[self.order.order_number]))
        self.assertContains(response, "Cancel order")
        self.assertContains(response, "Payment received")

    def test_unpaid_draft_not_listed_and_paid_order_has_no_pay_button(self):
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get(reverse("orders:list")), self.order.order_number)  # still PENDING
        self.pay()
        self.assertNotContains(self.client.get(reverse("orders:detail", args=[self.order.order_number])), "Complete payment")

    def test_awaiting_payment_order_offers_to_complete_payment(self):
        payments.start_payment(self.order)
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("orders:detail", args=[self.order.order_number])), "Complete payment")

    def test_other_users_cannot_see_or_cancel(self):
        self.pay()
        self.client.force_login(make_user(username="x", email="x@example.com", mobile="9444444444"))
        self.assertEqual(self.client.get(reverse("orders:detail", args=[self.order.order_number])).status_code, 404)
        self.assertEqual(self.client.post(reverse("orders:cancel", args=[self.order.order_number])).status_code, 404)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, S.PAID)

    def test_cancel_view_and_get_not_allowed(self):
        self.pay()
        self.client.force_login(self.user)
        url = reverse("orders:cancel", args=[self.order.order_number])
        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, follow=True)
        self.assertContains(response, "refund is on its way")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, S.CANCELLED)

    def test_login_required(self):
        for url in (reverse("orders:list"), reverse("orders:detail", args=[self.order.order_number])):
            self.assertEqual(self.client.get(url).status_code, 302)


class AdminTests(ManagementBase):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_superuser("root", "root@example.com", "Sturdy#Pass9271", mobile="9000000001", full_name="Root")
        self.client.force_login(self.staff)
        self.pay()

    def act(self, action):
        return self.client.post(reverse("admin:orders_order_changelist"), {"action": action, "_selected_action": [self.order.pk]}, follow=True)

    def test_dashboard_and_pages_load(self):
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "Last 7 days")
        self.assertContains(response, self.order.order_number)
        for name in ("orders_order", "orders_coupon", "payments_payment", "payments_webhookevent", "notifications_notification", "accounts_user"):
            self.assertEqual(self.client.get(reverse(f"admin:{name}_changelist")).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("admin:orders_order_change", args=[self.order.pk])).status_code, 200)

    def test_orders_are_searchable_by_razorpay_id(self):
        response = self.client.get(reverse("admin:orders_order_changelist"), {"q": "pay_1"})
        self.assertContains(response, self.order.order_number)

    def test_status_actions_follow_the_state_machine(self):
        self.act("mark_processing")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, S.PROCESSING)
        self.assertEqual(self.order.history.filter(status=S.PROCESSING, changed_by=self.staff).count(), 1)
        response = self.act("mark_delivered")  # not allowed from processing
        self.assertContains(response, "can&#x27;t be moved")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, S.PROCESSING)

    def test_cannot_add_or_delete_orders(self):
        self.assertEqual(self.client.get(reverse("admin:orders_order_add")).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin:orders_order_delete", args=[self.order.pk]), {"post": "yes"}).status_code, 403)

    def test_csv_export(self):
        response = self.act("export_csv")
        rows = list(csv.reader(io.StringIO(response.content.decode())))
        self.assertEqual(rows[0][0], "Order")
        self.assertEqual(rows[1][0], self.order.order_number)
        self.assertIn("pay_1", rows[1])
        self.assertIn("2x", rows[1][7])

    def test_csv_neutralises_formula_injection(self):
        Order.objects.filter(pk=self.order.pk).update(ship_name="=HYPERLINK(\"http://evil\")")
        response = self.act("export_csv")
        self.assertIn("'=HYPERLINK", response.content.decode())

    def test_detail_page_status_edit_uses_state_machine(self):
        """Editing the status field on the detail page can't skip the rules."""
        from apps.orders.admin import OrderAdmin
        from django.contrib import admin as dj_admin
        model_admin = OrderAdmin(Order, dj_admin.site)

        class FakeForm:
            changed_data = ["status"]

        from django.test import RequestFactory
        request = RequestFactory().post("/")
        request.user = self.staff
        request._messages = type("M", (), {"add": lambda *a, **k: None})()
        self.order.status = S.DELIVERED  # illegal jump from PAID
        model_admin.save_model(request, self.order, FakeForm(), True)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, S.PAID)

    def test_coupon_admin_counts_usage(self):
        make_coupon("ADMINTEST")
        response = self.client.get(reverse("admin:orders_coupon_changelist"))
        self.assertContains(response, "ADMINTEST")
