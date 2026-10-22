#!/usr/bin/env bash
set -o errexit
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.prod}"

# Elmito Store 1 deployment style: use the checked-in SQLite/media snapshot on
# Render Free. Ignore old optional disk settings if they are still configured.
if [[ -n "${RENDER_EXTERNAL_HOSTNAME:-}" ]]; then
  if [[ "${DATABASE_URL:-}" == sqlite:*"/var/data/"* ]]; then
    unset DATABASE_URL
  fi
  if [[ "${MEDIA_ROOT:-}" == "/var/data/media" ]]; then
    unset MEDIA_ROOT
  fi
fi

# If a custom MEDIA_ROOT is configured, seed it from the bundled source media.
if [[ -n "${MEDIA_ROOT:-}" && "$MEDIA_ROOT" != "$PWD/media" && -d "$PWD/media" ]]; then
  mkdir -p "$MEDIA_ROOT"
  cp -an "$PWD/media/." "$MEDIA_ROOT/"
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
