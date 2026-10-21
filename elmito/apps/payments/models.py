from django.db import models
from django.db.models import Q

from apps.core.models import TimeStampedModel


class Payment(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    order = models.ForeignKey("orders.Order", on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    razorpay_order_id = models.CharField(max_length=40, unique=True)
    razorpay_payment_id = models.CharField(max_length=40, unique=True, null=True, blank=True)
    razorpay_signature = models.CharField(max_length=128, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # At most one successful payment per order: duplicate-payment guard at DB level.
            models.UniqueConstraint(
                fields=["order"], condition=Q(status="paid"), name="one_paid_payment_per_order"
            )
        ]

    def __str__(self):
        return f"{self.razorpay_order_id} ({self.status})"


class WebhookEvent(TimeStampedModel):
    """Stores processed Razorpay webhook event IDs so redelivery is idempotent."""

    event_id = models.CharField(max_length=80, unique=True)
    event_type = models.CharField(max_length=60)
    processed = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.event_type} {self.event_id}"
