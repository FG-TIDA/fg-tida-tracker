#!/bin/sh
set -eu
if [ "${FGTIDA_HOSTING:-}" = render ] && [ "$1" = gunicorn ]; then
    exec python /app/deploy/render_start.py
fi
case "$1" in
  gunicorn|celery)
    python ietf/manage.py check_fgtida_publication --skip-checks
    ;;
esac
exec "$@"
