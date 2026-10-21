import re

from django.conf import settings

from apps.cart.services.cart import item_count
from apps.products.models import Category


def site(request):
    digits = re.sub(r"\D", "", settings.WHATSAPP_PROVIDER_NUMBER or "")
    if len(digits) == 10:
        digits = "91" + digits
    return {
        "nav_categories": list(Category.objects.filter(is_active=True)[:4]),
        "whatsapp_url": f"https://wa.me/{digits}" if digits else "",
        "cart_count": item_count(request),
        "SITE_URL": settings.SITE_URL,
        "FREE_SHIPPING_THRESHOLD": settings.FREE_SHIPPING_THRESHOLD,
        "FLAT_SHIPPING_CHARGE": settings.FLAT_SHIPPING_CHARGE,
    }
