import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.orders.models import Order
from apps.orders.services import flow
from apps.payments.services import gateway, payment as payments, webhooks

logger = logging.getLogger(__name__)


@login_required
def start(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    if order.is_paid:
        return redirect("orders:success", order_number=order.order_number)
    if flow.requires_otp(order):
        return redirect("orders:verify_otp", order_number=order.order_number)
    if not flow.is_open(order):
        messages.info(request, "This order is no longer awaiting payment.")
        return redirect("core:home")
    if not gateway.is_configured():
        logger.error("Razorpay keys are not configured")
        return render(request, "payments/unavailable.html", status=503)
    try:
        payment = payments.start_payment(order)
    except payments.PaymentError as exc:
        messages.error(request, str(exc))
        return redirect("cart:detail")

    config = {
        "key": settings.RAZORPAY_KEY_ID,  # public key id only; the secret never leaves the server
        "amount": gateway.to_paise(payment.amount),
        "currency": payment.currency,
        "name": "EL MITO",
        "description": f"Order {order.order_number}",
        "order_id": payment.razorpay_order_id,
        "prefill": {"name": order.user.full_name, "email": order.user.email, "contact": order.ship_phone},
        "theme": {"color": "#17181A"},
        "callbackUrl": reverse("payments:callback"),
        "failedUrl": reverse("payments:failed"),
        "retryUrl": reverse("payments:start", args=[order.order_number]),
    }
    return render(request, "payments/start.html", {"order": order, "config": config})


@csrf_exempt  # Authenticated by the HMAC signature, not by cookies: Razorpay's redirect flow is cross-site.
@require_POST
def callback(request):
    order_id = request.POST.get("razorpay_order_id", "")
    payment_id = request.POST.get("razorpay_payment_id", "")
    signature = request.POST.get("razorpay_signature", "")
    if not gateway.verify_checkout_signature(order_id, payment_id, signature):
        logger.warning("Rejected payment callback with invalid signature")
        return render(request, "payments/failed.html", status=400)
    try:
        outcome = payments.mark_paid(razorpay_order_id=order_id, razorpay_payment_id=payment_id)
    except payments.UnknownPayment:
        return render(request, "payments/failed.html", status=404)
    return redirect("orders:success", order_number=outcome.order.order_number)


@login_required
@require_POST
def failed(request):
    """The browser reports a failed/abandoned attempt. Only records it; never changes a paid order."""
    changed = payments.mark_failed(
        razorpay_order_id=request.POST.get("razorpay_order_id", ""),
        reason=request.POST.get("reason", "")[:200], user=request.user,
    )
    return JsonResponse({"recorded": changed})


@csrf_exempt  # Authenticated by the webhook signature.
@require_POST
def webhook(request):
    body = request.body
    if not gateway.verify_webhook_signature(body, request.headers.get("X-Razorpay-Signature", "")):
        logger.warning("Rejected webhook with invalid signature")
        return HttpResponseBadRequest("invalid signature")
    try:
        result = webhooks.process(body, request.headers.get("X-Razorpay-Event-Id", ""))
    except (ValueError, json.JSONDecodeError):
        return HttpResponseBadRequest("invalid payload")
    except Exception:  # noqa: BLE001 - a 500 makes Razorpay retry the delivery
        logger.exception("Webhook processing failed")
        return HttpResponse(status=500)
    return JsonResponse({"result": result})
