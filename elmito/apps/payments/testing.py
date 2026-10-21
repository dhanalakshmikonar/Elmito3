"""Test doubles and helpers for Razorpay. Not imported by production code."""
import hashlib
import hmac
import json
from unittest import mock

from django.test import override_settings

from apps.payments.services.gateway import GatewayError

KEY_SECRET = "test_key_secret"
WEBHOOK_SECRET = "test_webhook_secret"

razorpay_settings = override_settings(
    RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET=KEY_SECRET, RAZORPAY_WEBHOOK_SECRET=WEBHOOK_SECRET
)


class FakeGateway:
    def __init__(self):
        self.orders, self.refunds = [], []
        self.fail_create = self.fail_refund = False

    def create_order(self, *, amount_paise, receipt, notes):
        if self.fail_create:
            raise GatewayError("down")
        self.orders.append({"amount": amount_paise, "receipt": receipt, "notes": notes})
        return {"id": f"order_T{len(self.orders):04d}"}

    def refund(self, razorpay_payment_id, amount_paise):
        if self.fail_refund:
            raise GatewayError("down")
        self.refunds.append((razorpay_payment_id, amount_paise))
        return {"id": "rfnd_1"}


def patch_gateway(fake):
    return mock.patch("apps.payments.services.gateway.get_gateway", return_value=fake)


def sign(razorpay_order_id, razorpay_payment_id, secret=KEY_SECRET):
    return hmac.new(secret.encode(), f"{razorpay_order_id}|{razorpay_payment_id}".encode(), hashlib.sha256).hexdigest()


def webhook_request(event, razorpay_order_id, razorpay_payment_id, amount=None, status="captured", secret=WEBHOOK_SECRET, error=None):
    entity = {"id": razorpay_payment_id, "order_id": razorpay_order_id, "status": status}
    if amount is not None:
        entity["amount"] = amount
    if error:
        entity["error_description"] = error
    body = json.dumps({"event": event, "payload": {"payment": {"entity": entity}}}).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return body, signature
