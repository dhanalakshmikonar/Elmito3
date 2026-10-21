from django.db import transaction
from django.db.models import Q

from apps.accounts.models import OTP, Profile, User
from apps.accounts.services.otp import issue_otp, verify_otp
from apps.notifications.services.email import send_otp_email


def find_conflicts(*, email, username, mobile):
    """Fields already owned by a verified/staff account. Abandoned unverified sign-ups don't block."""
    blocking = User.objects.filter(Q(is_verified=True) | Q(is_staff=True))
    return {
        "email": blocking.filter(email__iexact=email).exists(),
        "username": blocking.filter(username__iexact=username).exists(),
        "mobile": blocking.filter(mobile=mobile).exists(),
    }


@transaction.atomic
def register_user(*, full_name, username, email, mobile, password):
    # Abandoned, never-verified registrations cannot have orders; free their identifiers.
    User.objects.filter(is_verified=False, is_staff=False).filter(
        Q(email__iexact=email) | Q(username__iexact=username) | Q(mobile=mobile)
    ).delete()
    user = User.objects.create_user(
        username=username,
        email=email.lower(),
        password=password,
        mobile=mobile,
        full_name=full_name,
        is_verified=False,
    )
    Profile.objects.create(user=user)
    return user


def start_verification(user):
    """Issue and deliver a registration OTP. Raises OTPError on cooldown/rate limits.

    Returns False if delivery failed (the user may request a resend)."""
    code = issue_otp(user, OTP.Purpose.REGISTRATION)
    return send_otp_email(user, code, OTP.Purpose.REGISTRATION)


@transaction.atomic
def complete_verification(user, code):
    verify_otp(user, OTP.Purpose.REGISTRATION, code)
    user.is_verified = True
    user.save(update_fields=["is_verified"])


def find_unverified_user(identifier, password):
    """Return an unverified user whose credentials are correct (so we can resume verification)."""
    try:
        user = User.objects.get_by_login(identifier.strip())
    except User.DoesNotExist:
        return None
    if not user.is_verified and user.is_active and user.check_password(password):
        return user
    return None
