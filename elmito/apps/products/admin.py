from django.contrib import admin, messages
from django.utils.html import format_html

from .models import Category, Product, ProductImage, ProductVariant, Review, StockMovement
from .services.inventory import log_movement


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "display_order")
    list_editable = ("is_active", "display_order")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ("image", "preview", "color", "alt_text", "is_primary", "position")
    readonly_fields = ("preview",)

    @admin.display(description="Preview")
    def preview(self, obj):
        return format_html('<img src="{}" style="height:48px">', obj.image.url) if obj.pk and obj.image else "—"


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0
    fields = ("color", "color_hex", "size", "sku", "stock", "is_active")


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "sku", "category", "price", "discount_price", "status", "stock", "is_featured", "is_new_arrival", "is_best_seller")
    list_filter = ("status", "category", "is_featured", "is_new_arrival", "is_best_seller")
    list_editable = ("status", "is_featured", "is_new_arrival", "is_best_seller")
    search_fields = ("name", "sku", "variants__sku")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [ProductImageInline, ProductVariantInline]
    actions = ["publish", "archive"]
    readonly_fields = ("popularity", "created_at", "updated_at")

    @admin.display(description="Stock")
    def stock(self, obj):
        return obj.total_stock

    @admin.action(description="Publish selected products")
    def publish(self, request, queryset):
        count = queryset.update(status=Product.Status.ACTIVE)
        self.message_user(request, f"{count} product(s) published.", messages.SUCCESS)

    @admin.action(description="Archive selected products")
    def archive(self, request, queryset):
        count = queryset.update(status=Product.Status.ARCHIVED)
        self.message_user(request, f"{count} product(s) archived.", messages.SUCCESS)

    def save_formset(self, request, form, formset, change):
        """Record manual stock edits in the movement ledger."""
        if formset.model is not ProductVariant:
            return super().save_formset(request, form, formset, change)
        deltas = {}
        for f in formset.forms:
            if f.instance.pk and f.has_changed() and "stock" in f.changed_data and f not in formset.deleted_forms:
                deltas[f.instance.pk] = f.cleaned_data["stock"] - f.initial.get("stock", 0)
        formset.save()
        for variant in formset.new_objects:
            if variant.stock:
                log_movement(variant, variant.stock, StockMovement.Reason.INITIAL)
        for variant in formset.changed_objects:
            variant = variant[0] if isinstance(variant, tuple) else variant
            if deltas.get(variant.pk):
                log_movement(variant, deltas[variant.pk], StockMovement.Reason.ADJUSTMENT, note=f"By {request.user}")


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    """Inventory view: filter by low stock, edit stock in bulk."""

    list_display = ("sku", "product", "color", "size", "stock", "is_active")
    list_editable = ("stock", "is_active")
    list_filter = ("is_active", "size", "color")
    search_fields = ("sku", "product__name", "product__sku")
    list_select_related = ("product",)
    ordering = ("stock",)

    def save_model(self, request, obj, form, change):
        old = ProductVariant.objects.filter(pk=obj.pk).values_list("stock", flat=True).first() if change else 0
        super().save_model(request, obj, form, change)
        if obj.stock != (old or 0):
            reason = StockMovement.Reason.ADJUSTMENT if change else StockMovement.Reason.INITIAL
            log_movement(obj, obj.stock - (old or 0), reason, note=f"By {request.user}")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("created_at", "sku", "delta", "reason", "order", "note")
    list_filter = ("reason",)
    search_fields = ("sku", "order__order_number")
    readonly_fields = [f.name for f in StockMovement._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("product", "user", "rating", "is_approved", "created_at")
    list_filter = ("is_approved", "rating")
    list_editable = ("is_approved",)
    search_fields = ("product__name", "user__email", "body")
