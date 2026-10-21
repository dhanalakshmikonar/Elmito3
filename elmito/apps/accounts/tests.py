import re
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OTP, User
from apps.accounts.services import otp as otp_service
from apps.accounts.services.otp import OTPCooldown, OTPExpired, OTPInvalid, OTPLocked, OTPRateLimited

VALID = {
    "full_name": "Asha Rao", "username": "asha_rao", "email": "asha@example.com",
    "mobile": "9876543210", "password1": "Sturdy#Pass9271", "password2": "Sturdy#Pass9271",
}


def make_user(verified=True, **overrides):
    data = dict(username="asha_rao", email="asha@example.com", mobile="9876543210", full_name="Asha Rao")
    data.update(overrides)
    return User.objects.create_user(password="Sturdy#Pass9271", is_verified=verified, **data)


def last_code():
    return re.search(r"\b(\d{6})\b", mail.outbox[-1].body).group(1)


class AccountTestCase(TestCase):
    def setUp(self):
        cache.clear()


@override_settings(OTP_RESEND_COOLDOWN_SECONDS=0)
class OTPServiceTests(AccountTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user(verified=False)

    def test_code_is_six_digits_and_stored_hashed(self):
        code = otp_service.issue_otp(self.user, "registration")
        self.assertRegex(code, r"^\d{6}$")
        record = OTP.objects.get()
        self.assertNotIn(code, record.code_hash)
        self.assertEqual(len(record.code_hash), 64)

    def test_valid_code_verifies_and_cannot_be_reused(self):
        code = otp_service.issue_otp(self.user, "registration")
        otp_service.verify_otp(self.user, "registration", code)
        with self.assertRaises(OTPInvalid):
            otp_service.verify_otp(self.user, "registration", code)

    def test_wrong_code_rejected(self):
        otp_service.issue_otp(self.user, "registration")
        with self.assertRaises(OTPInvalid):
            otp_service.verify_otp(self.user, "registration", "000000x")

    def test_expired_code_rejected(self):
        code = otp_service.issue_otp(self.user, "registration")
        OTP.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(OTPExpired):
            otp_service.verify_otp(self.user, "registration", code)

    def test_new_otp_invalidates_previous(self):
        old = otp_service.issue_otp(self.user, "registration")
        new = otp_service.issue_otp(self.user, "registration")
        with self.assertRaises(OTPInvalid):
            otp_service.verify_otp(self.user, "registration", old) if old != new else self.fail("collision")
        otp_service.verify_otp(self.user, "registration", new)

    def test_attempt_limit_locks_even_correct_code(self):
        code = otp_service.issue_otp(self.user, "registration")
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(4):
            with self.assertRaises(OTPInvalid):
                otp_service.verify_otp(self.user, "registration", wrong)
        with self.assertRaises(OTPLocked):
            otp_service.verify_otp(self.user, "registration", wrong)
        with self.assertRaises(OTPInvalid):  # OTP is dead; the right code no longer works
            otp_service.verify_otp(self.user, "registration", code)

    def test_reference_scoping(self):
        code = otp_service.issue_otp(self.user, "order", reference="ELM1")
        with self.assertRaises(OTPInvalid):
            otp_service.verify_otp(self.user, "order", code, reference="ELM2")
        otp_service.verify_otp(self.user, "order", code, reference="ELM1")

    def test_hourly_cap(self):
        for _ in range(5):
            otp_service.issue_otp(self.user, "registration")
        with self.assertRaises(OTPRateLimited):
            otp_service.issue_otp(self.user, "registration")


class OTPCooldownTests(AccountTestCase):
    def test_resend_cooldown(self):
        user = make_user(verified=False)
        otp_service.issue_otp(user, "registration")
        with self.assertRaises(OTPCooldown):
            otp_service.issue_otp(user, "registration")


class RegistrationTests(AccountTestCase):
    def test_signup_sends_otp_and_verifies_account(self):
        response = self.client.post(reverse("accounts:signup"), VALID)
        self.assertRedirects(response, reverse("accounts:verify_otp"))
        user = User.objects.get(username="asha_rao")
        self.assertFalse(user.is_verified)
        self.assertNotEqual(user.password, VALID["password1"])
        self.assertEqual(len(mail.outbox), 1)

        response = self.client.post(reverse("accounts:verify_otp"), {"code": last_code()})
        self.assertRedirects(response, reverse("accounts:login"))
        user.refresh_from_db()
        self.assertTrue(user.is_verified)
        self.assertTrue(hasattr(user, "profile"))

    def test_otp_not_in_response_html(self):
        response = self.client.post(reverse("accounts:signup"), VALID, follow=True)
        self.assertNotContains(response, last_code())

    def test_duplicate_email_username_mobile_rejected(self):
        make_user()
        response = self.client.post(reverse("accounts:signup"), {**VALID, "email": "ASHA@example.com"})
        self.assertContains(response, "email address is already registered")
        response = self.client.post(reverse("accounts:signup"), {**VALID, "username": "Asha_Rao", "email": "x@example.com"})
        self.assertContains(response, "username is already registered")
        response = self.client.post(reverse("accounts:signup"), {**VALID, "username": "other", "email": "x@example.com", "mobile": "+91 98765 43210"})
        self.assertContains(response, "mobile number is already registered")

    def test_invalid_mobile_and_weak_password(self):
        response = self.client.post(reverse("accounts:signup"), {**VALID, "mobile": "1234567890"})
        self.assertContains(response, "valid 10-digit Indian mobile")
        response = self.client.post(reverse("accounts:signup"), {**VALID, "password1": "password", "password2": "password"})
        self.assertEqual(User.objects.count(), 0)
        self.assertContains(response, "errorlist")

    def test_password_mismatch(self):
        response = self.client.post(reverse("accounts:signup"), {**VALID, "password2": "different"})
        self.assertContains(response, "Passwords do not match")

    def test_abandoned_unverified_signup_can_be_retried(self):
        self.client.post(reverse("accounts:signup"), VALID)
        self.client.logout()
        response = self.client.post(reverse("accounts:signup"), VALID)
        self.assertRedirects(response, reverse("accounts:verify_otp"))
        self.assertEqual(User.objects.count(), 1)

    def test_wrong_otp_shows_error_and_stays_unverified(self):
        self.client.post(reverse("accounts:signup"), VALID)
        wrong = "000000" if last_code() != "000000" else "111111"
        response = self.client.post(reverse("accounts:verify_otp"), {"code": wrong})
        self.assertContains(response, "incorrect")
        self.assertFalse(User.objects.get().is_verified)

    def test_email_failure_does_not_crash_signup(self):
        with mock.patch("apps.notifications.services.email.send_mail", side_effect=OSError("smtp down")):
            response = self.client.post(reverse("accounts:signup"), VALID)
        self.assertRedirects(response, reverse("accounts:verify_otp"))
        from apps.notifications.models import Notification
        self.assertEqual(Notification.objects.get().status, "failed")


class LoginTests(AccountTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user()
        self.url = reverse("accounts:login")

    def test_login_with_email_and_username(self):
        for ident in ("asha@example.com", "ASHA_RAO"):
            self.client.logout()
            response = self.client.post(self.url, {"identifier": ident, "password": "Sturdy#Pass9271"})
            self.assertRedirects(response, reverse("core:home"), fetch_redirect_response=False)
            self.assertIn("_auth_user_id", self.client.session)

    def test_invalid_login(self):
        response = self.client.post(self.url, {"identifier": "asha_rao", "password": "nope"})
        self.assertContains(response, "Invalid credentials")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_unverified_user_cannot_login_and_resumes_verification(self):
        make_user(verified=False, username="new", email="new@example.com", mobile="9123456780")
        response = self.client.post(self.url, {"identifier": "new", "password": "Sturdy#Pass9271"})
        self.assertRedirects(response, reverse("accounts:verify_otp"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_lockout_after_repeated_failures(self):
        for _ in range(5):
            self.client.post(self.url, {"identifier": "asha_rao", "password": "bad"})
        response = self.client.post(self.url, {"identifier": "asha_rao", "password": "Sturdy#Pass9271"})
        self.assertContains(response, "Too many failed attempts")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_open_redirect_blocked(self):
        response = self.client.post(self.url + "?next=https://evil.example/", {"identifier": "asha_rao", "password": "Sturdy#Pass9271"})
        self.assertRedirects(response, reverse("core:home"), fetch_redirect_response=False)

    def test_logout_requires_post(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.client.post(reverse("accounts:logout"))
        self.assertNotIn("_auth_user_id", self.client.session)


class PasswordFlowTests(AccountTestCase):
    def test_reset_with_otp(self):
        user = make_user()
        self.client.post(reverse("accounts:forgot_password"), {"email": "asha@example.com"})
        new_pw = "Another#Pass4410"
        response = self.client.post(reverse("accounts:reset_password"), {
            "code": last_code(), "password1": new_pw, "password2": new_pw})
        self.assertRedirects(response, reverse("accounts:login"))
        user.refresh_from_db()
        self.assertTrue(user.check_password(new_pw))

    def test_reset_for_unknown_email_is_indistinguishable(self):
        response = self.client.post(reverse("accounts:forgot_password"), {"email": "ghost@example.com"})
        self.assertRedirects(response, reverse("accounts:reset_password"))
        self.assertEqual(len(mail.outbox), 0)

    def test_change_password_keeps_session(self):
        user = make_user()
        self.client.force_login(user)
        new_pw = "Another#Pass4410"
        response = self.client.post(reverse("accounts:change_password"), {
            "old_password": "Sturdy#Pass9271", "new_password1": new_pw, "new_password2": new_pw})
        self.assertRedirects(response, reverse("accounts:account"))
        self.assertEqual(self.client.get(reverse("accounts:account")).status_code, 200)

    def test_account_pages_require_login(self):
        for name in ("account", "profile", "change_password"):
            response = self.client.get(reverse(f"accounts:{name}"))
            self.assertEqual(response.status_code, 302)
