import os

from django.conf import settings
from django.core.files import File
from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError

from apps.core.storage import CloudinaryMediaStorage


class Command(BaseCommand):
    help = (
        "Upload every file in the local MEDIA_ROOT to Cloudinary under the same names, so images already "
        "referenced by the database keep working after switching to cloud storage. Safe to re-run."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List what would be uploaded.")
        parser.add_argument("--verify", action="store_true",
                            help="Afterwards, request every image URL stored in the database and report any that fail.")

    def handle(self, *args, **options):
        storage = storages["default"]
        if not isinstance(storage, CloudinaryMediaStorage):
            raise CommandError("Cloud storage is not active. Set CLOUDINARY_URL first.")

        root = str(settings.MEDIA_ROOT)
        names = sorted(
            os.path.relpath(os.path.join(folder, f), root).replace(os.sep, "/")
            for folder, _, files in os.walk(root) for f in files if not f.startswith(".")
        )
        if not names:
            self.stdout.write(f"No local files under {root}; nothing to upload.")
        for name in names:
            if options["dry_run"]:
                self.stdout.write(f"would upload {name}")
                continue
            with open(os.path.join(root, name), "rb") as handle:
                storage.upload(name, File(handle))
            self.stdout.write(f"uploaded {name}")
        if not options["dry_run"] and names:
            self.stdout.write(self.style.SUCCESS(f"{len(names)} file(s) uploaded."))

        if options["verify"]:
            self._verify(storage)

    def _verify(self, storage):
        import requests

        from apps.products.models import Category, ProductImage

        fields = [(ProductImage, "image"), (Category, "image")]
        bad = checked = 0
        for model, field in fields:
            for obj in model.objects.exclude(**{field: ""}).exclude(**{f"{field}__isnull": True}):
                name = getattr(obj, field).name
                checked += 1
                try:
                    ok = requests.head(storage.url(name), timeout=15, allow_redirects=True).status_code == 200
                except requests.RequestException:
                    ok = False
                if not ok:
                    bad += 1
                    self.stdout.write(self.style.ERROR(f"missing: {model.__name__} -> {name}"))
        style = self.style.ERROR if bad else self.style.SUCCESS
        self.stdout.write(style(f"Verified {checked} image(s); {bad} missing."))
