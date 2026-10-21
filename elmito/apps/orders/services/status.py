"""The only place an order's status changes after payment. Validates the transition, handles
stock and refunds, writes the history, and announces the change for notifications."""
import logging

from django.db import transaction

from apps.orders.models import Order, OrderStatusHistory
from apps.orders.signals import order_status_changed
from apps.payments.services.payment import PaymentError, refund_order_payment
from apps.products.services.inventory import restore_stock_for_order

logger = logging.getLogger(__name__)
S = Order.Status

ALLOWED = {
    S.PENDING: {S.PAYMENT_PENDING, S.CANCELLED},
    S.PAYMENT_PENDING: {S.PAID, S.CANCELLED},
    S.PAID: {S.PROCESSING, S.CANCELLED, S.REFUNDED},
    S.PROCESSING: {S.PACKED, S.CANCELLED, S.REFUNDED},
    S.PACKED: {S.SHIPPED, S.CANCELLED, S.REFUNDED},
    S.SHIPPED: {S.OUT_FOR_DELIVERY, S.DELIVERED, S.RETURNED},
    S.OUT_FOR_DELIVERY: {S.DELIVERED, S.RETURNED},
    S.DELIVERED: {S.RETURNED, S.REFUNDED},
    S.RETURNED: {S.REFUNDED},
    S.CANCELLED: {S.REFUNDED},
    S.REFUNDED: set(),
}
# Reached only by the checkout and payment code, never by hand.
SYSTEM_ONLY = {S.PENDING, S.PAYMENT_PENDING, S.PAID}
CUSTOMER_CANCELLABLE = {S.PENDING, S.PAYMENT_PENDING, S.PAID, S.PROCESSING}


class TransitionError(Exception):
    """Message is safe to show to staff or the customer."""


def can_customer_cancel(order) -> bool:
    return order.status in CUSTOMER_CANCELLABLE


def next_statuses(order):
    """Statuses staff may move this order to."""
    return [s for s in ALLOWED[order.status] if s not in SYSTEM_ONLY]


@transaction.atomic
def change_status(order, new_status, *, by=None, note="") -> Order:
    order = Order.objects.select_for_update().get(pk=order.pk)
    old = order.status
    if new_status == old:
        raise TransitionError(f"Order is already {order.get_status_display().lower()}.")
    if new_status not in ALLOWED.get(old, set()):
        raise TransitionError(f"An order that is {order.get_status_display().lower()} can't be moved to {Order.Status(new_status).label.lower()}.")
    if by is not None and new_status in SYSTEM_ONLY:
        raise TransitionError("That status is set automatically.")

    try:
        if new_status == S.CANCELLED:
            restore_stock_for_order(order)  # no-op when stock was never taken
            refund_order_payment(order)
        elif new_status == S.REFUNDED and order.payment_status == Order.PaymentStatus.PAID:
            refund_order_payment(order)
    except PaymentError as exc:
        raise TransitionError(str(exc))

    order.status = new_status
    order.save(update_fields=["status", "payment_status", "updated_at"])
    OrderStatusHistory.objects.create(order=order, status=new_status, note=note[:255], changed_by=by)
    transaction.on_commit(lambda: _announce(order, old, new_status))
    return order


def _announce(order, old, new):
    for receiver, result in order_status_changed.send_robust(sender=Order, order=order, old_status=old, new_status=new):
        if isinstance(result, Exception):
            logger.error("order_status_changed receiver %s failed: %s", getattr(receiver, "__name__", receiver), type(result).__name__)


def cancel_by_customer(order, *, by) -> Order:
    if order.user_id != by.pk:
        raise TransitionError("That order isn't yours.")
    if not can_customer_cancel(order):
        raise TransitionError("This order has already been packed or shipped, so it can't be cancelled here. Please contact us.")
    return change_status(order, S.CANCELLED, by=by, note="Cancelled by customer")
