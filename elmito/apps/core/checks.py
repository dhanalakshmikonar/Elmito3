"""Deployment warnings for the two things Render's ephemeral disk silently destroys."""
from django.conf import settings
from django.core import checks


@checks.register(checks.Tags.security, deploy=True)
def ephemeral_storage_checks(app_configs, **kwargs):
    if settings.DEBUG:
        return []
    problems = []
    media_root = str(getattr(settings, "MEDIA_ROOT", "")).replace("\\", "/").rstrip("/")
    media_is_persistent = media_root == "/var/data" or media_root.startswith("/var/data/")
    if (settings.STORAGES["default"]["BACKEND"] == "django.core.files.storage.FileSystemStorage"
            and not media_is_persistent):
        problems.append(checks.Warning(
            "Uploaded product images are saved on the server's local disk.",
            hint="Render erases that disk on every restart, deploy and idle spin-down, so images disappear. "
                 "Set CLOUDINARY_URL, or attach a persistent disk and set MEDIA_ROOT to its mount path.",
            id="elmito.W001",
        ))
    database = settings.DATABASES["default"]
    db_name = str(database.get("NAME", "")).replace("\\", "/")
    database_is_persistent = db_name == "/var/data" or db_name.startswith("/var/data/")
    if database["ENGINE"].endswith("sqlite3") and not database_is_persistent:
        problems.append(checks.Warning(
            "The database is SQLite on the server's local disk.",
            hint="Unless that file is on a persistent disk, customers, orders and payments are erased on every "
                 "restart. Set DATABASE_URL to PostgreSQL, or use a persistent disk at /var/data.",
            id="elmito.W002",
        ))
    return problems
