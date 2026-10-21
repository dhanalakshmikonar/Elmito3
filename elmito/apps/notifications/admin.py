from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "channel", "event", "recipient", "order", "status", "error")
    list_filter = ("channel", "status", "event")
    search_fields = ("recipient", "order__order_number")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
