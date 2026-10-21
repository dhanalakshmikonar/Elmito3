from decimal import Decimal

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.core.models import ContactMessage, NewsletterSubscriber
from apps.core.templatetags.elmito import inr


class InrFilterTests(TestCase):
    def test_indian_grouping(self):
        self.assertEqual(inr(Decimal("999")), "₹999")
        self.assertEqual(inr(Decimal("1499")), "₹1,499")
        self.assertEqual(inr(Decimal("149999")), "₹1,49,999")
        self.assertEqual(inr(Decimal("12345678")), "₹1,23,45,678")
        self.assertEqual(inr(Decimal("1499.50")), "₹1,499.50")
        self.assertEqual(inr(None), "")


class NewsletterTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_subscribe_and_duplicate(self):
        for _ in range(2):
            self.client.post(reverse("core:newsletter"), {"email": "Fan@Example.com"})
        self.assertEqual(NewsletterSubscriber.objects.get().email, "fan@example.com")

    def test_invalid_email(self):
        self.client.post(reverse("core:newsletter"), {"email": "nope"})
        self.assertEqual(NewsletterSubscriber.objects.count(), 0)

    def test_open_redirect_blocked(self):
        response = self.client.post(reverse("core:newsletter"), {"email": "a@example.com", "next": "https://evil.example/"})
        self.assertRedirects(response, "/", fetch_redirect_response=False)

    def test_get_not_allowed(self):
        self.assertEqual(self.client.get(reverse("core:newsletter")).status_code, 405)


class ContactTests(TestCase):
    data = {"name": "Asha", "email": "asha@example.com", "phone": "", "message": "Is the tee true to size?"}

    def setUp(self):
        cache.clear()

    def test_message_saved(self):
        response = self.client.post(reverse("core:contact"), self.data)
        self.assertRedirects(response, reverse("core:contact"))
        self.assertEqual(ContactMessage.objects.count(), 1)

    def test_honeypot_blocks_bots(self):
        self.client.post(reverse("core:contact"), {**self.data, "website": "http://spam.example"})
        self.assertEqual(ContactMessage.objects.count(), 0)

    def test_rate_limited(self):
        for _ in range(7):
            self.client.post(reverse("core:contact"), self.data)
        self.assertEqual(ContactMessage.objects.count(), 5)
