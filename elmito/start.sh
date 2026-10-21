#!/usr/bin/env bash
set -o errexit
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.prod}"

# A Render persistent disk is mounted only at runtime, so copy bundled media
# there before Django starts serving files. Keep files already on the disk.
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
