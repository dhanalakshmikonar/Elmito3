"""Stock operations. All mutations lock rows and are idempotent per order."""
from collections import defaultdict

from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest

from apps.orders.models import Order
from apps.products.models import Product, ProductVariant, StockMovement


class InsufficientStock(Exception):
    def __init__(self, variant, requested, available):
        self.variant, self.requested, self.available = variant, requested, available
        super().__init__(f"{variant}: requested {requested}, available {available}")


def available_quantity(variant: ProductVariant) -> int:
    """Units that can be sold right now (0 for inactive variants or non-active products)."""
    if not variant.is_active or variant.product.status != Product.Status.ACTIVE:
        return 0
    return variant.stock


def log_movement(variant, delta, reason, *, order=None, note=""):
    StockMovement.objects.create(variant=variant, sku=variant.sku, delta=delta, reason=reason, order=order, note=note)


def _quantities_by_variant(order):
    needed = defaultdict(int)
    for item in order.items.filter(variant__isnull=False):
        needed[item.variant_id] += item.quantity
    return needed


@transaction.atomic
def reduce_stock_for_order(order) -> bool:
    """Deduct stock for a paid order. Returns False if already done (idempotent).

    Raises InsufficientStock (and changes nothing) if any line cannot be fulfilled."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.inventory_reduced:
        return False

    needed = _quantities_by_variant(order)
    # Lock in a consistent order to avoid deadlocks between concurrent orders.
    variants = {
        v.pk: v
        for v in ProductVariant.objects.select_for_update().filter(pk__in=needed.keys()).order_by("pk")
    }
    for variant_id, qty in needed.items():
        if variants[variant_id].stock < qty:
            raise InsufficientStock(variants[variant_id], qty, variants[variant_id].stock)

    for variant_id, qty in needed.items():
        variant = variants[variant_id]
        ProductVariant.objects.filter(pk=variant_id).update(stock=F("stock") - qty)
        Product.objects.filter(pk=variant.product_id).update(popularity=F("popularity") + qty)
        log_movement(variant, -qty, StockMovement.Reason.ORDER_PAID, order=order)

    Order.objects.filter(pk=order.pk).update(inventory_reduced=True)
    return True


@transaction.atomic
def restore_stock_for_order(order) -> bool:
    """Return stock for a cancelled/returned order. Idempotent."""
    order = Order.objects.select_for_update().get(pk=order.pk)
    if not order.inventory_reduced:
        return False

    needed = _quantities_by_variant(order)
    variants = {
        v.pk: v
        for v in ProductVariant.objects.select_for_update().filter(pk__in=needed.keys()).order_by("pk")
    }
    for variant_id, qty in needed.items():
        variant = variants[variant_id]
        ProductVariant.objects.filter(pk=variant_id).update(stock=F("stock") + qty)
        Product.objects.filter(pk=variant.product_id).update(
            popularity=Greatest(F("popularity") - qty, Value(0))
        )
        log_movement(variant, qty, StockMovement.Reason.ORDER_RESTOCK, order=order)

    Order.objects.filter(pk=order.pk).update(inventory_reduced=False)
    return True
