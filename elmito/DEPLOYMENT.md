# Deploying EL MITO on Render

| Setting | Value |
|---|---|
| Build command | `./build.sh` (installs dependencies, collects static files) |
| Start command | `bash start.sh` (migrates, creates the cache table, creates the admin if configured, starts Gunicorn) |
| `DJANGO_SETTINGS_MODULE` | `config.settings.prod` |
| `SECRET_KEY` | a long random value (required in production) |
| `ALLOWED_HOSTS`, `SITE_URL` | your domain (the `*.onrender.com` host is allowed automatically) |

Other variables (Razorpay, WhatsApp, email, admin bootstrap) are listed in `.env.example`.

## How product images and data are stored

This project is arranged **the same way as the Noplastiks project**: the product images (`media/`) and the
catalogue database (`db.sqlite3`) live in the repository next to the code. Every time Render starts or wakes
the service, it restores exactly that snapshot, so the images and products you ship are always there, even
after long inactivity. No extra service or setup is needed. `.gitignore` deliberately does **not** exclude
`media/` or `db.sqlite3`.

**Adding or changing products in this mode** (the Noplastiks workflow): run the site on your own computer,
add the products and upload their images in `/admin/`, then commit and push `media/` **and** `db.sqlite3`.
Render redeploys with the new snapshot.

**The limit of this arrangement:** Render erases its disk on every restart, deploy and idle spin-down (free
services sleep after 15 minutes). So anything created *on the live site* is lost, namely images uploaded in the
live admin, products added there, new customers, and **orders and payments**. That is fine for a catalogue
you manage on your own computer. It is **not safe once customers can pay**: an order could vanish after the
next sleep. Move to the permanent setup below before you take real payments.

## Permanent setup (use this for a live store)

Two independent upgrades. Nothing in the code changes; you only set environment variables.

### Images on Cloudinary (free, no card)
1. Create an account at cloudinary.com and copy the **API Environment variable**
   (`cloudinary://API_KEY:API_SECRET@CLOUD_NAME`).
2. In Render, add `CLOUDINARY_URL` with that value and redeploy. Images uploaded in the live admin now go
   to Cloudinary and stay there.
3. Upload the images you already have, once, from your computer in the project folder:
   ```bash
   export CLOUDINARY_URL="cloudinary://API_KEY:API_SECRET@CLOUD_NAME"   # PowerShell: $env:CLOUDINARY_URL="..."
   python manage.py sync_media --verify
   ```
   It uploads everything in `media/` under the names the database already uses, then requests each image
   URL and lists any that are missing. Safe to run again.

`CLOUDINARY_URL` is a secret: set it in Render, never commit it.

### Database on PostgreSQL
Create a PostgreSQL database (Render PostgreSQL, Neon or Supabase; check each provider's current free-tier
limits, because some free databases expire) and set `DATABASE_URL` in Render to its connection string.
`start.sh` then migrates it on the next start. Create the admin with the bootstrap variables below, add your
products in `/admin/` (or run `python manage.py seed_demo` once for demo data). Existing SQLite data is not
copied across. Once on PostgreSQL, stop committing `db.sqlite3`: it holds user accounts and password hashes.

### Checking which mode you are in
`start.sh` runs `python manage.py check --deploy` on every boot and prints the result in Render's logs.
`elmito.W001` means images are on the temporary disk; `elmito.W002` means the database is SQLite.
Both gone means nothing important depends on the temporary disk.

## Admin account
Set `ELMITO_ADMIN_USERNAME`, `ELMITO_ADMIN_EMAIL`, `ELMITO_ADMIN_MOBILE`, `ELMITO_ADMIN_FULL_NAME` and
`ELMITO_ADMIN_PASSWORD` (all five) and `start.sh` creates the superuser on boot. An existing account is left
unchanged unless you also set `ELMITO_ADMIN_RESET_PASSWORD=true`. With none of them set it does nothing.
Alternatively run `python manage.py createsuperuser` where you have shell access.

## Razorpay and notifications
Set the payment, email and domain variables from `.env.example`. Add the Razorpay webhook at
`https://YOUR-DOMAIN/payments/webhook/` (events `payment.captured`, `order.paid`, `payment.failed`). A free
service must be awake to receive it; Razorpay retries failed deliveries, and the browser callback after payment
also confirms the order. See `README.md` for the full Razorpay steps.

## Checklist before real customers
- [ ] `CLOUDINARY_URL` set and `sync_media --verify` reports 0 missing
- [ ] `DATABASE_URL` set to PostgreSQL (no `elmito.W001` / `elmito.W002` in the boot log)
- [ ] Live Razorpay keys and live-mode webhook configured; a real test order and refund done
- [ ] Real email provider configured (OTP codes are emailed)
- [ ] Policy pages reviewed; real product photography uploaded
