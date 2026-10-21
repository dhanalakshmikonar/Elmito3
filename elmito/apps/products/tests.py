from decimal import Decimal

from django.db import IntegrityError, transaction
from django.http import QueryDict
from django.test import TestCase

from apps.accounts.models import User
from apps.orders.models import Order, OrderItem
from apps.products.models import Category, Product, ProductVariant, StockMovement
from apps.products.services import catalog, inventory


def make_product(name="Essential Tee", sku="TEE-1", price="1499", discount=None, variants=(("Black", "M", 5),),
                 category=None, status=Product.Status.ACTIVE, **extra):
    category = category or Category.objects.get_or_create(name="Tees")[0]
    product = Product.objects.create(
        name=name, sku=sku, category=category, price=Decimal(price),
        discount_price=Decimal(discount) if discount else None, status=status, **extra,
    )
    for color, size, stock in variants:
        ProductVariant.objects.create(product=product, color=color, size=size, stock=stock, sku=f"{sku}-{color[:2]}-{size}".upper())
    return product


def make_order(variant_qty):
    user, _ = User.objects.get_or_create(username="buyer", defaults=dict(email="b@example.com", mobile="9876543210", full_name="Buyer"))
    order = Order.objects.create(
        user=user, subtotal=1, total=1, ship_name="B", ship_phone="9876543210",
        ship_line1="1 Road", ship_city="Mumbai", ship_state="MH", ship_pincode="400001",
    )
    for variant, qty in variant_qty:
        OrderItem.objects.create(order=order, variant=variant, product_name=variant.product.name, sku=variant.sku,
                                 color=variant.color, size=variant.size, unit_price=1, quantity=qty, line_total=qty)
    return order


class ProductModelTests(TestCase):
    def test_slug_generated_and_unique(self):
        a = make_product(name="Essential Tee", sku="A")
        b = make_product(name="Essential Tee", sku="B")
        self.assertEqual(a.slug, "essential-tee")
        self.assertEqual(b.slug, "essential-tee-2")

    def test_selling_price_uses_discount(self):
        p = make_product(price="1500", discount="1200")
        self.assertEqual(p.selling_price, Decimal("1200"))
        self.assertTrue(p.has_discount)
        self.assertEqual(make_product(sku="X").selling_price, Decimal("1499"))

    def test_discount_must_be_below_price(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_product(price="1000", discount="1000")

    def test_duplicate_variant_rejected(self):
        p = make_product(variants=(("Black", "M", 1),))
        with self.assertRaises(IntegrityError), transaction.atomic():
            ProductVariant.objects.create(product=p, color="Black", size="M", sku="OTHER")

    def test_each_variant_has_own_stock(self):
        p = make_product(variants=(("Black", "S", 2), ("Black", "M", 0), ("White", "S", 7)))
        self.assertEqual(p.total_stock, 9)
        self.assertEqual(sorted(v.in_stock for v in p.variants.all()), [False, True, True])

    def test_variant_matrix(self):
        p = make_product(variants=(("Black", "L", 1), ("Black", "S", 0), ("White", "M", 3)))
        matrix = {m["color"]: [s["size"] for s in m["sizes"]] for m in catalog.variant_matrix(p)}
        self.assertEqual(matrix, {"Black": ["S", "L"], "White": ["M"]})


class CatalogFilterTests(TestCase):
    def setUp(self):
        tees = Category.objects.create(name="Tees")
        polos = Category.objects.create(name="Polos")
        self.a = make_product("Black Tee", "A", "1000", category=tees, variants=(("Black", "M", 3), ("Black", "L", 0)), popularity=5)
        self.b = make_product("White Tee", "B", "2000", discount="900", category=tees, variants=(("White", "S", 2),), popularity=50)
        self.c = make_product("Linen Polo", "C", "3000", category=polos, variants=(("Navy", "XL", 0),), popularity=10)
        make_product("Hidden", "D", "500", status=Product.Status.DRAFT, category=tees)

    def run_filter(self, query=""):
        return list(catalog.list_products(catalog.ProductFilters.from_querydict(QueryDict(query))))

    def test_only_active_products_listed(self):
        self.assertEqual(len(self.run_filter()), 3)

    def test_search_matches_name_category_and_color(self):
        self.assertEqual(self.run_filter("q=polo"), [self.c])
        self.assertEqual(set(self.run_filter("q=tees")), {self.a, self.b})
        self.assertEqual(self.run_filter("q=navy"), [self.c])
        self.assertEqual(self.run_filter("q=black tee"), [self.a])

    def test_category_filter(self):
        self.assertEqual(self.run_filter("category=polos"), [self.c])

    def test_price_filter_uses_discounted_price(self):
        self.assertEqual(self.run_filter("max_price=950"), [self.b])
        self.assertEqual(set(self.run_filter("min_price=1000&max_price=1000")), {self.a})

    def test_size_and_color_apply_to_same_variant(self):
        self.assertEqual(self.run_filter("size=M&color=Black"), [self.a])
        self.assertEqual(self.run_filter("size=S&color=Black"), [])

    def test_availability_respects_size(self):
        self.assertEqual(set(self.run_filter("availability=in_stock")), {self.a, self.b})
        self.assertEqual(self.run_filter("availability=in_stock&size=L"), [])  # Black/L has 0 stock
        self.assertEqual(self.run_filter("size=L"), [self.a])  # without the flag it still lists

    def test_sorting(self):
        self.assertEqual(self.run_filter("sort=price_asc"), [self.b, self.a, self.c])
        self.assertEqual(self.run_filter("sort=price_desc"), [self.c, self.a, self.b])
        self.assertEqual(self.run_filter("sort=popular"), [self.b, self.c, self.a])
        self.assertEqual(self.run_filter("sort=newest")[0], self.c)

    def test_garbage_input_is_ignored(self):
        self.assertEqual(len(self.run_filter("min_price=abc&max_price=NaN&size=ZZ&sort=drop table")), 3)

    def test_no_duplicates_when_search_matches_many_variants(self):
        p = make_product("Multi", "M", variants=(("Red", "S", 1), ("Red", "M", 1), ("Red", "L", 1)))
        self.assertEqual(self.run_filter("q=red").count(p), 1)


class InventoryTests(TestCase):
    def setUp(self):
        self.product = make_product(variants=(("Black", "M", 5), ("White", "M", 1)))
        self.black, self.white = self.product.variants.order_by("color")

    def test_available_quantity_zero_for_inactive_or_draft(self):
        self.assertEqual(inventory.available_quantity(self.black), 5)
        self.black.is_active = False
        self.assertEqual(inventory.available_quantity(self.black), 0)
        self.white.product.status = Product.Status.DRAFT
        self.assertEqual(inventory.available_quantity(self.white), 0)

    def test_reduce_stock_and_ledger(self):
        order = make_order([(self.black, 2)])
        self.assertTrue(inventory.reduce_stock_for_order(order))
        self.black.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(self.black.stock, 3)
        self.assertEqual(self.product.popularity, 2)
        move = StockMovement.objects.get()
        self.assertEqual((move.delta, move.reason), (-2, "order_paid"))

    def test_reduce_is_idempotent(self):
        order = make_order([(self.black, 2)])
        inventory.reduce_stock_for_order(order)
        self.assertFalse(inventory.reduce_stock_for_order(order))
        self.black.refresh_from_db()
        self.assertEqual(self.black.stock, 3)

    def test_insufficient_stock_changes_nothing(self):
        order = make_order([(self.black, 2), (self.white, 2)])  # white has only 1
        with self.assertRaises(inventory.InsufficientStock):
            inventory.reduce_stock_for_order(order)
        self.black.refresh_from_db()
        self.assertEqual(self.black.stock, 5)
        order.refresh_from_db()
        self.assertFalse(order.inventory_reduced)

    def test_duplicate_lines_for_same_variant_are_summed(self):
        order = make_order([(self.black, 2), (self.black, 3)])
        inventory.reduce_stock_for_order(order)
        self.black.refresh_from_db()
        self.assertEqual(self.black.stock, 0)

    def test_restore_stock_is_idempotent(self):
        order = make_order([(self.black, 2)])
        inventory.reduce_stock_for_order(order)
        self.assertTrue(inventory.restore_stock_for_order(order))
        self.assertFalse(inventory.restore_stock_for_order(order))
        self.black.refresh_from_db()
        self.assertEqual(self.black.stock, 5)
        self.assertEqual(StockMovement.objects.filter(reason="order_restock").count(), 1)


class AdminSmokeTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser("root", "root@example.com", "Sturdy#Pass9271", mobile="9000000001", full_name="Root")
        self.client.force_login(self.admin_user)
        self.product = make_product(variants=(("Black", "M", 5),))
        self.variant = self.product.variants.get()

    def test_superuser_is_verified(self):
        self.assertTrue(self.admin_user.is_verified)

    def test_changelists_and_add_pages_load(self):
        for model in ("category", "product", "productvariant", "stockmovement", "review"):
            self.assertEqual(self.client.get(f"/admin/products/{model}/").status_code, 200, model)
        self.assertEqual(self.client.get("/admin/products/product/add/").status_code, 200)
        self.assertEqual(self.client.get(f"/admin/products/product/{self.product.pk}/change/").status_code, 200)

    def test_inline_stock_edit_is_logged(self):
        p = self.product
        data = {
            "name": p.name, "slug": p.slug, "sku": p.sku, "description": "", "category": p.category_id,
            "price": "1499", "discount_price": "", "status": "active", "popularity": 0,
            "images-TOTAL_FORMS": 0, "images-INITIAL_FORMS": 0, "images-MIN_NUM_FORMS": 0, "images-MAX_NUM_FORMS": 1000,
            "variants-TOTAL_FORMS": 2, "variants-INITIAL_FORMS": 1, "variants-MIN_NUM_FORMS": 0, "variants-MAX_NUM_FORMS": 1000,
            "variants-0-id": self.variant.pk, "variants-0-product": p.pk, "variants-0-color": "Black", "variants-0-color_hex": "#000000",
            "variants-0-size": "M", "variants-0-sku": self.variant.sku, "variants-0-stock": 12, "variants-0-is_active": "on",
            "variants-1-id": "", "variants-1-product": p.pk, "variants-1-color": "White", "variants-1-color_hex": "#ffffff",
            "variants-1-size": "L", "variants-1-sku": "NEW-WL", "variants-1-stock": 4, "variants-1-is_active": "on",
        }
        response = self.client.post(f"/admin/products/product/{p.pk}/change/", data)
        self.assertEqual(response.status_code, 302, getattr(response, "context_data", None) and response.context_data["errors"])
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock, 12)
        moves = {m.sku: (m.delta, m.reason) for m in StockMovement.objects.all()}
        self.assertEqual(moves[self.variant.sku], (7, "adjustment"))
        self.assertEqual(moves["NEW-WL"], (4, "initial"))

    def test_empty_product_image_inline_row_is_ignored(self):
        p = self.product
        data = {
            "name": p.name, "slug": p.slug, "sku": p.sku, "description": "", "category": p.category_id,
            "price": "1499", "discount_price": "", "status": "active", "popularity": 0,
            "images-TOTAL_FORMS": 1, "images-INITIAL_FORMS": 0, "images-MIN_NUM_FORMS": 0, "images-MAX_NUM_FORMS": 1000,
            "images-0-id": "", "images-0-product": p.pk, "images-0-image": "", "images-0-color": "",
            "images-0-alt_text": "", "images-0-is_primary": "", "images-0-position": 0,
            "variants-TOTAL_FORMS": 0, "variants-INITIAL_FORMS": 0, "variants-MIN_NUM_FORMS": 0, "variants-MAX_NUM_FORMS": 1000,
        }
        response = self.client.post(f"/admin/products/product/{p.pk}/change/", data)
        self.assertEqual(response.status_code, 302, getattr(response, "context_data", None) and response.context_data["errors"])
        self.assertEqual(p.images.count(), 0)
