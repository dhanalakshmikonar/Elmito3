from django.contrib import messages
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.core.forms import ContactForm, NewsletterForm
from apps.core.models import NewsletterSubscriber
from apps.products.models import Category, Product, Review
from apps.products.services.catalog import visible_products


def _rate_limited(request, scope, limit=5, window=3600):
    key = f"rl:{scope}:{request.META.get('REMOTE_ADDR', 'unknown')}"
    cache.add(key, 0, window)
    try:
        return cache.incr(key) > limit
    except ValueError:
        cache.set(key, 1, window)
        return False


def home(request):
    products = visible_products()
    hero = products.filter(is_featured=True).exclude(images=None).first() or products.exclude(images=None).first()
    context = {
        "hero_product": hero,
        "featured": products.filter(is_featured=True)[:4],
        "new_arrivals": products.filter(is_new_arrival=True)[:4],
        "best_sellers": products.filter(is_best_seller=True).order_by("-popularity")[:4],
        "showcase_categories": Category.objects.filter(is_active=True)[:3],
        "reviews": Review.objects.filter(is_approved=True).select_related("user", "product")[:3],
        "newsletter_form": NewsletterForm(),
    }
    return render(request, "core/home.html", context)


def contact(request):
    form = ContactForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if _rate_limited(request, "contact"):
            form.add_error(None, "You've sent several messages recently. Please try again later.")
        else:
            form.save()
            messages.success(request, "Thanks, we've got your message and will reply within one working day.")
            return redirect("core:contact")
    return render(request, "core/contact.html", {"form": form})


@require_POST
def newsletter_subscribe(request):
    form = NewsletterForm(request.POST)
    if _rate_limited(request, "newsletter", limit=10):
        messages.error(request, "Too many attempts. Please try again later.")
    elif form.is_valid():
        NewsletterSubscriber.objects.get_or_create(email=form.cleaned_data["email"].lower())
        # Same message whether or not the address already existed.
        messages.success(request, "You're on the list.")
    else:
        messages.error(request, "Enter a valid email address.")
    target = request.POST.get("next", "")
    if not (target and url_has_allowed_host_and_scheme(target, {request.get_host()}, request.is_secure())):
        target = "/"
    return redirect(target)
