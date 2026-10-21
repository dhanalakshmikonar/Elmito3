"""Cart operations and pricing. Every figure is derived from the database, never from the browser."""
import secrets
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum

from apps.cart.models import Cart, CartItem
from apps.orders.services.coupons import CouponError, validate_coupon
from apps.products.models import Product, ProductVariant
from apps.products.services.inventory import available_quantity

MAX_QTY_PER_ITEM = 10
MAX_LINES = 20
SESSION_KEY = "cart_token"
CENT = Decimal("0.01")


class CartError(Exception):
    pass


def money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


# ---------- lookup ----------

def get_cart(request, create=False):
    if request.user.is_authenticated:
        if create:
            return Cart.objects.get_or_create(user=request.user)[0]
        return Cart.objects.filter(user=request.user).first()
    token = request.session.get(SESSION_KEY)
    if token:
        cart = Cart.objects.filter(session_key=token, user__isnull=True).first()
        if cart:
            return cart
    if not create:
        return None
    token = secrets.token_urlsafe(16)
    request.session[SESSION_KEY] = token
    return Cart.objects.create(session_key=token)


def item_count(request) -> int:
    if request.user.is_authenticated:
        qs = CartItem.objects.filter(cart__user=request.user)
    else:
        token = request.session.get(SESSION_KEY)
        if not token:
            return 0
        qs = CartItem.objects.filter(cart__session_key=token)
    return qs.aggregate(n=Sum("quantity"))["n"] or 0


# ---------- mutation ----------

def _as_quantity(raw) -> int:
    try:
        quantity = int(raw)
    except (TypeError, ValueError):
        raise CartError("Enter a valid quantity.")
    if quantity < 1:
        raise CartError("Quantity must be at least 1.")
    return quantity


def resolve_variant(*, product_id, variant_id=None, color="", size="") -> ProductVariant:
    """Find the sellable variant a request refers to. Raises CartError if it can't be bought."""
    try:
        product_id = int(product_id)
        variant_id = int(variant_id) if variant_id not in (None, "") else None
    except (TypeError, ValueError):
        raise CartError("That product isn't available.")
    sellable = ProductVariant.objects.select_related("product").filter(
        is_active=True, product__status=Product.Status.ACTIVE, product__category__is_active=True, product_id=product_id
    )
    if variant_id:
        variant = sellable.filter(pk=variant_id).first()
        if variant and ((color and variant.color.lower() != color.lower()) or (size and variant.size != size.upper())):
            raise CartError("Your selection changed. Please choose your colour and size again.")
    elif color and size:
        variant = sellable.filter(color__iexact=color, size=size.upper()).first()
    else:
        raise CartError("Please select a size.")
    if variant is None:
        raise CartError("That colour and size isn't available.")
    return variant


def _limit_for(variant) -> int:
    return min(MAX_QTY_PER_ITEM, available_quantity(variant))


def _raise_over_limit(variant, limit):
    if limit <= 0:
        raise CartError("Sorry, that size is sold out.")
    available = available_quantity(variant)
    if available < MAX_QTY_PER_ITEM:
        raise CartError(f"Only {available} left in this size.")
    raise CartError(f"You can buy up to {MAX_QTY_PER_ITEM} of one item per order.")


@transaction.atomic
def add_item(cart, variant, quantity) -> CartItem:
    quantity = _as_quantity(quantity)
    Cart.objects.select_for_update().get(pk=cart.pk)  # serialise concurrent adds
    item = CartItem.objects.filter(cart=cart, variant=variant).first()
    if item is None and cart.items.count() >= MAX_LINES:
        raise CartError("Your cart is full. Please remove something first.")
    desired = (item.quantity if item else 0) + quantity
    limit = _limit_for(variant)
    if desired > limit:
        _raise_over_limit(variant, limit)
    if item:
        item.quantity = desired
        item.save(update_fields=["quantity", "updated_at"])
    else:
        item = CartItem.objects.create(cart=cart, variant=variant, quantity=desired)
    return item


@transaction.atomic
def set_quantity(cart, item_id, quantity):
    item = cart.items.select_related("variant__product").filter(pk=item_id).first()
    if item is None:
        raise CartError("That item isn't in your cart.")
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise CartError("Enter a valid quantity.")
    if quantity <= 0:
        item.delete()
        return None
    if quantity > item.quantity:  # decreasing is always allowed; increasing is capped
        limit = _limit_for(item.variant)
        if quantity > limit:
            _raise_over_limit(item.variant, limit)
    item.quantity = quantity
    item.save(update_fields=["quantity", "updated_at"])
    return item


def remove_item(cart, item_id):
    deleted, _ = cart.items.filter(pk=item_id).delete()
    if not deleted:
        raise CartError("That item isn't in your cart.")


def clear(cart):
    cart.items.all().delete()
    if cart.coupon_id:
        cart.coupon = None
        cart.save(update_fields=["coupon", "updated_at"])


@transaction.atomic
def merge_into_user_cart(session_cart, user):
    """Fold an anonymous cart into the user's cart on login."""
    user_cart = Cart.objects.select_for_update().filter(user=user).first()
    if user_cart is None:
        session_cart.user, session_cart.session_key = user, None
        session_cart.save(update_fields=["user", "session_key", "updated_at"])
        return session_cart
    for incoming in session_cart.items.select_related("variant__product"):
        limit = _limit_for(incoming.variant)
        if limit <= 0:
            continue
        existing = user_cart.items.filter(variant=incoming.variant).first()
        quantity = min((existing.quantity if existing else 0) + incoming.quantity, limit)
        if existing:
            existing.quantity = quantity
            existing.save(update_fields=["quantity", "updated_at"])
        else:
            CartItem.objects.create(cart=user_cart, variant=incoming.variant, quantity=quantity)
    if user_cart.coupon_id is None and session_cart.coupon_id:
        user_cart.coupon = session_cart.coupon
        user_cart.save(update_fields=["coupon", "updated_at"])
    session_cart.delete()
    return user_cart


# ---------- pricing ----------

@dataclass
class CartLine:
    item: CartItem
    variant: ProductVariant
    product: Product
    unit_price: Decimal
    quantity: int
    line_total: Decimal
    available: int
    problem: str = ""


@dataclass
class CartSummary:
    lines: list = field(default_factory=list)
    subtotal: Decimal = Decimal("0.00")
    discount: Decimal = Decimal("0.00")
    shipping: Decimal = Decimal("0.00")
    total: Decimal = Decimal("0.00")
    coupon: object = None
    coupon_error: str = ""
    free_shipping_remaining: Decimal = Decimal("0.00")

    @property
    def count(self):
        return sum(line.quantity for line in self.lines)

    @property
    def has_problems(self):
        return any(line.problem for line in self.lines)


def summarize(cart, user=None) -> CartSummary:
    summary = CartSummary()
    if cart is None:
        return summary
    items = cart.items.select_related("variant__product__category").prefetch_related("variant__product__images").order_by("created_at", "id")
    for item in items:
        variant, product = item.variant, item.variant.product
        unit = money(product.selling_price)
        available = available_quantity(variant)
        problem = ""
        if available <= 0:
            problem = "No longer available."
        elif item.quantity > available:
            problem = f"Only {available} left."
        summary.lines.append(CartLine(item, variant, product, unit, item.quantity, money(unit * item.quantity), available, problem))

    summary.subtotal = money(sum((l.line_total for l in summary.lines), Decimal(0)))
    if cart.coupon_id and summary.lines:
        try:
            result = validate_coupon(cart.coupon.code, subtotal=summary.subtotal, user=user)
            summary.coupon, summary.discount = result.coupon, result.discount
        except CouponError as exc:
            summary.coupon_error = str(exc)
    net = summary.subtotal - summary.discount
    if summary.lines and net < settings.FREE_SHIPPING_THRESHOLD:
        summary.shipping = money(settings.FLAT_SHIPPING_CHARGE)
        summary.free_shipping_remaining = money(settings.FREE_SHIPPING_THRESHOLD - net)
    summary.total = money(net + summary.shipping)
    return summary


def refresh(cart, user=None) -> CartSummary:
    """Summarise, and drop a coupon that has stopped being valid (the error stays on the summary)."""
    summary = summarize(cart, user)
    if summary.coupon_error and cart.coupon_id:
        cart.coupon = None
        cart.save(update_fields=["coupon", "updated_at"])
    return summary


def apply_coupon(cart, code, user=None):
    summary = summarize(cart, user)
    if not summary.lines:
        raise CouponError("Your cart is empty.")
    result = validate_coupon(code, subtotal=summary.subtotal, user=user)
    cart.coupon = result.coupon
    cart.save(update_fields=["coupon", "updated_at"])
    return result


def remove_coupon(cart):
    cart.coupon = None
    cart.save(update_fields=["coupon", "updated_at"])
