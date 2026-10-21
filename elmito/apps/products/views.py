from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from apps.products.models import Category, Product, ProductVariant
from apps.products.services import catalog

PAGE_SIZE = 12


def _listing(request, *, title, category=None, searching=False):
    filters = catalog.ProductFilters.from_querydict(request.GET)
    if category:
        filters.category = category.slug
    page = Paginator(catalog.list_products(filters), PAGE_SIZE).get_page(request.GET.get("page"))
    return render(request, "products/product_list.html", {
        "title": title,
        "category": category,
        "searching": searching,
        "filters": filters,
        "page_obj": page,
        "facets": catalog.facets(),
        "categories": Category.objects.filter(is_active=True),
        "sort_options": [("newest", "Newest"), ("price_asc", "Price: low to high"),
                         ("price_desc", "Price: high to low"), ("popular", "Most popular")],
        "active_filter_count": sum(bool(x) for x in (
            filters.min_price is not None, filters.max_price is not None,
            filters.sizes, filters.colors, filters.in_stock_only)),
    })


def shop(request):
    return _listing(request, title="Shop all")


def collection(request, slug):
    category = get_object_or_404(Category, slug=slug, is_active=True)
    return _listing(request, title=category.name, category=category)


def search(request):
    query = request.GET.get("q", "").strip()[:100]
    return _listing(request, title=f"Results for “{query}”" if query else "Search", searching=True)


def detail(request, slug):
    product = get_object_or_404(catalog.visible_products(), slug=slug)
    matrix = catalog.variant_matrix(product)
    images = list(product.images.all())
    present = {s["size"] for m in matrix for s in m["sizes"]}
    sizes = [s for s in ProductVariant.Size.values if s in present]
    reviews = product.reviews.filter(is_approved=True).select_related("user")[:5]
    return render(request, "products/product_detail.html", {
        "product": product,
        "images": images,
        "matrix": matrix,
        "sizes": sizes,
        "in_stock": any(s["in_stock"] for m in matrix for s in m["sizes"]),
        "related": catalog.related_products(product),
        "reviews": reviews,
    })
