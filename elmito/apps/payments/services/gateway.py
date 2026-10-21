"""Thin wrapper around Razorpay. Everything else talks to this, never to the SDK directly,
so tests can substitute a fake and the provider can change later."""
import hashlib
import hmac
import logging

import razorpay
from django.conf import settings

logger = logging.getLogger(__name__)
TIMEOUT_SECONDS = 15


class GatewayError(Exception):
    """The payment provider call failed. Carries no provider details, so it is safe to log."""


def is_configured() -> bool:
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


def to_paise(amount) -> int:
    return int((amount * 100).to_integral_value())


def _hmac_hex(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify_checkout_signature(razorpay_order_id: str, razorpay_payment_id: str, signature: str) -> bool:
    """Razorpay signs 'order_id|payment_id' with the API secret. Constant-time comparison."""
    if not (razorpay_order_id and razorpay_payment_id and signature and settings.RAZORPAY_KEY_SECRET):
        return False
    expected = _hmac_hex(settings.RAZORPAY_KEY_SECRET, f"{razorpay_order_id}|{razorpay_payment_id}".encode())
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    """Webhooks are signed over the raw request body with the separate webhook secret."""
    if not (signature and settings.RAZORPAY_WEBHOOK_SECRET):
        return False
    return hmac.compare_digest(_hmac_hex(settings.RAZORPAY_WEBHOOK_SECRET, body), signature)


class RazorpayGateway:
    def __init__(self):
        self._client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

    def create_order(self, *, amount_paise: int, receipt: str, notes: dict) -> dict:
        try:
            return self._client.order.create(
                {"amount": amount_paise, "currency": "INR", "receipt": receipt, "notes": notes, "payment_capture": 1},
                timeout=TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Razorpay order creation failed: %s", type(exc).__name__)
            raise GatewayError("order creation failed") from exc

    def refund(self, razorpay_payment_id: str, amount_paise: int) -> dict:
        try:
            return self._client.payment.refund(razorpay_payment_id, {"amount": amount_paise}, timeout=TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001
            logger.error("Razorpay refund failed: %s", type(exc).__name__)
            raise GatewayError("refund failed") from exc


def get_gateway():
    return RazorpayGateway()
