from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.accounts.forms import (
    AddressForm, ForgotPasswordForm, LoginForm, OTPForm, ProfileForm, ResetPasswordForm, SignupForm,
)
from apps.accounts.models import Address, Profile, User
from apps.accounts.services import login_guard, password_reset, registration
from apps.accounts.services.otp import OTPError

PENDING_USER_KEY = "pending_verification_user"
RESET_EMAIL_KEY = "password_reset_email"


def _form_page(request, form, *, title, subtitle="", submit="Continue", links=()):
    return render(request, "accounts/form_page.html", {
        "form": form, "title": title, "subtitle": subtitle, "submit_label": submit, "links": links,
    })


def _send_verification(request, user):
    try:
        delivered = registration.start_verification(user)
    except OTPError as exc:
        messages.error(request, exc.message)
        return
    if delivered:
        messages.success(request, "We've sent a verification code to your email.")
    else:
        messages.warning(request, "We couldn't send the code. Please use “Resend code”.")


def signup(request):
    if request.user.is_authenticated:
        return redirect("core:home")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            user = registration.register_user(
                full_name=data["full_name"], username=data["username"], email=data["email"],
                mobile=data["mobile"], password=data["password1"],
            )
        except IntegrityError:  # lost a race with a concurrent registration
            form.add_error(None, "That email, username or mobile number was just taken. Please try again.")
        else:
            request.session[PENDING_USER_KEY] = user.pk
            _send_verification(request, user)
            return redirect("accounts:verify_otp")
    return _form_page(request, form, title="Create your account", submit="Create account",
                      links=[("Already have an account? Sign in", "accounts:login")])


def _pending_user(request):
    pk = request.session.get(PENDING_USER_KEY)
    return User.objects.filter(pk=pk, is_verified=False).first() if pk else None


def verify_otp(request):
    user = _pending_user(request)
    if user is None:
        return redirect("accounts:signup")
    form = OTPForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            registration.complete_verification(user, form.cleaned_data["code"])
        except OTPError as exc:
            form.add_error("code", exc.message)
        else:
            request.session.pop(PENDING_USER_KEY, None)
            messages.success(request, "Your account is verified. Please sign in.")
            return redirect("accounts:login")
    return render(request, "accounts/verify_otp.html", {
        "form": form, "email": user.email, "title": "Verify your email",
        "resend_url": "accounts:resend_otp",
    })


@require_POST
def resend_otp(request):
    user = _pending_user(request)
    if user is None:
        return redirect("accounts:signup")
    _send_verification(request, user)
    return redirect("accounts:verify_otp")


def login_view(request):
    if request.user.is_authenticated:
        return redirect("core:home")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        identifier, password = form.cleaned_data["identifier"], form.cleaned_data["password"]
        if login_guard.is_locked(request, identifier):
            form.add_error(None, "Too many failed attempts. Please try again in a few minutes.")
        else:
            user = authenticate(request, username=identifier, password=password)
            if user is not None:
                login_guard.reset(request, identifier)
                login(request, user)  # rotates the session key
                target = request.POST.get("next") or request.GET.get("next") or ""
                if target and url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
                    return redirect(target)
                return redirect("core:home")
            login_guard.record_failure(request, identifier)
            pending = registration.find_unverified_user(identifier, password)
            if pending:  # correct password, but email never verified: resume verification
                request.session[PENDING_USER_KEY] = pending.pk
                _send_verification(request, pending)
                return redirect("accounts:verify_otp")
            form.add_error(None, "Invalid credentials.")
    return render(request, "accounts/form_page.html", {
        "form": form, "title": "Sign in", "submit_label": "Sign in", "next": request.GET.get("next", ""),
        "links": [("Forgot password?", "accounts:forgot_password"), ("Create an account", "accounts:signup")],
    })


@require_POST
def logout_view(request):
    logout(request)
    return redirect("core:home")


def forgot_password(request):
    form = ForgotPasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        password_reset.request_password_reset(email)
        request.session[RESET_EMAIL_KEY] = email
        messages.info(request, "If an account exists for that email, we've sent a code.")
        return redirect("accounts:reset_password")
    return _form_page(request, form, title="Reset your password", subtitle="Enter your account email.",
                      submit="Send code", links=[("Back to sign in", "accounts:login")])


def reset_password(request):
    email = request.session.get(RESET_EMAIL_KEY)
    if not email:
        return redirect("accounts:forgot_password")
    form = ResetPasswordForm(request.POST or None, user=password_reset.get_user_for_reset(email))
    if request.method == "POST" and form.is_valid():
        try:
            user = password_reset.reset_password(email, form.cleaned_data["code"], form.cleaned_data["password1"])
        except OTPError as exc:
            form.add_error("code", exc.message)
        else:
            request.session.pop(RESET_EMAIL_KEY, None)
            login_guard.reset(request, email)
            login_guard.reset(request, user.username)
            messages.success(request, "Password updated. Please sign in.")
            return redirect("accounts:login")
    return _form_page(request, form, title="Choose a new password", submit="Update password",
                      links=[("Request a new code", "accounts:forgot_password")])


@login_required
def account(request):
    return render(request, "accounts/account.html")


@login_required
def profile(request):
    profile_obj, _ = Profile.objects.get_or_create(user=request.user)
    initial = {"full_name": request.user.full_name, "marketing_opt_in": profile_obj.marketing_opt_in}
    form = ProfileForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        request.user.full_name = form.cleaned_data["full_name"]
        request.user.save(update_fields=["full_name"])
        profile_obj.marketing_opt_in = form.cleaned_data["marketing_opt_in"]
        profile_obj.save(update_fields=["marketing_opt_in", "updated_at"])
        messages.success(request, "Profile updated.")
        return redirect("accounts:profile")
    return _form_page(request, form, title="Profile",
                      subtitle=f"{request.user.email} · {request.user.mobile}", submit="Save changes",
                      links=[("Change password", "accounts:change_password")])


@login_required
def change_password(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)  # keep this session, invalidate others
        messages.success(request, "Password changed.")
        return redirect("accounts:account")
    return _form_page(request, form, title="Change password", submit="Update password")


MAX_ADDRESSES = 10


def _safe_next(request, default):
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure()):
        return target
    return default


@login_required
def address_list(request):
    return render(request, "accounts/addresses.html", {"addresses": request.user.addresses.all()})


def _save_address(user, form):
    with transaction.atomic():
        address = form.save(commit=False)
        address.user = user
        first = not user.addresses.exclude(pk=address.pk).exists()
        was_default = bool(address.pk) and Address.objects.filter(pk=address.pk, is_default=True).exists()
        address.is_default = form.cleaned_data["is_default"] or first or was_default
        if address.is_default:
            user.addresses.exclude(pk=address.pk).update(is_default=False)
        address.save()
    return address


@login_required
def address_create(request):
    if request.user.addresses.count() >= MAX_ADDRESSES:
        messages.error(request, f"You can save up to {MAX_ADDRESSES} addresses. Delete one to add another.")
        return redirect("accounts:addresses")
    initial = {"full_name": request.user.full_name, "phone": request.user.mobile}
    form = AddressForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        _save_address(request.user, form)
        messages.success(request, "Address saved.")
        return redirect(_safe_next(request, "accounts:addresses"))
    return render(request, "accounts/form_page.html", {
        "form": form, "title": "Add an address", "submit_label": "Save address",
        "next": request.GET.get("next", ""), "links": [("Back to addresses", "accounts:addresses")],
    })


@login_required
def address_edit(request, pk):
    address = get_object_or_404(Address, pk=pk, user=request.user)
    form = AddressForm(request.POST or None, instance=address)
    if request.method == "POST" and form.is_valid():
        _save_address(request.user, form)
        messages.success(request, "Address updated.")
        return redirect(_safe_next(request, "accounts:addresses"))
    return render(request, "accounts/form_page.html", {
        "form": form, "title": "Edit address", "submit_label": "Save changes",
        "next": request.GET.get("next", ""), "links": [("Back to addresses", "accounts:addresses")],
    })


@login_required
@require_POST
def address_delete(request, pk):
    address = get_object_or_404(Address, pk=pk, user=request.user)
    was_default = address.is_default
    address.delete()
    if was_default:
        replacement = request.user.addresses.first()
        if replacement:
            replacement.is_default = True
            replacement.save(update_fields=["is_default", "updated_at"])
    messages.success(request, "Address deleted.")
    return redirect("accounts:addresses")


@login_required
@require_POST
def address_make_default(request, pk):
    address = get_object_or_404(Address, pk=pk, user=request.user)
    with transaction.atomic():
        request.user.addresses.exclude(pk=address.pk).update(is_default=False)
        address.is_default = True
        address.save(update_fields=["is_default", "updated_at"])
    return redirect("accounts:addresses")
