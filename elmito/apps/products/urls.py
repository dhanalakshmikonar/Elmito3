from django.urls import path

from . import views

app_name = "products"

urlpatterns = [
    path("shop/", views.shop, name="shop"),
    path("search/", views.search, name="search"),
    path("collections/<slug:slug>/", views.collection, name="collection"),
    path("products/<slug:slug>/", views.detail, name="detail"),
]
