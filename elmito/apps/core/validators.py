import re

from django.core.exceptions import ValidationError

INDIAN_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
PINCODE_RE = re.compile(r"^[1-9]\d{5}$")


def normalize_mobile(value: str) -> str:
    """Strip spaces, dashes and a +91/91/0 prefix; return 10 digits."""
    digits = re.sub(r"[\s\-()]", "", value or "")
    if digits.startswith("+91"):
        digits = digits[3:]
    elif digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    return digits


def validate_indian_mobile(value: str) -> None:
    if not INDIAN_MOBILE_RE.match(value or ""):
        raise ValidationError("Enter a valid 10-digit Indian mobile number.")


def validate_pincode(value: str) -> None:
    if not PINCODE_RE.match(value or ""):
        raise ValidationError("Enter a valid 6-digit pincode.")
