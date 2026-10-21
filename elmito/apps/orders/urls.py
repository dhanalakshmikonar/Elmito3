from django.urls import path

from . import views

app_name = "orders"

urlpatterns = [
    path("checkout/", views.checkout, name="checkout"),
    path("checkout/review/", views.review, name="review"),
    path("checkout/confirm/", views.confirm, name="confirm"),
    path("orders/", views.order_list, name="list"),
    path("orders/<str:order_number>/", views.order_detail, name="detail"),
    path("orders/<str:order_number>/cancel/", views.cancel, name="cancel"),
    path("orders/<str:order_number>/success/", views.success, name="success"),
    path("orders/<str:order_number>/verify/", views.verify_otp, name="verify_otp"),
    path("orders/<str:order_number>/verify/resend/", views.resend_otp, name="resend_otp"),
]
