from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils.text import slugify

from apps.core.models import TimeStampedModel


class Category(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="categories/", blank=True)
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["display_order", "name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse("products:collection", args=[self.slug])

    def __str__(self):
        return self.name


class Product(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    sku = models.CharField("SKU", max_length=40, unique=True)
    description = models.TextField(blank=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    discount_price = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0.01"))]
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT, db_index=True)
    is_featured = models.BooleanField(default=False)
    is_new_arrival = models.BooleanField(default=False)
    is_best_seller = models.BooleanField(default=False)
    popularity = models.PositiveIntegerField(default=0, help_text="Incremented on each paid sale.")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(discount_price__isnull=True) | Q(discount_price__lt=models.F("price")),
                name="discount_price_below_price",
            )
        ]

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)
            slug, n = base, 2
            while Product.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def selling_price(self) -> Decimal:
        return self.discount_price if self.discount_price else self.price

    @property
    def has_discount(self) -> bool:
        return bool(self.discount_price)

    @property
    def total_stock(self) -> int:
        return sum(v.stock for v in self.variants.all() if v.is_active)

    @property
    def primary_image(self):
        images = list(self.images.all())
        return next((i for i in images if i.is_primary), images[0] if images else None)

    def get_absolute_url(self):
        return reverse("products:detail", args=[self.slug])

    def __str__(self):
        return self.name


class ProductImage(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    # Optionally tie an image to a colour so the gallery switches with the colour selector.
    color = models.CharField(max_length=40, blank=True)
    image = models.ImageField(upload_to="products/")
    alt_text = models.CharField(max_length=150, blank=True)
    is_primary = models.BooleanField(default=False)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.product.name} image {self.position}"


class ProductVariant(TimeStampedModel):
    class Size(models.TextChoices):
        XS = "XS", "XS"
        S = "S", "S"
        M = "M", "M"
        L = "L", "L"
        XL = "XL", "XL"
        XXL = "XXL", "XXL"

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    color = models.CharField(max_length=40)
    color_hex = models.CharField(max_length=7, default="#000000")
    size = models.CharField(max_length=3, choices=Size.choices)
    sku = models.CharField("SKU", max_length=50, unique=True)
    stock = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["color", "id"]
        constraints = [
            models.UniqueConstraint(fields=["product", "color", "size"], name="uniq_variant_per_product")
        ]

    @property
    def in_stock(self) -> bool:
        return self.is_active and self.stock > 0

    def __str__(self):
        return f"{self.product.name} / {self.color} / {self.size}"


class Review(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="reviews")
    rating = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=120, blank=True)
    body = models.TextField()
    is_approved = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["product", "user"], name="one_review_per_user_product"),
            models.CheckConstraint(condition=Q(rating__gte=1, rating__lte=5), name="review_rating_1_to_5"),
        ]

    def __str__(self):
        return f"{self.rating}★ {self.product.name}"


class StockMovement(TimeStampedModel):
    """Append-only ledger of every stock change, for audit and reconciliation."""

    class Reason(models.TextChoices):
        INITIAL = "initial", "Initial stock"
        ADJUSTMENT = "adjustment", "Manual adjustment"
        ORDER_PAID = "order_paid", "Order paid"
        ORDER_RESTOCK = "order_restock", "Order cancelled/returned"

    variant = models.ForeignKey(ProductVariant, null=True, on_delete=models.SET_NULL, related_name="movements")
    sku = models.CharField(max_length=50)  # snapshot, survives variant deletion
    delta = models.IntegerField()
    reason = models.CharField(max_length=15, choices=Reason.choices)
    order = models.ForeignKey("orders.Order", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.sku} {self.delta:+d} ({self.reason})"
