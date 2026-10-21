"""Read-side catalogue logic: search, filtering, sorting and variant matrices."""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.db.models import Exists, OuterRef, Q
from django.db.models.functions import Coalesce

from apps.products.models import Product, ProductVariant

SORT_OPTIONS = {
    "newest": ("-created_at", "-id"),
    "price_asc": ("effective_price", "-created_at"),
    "price_desc": ("-effective_price", "-created_at"),
    "popular": ("-popularity", "-created_at"),
}
MAX_SEARCH_TERMS = 6


def _parse_price(raw):
    try:
        value = Decimal((raw or "").strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() and value >= 0 else None


@dataclass
class ProductFilters:
    q: str = ""
    category: str = ""
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    sizes: list = field(default_factory=list)
    colors: list = field(default_factory=list)
    in_stock_only: bool = False
    sort: str = "newest"

    @classmethod
    def from_querydict(cls, params):
        valid_sizes = set(ProductVariant.Size.values)
        return cls(
            q=(params.get("q") or "").strip()[:100],
            category=(params.get("category") or "").strip()[:120],
            min_price=_parse_price(params.get("min_price")),
            max_price=_parse_price(params.get("max_price")),
            sizes=[s.upper() for s in params.getlist("size") if s.upper() in valid_sizes],
            colors=[c.strip()[:40] for c in params.getlist("color") if c.strip()][:10],
            in_stock_only=params.get("availability") == "in_stock",
            sort=params.get("sort") if params.get("sort") in SORT_OPTIONS else "newest",
        )


def visible_products():
    return (
        Product.objects.filter(status=Product.Status.ACTIVE, category__is_active=True)
        .select_related("category")
        .prefetch_related("images", "variants")
    )


def list_products(filters: ProductFilters, base=None):
    qs = (base if base is not None else visible_products()).annotate(
        effective_price=Coalesce("discount_price", "price")
    )

    for term in filters.q.split()[:MAX_SEARCH_TERMS]:
        qs = qs.filter(
            Q(name__icontains=term) | Q(description__icontains=term)
            | Q(sku__icontains=term) | Q(category__name__icontains=term)
            | Q(variants__color__icontains=term)
        )
    if filters.q:
        qs = qs.distinct()

    if filters.category:
        qs = qs.filter(category__slug=filters.category)
    if filters.min_price is not None:
        qs = qs.filter(effective_price__gte=filters.min_price)
    if filters.max_price is not None:
        qs = qs.filter(effective_price__lte=filters.max_price)

    # Size, colour and availability must hold on the *same* variant, so combine them in one EXISTS.
    if filters.sizes or filters.colors or filters.in_stock_only:
        variants = ProductVariant.objects.filter(product=OuterRef("pk"), is_active=True)
        if filters.sizes:
            variants = variants.filter(size__in=filters.sizes)
        if filters.colors:
            color_q = Q()
            for color in filters.colors:
                color_q |= Q(color__iexact=color)
            variants = variants.filter(color_q)
        if filters.in_stock_only:
            variants = variants.filter(stock__gt=0)
        qs = qs.filter(Exists(variants))

    return qs.order_by(*SORT_OPTIONS[filters.sort])


def related_products(product, limit=4):
    return list(visible_products().filter(category=product.category).exclude(pk=product.pk)[:limit])


def variant_matrix(product):
    """Structure for the product page selectors: colours, each with its sizes and stock."""
    matrix = {}
    for variant in product.variants.all():
        if not variant.is_active:
            continue
        entry = matrix.setdefault(
            variant.color, {"color": variant.color, "hex": variant.color_hex, "sizes": []}
        )
        entry["sizes"].append(
            {"size": variant.size, "variant_id": variant.pk, "stock": variant.stock, "in_stock": variant.stock > 0}
        )
    order = {s: i for i, s in enumerate(ProductVariant.Size.values)}
    for entry in matrix.values():
        entry["sizes"].sort(key=lambda s: order[s["size"]])
    return list(matrix.values())


def facets():
    """Options for the filter UI, built from what's actually sellable."""
    variants = ProductVariant.objects.filter(
        is_active=True, product__status=Product.Status.ACTIVE, product__category__is_active=True
    )
    colors, seen = [], set()
    for color, hex_value in variants.order_by("color").values_list("color", "color_hex").distinct():
        if color.lower() not in seen:
            seen.add(color.lower())
            colors.append({"name": color, "hex": hex_value})
    present = set(variants.values_list("size", flat=True))
    sizes = [s for s in ProductVariant.Size.values if s in present]
    return {"colors": colors, "sizes": sizes}
