#!/usr/bin/env bash
# Render build command: ./build.sh
set -o errexit
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings.prod}"
pip install -r requirements.txt
python manage.py collectstatic --noinput
