# Deploying EL MITO on Render

| Setting | Value |
|---|---|
| Build command | `./build.sh` (installs dependencies, collects static files) |
| Start command | `bash start.sh` (migrates, creates the cache table, creates the admin if configured, starts Gunicorn) |
| `DJANGO_SETTINGS_MODULE` | `config.settings.prod` |
| `SECRET_KEY` | a long random value (required in production) |
| `ALLOWED_HOSTS`, `SITE_URL` | your domain (the `*.onrender.com` host is allowed automatically) |

Other variables (Razorpay, WhatsApp, email, admin bootstrap) are listed in `.env.example`.

## Persistent products, images and orders

Attach a Render persistent disk to the web service:

1. Open the service's **Settings > Disks** and add a disk mounted at `/var/data`. The service cannot start in
   persistent-disk mode until this disk is attached; the startup script reports this directly if the mount is
   missing or not writable.
2. Redeploy. The start script seeds `/var/data/db.sqlite3` from the committed catalogue once, then uses that
   database and `/var/data/media` for future requests. Product or image changes made in `/admin/` on the live
   site survive code pushes, restarts and free-service spin-downs. Customers, orders and payments are stored
   there too.
3. Leave `DATABASE_URL` and `MEDIA_ROOT` unset to use these persistent-disk defaults. If you set them yourself,
   use `sqlite:////var/data/db.sqlite3` and `/var/data/media` respectively. Do not set `CLOUDINARY_URL` unless
   you want images stored in Cloudinary instead.

Localhost continues to use the repository's `db.sqlite3` and `media/`, so you can add products through the local
`/admin/`. Those local changes remain local after you push code: Render's persistent disk is independent of Git
and deliberately does not get overwritten on every deploy. If both localhost and the live site must edit the
same catalogue, configure a shared PostgreSQL `DATABASE_URL` and `CLOUDINARY_URL` in both environments (see
below). This makes admin changes from either place immediately visible in both.

**Existing live products:** on the first boot after attaching the disk, the committed `db.sqlite3` and `media/`
are copied onto it only if the disk paths are empty. Existing files already on the disk are kept. If the live
service currently has products or images that exist only on its temporary filesystem, export/backup them before
restarting and restore them to the persistent database/disk; a redeploy cannot recover files Render has erased.

## Shared localhost and live editing (optional)

For localhost and Render to edit the same catalogue, use PostgreSQL and Cloudinary. Set the same
`DATABASE_URL` and `CLOUDINARY_URL` in both environments. The SQLite persistent disk setup above is the simplest
durable option when product changes happen on the live admin.

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
