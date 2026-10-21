import csv

from django.contrib import admin, messages
from django.db.models import Count
from django.http import HttpResponse

from apps.orders.models import Coupon, CouponRedemption, Order, OrderItem, OrderStatusHistory
from apps.orders.services import status as order_status


class ReadOnlyInline(admin.TabularInline):
    extra = 0
    can_delete = False

    def get_readonly_fields(self, request, obj=None):
        return list(self.fields or [])

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class OrderItemInline(ReadOnlyInline):
    model = OrderItem
    fields = ("product_name", "sku", "color", "size", "unit_price", "quantity", "line_total")


class HistoryInline(ReadOnlyInline):
    model = OrderStatusHistory
    fields = ("created_at", "status", "note", "changed_by")
    ordering = ("-created_at",)


def _safe(value):
    """Neutralise spreadsheet formula injection in exported text."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in "=+-@\t\r" else text


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("order_number", "created_at", "customer", "total", "status", "payment_status", "tracking_number")
    list_filter = ("status", "payment_status", "created_at")
    search_fields = ("order_number", "user__email", "user__full_name", "user__mobile", "ship_phone", "payments__razorpay_order_id", "payments__razorpay_payment_id")
    date_hierarchy = "created_at"
    list_select_related = ("user",)
    inlines = [OrderItemInline, HistoryInline]
    actions = ["mark_processing", "mark_packed", "mark_shipped", "mark_out_for_delivery", "mark_delivered", "export_csv"]
    fieldsets = (
        ("Order", {"fields": ("order_number", "user", "status", "payment_status", "tracking_number", "created_at", "paid_at")}),
        ("Amounts", {"fields": ("subtotal", "discount_total", "coupon_code", "shipping_charge", "total")}),
        ("Payment", {"fields": ("razorpay_order", "razorpay_payment")}),
        ("Shipping address", {"fields": ("ship_name", "ship_phone", "ship_line1", "ship_city", "ship_state", "ship_pincode", "ship_landmark")}),
    )
    readonly_fields = ("order_number", "user", "payment_status", "created_at", "paid_at", "subtotal", "discount_total", "coupon_code",
                       "shipping_charge", "total", "razorpay_order", "razorpay_payment", "ship_name", "ship_phone", "ship_line1",
                       "ship_city", "ship_state", "ship_pincode", "ship_landmark")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False  # orders are financial records

    @admin.display(description="Customer", ordering="user__full_name")
    def customer(self, obj):
        return obj.user.full_name

    @admin.display(description="Razorpay order")
    def razorpay_order(self, obj):
        return obj.razorpay_order_id or "—"

    @admin.display(description="Razorpay payment")
    def razorpay_payment(self, obj):
        return obj.payment_id or "—"

    def save_model(self, request, obj, form, change):
        """Status edits on the detail page go through the status service (validation, stock, refunds, history)."""
        requested = obj.status
        if change and "status" in form.changed_data:
            obj.status = Order.objects.values_list("status", flat=True).get(pk=obj.pk)
        super().save_model(request, obj, form, change)
        if change and "status" in form.changed_data:
            self._move(request, obj, requested)

    def _move(self, request, order, new_status):
        try:
            order_status.change_status(order, new_status, by=request.user, note="Updated in admin")
            return True
        except order_status.TransitionError as exc:
            self.message_user(request, f"{order.order_number}: {exc}", messages.ERROR)
            return False

    def _bulk(self, request, queryset, new_status):
        moved = sum(self._move(request, order, new_status) for order in queryset)
        if moved:
            self.message_user(request, f"{moved} order(s) updated.", messages.SUCCESS)

    @admin.action(description="Mark as processing")
    def mark_processing(self, request, queryset):
        self._bulk(request, queryset, Order.Status.PROCESSING)

    @admin.action(description="Mark as packed")
    def mark_packed(self, request, queryset):
        self._bulk(request, queryset, Order.Status.PACKED)

    @admin.action(description="Mark as shipped")
    def mark_shipped(self, request, queryset):
        self._bulk(request, queryset, Order.Status.SHIPPED)

    @admin.action(description="Mark as out for delivery")
    def mark_out_for_delivery(self, request, queryset):
        self._bulk(request, queryset, Order.Status.OUT_FOR_DELIVERY)

    @admin.action(description="Mark as delivered")
    def mark_delivered(self, request, queryset):
        self._bulk(request, queryset, Order.Status.DELIVERED)

    @admin.action(description="Export selected orders (CSV)")
    def export_csv(self, request, queryset):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="elmito-orders.csv"'
        writer = csv.writer(response)
        writer.writerow(["Order", "Date", "Customer", "Email", "Phone", "Status", "Payment status", "Items", "Subtotal", "Discount",
                         "Shipping", "Total", "Coupon", "Razorpay order", "Razorpay payment", "Ship to", "Tracking"])
        for o in queryset.select_related("user").prefetch_related("items", "payments"):
            items = "; ".join(f"{i.quantity}x {i.product_name} ({i.color}/{i.size})" for i in o.items.all())
            address = f"{o.ship_name}, {o.ship_line1}, {o.ship_city}, {o.ship_state} {o.ship_pincode}"
            writer.writerow([_safe(v) for v in (
                o.order_number, o.created_at.strftime("%Y-%m-%d %H:%M"), o.user.full_name, o.user.email, o.ship_phone,
                o.get_status_display(), o.get_payment_status_display(), items, o.subtotal, o.discount_total, o.shipping_charge,
                o.total, o.coupon_code, o.razorpay_order_id, o.payment_id, address, o.tracking_number)])
        return response


class RedemptionInline(ReadOnlyInline):
    model = CouponRedemption
    fields = ("user", "order", "created_at")


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ("code", "discount_type", "value", "min_order_value", "max_discount", "starts_at", "expires_at", "uses", "is_active")
    list_filter = ("is_active", "discount_type")
    list_editable = ("is_active",)
    search_fields = ("code",)
    inlines = [RedemptionInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_uses=Count("redemptions"))

    @admin.display(description="Used", ordering="_uses")
    def uses(self, obj):
        return obj._uses
