"""Turn a validated cart into an order. Safe to call repeatedly for the same cart."""
import hashlib
import json

from django.db import IntegrityError, transaction

from apps.cart.services import cart as cart_service
from apps.orders.models import Order, OrderItem, OrderStatusHistory


class CheckoutError(Exception):
    pass


def _fingerprint(user, summary, address) -> str:
    payload = {
        "user": user.pk,
        "lines": sorted((l.variant.pk, l.quantity, str(l.unit_price)) for l in summary.lines),
        "coupon": summary.coupon.code if summary.coupon else "",
        "total": str(summary.total),
        "address": [address.full_name, address.phone, address.line1, address.city, address.state, address.pincode, address.landmark],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _open_order(user, key):
    return Order.objects.filter(
        user=user, idempotency_key=key, status__in=[Order.Status.PENDING, Order.Status.PAYMENT_PENDING]
    ).first()


def create_order_from_cart(*, user, cart, address) -> Order:
    """Re-price the cart from the database and create (or reuse) a pending order."""
    if address.user_id != user.pk:
        raise CheckoutError("That address doesn't belong to your account.")

    summary = cart_service.refresh(cart, user)
    if not summary.lines:
        raise CheckoutError("Your cart is empty.")
    if summary.coupon_error:
        raise CheckoutError(f"{summary.coupon_error} It has been removed from your cart.")
    if summary.has_problems:
        raise CheckoutError("Some items are no longer available in the quantity you chose. Please review your cart.")

    key = _fingerprint(user, summary, address)
    existing = _open_order(user, key)
    if existing:
        return existing

    try:
        with transaction.atomic():
            order = Order.objects.create(
                user=user,
                subtotal=summary.subtotal,
                discount_total=summary.discount,
                shipping_charge=summary.shipping,
                total=summary.total,
                coupon=summary.coupon,
                coupon_code=summary.coupon.code if summary.coupon else "",
                ship_name=address.full_name,
                ship_phone=address.phone,
                ship_line1=address.line1,
                ship_city=address.city,
                ship_state=address.state,
                ship_pincode=address.pincode,
                ship_landmark=address.landmark,
                idempotency_key=key,
            )
            OrderItem.objects.bulk_create(
                OrderItem(
                    order=order,
                    variant=line.variant,
                    product_name=line.product.name,
                    sku=line.variant.sku,
                    color=line.variant.color,
                    size=line.variant.size,
                    unit_price=line.unit_price,
                    quantity=line.quantity,
                    line_total=line.line_total,
                )
                for line in summary.lines
            )
            OrderStatusHistory.objects.create(order=order, status=order.status, note="Order created")
            # Supersede earlier unpaid drafts. Orders already awaiting payment are left alone
            # because a payment may be in flight for them.
            Order.objects.filter(user=user, status=Order.Status.PENDING).exclude(pk=order.pk).update(
                status=Order.Status.CANCELLED
            )
    except IntegrityError:  # a concurrent request created the same order first
        existing = _open_order(user, key)
        if existing:
            return existing
        raise
    return order
