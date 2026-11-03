#!/bin/sh
set -eu
case "$1" in
  gunicorn|celery)
    python ietf/manage.py check_fgtida_publication --skip-checks
    ;;
esac
exec "$@"
