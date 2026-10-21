from django.urls import path

from . import views

app_name = "cart"

urlpatterns = [
    path("", views.detail, name="detail"),
    path("add/", views.add, name="add"),
    path("items/<int:item_id>/update/", views.update, name="update"),
    path("items/<int:item_id>/remove/", views.remove, name="remove"),
    path("coupon/apply/", views.apply_coupon, name="apply_coupon"),
    path("coupon/remove/", views.remove_coupon, name="remove_coupon"),
]
