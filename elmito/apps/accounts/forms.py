import re

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from apps.accounts.models import Address, User
from apps.accounts.services.registration import find_conflicts
from apps.core.constants import STATE_CHOICES
from apps.core.validators import normalize_mobile, validate_indian_mobile, validate_pincode

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.]{3,30}$")


class SignupForm(forms.Form):
    full_name = forms.CharField(max_length=150)
    username = forms.CharField(max_length=30)
    email = forms.EmailField()
    mobile = forms.CharField(max_length=20, label="Mobile number")
    password1 = forms.CharField(widget=forms.PasswordInput, label="Password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm password")

    def clean_full_name(self):
        return " ".join(self.cleaned_data["full_name"].split())

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if not USERNAME_RE.match(username):
            raise ValidationError("3–30 characters: letters, numbers, underscore or dot.")
        return username

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()

    def clean_mobile(self):
        mobile = normalize_mobile(self.cleaned_data["mobile"])
        validate_indian_mobile(mobile)
        return mobile

    def clean(self):
        data = super().clean()
        if {"email", "username", "mobile"} <= data.keys():
            conflicts = find_conflicts(email=data["email"], username=data["username"], mobile=data["mobile"])
            labels = {"email": "email address", "username": "username", "mobile": "mobile number"}
            for field, taken in conflicts.items():
                if taken:
                    self.add_error(field, f"This {labels[field]} is already registered.")

        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2:
            if p1 != p2:
                self.add_error("password2", "Passwords do not match.")
            else:
                candidate = User(username=data.get("username", ""), email=data.get("email", ""), full_name=data.get("full_name", ""))
                try:
                    validate_password(p1, candidate)
                except ValidationError as exc:
                    self.add_error("password1", exc)
        return data


class OTPForm(forms.Form):
    code = forms.RegexField(
        regex=r"^\d{6}$",
        label="Verification code",
        error_messages={"invalid": "Enter the 6-digit code."},
        widget=forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "maxlength": 6}),
    )


class LoginForm(forms.Form):
    identifier = forms.CharField(label="Email or username")
    password = forms.CharField(widget=forms.PasswordInput)


class ForgotPasswordForm(forms.Form):
    email = forms.EmailField()


class ResetPasswordForm(OTPForm):
    password1 = forms.CharField(widget=forms.PasswordInput, label="New password")
    password2 = forms.CharField(widget=forms.PasswordInput, label="Confirm new password")

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2:
            if p1 != p2:
                self.add_error("password2", "Passwords do not match.")
            else:
                try:
                    validate_password(p1, self.user)
                except ValidationError as exc:
                    self.add_error("password1", exc)
        return data


class ProfileForm(forms.Form):
    full_name = forms.CharField(max_length=150)
    marketing_opt_in = forms.BooleanField(required=False, label="Send me news and offers")

    def clean_full_name(self):
        return " ".join(self.cleaned_data["full_name"].split())


class AddressForm(forms.ModelForm):
    # Accept +91 / spaces / dashes on input; clean_phone normalises to 10 digits.
    phone = forms.CharField(max_length=20, label="Mobile number")
    state = forms.ChoiceField(choices=STATE_CHOICES)
    is_default = forms.BooleanField(required=False, label="Make this my default address")

    class Meta:
        model = Address
        fields = ["full_name", "phone", "line1", "city", "state", "pincode", "landmark", "is_default"]
        labels = {"line1": "Address (house, street, area)"}

    def clean_full_name(self):
        return " ".join(self.cleaned_data["full_name"].split())

    def clean_phone(self):
        phone = normalize_mobile(self.cleaned_data["phone"])
        validate_indian_mobile(phone)
        return phone

    def clean_pincode(self):
        pincode = self.cleaned_data["pincode"].strip()
        validate_pincode(pincode)
        return pincode

    def clean_line1(self):
        value = " ".join(self.cleaned_data["line1"].split())
        if len(value) < 8:
            raise ValidationError("Enter your full address.")
        return value

    def clean_city(self):
        city = " ".join(self.cleaned_data["city"].split())
        if not re.match(r"^[A-Za-z][A-Za-z .'-]{1,99}$", city):
            raise ValidationError("Enter a valid city.")
        return city

    def clean_landmark(self):
        return " ".join(self.cleaned_data["landmark"].split())
