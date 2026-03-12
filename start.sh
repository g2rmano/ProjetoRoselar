#!/bin/bash
set -e

cd config
echo "==> Running migrations"
python manage.py migrate --no-input --verbosity 2
echo "==> Creating superuser (if env vars set)"
python manage.py create_superuser_from_env
echo "==> Collecting static files"
python manage.py collectstatic --no-input
echo "==> Starting gunicorn"
exec gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --timeout 120
