"""Payment lifecycle. `mark_paid` is the single, idempotent path to a paid order; the browser
callback and the webhook both go through it."""
import logging
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.cart.models import Cart, CartItem
from apps.orders.models import CouponRedemption, Order, OrderStatusHistory
from apps.orders.services import flow
from apps.orders.signals import order_paid
from apps.payments.models import Payment
from apps.payments.services import gateway
from apps.products.services.inventory import InsufficientStock, available_quantity, reduce_stock_for_order

logger = logging.getLogger(__name__)
REFUND_PENDING = "refund_pending"


class PaymentError(Exception):
    """Message is safe to show to the customer."""


class UnknownPayment(Exception):
    pass


@dataclass
class PaymentOutcome:
    order: Order
    payment: Payment
    status: str  # paid | already_paid | refunded | refund_failed | duplicate | mismatch
    needs_refund: bool = False
    refund_order: bool = False  # also move the order to REFUNDED (False for a duplicate payment)


def _note(order, note, by=None):
    OrderStatusHistory.objects.create(order=order, status=order.status, note=note[:255], changed_by=by)


# ---------- starting a payment ----------

def _ensure_sellable(order):
    needed = {}
    for item in order.items.select_related("variant__product"):
        if item.variant is None:
            raise PaymentError("An item in your order is no longer available.")
        needed[item.variant] = needed.get(item.variant, 0) + item.quantity
    for variant, quantity in needed.items():
        if available_quantity(variant) < quantity:
            raise PaymentError(f"{variant.product.name} ({variant.color}, {variant.size}) just sold out. Please update your cart.")


def start_payment(order) -> Payment:
    """Create (or reuse) the Razorpay order for this order. Safe to call on every page load."""
    with transaction.atomic():
        order = Order.objects.select_for_update().get(pk=order.pk)
        if not flow.is_open(order):
            raise PaymentError("This order can no longer be paid.")
        if flow.requires_otp(order):
            raise PaymentError("Please confirm your order with the emailed code first.")
        _ensure_sellable(order)
        flow.prepare_for_payment(order)

        existing = order.payments.filter(status__in=[Payment.Status.PENDING, Payment.Status.FAILED]).first()
        if existing is None:
            amount = order.total
            try:
                data = gateway.get_gateway().create_order(
                    amount_paise=gateway.to_paise(amount), receipt=order.order_number,
                    notes={"order_number": order.order_number},
                )
            except gateway.GatewayError:
                raise PaymentError("We couldn't start the payment. Please try again in a moment.")
            existing = Payment.objects.create(order=order, amount=amount, razorpay_order_id=data["id"])
        elif existing.status == Payment.Status.FAILED:
            existing.status = Payment.Status.PENDING  # same Razorpay order can be retried
            existing.save(update_fields=["status", "updated_at"])
        if order.payment_status != Order.PaymentStatus.PENDING:
            order.payment_status = Order.PaymentStatus.PENDING
            order.save(update_fields=["payment_status", "updated_at"])
        return existing


# ---------- confirming a payment ----------

def _finish_order(order):
    """Everything that follows a successful, fulfillable payment (inside the caller's transaction)."""
    if order.coupon_id:
        CouponRedemption.objects.get_or_create(order=order, defaults={"coupon_id": order.coupon_id, "user": order.user})
    variant_ids = [v for v in order.items.values_list("variant_id", flat=True) if v]
    CartItem.objects.filter(cart__user=order.user, variant_id__in=variant_ids).delete()
    cart = Cart.objects.filter(user=order.user).first()
    if cart and cart.coupon_id and not cart.items.exists():
        cart.coupon = None
        cart.save(update_fields=["coupon", "updated_at"])


def _fire_order_paid(order):
    def send():
        for receiver, result in order_paid.send_robust(sender=Order, order=order):
            if isinstance(result, Exception):
                logger.error("order_paid receiver %s failed: %s", getattr(receiver, "__name__", receiver), type(result).__name__)
    transaction.on_commit(send)


def _apply(payment, order, razorpay_payment_id, amount_paise) -> PaymentOutcome:
    if payment.status == Payment.Status.PAID:
        if payment.razorpay_payment_id == razorpay_payment_id:
            retry = payment.failure_reason == REFUND_PENDING and order.status == Order.Status.CANCELLED
            return PaymentOutcome(order, payment, "already_paid", needs_refund=retry, refund_order=retry)
        logger.error("Second payment on already-paid Razorpay order %s", payment.razorpay_order_id)
        return PaymentOutcome(order, payment, "duplicate")
    if payment.status == Payment.Status.REFUNDED:
        return PaymentOutcome(order, payment, "refunded")

    if amount_paise is not None and amount_paise != gateway.to_paise(payment.amount):
        payment.status, payment.failure_reason = Payment.Status.FAILED, "Amount mismatch"
        payment.save(update_fields=["status", "failure_reason", "updated_at"])
        _note(order, "Payment rejected: amount did not match the order")
        logger.error("Amount mismatch on order %s", order.order_number)
        return PaymentOutcome(order, payment, "mismatch")

    if order.payment_status == Order.PaymentStatus.PAID:
        # The customer paid this order twice through different attempts; refund the extra one.
        payment.razorpay_payment_id = razorpay_payment_id
        payment.status, payment.failure_reason = Payment.Status.FAILED, REFUND_PENDING
        payment.save(update_fields=["razorpay_payment_id", "status", "failure_reason", "updated_at"])
        _note(order, "Duplicate payment received; refunding the extra payment")
        return PaymentOutcome(order, payment, "duplicate", needs_refund=True, refund_order=False)

    now = timezone.now()
    payment.razorpay_payment_id = razorpay_payment_id
    payment.status, payment.paid_at, payment.failure_reason = Payment.Status.PAID, now, ""
    payment.save(update_fields=["razorpay_payment_id", "status", "paid_at", "failure_reason", "updated_at"])
    order.payment_status, order.paid_at = Order.PaymentStatus.PAID, now

    if order.status in (Order.Status.PENDING, Order.Status.PAYMENT_PENDING):
        try:
            reduce_stock_for_order(order)  # runs in a savepoint; a failure leaves the payment record intact
        except InsufficientStock:
            order.status = Order.Status.CANCELLED
            order.save(update_fields=["status", "payment_status", "paid_at", "updated_at"])
            payment.failure_reason = REFUND_PENDING
            payment.save(update_fields=["failure_reason", "updated_at"])
            _note(order, "Item sold out before payment completed; refunding automatically")
            return PaymentOutcome(order, payment, "paid", needs_refund=True, refund_order=True)
        order.status = Order.Status.PAID
        order.save(update_fields=["status", "payment_status", "paid_at", "updated_at"])
        _note(order, "Payment received")
        _finish_order(order)
        _fire_order_paid(order)
        return PaymentOutcome(order, payment, "paid")

    order.save(update_fields=["payment_status", "paid_at", "updated_at"])
    payment.failure_reason = REFUND_PENDING
    payment.save(update_fields=["failure_reason", "updated_at"])
    _note(order, f"Payment received on a {order.get_status_display().lower()} order; refunding automatically")
    return PaymentOutcome(order, payment, "paid", needs_refund=True, refund_order=True)


def _refund(outcome: PaymentOutcome):
    payment = outcome.payment
    try:
        gateway.get_gateway().refund(payment.razorpay_payment_id, gateway.to_paise(payment.amount))
    except gateway.GatewayError:
        _note(outcome.order, "Automatic refund failed. Refund this payment manually in Razorpay.")
        outcome.status = "refund_failed"
        return
    with transaction.atomic():
        payment = Payment.objects.select_for_update().get(pk=payment.pk)
        order = Order.objects.select_for_update().get(pk=outcome.order.pk)
        payment.status, payment.failure_reason = Payment.Status.REFUNDED, ""
        payment.save(update_fields=["status", "failure_reason", "updated_at"])
        if outcome.refund_order:
            order.payment_status, order.status = Order.PaymentStatus.REFUNDED, Order.Status.REFUNDED
            order.save(update_fields=["payment_status", "status", "updated_at"])
        _note(order, "Refunded automatically")
        outcome.order, outcome.payment = order, payment
    outcome.status = "refunded"


def mark_paid(*, razorpay_order_id, razorpay_payment_id, amount_paise=None) -> PaymentOutcome:
    """Idempotent. Call only with a payment whose authenticity is already proven
    (verified checkout signature, or a verified webhook)."""
    with transaction.atomic():
        payment = Payment.objects.select_for_update().filter(razorpay_order_id=razorpay_order_id).first()
        if payment is None:
            raise UnknownPayment(razorpay_order_id)
        order = Order.objects.select_for_update().get(pk=payment.order_id)
        outcome = _apply(payment, order, razorpay_payment_id, amount_paise)
    if outcome.needs_refund:
        _refund(outcome)
    return outcome


def mark_failed(*, razorpay_order_id, reason="", user=None) -> bool:
    """Record a failed attempt. Never downgrades a paid order. Returns True if anything changed."""
    with transaction.atomic():
        qs = Payment.objects.select_for_update().filter(razorpay_order_id=razorpay_order_id)
        if user is not None:
            qs = qs.filter(order__user=user)
        payment = qs.first()
        if payment is None or payment.status != Payment.Status.PENDING:
            return False
        order = Order.objects.select_for_update().get(pk=payment.order_id)
        if order.payment_status == Order.PaymentStatus.PAID:
            return False
        payment.status, payment.failure_reason = Payment.Status.FAILED, (reason or "Payment failed")[:255]
        payment.save(update_fields=["status", "failure_reason", "updated_at"])
        order.payment_status = Order.PaymentStatus.FAILED
        order.save(update_fields=["payment_status", "updated_at"])
        _note(order, "Payment attempt failed")
        return True


def refund_order_payment(order) -> bool:
    """Refund the order's paid payment through Razorpay. Caller holds the order lock and saves the order.

    Returns False if there was nothing to refund. Raises PaymentError (changing nothing) if Razorpay refuses."""
    payment = order.payments.select_for_update().filter(status=Payment.Status.PAID).first()
    if payment is None:
        return False
    try:
        gateway.get_gateway().refund(payment.razorpay_payment_id, gateway.to_paise(payment.amount))
    except gateway.GatewayError:
        raise PaymentError("The refund could not be processed. Please try again, or refund it from the Razorpay dashboard.")
    payment.status, payment.failure_reason = Payment.Status.REFUNDED, ""
    payment.save(update_fields=["status", "failure_reason", "updated_at"])
    order.payment_status = Order.PaymentStatus.REFUNDED
    return True
