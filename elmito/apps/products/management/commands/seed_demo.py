import random
from decimal import Decimal

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import User
from apps.products.models import Category, Product, ProductImage, ProductVariant, Review

from .placeholder_images import render_detail, render_front, to_jpeg_bytes

COLORS = {
    "Black": "#1C1D1F", "White": "#F4F4F1", "Sand": "#CDBFA6", "Olive": "#59614B", "Navy": "#212C46", "Stone": "#9C9C97",
}

CATALOG = [
    ("Essentials", "Everyday tees in a soft, structured cotton.", [
        ("Essential Tee", "ELM-ESS-001", 1499, None, ["Black", "White", "Sand"], dict(is_featured=True, is_best_seller=True),
         "The tee we wear most. Midweight combed cotton with a clean crew neck and a straight, easy fit. It softens with every wash and keeps its shape."),
        ("Essential Long Sleeve", "ELM-ESS-002", 1899, None, ["Black", "Stone"], dict(is_new_arrival=True),
         "The Essential Tee with full-length sleeves and ribbed cuffs. Layers under a jacket or stands alone."),
        ("Pocket Tee", "ELM-ESS-003", 1699, 1499, ["White", "Olive", "Navy"], dict(is_best_seller=True),
         "A single chest pocket, set low and square. Same cotton and fit as the Essential Tee."),
    ]),
    ("Heavyweight", "Dense, substantial cotton with a boxier cut.", [
        ("Heavyweight Boxy Tee", "ELM-HVY-001", 2299, None, ["Black", "Sand", "Olive"], dict(is_featured=True, is_new_arrival=True),
         "Thick cotton jersey cut wide through the body and short in the length. Holds its shape instead of clinging."),
        ("Heavyweight Crew", "ELM-HVY-002", 2499, 2099, ["Navy", "Stone"], dict(is_featured=True),
         "Our densest fabric with a close, ribbed collar that stays flat after years of wear."),
    ]),
    ("Longline", "Extended hems for a lean, elongated line.", [
        ("Longline Tee", "ELM-LNG-001", 1799, None, ["Black", "White"], dict(is_new_arrival=True, is_best_seller=True),
         "Extra length in the body with a gently curved hem. Sits well untucked."),
        ("Longline Henley", "ELM-LNG-002", 2199, None, ["Sand", "Navy"], dict(is_featured=True),
         "A three-button placket on our longline block. Dressed enough for dinner, easy enough for the weekend."),
    ]),
]
SIZES = ["S", "M", "L", "XL"]
REVIEWS = [
    (5, "Fits exactly as shown", "The fabric has real weight and it hasn't lost shape after a month of washing. Sizing is true."),
    (5, "My daily tee now", "Clean neckline, no twisting. I ordered a second colour within the week."),
    (4, "Great quality", "Looks far more expensive than it is. Wish the Sand came in more sizes."),
]


class Command(BaseCommand):
    help = "Create demo categories, products, variants, placeholder images and reviews."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing demo catalogue first.")

    @transaction.atomic
    def handle(self, *args, **options):
        if options["reset"]:
            Product.objects.filter(sku__startswith="ELM-").delete()
        random.seed(7)
        products = []
        for order, (cat_name, cat_desc, items) in enumerate(CATALOG):
            category, _ = Category.objects.get_or_create(name=cat_name, defaults={"description": cat_desc, "display_order": order})
            if not category.image:
                color = items[0][4][0]
                category.image.save(f"{category.slug}.jpg", ContentFile(to_jpeg_bytes(render_front(COLORS[color]))), save=True)
            for name, sku, price, discount, colors, flags, description in items:
                product, created = Product.objects.get_or_create(
                    sku=sku, defaults=dict(name=name, category=category, price=Decimal(price),
                                           discount_price=Decimal(discount) if discount else None,
                                           description=description, status=Product.Status.ACTIVE, **flags))
                if not created:
                    continue
                products.append(product)
                position = 0
                for color in colors:
                    for kind, renderer in (("front", render_front), ("detail", render_detail)):
                        image = ProductImage(product=product, color=color, alt_text=f"{name} in {color}, {kind} view",
                                             is_primary=(position == 0), position=position)
                        image.image.save(f"{sku.lower()}-{color.lower()}-{kind}.jpg",
                                         ContentFile(to_jpeg_bytes(renderer(COLORS[color]))), save=True)
                        position += 1
                    for size in SIZES:
                        stock = random.choice([0, 2, 5, 8, 12, 20]) if not (color == colors[0] and size == "M") else 15
                        ProductVariant.objects.create(product=product, color=color, color_hex=COLORS[color], size=size, stock=stock,
                                                      sku=f"{sku}-{color[:3].upper()}-{size}")
                product.popularity = random.randint(5, 120)
                product.save(update_fields=["popularity"])

        if products or Review.objects.count() == 0:
            reviewer, _ = User.objects.get_or_create(
                username="demo_customer", defaults=dict(email="demo@example.com", mobile="9000000000", full_name="Demo Customer", is_verified=True))
            for product, (rating, title, body) in zip(Product.objects.order_by("-popularity")[:3], REVIEWS):
                Review.objects.get_or_create(product=product, user=reviewer, defaults=dict(rating=rating, title=title, body=body, is_approved=True))
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(products)} new product(s)."))
