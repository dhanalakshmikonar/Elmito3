import json
import re

from django.test import TestCase
from django.urls import reverse

from apps.products.models import Category, Product
from apps.products.tests import make_product


class StorefrontViewTests(TestCase):
    def setUp(self):
        self.tees = Category.objects.create(name="Tees")
        self.a = make_product("Black Tee", "A", "1000", category=self.tees, variants=(("Black", "M", 3), ("Black", "L", 0), ("White", "M", 2)))
        self.b = make_product("Sale Tee", "B", "2000", discount="1500", category=self.tees, variants=(("Navy", "S", 4),))
        self.sold_out = make_product("Gone Tee", "C", "900", category=self.tees, variants=(("Black", "M", 0),))
        self.draft = make_product("Draft Tee", "D", "500", category=self.tees, status=Product.Status.DRAFT)

    def test_shop_lists_active_products_only(self):
        response = self.client.get(reverse("products:shop"))
        names = [p.name for p in response.context["page_obj"]]
        self.assertIn("Black Tee", names)
        self.assertNotIn("Draft Tee", names)

    def test_collection_and_inactive_collection(self):
        self.assertEqual(self.client.get(self.tees.get_absolute_url()).status_code, 200)
        self.tees.is_active = False
        self.tees.save()
        self.assertEqual(self.client.get(reverse("products:collection", args=["tees"])).status_code, 404)

    def test_search_page_and_empty_state(self):
        response = self.client.get(reverse("products:search"), {"q": "sale"})
        self.assertEqual([p.name for p in response.context["page_obj"]], ["Sale Tee"])
        response = self.client.get(reverse("products:search"), {"q": "zzzz"})
        self.assertContains(response, "Nothing matches")

    def test_filters_via_query_string(self):
        response = self.client.get(reverse("products:shop"), {"size": "M", "color": "White", "availability": "in_stock"})
        self.assertEqual([p.name for p in response.context["page_obj"]], ["Black Tee"])
        self.assertEqual(response.context["active_filter_count"], 3)

    def test_xss_in_search_is_escaped(self):
        response = self.client.get(reverse("products:search"), {"q": "<script>alert(1)</script>"})
        self.assertNotContains(response, "<script>alert(1)</script>")

    def test_pagination_keeps_query(self):
        for i in range(14):
            make_product(f"Bulk {i}", f"BULK{i}", category=self.tees)
        response = self.client.get(reverse("products:shop"), {"sort": "price_asc"})
        self.assertContains(response, "page=2")
        self.assertContains(response, "sort=price_asc")

    def test_detail_exposes_variant_matrix(self):
        response = self.client.get(self.a.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        data = json.loads(re.search(r'id="variant-data"[^>]*>(.*?)</script>', response.content.decode(), re.S).group(1))
        sizes = {m["color"]: {s["size"]: s["in_stock"] for s in m["sizes"]} for m in data}
        self.assertEqual(sizes, {"Black": {"M": True, "L": False}, "White": {"M": True}})
        self.assertContains(response, 'value="L"')  # union of sizes rendered as options

    def test_detail_sale_price_and_sold_out(self):
        self.assertContains(self.client.get(self.b.get_absolute_url()), "Save 25%")
        sold = self.client.get(self.sold_out.get_absolute_url())
        self.assertContains(sold, "Sold out")
        self.assertNotContains(sold, 'data-add')

    def test_draft_product_is_404(self):
        self.assertEqual(self.client.get(self.draft.get_absolute_url()).status_code, 404)


class StaticPageTests(TestCase):
    def test_all_public_pages_render(self):
        for name in ("home", "about", "contact", "faq", "shipping", "returns", "privacy", "terms"):
            self.assertEqual(self.client.get(reverse(f"core:{name}")).status_code, 200, name)

    def test_home_renders_with_catalogue(self):
        make_product("Hero Tee", "H", is_featured=True, is_new_arrival=True, is_best_seller=True)
        response = self.client.get(reverse("core:home"))
        self.assertContains(response, "Hero Tee")
