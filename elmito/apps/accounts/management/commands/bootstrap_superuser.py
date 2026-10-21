import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q


class Command(BaseCommand):
    help = "Create or explicitly reset a superuser using environment variables."

    def handle(self, *args, **options):
        names = (
            "ELMITO_ADMIN_USERNAME",
            "ELMITO_ADMIN_EMAIL",
            "ELMITO_ADMIN_MOBILE",
            "ELMITO_ADMIN_FULL_NAME",
            "ELMITO_ADMIN_PASSWORD",
        )
        values = {name: os.environ.get(name) for name in names}
        if not any(values.values()):
            self.stdout.write("Superuser bootstrap is not configured; skipped.")
            return
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise CommandError("Missing required environment variable(s): " + ", ".join(missing))

        User = get_user_model()
        username = values["ELMITO_ADMIN_USERNAME"]
        password = values["ELMITO_ADMIN_PASSWORD"]
        reset_password = os.environ.get("ELMITO_ADMIN_RESET_PASSWORD", "").lower() in {
            "1", "true", "yes", "on"
        }
        user = User.objects.filter(username__iexact=username).first()

        if user:
            if reset_password:
                self._validate_password(password, user)
                user.set_password(password)
                user.is_active = True
                user.is_staff = True
                user.is_superuser = True
                user.is_verified = True
                user.save(
                    update_fields=[
                        "password", "is_active", "is_staff", "is_superuser", "is_verified"
                    ]
                )
                self.stdout.write("Configured account restored as a superuser.")
            elif user.is_superuser:
                self.stdout.write("Configured superuser already exists; left unchanged.")
            else:
                raise CommandError(
                    "The configured username exists but is not a superuser. "
                    "Set ELMITO_ADMIN_RESET_PASSWORD=true to explicitly restore it."
                )
            return

        email = values["ELMITO_ADMIN_EMAIL"]
        mobile = values["ELMITO_ADMIN_MOBILE"]
        full_name = values["ELMITO_ADMIN_FULL_NAME"]
        if User.objects.filter(Q(email__iexact=email) | Q(mobile=mobile)).exists():
            raise CommandError("The configured email or mobile number is already in use.")

        candidate = User(username=username, email=email, mobile=mobile, full_name=full_name)
        self._validate_password(password, candidate)
        User.objects.create_superuser(
            username=username,
            email=email,
            password=password,
            mobile=mobile,
            full_name=full_name,
        )
        self.stdout.write("Configured superuser created successfully.")

    @staticmethod
    def _validate_password(password, user):
        try:
            validate_password(password, user=user)
        except ValidationError as exc:
            raise CommandError("Password rejected: " + "; ".join(exc.messages)) from exc
