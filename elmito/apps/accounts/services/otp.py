"""OTP generation and verification.

Codes are never stored in plain text (HMAC-SHA256 keyed with SECRET_KEY) and are
never logged. `issue_otp` returns the plain code once, solely so the caller can
deliver it to the user.
"""
import hashlib
import hmac
import math
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import OTP, User


class OTPError(Exception):
    default_message = "We couldn't verify that code. Please try again."

    def __init__(self, message=None):
        self.message = message or self.default_message
        super().__init__(self.message)


class OTPCooldown(OTPError):
    def __init__(self, wait_seconds):
        self.wait_seconds = wait_seconds
        super().__init__(f"Please wait {wait_seconds}s before requesting a new code.")


class OTPRateLimited(OTPError):
    default_message = "Too many codes requested. Please try again in an hour."


class OTPInvalid(OTPError):
    default_message = "That code is incorrect."


class OTPExpired(OTPError):
    default_message = "That code has expired. Please request a new one."


class OTPLocked(OTPError):
    default_message = "Too many incorrect attempts. Please request a new code."


def _hash(user_id, purpose, reference, code):
    message = f"{user_id}:{purpose}:{reference}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, hashlib.sha256).hexdigest()


def _generate_code():
    return str(secrets.randbelow(10 ** settings.OTP_LENGTH)).zfill(settings.OTP_LENGTH)


@transaction.atomic
def issue_otp(user, purpose, reference=""):
    """Create a fresh OTP, invalidating any previous active one. Returns the plain code."""
    now = timezone.now()
    User.objects.select_for_update().get(pk=user.pk)  # serialise concurrent requests per user

    history = OTP.objects.filter(user=user, purpose=purpose)
    latest = history.order_by("-created_at").first()
    if latest:
        elapsed = (now - latest.created_at).total_seconds()
        if elapsed < settings.OTP_RESEND_COOLDOWN_SECONDS:
            raise OTPCooldown(math.ceil(settings.OTP_RESEND_COOLDOWN_SECONDS - elapsed))
    if history.filter(created_at__gte=now - timedelta(hours=1)).count() >= settings.OTP_MAX_SENDS_PER_HOUR:
        raise OTPRateLimited()

    history.filter(is_used=False, is_invalidated=False).update(is_invalidated=True)
    code = _generate_code()
    OTP.objects.create(
        user=user,
        purpose=purpose,
        reference=reference,
        code_hash=_hash(user.pk, purpose, reference, code),
        expires_at=now + timedelta(seconds=settings.OTP_EXPIRY_SECONDS),
    )
    return code


@transaction.atomic
def _check(user, purpose, code, reference):
    """Return an outcome string. Never raises inside the transaction so attempt counts persist."""
    now = timezone.now()
    otp = (
        OTP.objects.select_for_update()
        .filter(user=user, purpose=purpose, is_used=False, is_invalidated=False)
        .order_by("-created_at")
        .first()
    )
    if otp is None or otp.reference != reference:
        return "invalid"
    if otp.expires_at <= now:
        otp.is_invalidated = True
        otp.save(update_fields=["is_invalidated", "updated_at"])
        return "expired"
    if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
        return "locked"

    otp.attempts += 1
    expected = _hash(user.pk, purpose, reference, (code or "").strip())
    if hmac.compare_digest(otp.code_hash, expected):
        otp.is_used = True  # single use: replaying the same code fails
        otp.save(update_fields=["attempts", "is_used", "updated_at"])
        return "ok"

    locked = otp.attempts >= settings.OTP_MAX_ATTEMPTS
    if locked:
        otp.is_invalidated = True
    otp.save(update_fields=["attempts", "is_invalidated", "updated_at"])
    return "locked" if locked else "invalid"


def verify_otp(user, purpose, code, reference=""):
    """Raise an OTPError subclass unless the code is valid. Consumes the OTP on success."""
    outcome = _check(user, purpose, code, reference)
    if outcome == "ok":
        return
    raise {"expired": OTPExpired, "locked": OTPLocked}.get(outcome, OTPInvalid)()
