"""Product and category images on Cloudinary.

Render's disk is wiped on every restart, deploy and spin-down, so uploaded images must live elsewhere.
Enabled automatically when CLOUDINARY_URL is set (see config/settings/base.py). Models need no changes:
`image.url` simply becomes a Cloudinary URL.

Stored names stay in the familiar `products/foo_ab12cd34.jpg` form, so switching storage backends never
requires a data migration. The Cloudinary public id is the name without its extension, under `elmito/`.
"""
import logging
import os
import secrets

import cloudinary
import cloudinary.uploader
import requests
from cloudinary.utils import cloudinary_url
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible

logger = logging.getLogger(__name__)
PREFIX = "elmito"
TIMEOUT = 20


@deconstructible
class CloudinaryMediaStorage(Storage):
    @staticmethod
    def _public_id(name: str) -> str:
        stem, _ = os.path.splitext(name.replace("\\", "/").lstrip("/"))
        return f"{PREFIX}/{stem}"

    def get_available_name(self, name, max_length=None):
        """Always add a random suffix: names never collide, so nothing is ever silently overwritten
        and we don't need a network call to ask whether a name is taken."""
        stem, ext = os.path.splitext(name.replace("\\", "/"))
        suffix = f"_{secrets.token_hex(4)}{ext.lower()}"
        if max_length and len(stem) + len(suffix) > max_length:
            stem = stem[: max_length - len(suffix)]
        return f"{stem}{suffix}"

    def upload(self, name, content):
        """Upload `content` so that `name` resolves. Replaces any asset with the same public id."""
        if hasattr(content, "seek"):
            content.seek(0)
        try:
            cloudinary.uploader.upload(
                content, public_id=self._public_id(name), resource_type="image",
                overwrite=True, invalidate=True, unique_filename=False, use_filename=False,
            )
        except Exception as exc:
            logger.error("Cloudinary upload failed for %s: %s", name, type(exc).__name__)
            raise

    def _save(self, name, content):
        self.upload(name, content)
        return name

    def url(self, name):
        # f_auto/q_auto: serve WebP/AVIF where supported and compress sensibly, which also speeds up mobile.
        return cloudinary_url(self._public_id(name), secure=True, fetch_format="auto", quality="auto")[0]

    def exists(self, name):
        return False  # names are unique by construction (see get_available_name)

    def delete(self, name):
        try:
            cloudinary.uploader.destroy(self._public_id(name), invalidate=True)
        except Exception as exc:  # a failed cleanup must never break the request
            logger.warning("Cloudinary delete failed for %s: %s", name, type(exc).__name__)

    def _open(self, name, mode="rb"):
        original_url = cloudinary_url(self._public_id(name), secure=True)[0]
        response = requests.get(original_url, timeout=TIMEOUT)
        response.raise_for_status()
        return ContentFile(response.content, name=name)

    def size(self, name):
        return len(self._open(name).read())
