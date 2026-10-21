"""Deployment warnings for the two things Render's ephemeral disk silently destroys."""
from django.conf import settings
from django.core import checks


@checks.register(checks.Tags.security, deploy=True)
def ephemeral_storage_checks(app_configs, **kwargs):
    if settings.DEBUG:
        return []
    problems = []
    if settings.STORAGES["default"]["BACKEND"] == "django.core.files.storage.FileSystemStorage":
        problems.append(checks.Warning(
            "Uploaded product images are saved on the server's local disk.",
            hint="Render erases that disk on every restart, deploy and idle spin-down, so images disappear. "
                 "Set CLOUDINARY_URL (see DEPLOYMENT.md), or attach a persistent disk and set MEDIA_ROOT to it.",
            id="elmito.W001",
        ))
    if settings.DATABASES["default"]["ENGINE"].endswith("sqlite3"):
        problems.append(checks.Warning(
            "The database is SQLite on the server's local disk.",
            hint="Unless that file is on a persistent disk, customers, orders and payments are erased on every "
                 "restart. Set DATABASE_URL to a PostgreSQL database.",
            id="elmito.W002",
        ))
    return problems
