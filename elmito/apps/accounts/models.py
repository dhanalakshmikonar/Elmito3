from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.db.models.functions import Lower

from apps.core.models import TimeStampedModel
from apps.core.validators import validate_indian_mobile, validate_pincode


class CustomerManager(UserManager):
    def get_by_login(self, identifier):
        """Resolve a user by email or username (case-insensitive)."""
        field = "email" if "@" in identifier else "username"
        return self.get(**{f"{field}__iexact": identifier})

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_verified", True)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    email = models.EmailField(unique=True)
    mobile = models.CharField(max_length=10, unique=True, validators=[validate_indian_mobile])
    full_name = models.CharField(max_length=150)
    is_verified = models.BooleanField(default=False)

    objects = CustomerManager()
    REQUIRED_FIELDS = ["email", "mobile", "full_name"]

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("email"), name="uniq_user_email_ci"),
            models.UniqueConstraint(Lower("username"), name="uniq_user_username_ci"),
        ]

    def __str__(self):
        return self.full_name or self.username


class Profile(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    date_of_birth = models.DateField(null=True, blank=True)
    marketing_opt_in = models.BooleanField(default=False)

    def __str__(self):
        return f"Profile of {self.user}"


class Address(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="addresses")
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=10, validators=[validate_indian_mobile])
    line1 = models.CharField("Address", max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    pincode = models.CharField(max_length=6, validators=[validate_pincode])
    landmark = models.CharField(max_length=150, blank=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "addresses"
        ordering = ["-is_default", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"], condition=models.Q(is_default=True), name="one_default_address_per_user"
            )
        ]

    def __str__(self):
        return f"{self.full_name}, {self.city} {self.pincode}"


class OTP(TimeStampedModel):
    class Purpose(models.TextChoices):
        REGISTRATION = "registration", "Registration"
        ORDER = "order", "Order verification"
        PASSWORD_RESET = "password_reset", "Password reset"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="otps")
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    # Optional scope, e.g. the order a verification OTP belongs to.
    reference = models.CharField(max_length=64, blank=True)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    is_used = models.BooleanField(default=False)
    is_invalidated = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=["user", "purpose", "reference", "-created_at"])]

    def __str__(self):
        return f"OTP({self.purpose}) for {self.user_id}"
