"""Where an order goes next. The OTP stage lives here so it can change without touching views.

ORDER_OTP_STAGE:
  before_payment  customer must verify an emailed code before paying (default)
  none            no order OTP
"""
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import OTP
from apps.accounts.services.otp import issue_otp, verify_otp
from apps.notifications.services.email import send_otp_email
from apps.orders.models import Order, OrderStatusHistory

OPEN_STATUSES = (Order.Status.PENDING, Order.Status.PAYMENT_PENDING)


def is_open(order) -> bool:
    return order.status in OPEN_STATUSES and order.payment_status in (
        Order.PaymentStatus.PENDING, Order.PaymentStatus.FAILED)


def requires_otp(order) -> bool:
    return settings.ORDER_OTP_STAGE == "before_payment" and order.otp_verified_at is None


def next_step(order) -> str:
    """'otp' or 'payment'."""
    return "otp" if requires_otp(order) else "payment"


def prepare_for_payment(order) -> None:
    updated = Order.objects.filter(pk=order.pk, status=Order.Status.PENDING).update(status=Order.Status.PAYMENT_PENDING)
    if updated:
        order.status = Order.Status.PAYMENT_PENDING
        OrderStatusHistory.objects.create(order=order, status=order.status, note="Ready for payment")


def start_otp(order) -> bool:
    """Send the order OTP. Raises OTPError on cooldown/rate limit. Returns False if delivery failed."""
    code = issue_otp(order.user, OTP.Purpose.ORDER, reference=order.order_number)
    return send_otp_email(order.user, code, OTP.Purpose.ORDER)


@transaction.atomic
def verify_order_otp(order, code) -> None:
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.otp_verified_at is not None:
        return  # already verified; replaying is harmless
    verify_otp(order.user, OTP.Purpose.ORDER, code, reference=order.order_number)
    order.otp_verified_at = timezone.now()
    order.save(update_fields=["otp_verified_at", "updated_at"])
    prepare_for_payment(order)
