from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.cart.services import cart as cart_service
from apps.cart.services.cart import CartError
from apps.orders.services.coupons import CouponError
from apps.products.models import Product


def _line_image(line):
    images = list(line.product.images.all())
    match = next((i for i in images if i.color.lower() == line.variant.color.lower()), None)
    return match or (images[0] if images else None)


def detail(request):
    cart = cart_service.get_cart(request)
    summary = cart_service.refresh(cart, request.user)
    if summary.coupon_error:
        messages.warning(request, f"{summary.coupon_error} The coupon was removed.")
    for line in summary.lines:
        line.image = _line_image(line)
    return render(request, "cart/detail.html", {"summary": summary, "cart": cart})


@require_POST
def add(request):
    post = request.POST
    product = Product.objects.filter(pk=post.get("product") or 0).first()
    fallback = product.get_absolute_url() if product else "/shop/"
    try:
        variant = cart_service.resolve_variant(
            product_id=post.get("product"), variant_id=post.get("variant"),
            color=post.get("color", ""), size=post.get("size", ""),
        )
        cart_service.add_item(cart_service.get_cart(request, create=True), variant, post.get("quantity", 1))
    except CartError as exc:
        messages.error(request, str(exc))
        return redirect(fallback)
    if post.get("action") == "buy_now":
        return redirect("orders:checkout")
    messages.success(request, f"{variant.product.name} ({variant.color}, {variant.size}) added to your cart.")
    return redirect("cart:detail")


@require_POST
def update(request, item_id):
    cart = cart_service.get_cart(request)
    try:
        if cart is None:
            raise CartError("Your cart is empty.")
        cart_service.set_quantity(cart, item_id, request.POST.get("quantity"))
    except CartError as exc:
        messages.error(request, str(exc))
    return redirect("cart:detail")


@require_POST
def remove(request, item_id):
    cart = cart_service.get_cart(request)
    try:
        if cart is None:
            raise CartError("Your cart is empty.")
        cart_service.remove_item(cart, item_id)
        messages.success(request, "Item removed.")
    except CartError as exc:
        messages.error(request, str(exc))
    return redirect("cart:detail")


@require_POST
def apply_coupon(request):
    cart = cart_service.get_cart(request)
    try:
        if cart is None:
            raise CouponError("Your cart is empty.")
        result = cart_service.apply_coupon(cart, request.POST.get("code", ""), request.user)
        messages.success(request, f"Coupon {result.coupon.code} applied.")
    except CouponError as exc:
        messages.error(request, str(exc))
    return redirect("cart:detail")


@require_POST
def remove_coupon(request):
    cart = cart_service.get_cart(request)
    if cart:
        cart_service.remove_coupon(cart)
    return redirect("cart:detail")
