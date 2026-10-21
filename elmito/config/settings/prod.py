from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa

DEBUG = False
if not SECRET_KEY:
    raise ImproperlyConfigured("SECRET_KEY must be set in production.")

# Django serves uploaded product/category media from MEDIA_ROOT through config.urls.
# A Render persistent disk requires MEDIA_ROOT to point inside its mount path.
SERVE_MEDIA = env_bool("SERVE_MEDIA", True)  # noqa: F405

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)  # noqa
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

STORAGES = {
    **STORAGES,  # noqa: F405
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Shared cache so login lockout and form rate limits hold across Gunicorn workers.
# Create the table once with: python manage.py createcachetable
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
    }
}
