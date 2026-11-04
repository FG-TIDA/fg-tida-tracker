"""Single-instance Render adapter; generic Compose production stays unchanged."""
import os
import re

from django.core.exceptions import ImproperlyConfigured


def apply_render_settings(config):
    # Preloading is a separate, durable browser-list policy, not a deployment
    # prerequisite. Current https://hstspreload.org/ guidance discourages it.
    # Keep ordinary HSTS enabled and retain all other security checks, including
    # W005 until the owner explicitly opts into HTTPS for all web subdomains.
    config['SILENCED_SYSTEM_CHECKS'] = [*config.get('SILENCED_SYSTEM_CHECKS', []), 'security.W021']
    hostname = os.environ.get('RENDER_EXTERNAL_HOSTNAME', '').strip().lower()
    if hostname and not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.onrender\.com', hostname):
        raise ImproperlyConfigured('RENDER_EXTERNAL_HOSTNAME must be the service onrender.com hostname.')
    if hostname:
        config['ALLOWED_HOSTS'] = [*config['ALLOWED_HOSTS'], hostname]
    security = 'django.middleware.security.SecurityMiddleware'
    config['MIDDLEWARE'] = [
        'ietf.fgtida.render_http.RenderEdgeMiddleware',
        security,
        'whitenoise.middleware.WhiteNoiseMiddleware',
        *[item for item in config['MIDDLEWARE'] if item != security],
    ]
    # Only collected application assets are public. Never include MEDIA_ROOT.
    config['WHITENOISE_USE_FINDERS'] = False
    config['WHITENOISE_AUTOREFRESH'] = False
    config['WHITENOISE_MAX_AGE'] = 3600
    config['CACHES'] = {alias: {
        'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
        'LOCATION': '127.0.0.1:11211',
        'KEY_PREFIX': 'fgtida:' + alias,
        'TIMEOUT': 300,
        'OPTIONS': {'connect_timeout': 1, 'timeout': 2},
    } for alias in config['CACHES']}
