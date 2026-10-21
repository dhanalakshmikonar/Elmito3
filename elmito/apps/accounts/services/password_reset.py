from django.db import transaction

from apps.accounts.models import OTP, User
from apps.accounts.services.otp import OTPError, OTPInvalid, issue_otp, verify_otp
from apps.notifications.services.email import send_otp_email


def _get_eligible_user(email):
    return User.objects.filter(email__iexact=email.strip(), is_verified=True, is_active=True).first()


def request_password_reset(email):
    """Send a reset OTP if the account exists. Silent otherwise, to avoid account enumeration."""
    user = _get_eligible_user(email)
    if user is None:
        return
    try:
        code = issue_otp(user, OTP.Purpose.PASSWORD_RESET)
    except OTPError:
        return
    send_otp_email(user, code, OTP.Purpose.PASSWORD_RESET)


def get_user_for_reset(email):
    return _get_eligible_user(email)


@transaction.atomic
def reset_password(email, code, new_password):
    user = _get_eligible_user(email)
    if user is None:
        raise OTPInvalid()
    verify_otp(user, OTP.Purpose.PASSWORD_RESET, code)
    user.set_password(new_password)
    user.save(update_fields=["password"])
    return user
