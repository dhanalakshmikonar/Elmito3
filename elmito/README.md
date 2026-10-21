# EL MITO: T-shirt store

Django e-commerce site for the EL MITO brand: premium, minimal storefront, OTP-verified accounts,
variant-aware inventory, server-side cart and pricing, coupons, Razorpay payments, order management and a
customised admin.

**Status:** storefront, accounts/OTP, catalogue, cart, checkout, Razorpay, order management and admin are
built and tested (200+ tests). Order **notifications by WhatsApp and email** are the remaining feature; see
[What's next](#whats-next).

---

## Quick start (local)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then edit; defaults work for local use
python manage.py migrate
python manage.py seed_demo      # demo catalogue with generated placeholder images
python manage.py createsuperuser
python manage.py runserver
```

- Store: http://localhost:8000/   Admin: http://localhost:8000/admin/
- Emails (OTP codes) print to the terminal in development.
- Run the tests: `python manage.py test apps`

---

## Adding Razorpay (when you receive the keys)

The integration is complete. It only needs your credentials.

1. **API keys.** In the Razorpay dashboard (switch to *Test Mode* first): *Account & Settings, API Keys, Generate*.
   Put them in `.env`:
   ```
   RAZORPAY_KEY_ID=rzp_test_xxxxxxxx
   RAZORPAY_KEY_SECRET=xxxxxxxxxxxxxxxx
   ```
   Restart the server. Until the keys are set, the payment page shows "Payments unavailable"; nothing breaks.

2. **Webhook** (recommended; this is what confirms orders when a customer closes the tab after paying).
   *Dashboard, Webhooks, Add new webhook*:
   - URL: `https://YOUR-DOMAIN/payments/webhook/`
   - Secret: any long random string you choose. Put the same value in `RAZORPAY_WEBHOOK_SECRET`
   - Events: `payment.captured`, `order.paid`, `payment.failed`

   Razorpay can't reach `localhost`. To test webhooks locally, use a tunnel such as ngrok.

3. **Test it.** Add something to the cart, check out, enter the emailed code, and pay with Razorpay's
   [test cards / UPI](https://razorpay.com/docs/payments/payments/test-card-details/).
   Confirm the order shows *Paid* in `/admin/orders/order/` and stock went down.

4. **Go live.** Generate *Live Mode* keys, replace the values, and create the webhook again in Live Mode
   (live and test have separate webhooks and secrets).

### How payments stay safe
- The Razorpay order and its amount are created **on the server** from the stored order total. The browser
  can't change the price.
- A "payment success" in the browser is **never trusted**. The server checks Razorpay's HMAC signature
  (`order_id|payment_id`) before confirming anything.
- The browser callback and the webhook share one idempotent function, so replays, double clicks, and
  callback/webhook races confirm an order exactly once (stock is reduced once, the coupon is counted once).
  The database also forbids two paid payments for one order.
- If money arrives but the order can't be fulfilled (the last unit sold meanwhile, the order was cancelled, or
  a duplicate payment), the customer is **refunded automatically**. If the refund call fails it is flagged on
  the order and retried when Razorpay redelivers the webhook.
- `RAZORPAY_KEY_SECRET` never reaches the browser; the payment page receives only the public key id.

---

## Design themes

Two interchangeable storefront designs. Set `ELMITO_THEME`:

| Value | Design |
|---|---|
| `editorial` (default) | Ivory/charcoal/olive editorial look: serif display type, asymmetric layouts, numbered collection index, oversized rail, split-screen sign-in, full-screen menu. |
| `classic` | The original design, unchanged. |

Nothing else changes: same views, URLs, forms, cart, checkout and payments. See
[`themes/editorial/README.md`](themes/editorial/README.md).

## Where data lives on Render (read before deploying)

Render's disk is wiped on every restart and idle spin-down. By default this project follows the Noplastiks
arrangement: `media/` (product images) and `db.sqlite3` (catalogue) are committed with the code, so shipped
images and products always come back. Things created on the live site (new uploads, customers, orders) do not
survive. For a live store set `CLOUDINARY_URL` (images) and `DATABASE_URL` (PostgreSQL); no code changes
needed. The boot log shows `elmito.W001`/`W002` until both are set. Full steps in [`DEPLOYMENT.md`](DEPLOYMENT.md).

## Configuration

All secrets come from environment variables. See `.env.example` for the full list and comments.

| Variable | Purpose |
|---|---|
| `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `SITE_URL` | Django basics |
| `DATABASE_URL` | SQLite locally and in production. On Render Free, omit it to use the source snapshot's `db.sqlite3`; see [DEPLOYMENT.md](DEPLOYMENT.md) for the limits of retaining live changes. |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` | Payments |
| `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_PROVIDER_NUMBER` | WhatsApp |
| `EMAIL_API_KEY`, `EMAIL_FROM` | Email provider |
| `ORDER_OTP_STAGE` | `before_payment` (default) or `none` |

Business rules live in `config/settings/base.py`: OTP length/expiry/attempts/cooldown, login lockout, free
shipping threshold (`FREE_SHIPPING_THRESHOLD`, default ₹1,999) and flat shipping charge (₹99).

**Changing the order OTP step:** edit `apps/orders/services/flow.py` only. Views and templates just ask
`flow.next_step(order)`.

---

## Project layout

```
config/settings/{base,dev,prod}.py   settings (prod is the default for wsgi/asgi)
apps/
  core/            home, content pages, contact, newsletter, template filters, admin dashboard
  accounts/        user model, OTP, signup/login/reset, addresses
  products/        categories, products, variants, stock ledger, catalogue + inventory services
  cart/            cart model and pricing service (server-side)
  orders/          checkout, order creation, OTP gate, status machine, coupons, customer order pages
  payments/        Razorpay gateway wrapper, payment lifecycle, callback + webhook
  notifications/   Notification log and email service (WhatsApp/email order messages come next)
templates/  static/  media/ (product images, committed)  db.sqlite3 (committed snapshot)  start.sh (Render boot)
```

Views are thin; logic is in each app's `services/`. The important ones:
`orders/services/status.py` (the only place order status changes),
`payments/services/payment.py` (the only path to a paid order),
`products/services/inventory.py` (atomic, idempotent stock changes with an audit ledger).

---

## Admin

`/admin/` opens with a sales dashboard (revenue today/7/30 days, orders to fulfil, low stock, new customers).

- **Products:** inline variants (colour/size/stock) and images; publish/archive actions. Manual stock edits
  are recorded in the **Stock movements** ledger.
- **Orders:** search by order number, customer, phone, or Razorpay ids; filter by status/date; actions to mark
  *processing / packed / shipped / out for delivery / delivered*; **CSV export**. Status changes are validated
  (no skipping steps), restock on cancellation, and refund through Razorpay when needed. Orders can't be deleted.
- **Payments**, **Webhook events**, **Notifications:** read-only logs.
- **Coupons**, **Customers** (with addresses), **Reviews** (approve before they appear), **Contact messages**,
  **Newsletter subscribers**.

---

## Before you launch: content to replace

- **Product photography.** The demo images are generated placeholders.
  Delete the demo products (or run `seed_demo --reset`) and upload real images in the admin.
- **Policy pages** (shipping, returns, privacy, terms) are clearly marked drafts. Have them reviewed.
- **Size guide** measurements (`templates/products/product_detail.html`) and the fabric/care wording.
- **Fonts** load from Google Fonts; self-host them for speed and privacy if you prefer.
- Set a real `FREE_SHIPPING_THRESHOLD` / `FLAT_SHIPPING_CHARGE`.

---

## What's next

1. **Notifications** (WhatsApp Cloud API and email). The hooks already exist: the payment code sends
   `orders.signals.order_paid` after commit, and the status service sends `order_status_changed`. Receivers are
   isolated, so a failing notification can never affect a paid order. The `Notification` table logs every attempt.
   WhatsApp needs a Meta-approved message template for each message.
2. Final security pass and deployment polish (see `DEPLOYMENT.md`).

See also [`DEPLOYMENT.md`](DEPLOYMENT.md).
