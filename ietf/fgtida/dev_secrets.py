"""A private, stable development key; never a repository constant."""
import os
import secrets
import time
from pathlib import Path


def django_key():
    configured = os.environ.get('FGTIDA_DEV_SECRET_KEY')
    if configured:
        return configured
    path = Path(__file__).resolve().parents[2] / '.local-django-key'
    try:
        with open(path, 'x', opener=lambda name, flags: os.open(name, flags, 0o600)) as output:
            output.write(secrets.token_urlsafe(64))
    except FileExistsError:
        pass
    for _ in range(10):
        value = path.read_text().strip()
        if len(value) >= 50:
            return value
        time.sleep(.1)
    raise RuntimeError('The private development key file is incomplete; restore or regenerate it locally.')
