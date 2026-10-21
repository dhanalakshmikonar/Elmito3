"""Email delivery behind a provider abstraction.

Failures are recorded in `Notification` and swallowed: an email problem must never
break the calling flow (and never a paid order). OTP values are never logged or stored.
"""
import logging
from abc import ABC, abstractmethod

from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

from apps.notifications.models import Notification

logger = logging.getLogger(__name__)


class EmailProvider(ABC):
    @abstractmethod
    def send(self, *, to: str, subject: str, body: str) -> None: ...


class DjangoEmailProvider(EmailProvider):
    """Uses Django's configured EMAIL_BACKEND. Swap for an API provider later."""

    def send(self, *, to, subject, body):
        send_mail(subject, body, settings.EMAIL_FROM, [to], fail_silently=False)


def get_provider() -> EmailProvider:
    return DjangoEmailProvider()


def send_email(*, to, subject, body, event, order=None) -> bool:
    record = Notification.objects.create(
        channel=Notification.Channel.EMAIL, event=event, recipient=to, order=order
    )
    try:
        get_provider().send(to=to, subject=subject, body=body)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see module docstring
        record.status = Notification.Status.FAILED
        record.error = type(exc).__name__
        record.save(update_fields=["status", "error", "updated_at"])
        logger.warning("Email failed: event=%s error=%s", event, type(exc).__name__)
        return False
    record.status = Notification.Status.SENT
    record.sent_at = timezone.now()
    record.save(update_fields=["status", "sent_at", "updated_at"])
    return True


_OTP_SUBJECTS = {
    "registration": "Verify your EL MITO account",
    "order": "Confirm your EL MITO order",
    "password_reset": "Reset your EL MITO password",
}


def send_otp_email(user, code, purpose) -> bool:
    minutes = settings.OTP_EXPIRY_SECONDS // 60
    body = (
        f"Hello {user.full_name},\n\n"
        f"Your EL MITO verification code is {code}.\n"
        f"It expires in {minutes} minutes. Never share this code with anyone.\n\n"
        "If you didn't request this, you can ignore this email."
    )
    return send_email(
        to=user.email, subject=_OTP_SUBJECTS.get(purpose, "Your EL MITO code"), body=body, event=f"otp_{purpose}"
    )
