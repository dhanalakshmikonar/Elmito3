import secrets
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.core.models import TimeStampedModel
from apps.core.validators import validate_indian_mobile, validate_pincode


class Coupon(TimeStampedModel):
    class DiscountType(models.TextChoices):
        PERCENT = "percent", "Percentage"
        FIXED = "fixed", "Fixed amount"

    code = models.CharField(max_length=30, unique=True)
    discount_type = models.CharField(max_length=7, choices=DiscountType.choices)
    value = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    min_order_value = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    max_discount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    starts_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    usage_limit = models.PositiveIntegerField(null=True, blank=True, help_text="Total redemptions. Blank = unlimited.")
    per_user_limit = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(expires_at__gt=models.F("starts_at")), name="coupon_expiry_after_start"),
        ]

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code


class Order(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAYMENT_PENDING = "payment_pending", "Payment Pending"
        PAID = "paid", "Paid"
        PROCESSING = "processing", "Processing"
        PACKED = "packed", "Packed"
        SHIPPED = "shipped", "Shipped"
        OUT_FOR_DELIVERY = "out_for_delivery", "Out for Delivery"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"
        RETURNED = "returned", "Returned"
        REFUNDED = "refunded", "Refunded"

    class PaymentStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    order_number = models.CharField(max_length=20, unique=True, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    payment_status = models.CharField(max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.PENDING, db_index=True)

    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    discount_total = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    shipping_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    coupon = models.ForeignKey(Coupon, null=True, blank=True, on_delete=models.SET_NULL, related_name="orders")
    coupon_code = models.CharField(max_length=30, blank=True)

    # Shipping address snapshot (immutable after order is placed)
    ship_name = models.CharField(max_length=150)
    ship_phone = models.CharField(max_length=10, validators=[validate_indian_mobile])
    ship_line1 = models.CharField(max_length=255)
    ship_city = models.CharField(max_length=100)
    ship_state = models.CharField(max_length=100)
    ship_pincode = models.CharField(max_length=6, validators=[validate_pincode])
    ship_landmark = models.CharField(max_length=150, blank=True)

    otp_verified_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    tracking_number = models.CharField(max_length=60, blank=True)
    inventory_reduced = models.BooleanField(default=False, editable=False)
    # Fingerprint of cart + address + totals. Lets "Confirm order" be retried safely.
    idempotency_key = models.CharField(max_length=64, blank=True, editable=False)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(total__gte=0), name="order_total_non_negative"),
            models.UniqueConstraint(
                fields=["user", "idempotency_key"],
                condition=Q(status__in=["pending", "payment_pending"]) & ~Q(idempotency_key=""),
                name="one_open_order_per_fingerprint",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = self.generate_order_number()
        super().save(*args, **kwargs)

    @staticmethod
    def generate_order_number() -> str:
        for _ in range(10):
            candidate = f"ELM{timezone.now():%y%m%d}{secrets.token_hex(3).upper()}"
            if not Order.objects.filter(order_number=candidate).exists():
                return candidate
        raise RuntimeError("Could not generate a unique order number.")

    @property
    def is_paid(self) -> bool:
        return self.payment_status == self.PaymentStatus.PAID

    @property
    def payment_id(self) -> str:
        payment = self.payments.filter(status="paid").first()
        return payment.razorpay_payment_id if payment else ""

    @property
    def razorpay_order_id(self) -> str:
        payment = self.payments.first()
        return payment.razorpay_order_id if payment else ""

    def __str__(self):
        return self.order_number


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    variant = models.ForeignKey("products.ProductVariant", null=True, on_delete=models.SET_NULL, related_name="+")
    # Snapshot so history survives catalogue edits
    product_name = models.CharField(max_length=200)
    sku = models.CharField(max_length=50)
    color = models.CharField(max_length=40)
    size = models.CharField(max_length=3)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveSmallIntegerField()
    line_total = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(quantity__gte=1), name="order_item_qty_gte_1")]

    def __str__(self):
        return f"{self.quantity} × {self.product_name} ({self.color}/{self.size})"


class CouponRedemption(TimeStampedModel):
    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name="redemptions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="redemption")

    def __str__(self):
        return f"{self.coupon} → {self.order}"


class OrderStatusHistory(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="history")
    status = models.CharField(max_length=20, choices=Order.Status.choices)
    note = models.CharField(max_length=255, blank=True)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "order status history"
