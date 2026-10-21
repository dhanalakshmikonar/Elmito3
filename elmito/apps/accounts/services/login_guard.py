"""Brute-force protection for login, keyed by identifier + client address.

Uses Django's cache. In production with several workers, configure a shared cache
(e.g. Redis or database cache) so counters are shared.
"""
from django.conf import settings
from django.core.cache import cache


def _key(request, identifier):
    ip = request.META.get("REMOTE_ADDR", "unknown")
    return f"login_fail:{identifier.strip().lower()}:{ip}"


def is_locked(request, identifier):
    return cache.get(_key(request, identifier), 0) >= settings.LOGIN_MAX_FAILED_ATTEMPTS


def record_failure(request, identifier):
    key = _key(request, identifier)
    cache.add(key, 0, settings.LOGIN_LOCKOUT_SECONDS)
    try:
        cache.incr(key)
    except ValueError:
        cache.set(key, 1, settings.LOGIN_LOCKOUT_SECONDS)


def reset(request, identifier):
    cache.delete(_key(request, identifier))
