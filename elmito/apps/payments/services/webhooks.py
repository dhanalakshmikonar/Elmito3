import hashlib
import json
import logging

from django.db import IntegrityError

from apps.payments.models import WebhookEvent
from apps.payments.services import payment as payment_service

logger = logging.getLogger(__name__)


def _entity(payload, key):
    return (payload.get("payload", {}).get(key) or {}).get("entity") or {}


def process(body: bytes, event_id: str = "") -> str:
    """Handle a signature-verified webhook. Returns a short result label. Idempotent per event."""
    event = json.loads(body)
    event_type = event.get("event", "")
    event_id = event_id or hashlib.sha256(body).hexdigest()

    try:
        record, created = WebhookEvent.objects.get_or_create(event_id=event_id, defaults={"event_type": event_type[:60]})
    except IntegrityError:
        record, created = WebhookEvent.objects.get(event_id=event_id), False
    if not created and record.processed:
        return "duplicate_event"

    result = _dispatch(event_type, event)
    WebhookEvent.objects.filter(pk=record.pk).update(processed=True)
    return result


def _dispatch(event_type, event) -> str:
    pay, order = _entity(event, "payment"), _entity(event, "order")
    razorpay_order_id = pay.get("order_id") or order.get("id") or ""
    razorpay_payment_id = pay.get("id") or ""

    if event_type in ("payment.captured", "order.paid"):
        if event_type == "payment.captured" and pay.get("status") not in (None, "captured"):
            return "ignored"
        if not (razorpay_order_id and razorpay_payment_id):
            return "ignored"
        try:
            outcome = payment_service.mark_paid(
                razorpay_order_id=razorpay_order_id, razorpay_payment_id=razorpay_payment_id, amount_paise=pay.get("amount"),
            )
        except payment_service.UnknownPayment:
            return "unknown_order"  # not one of ours (or from another environment)
        return outcome.status

    if event_type == "payment.failed" and razorpay_order_id:
        error = pay.get("error_description") or pay.get("error_reason") or "Payment failed"
        return "failed_recorded" if payment_service.mark_failed(razorpay_order_id=razorpay_order_id, reason=error) else "ignored"

    return "ignored"
