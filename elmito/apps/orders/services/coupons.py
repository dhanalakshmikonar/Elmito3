"""Server-side coupon validation. The browser never decides a discount."""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from apps.orders.models import Coupon, CouponRedemption

CENT = Decimal("0.01")


class CouponError(Exception):
    pass


@dataclass(frozen=True)
class CouponResult:
    coupon: Coupon
    discount: Decimal


def compute_discount(coupon: Coupon, subtotal: Decimal) -> Decimal:
    if coupon.discount_type == Coupon.DiscountType.PERCENT:
        discount = subtotal * coupon.value / Decimal(100)
    else:
        discount = coupon.value
    if coupon.max_discount is not None:
        discount = min(discount, coupon.max_discount)
    discount = min(discount, subtotal)  # never below zero
    return max(discount, Decimal(0)).quantize(CENT, rounding=ROUND_HALF_UP)


def validate_coupon(code: str, *, subtotal: Decimal, user=None) -> CouponResult:
    code = (code or "").strip().upper()
    if not code:
        raise CouponError("Enter a coupon code.")
    coupon = Coupon.objects.filter(code=code).first()
    now = timezone.now()
    # One message for unknown/inactive/outside-window so codes can't be probed.
    if coupon is None or not coupon.is_active or not (coupon.starts_at <= now < coupon.expires_at):
        raise CouponError("This coupon isn't valid.")
    if subtotal < coupon.min_order_value:
        raise CouponError(f"This coupon needs a minimum order of ₹{coupon.min_order_value:,.0f}.")
    if coupon.usage_limit is not None and coupon.redemptions.count() >= coupon.usage_limit:
        raise CouponError("This coupon has been fully redeemed.")
    if user is not None and getattr(user, "is_authenticated", False):
        used = CouponRedemption.objects.filter(coupon=coupon, user=user).count()
        if used >= coupon.per_user_limit:
            raise CouponError("You've already used this coupon.")
    discount = compute_discount(coupon, subtotal)
    if discount <= 0:
        raise CouponError("This coupon doesn't apply to your order.")
    return CouponResult(coupon=coupon, discount=discount)
