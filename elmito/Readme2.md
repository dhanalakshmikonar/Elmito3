# EL MITO: project context for AI coding agents

> **Read this file first. Do not scan the whole repo.** It maps every module, model, route and rule.
> Open only the files named for your task. Everything below was generated from the working code
> (225 passing tests, `check --deploy` clean).

## 1. What this is
Premium T-shirt e-commerce site (brand **EL MITO**), Django 5.2 + server-rendered templates + vanilla JS/CSS.
Indian market: INR, Indian mobile numbers, Razorpay, WhatsApp Cloud API. Mobile-first, minimal "Apple-like" aesthetic
(original design, not a copy).

**Stack:** Python 3.12, Django 5.2, SQLite, WhiteNoise, Gunicorn, Razorpay SDK, Pillow.
No JS framework, no CSS framework, no Celery/Redis. Keep it that way (the brief says: no unnecessary dependencies).

## 2. Status
| Area | State |
|---|---|
| Accounts, OTP, login, reset, addresses | Done, tested |
| Catalogue, variants, inventory ledger, search/filter/sort | Done, tested |
| Storefront pages (home, shop, PDP, cart, checkout, content pages) | Built; **never viewed in a real browser** (no browser available when built) |
| Cart, coupons, checkout, order OTP gate | Done, tested |
| Razorpay (order create, signature verify, webhook, auto-refund) | Done, tested against a **fake gateway only**; real keys not yet used |
| Order management, cancellation + refund, admin, dashboard | Done, tested |
| **Email + WhatsApp order notifications** | **NOT BUILT** (hooks ready, see §10) |
| Frontend polish (auth pages, account page, 404/500, favicon) | Not done (see §11) |
| Real email provider (OTP is emailed; dev prints to console) | Not configured |
| Product photos | Generated placeholders (`seed_demo`) |

## 3. Commands
```bash
python manage.py runserver
python manage.py seed_demo [--reset]      # demo catalogue + placeholder images + 3 reviews
python manage.py createsuperuser           # superusers are auto is_verified=True
python manage.py test apps                 # ~225 tests, ~2 min
python manage.py test apps.payments        # one app;  apps.orders.test_management, apps.products.test_views, ...
DJANGO_SETTINGS_MODULE=config.settings.prod SECRET_KEY=x python manage.py check --deploy
```
Settings: `manage.py` defaults to `config.settings.dev`; **`wsgi.py`/`asgi.py` default to `config.settings.prod`** (deliberate safe default).
`base.py` auto-loads `.env` (tiny loader, no dependency). Prod uses `DatabaseCache` (`createcachetable` runs in `start.sh`).

## 4. Non-negotiable invariants (do not break these)
1. **Never trust the browser for money or stock.** Prices come from the DB in `cart/services/cart.py::summarize`.
   Totals are re-derived at order creation and Razorpay amount comes from `Order.total`.
2. **An order becomes paid in exactly one place:** `payments/services/payment.py::mark_paid` (idempotent). Both the browser
   callback and the webhook call it. Only call it after verifying a signature. DB constraint `one_paid_payment_per_order`
   backs it up.
3. **Order status changes only through** `orders/services/status.py::change_status` (state machine, restock, refund, history,
   `order_status_changed` signal). Never `order.status = ...; save()` outside services/tests. (`PENDING/PAYMENT_PENDING/PAID`
   are system-only; staff can't set them.)
4. **Stock changes only through** `products/services/inventory.py` (`reduce_stock_for_order`, `restore_stock_for_order`,
   `log_movement`): row locks, idempotent via `Order.inventory_reduced`, writes `StockMovement`.
5. **OTP only through** `accounts/services/otp.py` (`issue_otp`, `verify_otp`). Codes are HMAC-hashed, never logged,
   never in responses. Order OTPs are scoped by `reference=order_number`.
6. **Notification/external failures must never fail a paid order.** Receivers run after commit via `send_robust`; email
   service swallows and records failures. Store only exception *class names* in `Notification.error`, never secrets/OTPs.
7. **No secrets in code.** Everything from env vars (§12). Never log OTPs, passwords, tokens, signatures.
8. Custom user model `accounts.User` (`AUTH_USER_MODEL`). Login requires `is_verified=True` (see `accounts/backends.py`).
9. Keep views thin; business logic in each app's `services/`. Reuse templates/includes; no duplicated logic.

## 5. Layout
```
config/settings/{base,dev,prod}.py   config/urls.py
apps/core/            home, static pages, contact, newsletter, context_processors.py, templatetags/elmito.py, admin dashboard, constants.py (states), validators.py
apps/accounts/        models (User, Profile, Address, OTP), forms, views, backends.py, services/{otp,registration,password_reset,login_guard}.py
apps/products/        models, views, services/{catalog,inventory}.py, management/commands/{seed_demo,placeholder_images}.py, admin
apps/cart/            models (Cart, CartItem), services/cart.py, signals.py (merge on login), views
apps/orders/          models (Order, OrderItem, Coupon, CouponRedemption, OrderStatusHistory), signals.py,
                      services/{checkout,flow,status,coupons}.py, views, admin
apps/payments/        models (Payment, WebhookEvent), services/{gateway,payment,webhooks}.py, views, testing.py (test doubles)
apps/notifications/   models (Notification), services/email.py   <- WhatsApp + order emails go here
templates/  static/{css/site.css,js/site.js,js/payment.js}  media/ (product images, COMMITTED on purpose)
README.md  DEPLOYMENT.md  .env.example  requirements.txt  build.sh (install+collectstatic)  start.sh (migrate, cache table, bootstrap_superuser, check --deploy, gunicorn)  Procfile  db.sqlite3 (committed snapshot)
```

## 6. Data model (essentials; all have `created_at/updated_at` via `core.TimeStampedModel` unless noted)
- **User**(email unique ci, username unique ci, mobile unique 10-digit, full_name, is_verified). **Profile**(1:1). **Address**(user, full_name, phone, line1, city, state, pincode, landmark, is_default; one default per user via constraint).
- **OTP**(user, purpose=registration|order|password_reset, reference, code_hash, expires_at, attempts, is_used, is_invalidated).
- **Category**(slug, is_active, image). **Product**(sku, slug auto, category PROTECT, price, discount_price < price constraint, status draft|active|archived, is_featured/is_new_arrival/is_best_seller, popularity). `selling_price`, `has_discount`, `primary_image`, `total_stock`.
- **ProductVariant**(product, color, color_hex, size XS-XXL, sku unique, stock, is_active; unique(product,color,size)). **Stock lives here.** **ProductImage**(color optional ties image to colour). **Review**(is_approved gate). **StockMovement** (append-only ledger).
- **Cart**(user 1:1 nullable | session_key nullable, coupon) **CartItem**(unique(cart,variant), qty>=1).
- **Coupon**(code upper, percent|fixed, value, min_order_value, max_discount, starts/expires, usage_limit, per_user_limit, is_active). **CouponRedemption**(1:1 order; created at payment success).
- **Order**(order_number `ELM+yymmdd+6hex`, user PROTECT, status, payment_status, subtotal, discount_total, shipping_charge, total, coupon/coupon_code, `ship_*` address snapshot, otp_verified_at, paid_at, tracking_number, inventory_reduced, idempotency_key). Properties `is_paid`, `payment_id`, `razorpay_order_id`. Unique open order per (user, idempotency_key).
- **OrderItem** (snapshot: product_name, sku, color, size, unit_price, quantity, line_total; `variant` SET_NULL). **OrderStatusHistory**(order, status, note, changed_by, created_at).
- **Payment**(order, amount, status pending|paid|failed|refunded, razorpay_order_id unique, razorpay_payment_id unique nullable, failure_reason; partial-unique one `paid` per order). **WebhookEvent**(event_id unique, processed).
- **Notification**(channel email|whatsapp, event, recipient, order, status pending|sent|failed, error, sent_at). **ContactMessage**, **NewsletterSubscriber**.

## 7. Routes (namespace:name -> path)
```
core:home /  core:about /about/  core:faq /faq/  core:shipping /shipping-policy/  core:returns /returns-and-refunds/
core:privacy /privacy-policy/  core:terms /terms-and-conditions/  core:contact /contact/  core:newsletter(POST) /newsletter/
products:shop /shop/  products:search /search/?q=  products:collection /collections/<slug>/  products:detail /products/<slug>/
   (filters via GET: category, min_price, max_price, size[], color[], availability=in_stock, sort=newest|price_asc|price_desc|popular, page)
cart:detail /cart/  cart:add(POST) /cart/add/  cart:update(POST) /cart/items/<id>/update/  cart:remove(POST) /cart/items/<id>/remove/
cart:apply_coupon(POST) /cart/coupon/apply/  cart:remove_coupon(POST) /cart/coupon/remove/
accounts: signup /account/signup/  verify_otp /account/verify/  resend_otp(POST) .../verify/resend/  login  logout(POST)
   forgot_password  reset_password  account /account/  profile  change_password  addresses /account/addresses/ (+ new, <pk>/edit, <pk>/delete POST, <pk>/default POST)
orders: checkout /checkout/  review /checkout/review/  confirm(POST) /checkout/confirm/
   list /orders/  detail /orders/<no>/  cancel(POST)  success /orders/<no>/success/  verify_otp /orders/<no>/verify/  resend_otp(POST)
payments: start /pay/<no>/  callback(POST, csrf_exempt, signature-authed) /payments/callback/  failed(POST, login) /payments/failed/
   webhook(POST, csrf_exempt, signature-authed) /payments/webhook/
/admin/   (custom dashboard on index)
```
Orders/addresses/payments views always scope by `user=request.user` (404 otherwise). Keep that.

## 8. Core flows
**Signup:** `SignupForm` -> `registration.register_user` (purges abandoned unverified duplicates) -> `start_verification` (OTP by email) -> `/account/verify/` -> `complete_verification` sets `is_verified`. Pending user id kept in session (`pending_verification_user`). Login by email or username; wrong password on an unverified account resumes verification. Lockout: 5 failures/15 min via cache (`login_guard`).

**Cart:** session cart token (`cart_token`) or user cart; merged into the user cart on login (`cart/signals.py`). `resolve_variant` accepts variant id and/or colour+size and cross-checks. Caps: 10 per item, 20 lines. Coupon revalidated on every summary; invalid coupons are dropped (`refresh`). Shipping = `FLAT_SHIPPING_CHARGE` unless post-discount subtotal >= `FREE_SHIPPING_THRESHOLD`.

**Checkout:** `/checkout/` (pick saved address, stored in session `checkout_address_id`) -> `/checkout/review/` -> POST `/checkout/confirm/` -> `checkout.create_order_from_cart` (re-prices; idempotent via fingerprint; supersedes older `PENDING` drafts) -> `flow.next_step(order)`:
- `ORDER_OTP_STAGE=before_payment` (default): email OTP (`reference=order_number`) -> `/orders/<no>/verify/` -> `flow.verify_order_otp` -> `PAYMENT_PENDING`.
- `none`: straight to payment. **This is the only place to change the OTP stage** (`orders/services/flow.py`).

**Payment:** `/pay/<no>/` -> `payment.start_payment` (re-checks stock, creates/reuses Razorpay order server-side, amount in paise) -> Razorpay Checkout JS (`static/js/payment.js`, config via `json_script`) -> POST `/payments/callback/` (verifies HMAC `order_id|payment_id`) -> `mark_paid` -> `/orders/<no>/success/`. Webhook (`payment.captured`, `order.paid`, `payment.failed`) verifies raw-body HMAC with `RAZORPAY_WEBHOOK_SECRET`, dedupes by `X-Razorpay-Event-Id`, calls the same functions. `mark_paid` on success: payment PAID, reduce stock, order PAID, coupon redemption, remove purchased items from cart, `order_paid` signal on commit. If stock is gone / order cancelled / duplicate payment: auto-refund through the gateway (`REFUND_PENDING` marker lets a replayed webhook retry a failed refund; admin-cancelled paid orders are NOT auto-refunded).

**After payment:** statuses (via `status.change_status`):
```
PENDING -> PAYMENT_PENDING -> PAID -> PROCESSING -> PACKED -> SHIPPED -> OUT_FOR_DELIVERY -> DELIVERED -> RETURNED -> REFUNDED
cancel allowed from PENDING/PAYMENT_PENDING/PAID/PROCESSING/PACKED (customer: not after PACKED). CANCELLED -> REFUNDED allowed.
```
CANCELLED restocks and refunds via Razorpay if paid (failed refund => nothing changes, error shown). RETURNED does **not** auto-restock (inspection). Customer cancel: `orders:cancel`.

## 9. Signals & extension points
- `orders.signals.order_paid(order=)`: after commit, once per paid order.
- `orders.signals.order_status_changed(order=, old_status=, new_status=)`: after commit.
- Receivers are called with `send_robust`; exceptions are logged and ignored. **Connect receivers in `apps/notifications/apps.py::ready()`.**
- Gateway is swappable/fakeable: `payments/services/gateway.py::get_gateway()`; tests use `payments/testing.FakeGateway` + `patch_gateway`.
- Email: `notifications/services/email.py` has `EmailProvider` ABC, `DjangoEmailProvider`, `send_email(...)` (records `Notification`, returns bool, never raises), `send_otp_email`.

## 10. TODO: notifications (spec to implement, not yet built)
Build in `apps/notifications/` (new `services/whatsapp.py`, `services/order_messages.py`, `receivers.py`). Env: `WHATSAPP_ACCESS_TOKEN`,
`WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_PROVIDER_NUMBER` (provider/admin number; also used for site WhatsApp links), `EMAIL_API_KEY`, `EMAIL_FROM`.
- WhatsApp **Cloud API**, via **approved message templates** (Meta requires them for business-initiated messages). `requests` is already installed (dependency of the razorpay SDK), so use it without adding a new dependency. Always set timeouts.
- On `order_paid`: WhatsApp to **customer** ("Hello <name>, Your EL MITO order <id> has been successfully placed. Order Amount: ₹<amount>. Thank you for shopping with EL MITO.") and to **provider** (New EL MITO Order: Order ID, Customer, Phone, Amount, Items, Payment: Successful. Please process the order.) + emails (order confirmation, payment confirmation to customer; new order + payment received to admin).
- On `order_status_changed`: email/WhatsApp for shipped, out for delivery, delivered; admin on important changes (cancelled, refunded).
- Also from the brief: login notification email, registration verification (exists), password reset (exists).
- Every send: create `Notification`, catch **all** exceptions, mark failed with exception class only, never raise. Add tests: WhatsApp failure and email failure must not affect a paid order (patterns exist in `apps/payments/tests.py::NotificationIsolationTests`).
- Indian numbers: store 10 digits; WhatsApp needs `91` + number, no `+`.

## 11. TODO: frontend polish (known gaps)
Design tokens/components are in `static/css/site.css`; base layout `templates/base.html`.
- Auth pages (`templates/accounts/form_page.html`, `verify_otp.html`) are plain centred forms; `account.html` is just links.
- No custom `404.html`/`500.html`; no favicon / OG image.
- Layout, hero, gallery zoom, sticky mobile bar, filter panel were **never visually verified**; expect spacing/mobile bugs. Verify with screenshots at 390px and 1440px.
- Accessibility pass (focus order, contrast, aria on dialogs/gallery).
- Fonts load from Google Fonts (self-host later). Real product photography replaces generated placeholders.

**Frontend conventions**
- Tokens: `--ink #17181A, --stone #F2F2EF, --line #E2E2DE, --muted #6C6C70, --accent #1E4D4A, --danger #A3261F`; fonts Bricolage Grotesque (display) + Hanken Grotesk (text); spacing via `--gutter`, `.wrap` (max 1440px), `.section`.
- Style: sober, whitespace, hairline dividers, **no cards/shadows/gradients** (one soft gradient on collection labels), square corners. Only motion: hero load + subtle hovers; respect `prefers-reduced-motion`.
- Components: `.btn(.btn--ghost/.btn--block/.link)`, `.chip` (radio/checkbox pills), `.grid(.grid--4)`, `.card`, `.price`, `.summary` + `.totals`, `.cart-line`, `.accordion`, `.timeline`, `.messages`, `.errorlist`.
- Reusable includes: `includes/product_card.html`, `totals.html`, `order_lines.html`, `form_fields.html`, `icon.html`. Money filter: `{% load elmito %}{{ x|inr }}` (Indian grouping). Pagination uses Django 5.2 `{% querystring %}`.
- JS: `site.js` = search panel, filter toggle, gallery/zoom/lightbox, variant selection (reads `{{ matrix|json_script:"variant-data" }}`), qty stepper, sticky buy bar. `payment.js` = Razorpay Checkout. Progressive enhancement: pages work without JS where feasible. Templates for forms post normally (no fetch) except payment failure report.
- Content pages are `templates/pages/*.html` extending `pages/page.html`; policies are marked draft.

## 12. Environment variables (`.env.example` is the source of truth)
`DJANGO_SETTINGS_MODULE, SECRET_KEY, DEBUG, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, SITE_URL, DATABASE_URL, RAZORPAY_KEY_ID,
RAZORPAY_KEY_SECRET, RAZORPAY_WEBHOOK_SECRET, WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_PROVIDER_NUMBER,
EMAIL_API_KEY, EMAIL_FROM, ORDER_OTP_STAGE, SECURE_SSL_REDIRECT, MEDIA_ROOT, SERVE_MEDIA, RENDER_EXTERNAL_HOSTNAME (auto)`.
Policy knobs in `base.py`: `OTP_*` (6 digits, 300s expiry, 5 attempts, 30s resend cooldown, 5 sends/hour), `LOGIN_MAX_FAILED_ATTEMPTS`, `LOGIN_LOCKOUT_SECONDS`, `FREE_SHIPPING_THRESHOLD=1999`, `FLAT_SHIPPING_CHARGE=99`.
Razorpay keys unset => `/pay/<no>/` returns 503 "Payments unavailable" (by design; nothing else breaks).

## 13. Testing guide
- Helpers: `apps/accounts/tests.py::make_user`, `apps/products/tests.py::{make_product, make_order}`, `apps/cart/tests.py::make_coupon`,
  `apps/orders/tests.py::CheckoutBase` (user + address + product + cart), `apps/payments/testing.py` (`FakeGateway`, `razorpay_settings`, `patch_gateway`, `sign`, `webhook_request`).
- Test files: `accounts/tests.py`, `products/{tests,test_views}.py`, `cart/tests.py`, `orders/{tests,test_management}.py`, `payments/tests.py`, `core/tests.py`.
- `transaction.on_commit` callbacks do not fire in `TestCase` unless wrapped in `self.captureOnCommitCallbacks(execute=True)`.
- Tests run on SQLite, where `select_for_update` is a no-op: **real concurrency is untested**. Render must use a single instance with SQLite stored on a persistent disk.
- Add a test for every behaviour change; keep the suite green before moving on.

## 14. Gotchas already hit (don't repeat)
- ModelForm fields inherit model `max_length`; a 10-char phone field rejects `+91 98765-43210` before `clean_phone` runs. Override the form field (see `AddressForm`).
- Django admin inlines must list auto fields (`created_at`) as read-only (`ReadOnlyInline.get_readonly_fields`).
- Raising inside `transaction.atomic` rolls back counters. OTP `_check` returns an outcome string and raises *after* the atomic block so attempt counts persist.
- `ProductVariant.stock` edits in admin go through `ProductAdmin.save_formset`/`ProductVariantAdmin.save_model` to write the ledger; bulk `queryset.update(stock=...)` bypasses it.
- Manifest static storage is prod-only (`prod.py`); dev uses plain `CompressedStaticFilesStorage`.
- `.env` must never be committed. `media/` and `db.sqlite3` ARE committed deliberately (Noplastiks-style snapshot); `staticfiles/` and logs are not.

## 15. Recipes
- **New notification:** write a function in `notifications/services/`, connect a receiver in `apps/notifications/apps.py::ready()` to `order_paid`/`order_status_changed`, create a `Notification` row, wrap in try/except, add an isolation test.
- **New order status:** add to `Order.Status`, `status.ALLOWED`, admin actions, templates' `status--<value>` CSS, migration, tests.
- **New product attribute:** model field + migration, admin `fieldsets`, template, `catalog.py` filter if searchable, tests.
- **New page:** add `templates/pages/<x>.html` extending `pages/page.html` and one `page(...)` line in `apps/core/urls.py`.

## 16. Deploy (Render) summary
Build `./build.sh` (install, collectstatic). Start `bash start.sh` (migrate, createcachetable, bootstrap_superuser, check --deploy, then gunicorn with 3 workers). There is no `gunicorn.conf.py`; all boot logic is in `start.sh`.
Set env vars (§12) with `DJANGO_SETTINGS_MODULE=config.settings.prod`. Default (no env): committed `media/` + `db.sqlite3` snapshot, served by Django (`SERVE_MEDIA` defaults True in prod). Live uploads/orders need `CLOUDINARY_URL` + `DATABASE_URL`;
or object storage. Add the Razorpay webhook at `https://<domain>/payments/webhook/` (events in §8). Full steps: `DEPLOYMENT.md`; Razorpay setup: `README.md`.

## 17. Media storage (added after deploy problems)
- Render's disk is ephemeral (restart, deploy, idle spin-down). With `CLOUDINARY_URL` set, `STORAGES['default']` =
  `apps.core.storage.CloudinaryMediaStorage` (switch in `config/settings/base.py`). Without it, local `MEDIA_ROOT` is used.
- Stored names stay `products/<name>_<8hex>.jpg`; Cloudinary public id = `elmito/<name-without-ext>`. Model code unchanged: use `image.url`.
- `python manage.py sync_media [--dry-run] [--verify]` uploads existing local `media/` under the same names (run once, locally).
- `apps/core/checks.py` adds deploy warnings `elmito.W001` (images on local disk) and `elmito.W002` (SQLite); `start.sh` prints them.
- Production DB must be PostgreSQL (`DATABASE_URL`); SQLite on Render loses orders/customers on every spin-down.
- Tests: `apps/core/test_storage.py` (includes real-SDK-against-local-fake-server wire tests). The real Cloudinary API was never contacted from the dev sandbox.

## 18. Changes made after the first build (so you don't rediscover them)
- About page redesigned (`templates/pages/about.html`, image `static/images/about-editorial.png`).
- Mobile navigation drawer: `templates/base.html` (`#mobile-nav`, `data-mobile-menu-toggle`), logic in `static/js/site.js`, styles in `static/css/site.css`.
- Themed admin: `templates/admin/base_site.html` + `static/admin/css/{elmito_admin,product_admin}.css`, `static/admin/js/product_admin.js`.
- `python manage.py bootstrap_superuser` (reads `ELMITO_ADMIN_*` env vars; no-op if none set), called from `start.sh`.
- Verified by a real production boot (`bash start.sh`, Gunicorn, DEBUG off): 24 pages, 37 media + 11 static assets all return 200.

## 19. Editorial theme (second design layer)
- `ELMITO_THEME=editorial` (default) | `classic`. Templates in `themes/editorial/templates/` override same-named files in `templates/`; assets in `static/editorial/`. Guide: `themes/editorial/README.md`.
- Backend untouched: frontend files + 3 template filters (`swatches`, `css_color`, `first_name`) + one `TEMPLATES[DIRS]` line in `base.py`.
- Shared pages (cart, checkout, orders, payments, FAQ, policies, contact, About) keep their markup and are restyled by `editorial.css`. A new CSS class in a shared template must be styled in both `site.css` and `editorial.css`.
- Saved items = browser `localStorage` only (no backend wishlist). Navigation says Cart (not Bag) to match backend messages.
