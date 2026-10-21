from django.urls import path
from django.views.generic import TemplateView

from . import views

app_name = "core"


def page(route, template, name):
    return path(route, TemplateView.as_view(template_name=f"pages/{template}.html"), name=name)


urlpatterns = [
    path("", views.home, name="home"),
    path("contact/", views.contact, name="contact"),
    path("newsletter/", views.newsletter_subscribe, name="newsletter"),
    page("about/", "about", "about"),
    page("faq/", "faq", "faq"),
    page("shipping-policy/", "shipping", "shipping"),
    page("returns-and-refunds/", "returns", "returns"),
    page("privacy-policy/", "privacy", "privacy"),
    page("terms-and-conditions/", "terms", "terms"),
]
