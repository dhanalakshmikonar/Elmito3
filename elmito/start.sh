#!/usr/bin/env bash
set -o errexit
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.prod}"

# Render's service filesystem is temporary. Use the attached persistent disk
# automatically unless the service explicitly supplies a database/media path.
if [[ -n "${RENDER_EXTERNAL_HOSTNAME:-}" ]]; then
  export MEDIA_ROOT="${MEDIA_ROOT:-/var/data/media}"
  if [[ -z "${DATABASE_URL:-}" || ( "$DATABASE_URL" == sqlite:* && "$DATABASE_URL" != *"/var/data/"* ) ]]; then
    export DATABASE_URL="sqlite:////var/data/db.sqlite3"
  fi
  # Never try to create /var/data on Render's read-only service filesystem.
  # It exists and is writable only after a persistent disk is attached there.
  if [[ "$DATABASE_URL" == *"/var/data/"* || ( -z "${CLOUDINARY_URL:-}" && "$MEDIA_ROOT" == /var/data* ) ]]; then
    if [[ ! -d /var/data || ! -w /var/data ]]; then
      echo "ERROR: Render persistent disk is missing or not writable at /var/data." >&2
      echo "In Render > Elmito3 > Settings/Disks, attach a disk with mount path /var/data, then redeploy." >&2
      echo "Alternatively, configure PostgreSQL DATABASE_URL and Cloudinary CLOUDINARY_URL instead." >&2
      exit 1
    fi
  fi
fi

# A Render persistent disk is mounted only at runtime, so copy bundled media
# there before Django starts serving files. Keep files already on the disk.
if [[ -n "${MEDIA_ROOT:-}" && "$MEDIA_ROOT" != "$PWD/media" && -d "$PWD/media" ]]; then
  mkdir -p "$MEDIA_ROOT"
  cp -an "$PWD/media/." "$MEDIA_ROOT/"
fi

# Seed the persistent SQLite database from the committed catalogue only on
# its first start; subsequent deploys keep all live admin changes on the disk.
if [[ "${DATABASE_URL:-}" == *"/var/data/db.sqlite3" ]]; then
  if [[ ! -f /var/data/db.sqlite3 && -f "$PWD/db.sqlite3" ]]; then
    cp "$PWD/db.sqlite3" /var/data/db.sqlite3
  fi
fi

python manage.py migrate --noinput
python manage.py createcachetable
# Creates/updates the admin account only when the ELMITO_ADMIN_* variables are set; otherwise does nothing.
python manage.py bootstrap_superuser
# Fail before Gunicorn if migration and application settings point at different
# databases, or the products migration did not create its required table.
python manage.py shell -c "from django.db import connection; assert 'products_product' in connection.introspection.table_names(), 'products_product missing after migrate in ' + str(connection.settings_dict['NAME'])"
# Print deployment warnings (e.g. images or database on Render's ephemeral disk). Never blocks startup.
python manage.py check --deploy || true
exec gunicorn config.wsgi:application --workers 3 --timeout 60 --access-logfile -
