import os
import subprocess
import sys
import tempfile
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import cloudinary
from django.conf import settings
from django.core import checks
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from apps.core import checks as core_checks
from apps.core.storage import CloudinaryMediaStorage
from apps.products.models import ProductImage
from apps.products.tests import make_product

CLOUD_STORAGES = {
    "default": {"BACKEND": "apps.core.storage.CloudinaryMediaStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
UPLOAD = "apps.core.storage.cloudinary.uploader.upload"
DESTROY = "apps.core.storage.cloudinary.uploader.destroy"


def configure_cloudinary():
    cloudinary.config(cloud_name="democloud", api_key="key", api_secret="secret", secure=True)


class StorageUnitTests(SimpleTestCase):
    def setUp(self):
        configure_cloudinary()
        self.storage = CloudinaryMediaStorage()

    def test_available_names_are_unique_and_keep_folder_and_extension(self):
        names = {self.storage.get_available_name("products/Front.JPG") for _ in range(50)}
        self.assertEqual(len(names), 50)
        for name in names:
            self.assertRegex(name, r"^products/Front_[0-9a-f]{8}\.jpg$")

    def test_available_name_respects_max_length(self):
        name = self.storage.get_available_name("products/" + "a" * 200 + ".jpg", max_length=100)
        self.assertLessEqual(len(name), 100)
        self.assertTrue(name.endswith(".jpg"))

    def test_public_id_drops_extension_and_is_namespaced(self):
        self.assertEqual(self.storage._public_id("products/elm-ess-001-black-front.jpg"), "elmito/products/elm-ess-001-black-front")
        self.assertEqual(self.storage._public_id("categories\\x.png"), "elmito/categories/x")

    def test_url_is_https_cloudinary_with_auto_format_and_quality(self):
        url = self.storage.url("products/tee_ab12cd34.jpg")
        self.assertTrue(url.startswith("https://res.cloudinary.com/democloud/image/upload/"))
        self.assertIn("f_auto", url)
        self.assertIn("q_auto", url)
        self.assertTrue(url.endswith("elmito/products/tee_ab12cd34"))

    def test_save_uploads_once_with_unique_public_id_and_returns_the_name(self):
        with mock.patch(UPLOAD) as upload:
            first = self.storage.save("products/tee.jpg", ContentFile(b"one"))
            second = self.storage.save("products/tee.jpg", ContentFile(b"two"))
        self.assertNotEqual(first, second)  # same filename never overwrites an earlier image
        self.assertEqual(upload.call_count, 2)
        kwargs = upload.call_args_list[0].kwargs
        self.assertEqual(kwargs["public_id"], self.storage._public_id(first))
        self.assertTrue(kwargs["overwrite"])
        self.assertEqual(kwargs["resource_type"], "image")

    def test_failed_upload_is_not_swallowed(self):
        with mock.patch(UPLOAD, side_effect=RuntimeError("cloud down")):
            with self.assertRaises(RuntimeError):
                self.storage.save("products/tee.jpg", ContentFile(b"x"))

    def test_exists_is_false_without_a_network_call(self):
        with mock.patch(UPLOAD) as upload:
            self.assertFalse(self.storage.exists("products/anything.jpg"))
        upload.assert_not_called()

    def test_delete_calls_destroy_and_never_raises(self):
        with mock.patch(DESTROY) as destroy:
            self.storage.delete("products/tee_ab12cd34.jpg")
        destroy.assert_called_once_with("elmito/products/tee_ab12cd34", invalidate=True)
        with mock.patch(DESTROY, side_effect=RuntimeError("down")):
            self.storage.delete("products/tee_ab12cd34.jpg")  # no exception

    def test_open_downloads_the_original(self):
        response = mock.Mock(content=b"pixels", raise_for_status=mock.Mock())
        with mock.patch("apps.core.storage.requests.get", return_value=response) as get:
            self.assertEqual(self.storage.open("products/tee.jpg").read(), b"pixels")
        self.assertTrue(get.call_args.args[0].startswith("https://res.cloudinary.com/democloud/"))
        self.assertNotIn("f_auto", get.call_args.args[0])


@override_settings(STORAGES=CLOUD_STORAGES)
class ModelIntegrationTests(TestCase):
    def setUp(self):
        configure_cloudinary()

    def test_uploading_a_product_image_stores_a_name_and_serves_a_cloudinary_url(self):
        product = make_product()
        with mock.patch(UPLOAD) as upload:
            image = ProductImage.objects.create(
                product=product, image=SimpleUploadedFile("tee.jpg", b"data", content_type="image/jpeg"))
        self.assertRegex(image.image.name, r"^products/tee_[0-9a-f]{8}\.jpg$")
        self.assertTrue(image.image.url.startswith("https://res.cloudinary.com/democloud/"))
        upload.assert_called_once()
        # The stored name is backend-independent, so switching storage never needs a data migration.
        image.refresh_from_db()
        self.assertEqual(image.image.url, ProductImage.objects.get(pk=image.pk).image.url)

    def test_storefront_pages_render_cloudinary_urls(self):
        product = make_product()
        ProductImage.objects.create(product=product, image="products/existing_ab12cd34.jpg", is_primary=True)
        for path in ("/", "/shop/", product.get_absolute_url()):
            response = self.client.get(path)
            self.assertContains(response, "https://res.cloudinary.com/democloud/image/upload/", msg_prefix=path)
            self.assertNotContains(response, "/media/products/", msg_prefix=path)


@override_settings(STORAGES=CLOUD_STORAGES)
class SyncMediaCommandTests(TestCase):
    def setUp(self):
        configure_cloudinary()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        (root / "products").mkdir()
        (root / "categories").mkdir()
        (root / "products" / "a.jpg").write_bytes(b"a")
        (root / "categories" / "b.jpg").write_bytes(b"b")
        (root / ".DS_Store").write_bytes(b"junk")

    def run_command(self, *args):
        out = StringIO()
        with override_settings(MEDIA_ROOT=self.tmp.name):
            call_command("sync_media", *args, stdout=out)
        return out.getvalue()

    def test_uploads_every_file_under_its_existing_name(self):
        with mock.patch(UPLOAD) as upload:
            output = self.run_command()
        ids = sorted(c.kwargs["public_id"] for c in upload.call_args_list)
        self.assertEqual(ids, ["elmito/categories/b", "elmito/products/a"])  # exact names, no random suffix
        self.assertIn("2 file(s) uploaded", output)

    def test_dry_run_uploads_nothing(self):
        with mock.patch(UPLOAD) as upload:
            output = self.run_command("--dry-run")
        upload.assert_not_called()
        self.assertIn("would upload products/a.jpg", output)

    def test_refuses_to_run_without_cloud_storage(self):
        with override_settings(STORAGES={**CLOUD_STORAGES, "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}}):
            with self.assertRaisesMessage(CommandError, "CLOUDINARY_URL"):
                call_command("sync_media", stdout=StringIO())

    def test_verify_reports_missing_images(self):
        product = make_product()
        ProductImage.objects.create(product=product, image="products/a.jpg")
        ProductImage.objects.create(product=product, image="products/gone.jpg")
        responses = {"a": mock.Mock(status_code=200), "gone": mock.Mock(status_code=404)}
        head = lambda url, **kw: responses["a" if url.endswith("/a") else "gone"]  # noqa: E731
        with mock.patch(UPLOAD), mock.patch("requests.head", side_effect=head):
            output = self.run_command("--verify")
        self.assertIn("missing: ProductImage -> products/gone.jpg", output)
        self.assertIn("Verified 2 image(s); 1 missing.", output)


class DeploymentCheckTests(SimpleTestCase):
    def run_check(self, **overrides):
        fake = SimpleNamespace(
            DEBUG=False,
            STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}},
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3"}},
        )
        for key, value in overrides.items():
            setattr(fake, key, value)
        with mock.patch.object(core_checks, "settings", fake):
            return {m.id for m in core_checks.ephemeral_storage_checks(None)}

    def test_warns_about_local_disk_images_and_sqlite(self):
        self.assertEqual(self.run_check(), {"elmito.W001", "elmito.W002"})

    def test_cloud_storage_and_postgres_are_clean(self):
        ok = self.run_check(
            STORAGES={"default": {"BACKEND": "apps.core.storage.CloudinaryMediaStorage"}},
            DATABASES={"default": {"ENGINE": "django.db.backends.postgresql"}},
        )
        self.assertEqual(ok, set())

    def test_silent_in_debug(self):
        self.assertEqual(self.run_check(DEBUG=True), set())

    def test_registered_as_a_deploy_check(self):
        ids = {m.id for m in checks.run_checks(include_deployment_checks=True)}
        self.assertTrue({"elmito.W001", "elmito.W002"} <= ids or settings.DEBUG)


class SettingsSwitchTests(SimpleTestCase):
    def backend(self, cloudinary_url):
        env = {k: v for k, v in os.environ.items() if k != "CLOUDINARY_URL"}
        env["DJANGO_SETTINGS_MODULE"] = "config.settings.dev"
        if cloudinary_url:
            env["CLOUDINARY_URL"] = cloudinary_url
        code = "import django;django.setup();from django.conf import settings;print(settings.STORAGES['default']['BACKEND'])"
        out = subprocess.run([sys.executable, "-c", code], env=env, cwd=settings.BASE_DIR, capture_output=True, text=True, check=True)
        return out.stdout.strip()

    def test_cloudinary_url_switches_the_backend(self):
        self.assertEqual(self.backend("cloudinary://k:s@democloud"), "apps.core.storage.CloudinaryMediaStorage")

    def test_without_it_local_disk_is_used(self):
        self.assertEqual(self.backend(""), "django.core.files.storage.FileSystemStorage")


class FakeCloudinaryServer:
    """A local stand-in for Cloudinary's upload API, so the real SDK code path is exercised."""

    def __enter__(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        received = self.received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                received.append({"path": self.path, "body": body, "type": self.headers["Content-Type"]})
                payload = json.dumps({"public_id": "x", "version": 1, "format": "jpg"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        cloudinary.config(cloud_name="democloud", api_key="key", api_secret="secret", secure=True,
                          upload_prefix=f"http://127.0.0.1:{self.server.server_port}")
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        cloudinary.config(upload_prefix="https://api.cloudinary.com")


class RealSdkWireTests(SimpleTestCase):
    """What actually goes over HTTP when Django hands the SDK each kind of file object."""

    def assert_valid_upload(self, request, payload, public_id_prefix):
        self.assertEqual(request["path"], "/v1_1/democloud/image/upload")
        self.assertIn("multipart/form-data", request["type"])
        body = request["body"]
        self.assertIn(payload, body)  # the image bytes were really sent
        for field in (b'name="public_id"', b'name="signature"', b'name="api_key"', b'name="overwrite"'):
            self.assertIn(field, body)
        self.assertIn(public_id_prefix.encode(), body)
        self.assertNotIn(b"secret", body)  # the API secret signs the request but is never transmitted

    def test_contentfile(self):
        with FakeCloudinaryServer() as server:
            name = CloudinaryMediaStorage().save("products/a.jpg", ContentFile(b"IMAGEBYTES-1"))
        self.assertEqual(len(server.received), 1)
        self.assert_valid_upload(server.received[0], b"IMAGEBYTES-1", "elmito/products/a_")
        self.assertRegex(name, r"^products/a_[0-9a-f]{8}\.jpg$")

    def test_admin_style_uploaded_file(self):
        """What a browser upload through the Django admin looks like."""
        with FakeCloudinaryServer() as server:
            CloudinaryMediaStorage().save("products/b.jpg", SimpleUploadedFile("b.jpg", b"IMAGEBYTES-2", content_type="image/jpeg"))
        self.assert_valid_upload(server.received[0], b"IMAGEBYTES-2", "elmito/products/b_")

    def test_open_file_handle_as_used_by_sync_media(self):
        from django.core.files import File

        with tempfile.NamedTemporaryFile(suffix=".jpg") as handle, FakeCloudinaryServer() as server:
            handle.write(b"IMAGEBYTES-3")
            handle.flush()
            handle.seek(0)
            CloudinaryMediaStorage().upload("products/c.jpg", File(handle))
        self.assert_valid_upload(server.received[0], b"IMAGEBYTES-3", "elmito/products/c")
        self.assertNotIn(b"elmito/products/c_", server.received[0]["body"])  # exact name, no suffix

    def test_a_large_realistic_image_survives_intact(self):
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (900, 1125), (30, 30, 30)).save(buffer, "JPEG", quality=84)
        original = buffer.getvalue()
        with FakeCloudinaryServer() as server:
            CloudinaryMediaStorage().save("products/big.jpg", SimpleUploadedFile("big.jpg", original, content_type="image/jpeg"))
        self.assertIn(original, server.received[0]["body"])  # byte-for-byte
