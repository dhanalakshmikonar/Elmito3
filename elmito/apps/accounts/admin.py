from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.db.models import Count

from .models import Address, User


class AddressInline(admin.TabularInline):
    model = Address
    extra = 0
    fields = ("full_name", "phone", "line1", "city", "state", "pincode", "is_default")


@admin.register(User)
class CustomerAdmin(UserAdmin):
    list_display = ("username", "full_name", "email", "mobile", "is_verified", "is_staff", "date_joined", "orders")
    list_filter = ("is_verified", "is_staff", "is_active")
    search_fields = ("username", "email", "mobile", "full_name")
    fieldsets = UserAdmin.fieldsets + (("EL MITO", {"fields": ("full_name", "mobile", "is_verified")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("EL MITO", {"fields": ("email", "full_name", "mobile", "is_verified")}),)
    inlines = [AddressInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_orders=Count("orders"))

    @admin.display(description="Orders", ordering="_orders")
    def orders(self, obj):
        return obj._orders
