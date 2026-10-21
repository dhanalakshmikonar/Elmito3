# Editorial theme

A second storefront design on top of the unchanged Django backend.

## Switch
`ELMITO_THEME=editorial` (default) or `ELMITO_THEME=classic`. This only changes which template folder is searched first
(`config/settings/base.py`, `TEMPLATES["DIRS"]`), so reverting is one variable.

## How it works
- `themes/editorial/templates/` holds templates with the same names as those in `templates/`; Django finds these first.
  Everything not overridden (cart, checkout, orders, payments, FAQ, policies, contact, About) keeps its markup and is
  restyled by CSS only.
- Overridden: `base.html`, `core/home.html`, `products/product_list.html`, `products/product_detail.html`,
  `includes/product_card.html`, `includes/icon.html`, `includes/auth_open.html`, `accounts/{form_page,verify_otp,account}.html`,
  `404.html`, `500.html`.
- Assets: `static/editorial/css/editorial.css`, `static/editorial/js/editorial.js`, self-hosted fonts in
  `static/editorial/fonts/` (Instrument Serif and Instrument Sans, SIL OFL; no Google requests).
- Template helpers in `apps/core/templatetags/elmito.py`: `swatches`, `css_color`, `first_name`.

## Contracts that must not change
Form field names (`product`, `variant`, `color`, `size`, `quantity`, `action`, `code`, `address`...), URL names,
`{% csrf_token %}`, and the product-page JS hooks: `[data-pdp]`, `#variant-data`, `[data-stock]`, `[data-error]`,
`[data-add]`, `#sticky-buy`, `#lightbox`, `.pdp__fig[data-color]`.

## Notes
- **Saved items** (heart buttons and drawer) live in the visitor's browser (`localStorage`); not tied to an account.
- Works without JavaScript except the extras (size availability feedback, saved drawer, menu animation, filter sheet).
- Images fade in and fall back to a neutral panel if a file is missing. Motion respects `prefers-reduced-motion`.
- Tokens (`editorial.css :root`): ivory `#F3EFE6`, charcoal `#1C1C1A`, olive `#5B6046`, clay `#9B4B33` (sale tags only).
