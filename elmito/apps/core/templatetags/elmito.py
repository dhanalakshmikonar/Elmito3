import re
from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def _indian_group(integer: str) -> str:
    if len(integer) <= 3:
        return integer
    head, tail = integer[:-3], integer[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


@register.filter
def inr(value):
    """₹1,49,999 style formatting; decimals only when needed."""
    try:
        amount = Decimal(value).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return ""
    whole, _, fraction = f"{amount:.2f}".partition(".")
    text = _indian_group(whole)
    return f"₹{text}" if fraction == "00" else f"₹{text}.{fraction}"


@register.filter
def savings_percent(product):
    if not product.discount_price:
        return 0
    return int(round((1 - product.discount_price / product.price) * 100))


@register.filter
def swatches(product):
    """Distinct colours of a product's active variants (for the colour dots on a card).
    Uses the prefetched variants, so it adds no queries."""
    seen, colours = set(), []
    for variant in product.variants.all():
        key = variant.color.lower()
        if variant.is_active and key not in seen:
            seen.add(key)
            hex_value = variant.color_hex if re.fullmatch(r"#[0-9a-fA-F]{3,8}", variant.color_hex or "") else "#999999"
            colours.append({"name": variant.color, "hex": hex_value})
    return colours


@register.filter
def css_color(value):
    """A colour value that is safe to place inside a style attribute."""
    return value if re.fullmatch(r"#[0-9a-fA-F]{3,8}", value or "") else "#999999"


@register.filter
def first_name(full_name):
    """First word of a name, without the trailing ellipsis that truncatewords adds."""
    parts = (full_name or "").split()
    return parts[0] if parts else ""
