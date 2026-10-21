from django.contrib import admin

from .models import Payment, WebhookEvent


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "order", "amount", "status", "razorpay_order_id", "razorpay_payment_id", "paid_at")
    list_filter = ("status",)
    search_fields = ("razorpay_order_id", "razorpay_payment_id", "order__order_number", "order__user__email")
    list_select_related = ("order",)
    exclude = ("razorpay_signature",)


@admin.register(WebhookEvent)
class WebhookEventAdmin(ReadOnlyAdmin):
    list_display = ("created_at", "event_type", "event_id", "processed")
    list_filter = ("event_type", "processed")
    search_fields = ("event_id",)
