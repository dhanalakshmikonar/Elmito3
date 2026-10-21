from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.forms import OTPForm
from apps.accounts.services.otp import OTPError
from apps.cart.services import cart as cart_service
from apps.orders.models import Order
from apps.orders.services import flow, status as order_status
from apps.orders.services.checkout import CheckoutError, create_order_from_cart

ADDRESS_KEY = "checkout_address_id"


def _cart_or_redirect(request):
    """Return (cart, summary) or (None, redirect response) when checkout can't proceed."""
    cart = cart_service.get_cart(request)
    summary = cart_service.refresh(cart, request.user)
    if not summary.lines:
        messages.info(request, "Your cart is empty.")
        return None, redirect("cart:detail")
    if summary.coupon_error:
        messages.warning(request, f"{summary.coupon_error} The coupon was removed.")
        return None, redirect("cart:detail")
    if summary.has_problems:
        messages.error(request, "Some items are no longer available in the quantity you chose.")
        return None, redirect("cart:detail")
    return (cart, summary), None


def _selected_address(request):
    pk = request.session.get(ADDRESS_KEY)
    return request.user.addresses.filter(pk=pk).first() if pk else None


def _next_response(request, order):
    if flow.next_step(order) == "otp":
        try:
            if not flow.start_otp(order):
                messages.warning(request, "We couldn't send the code. Use “Resend code”.")
        except OTPError:
            pass  # a code was sent moments ago; the customer can use it
        return redirect("orders:verify_otp", order_number=order.order_number)
    flow.prepare_for_payment(order)
    return redirect("payments:start", order_number=order.order_number)


@login_required
def checkout(request):
    result, response = _cart_or_redirect(request)
    if response:
        return response
    _, summary = result
    addresses = list(request.user.addresses.all())
    if request.method == "POST":
        chosen = next((a for a in addresses if str(a.pk) == request.POST.get("address")), None)
        if chosen is None:
            messages.error(request, "Choose a delivery address.")
        else:
            request.session[ADDRESS_KEY] = chosen.pk
            return redirect("orders:review")
    selected = _selected_address(request)
    if selected is None and addresses:
        selected = next((a for a in addresses if a.is_default), addresses[0])
    return render(request, "orders/checkout.html", {"summary": summary, "addresses": addresses, "selected": selected})


@login_required
def review(request):
    result, response = _cart_or_redirect(request)
    if response:
        return response
    _, summary = result
    address = _selected_address(request)
    if address is None:
        return redirect("orders:checkout")
    return render(request, "orders/review.html", {
        "summary": summary, "address": address, "otp_required": settings.ORDER_OTP_STAGE == "before_payment",
    })


@login_required
@require_POST
def confirm(request):
    result, response = _cart_or_redirect(request)
    if response:
        return response
    cart, _ = result
    address = _selected_address(request)
    if address is None:
        return redirect("orders:checkout")
    try:
        order = create_order_from_cart(user=request.user, cart=cart, address=address)
    except CheckoutError as exc:
        messages.error(request, str(exc))
        return redirect("cart:detail")
    return _next_response(request, order)


def _open_order_or_redirect(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    if not flow.is_open(order):
        messages.info(request, "This order is no longer awaiting payment.")
        return order, redirect("core:home")
    return order, None


@login_required
def verify_otp(request, order_number):
    order, response = _open_order_or_redirect(request, order_number)
    if response:
        return response
    if not flow.requires_otp(order):
        return redirect("payments:start", order_number=order.order_number)
    form = OTPForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            flow.verify_order_otp(order, form.cleaned_data["code"])
        except OTPError as exc:
            form.add_error("code", exc.message)
        else:
            return redirect("payments:start", order_number=order.order_number)
    return render(request, "orders/verify_otp.html", {"form": form, "order": order})


@login_required
@require_POST
def resend_otp(request, order_number):
    order, response = _open_order_or_redirect(request, order_number)
    if response:
        return response
    try:
        if flow.start_otp(order):
            messages.success(request, "We've sent a new code to your email.")
        else:
            messages.warning(request, "We couldn't send the code. Please try again.")
    except OTPError as exc:
        messages.error(request, exc.message)
    return redirect("orders:verify_otp", order_number=order.order_number)


@login_required
def success(request, order_number):
    order = get_object_or_404(Order.objects.prefetch_related("items"), order_number=order_number, user=request.user)
    # Still waiting on the payment confirmation (callback/webhook). Stop auto-refreshing after 10 minutes.
    confirming = flow.is_open(order) and (timezone.now() - order.updated_at) < timedelta(minutes=10)
    return render(request, "orders/success.html", {"order": order, "confirming": confirming})


@login_required
def order_list(request):
    orders = request.user.orders.exclude(status=Order.Status.PENDING).prefetch_related("items")
    page = Paginator(orders, 10).get_page(request.GET.get("page"))
    return render(request, "orders/list.html", {"page_obj": page})


@login_required
def order_detail(request, order_number):
    order = get_object_or_404(Order.objects.prefetch_related("items", "history"), order_number=order_number, user=request.user)
    return render(request, "orders/detail.html", {
        "order": order,
        "timeline": [h for h in order.history.all()][::-1],
        "can_cancel": order_status.can_customer_cancel(order),
        "can_pay": flow.is_open(order) and not order.is_paid and order.status != Order.Status.PENDING,
    })


@login_required
@require_POST
def cancel(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    try:
        was_paid = order.is_paid
        order_status.cancel_by_customer(order, by=request.user)
    except order_status.TransitionError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Order cancelled." + (" Your refund is on its way and should arrive in 5–7 working days." if was_paid else ""))
    return redirect("orders:detail", order_number=order.order_number)
