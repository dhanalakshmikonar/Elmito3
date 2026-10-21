from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from apps.cart.models import Cart
from apps.cart.services import cart as cart_service


@receiver(user_logged_in)
def merge_session_cart(sender, request, user, **kwargs):
    token = request.session.pop(cart_service.SESSION_KEY, None) if request is not None else None
    if not token:
        return
    session_cart = Cart.objects.filter(session_key=token, user__isnull=True).first()
    if session_cart:
        cart_service.merge_into_user_cart(session_cart, user)
