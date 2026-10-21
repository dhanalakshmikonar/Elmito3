from django.contrib import admin

from .models import ContactMessage, NewsletterSubscriber


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("created_at", "name", "email", "is_resolved")
    list_filter = ("is_resolved",)
    list_editable = ("is_resolved",)
    search_fields = ("name", "email", "message")
    readonly_fields = ("name", "email", "phone", "message", "created_at")


@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ("email", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("email",)


# ---- Sales dashboard on the admin home page ----
from datetime import timedelta  # noqa: E402

from django.db.models import Count, Sum  # noqa: E402
from django.utils import timezone  # noqa: E402


def dashboard_stats():
    from apps.accounts.models import User
    from apps.orders.models import Order
    from apps.products.models import ProductVariant

    now = timezone.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    paid = Order.objects.filter(payment_status=Order.PaymentStatus.PAID).exclude(status__in=[Order.Status.CANCELLED, Order.Status.REFUNDED])

    def revenue(since):
        return paid.filter(paid_at__gte=since).aggregate(total=Sum("total"), n=Count("id"))

    today_stats, week_stats, month_stats = revenue(today), revenue(now - timedelta(days=7)), revenue(now - timedelta(days=30))
    return {"dashboard": {
        "today": today_stats, "week": week_stats, "month": month_stats,
        "to_fulfil": Order.objects.filter(status__in=[Order.Status.PAID, Order.Status.PROCESSING, Order.Status.PACKED]).count(),
        "in_transit": Order.objects.filter(status__in=[Order.Status.SHIPPED, Order.Status.OUT_FOR_DELIVERY]).count(),
        "low_stock": ProductVariant.objects.filter(is_active=True, stock__lte=3, product__status="active").count(),
        "new_customers": User.objects.filter(is_verified=True, date_joined__gte=now - timedelta(days=7)).count(),
        "open_messages": ContactMessage.objects.filter(is_resolved=False).count(),
        "recent": Order.objects.select_related("user").exclude(status__in=[Order.Status.PENDING]).order_by("-created_at")[:8],
    }}


_default_index = admin.site.index


def _index_with_dashboard(request, extra_context=None):
    return _default_index(request, {**(extra_context or {}), **dashboard_stats()})


admin.site.index = _index_with_dashboard
admin.site.site_header = "EL MITO"
admin.site.site_title = "EL MITO admin"
admin.site.index_title = "Store overview"
