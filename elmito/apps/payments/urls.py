from django.urls import path

from . import views

app_name = "payments"

urlpatterns = [
    path("pay/<str:order_number>/", views.start, name="start"),
    path("payments/callback/", views.callback, name="callback"),
    path("payments/failed/", views.failed, name="failed"),
    path("payments/webhook/", views.webhook, name="webhook"),
]
