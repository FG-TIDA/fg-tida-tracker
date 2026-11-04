"""Fail-closed production configuration. No development secrets or services."""
import os
from pathlib import Path
from urllib.parse import urlsplit
from django.core.exceptions import ImproperlyConfigured


def required(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise ImproperlyConfigured(f'{name} must be configured for production.')
    return value


def secret(name):
    # Mounted secret files keep credentials out of command lines and compose.
    value = Path(required(name + '_FILE')).read_text().strip()
    if not value:
        raise ImproperlyConfigured(f'{name} must not be empty.')
    return value


DEBUG = False
SERVER_MODE = 'production'
SECRET_KEY = secret('FGTIDA_SECRET_KEY')
if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 8:
    raise ImproperlyConfigured('Use a new randomly generated production secret key.')
if SECRET_KEY in (Path(__file__).resolve().parents[1] / 'settings.py').read_text():
    raise ImproperlyConfigured('Do not reuse a repository development secret.')
origin = urlsplit(required('FGTIDA_ORIGIN'))
if origin.scheme != 'https' or not origin.hostname or origin.path not in ('', '/') or origin.query or origin.fragment or origin.username or origin.password or '*' in origin.netloc:
    raise ImproperlyConfigured('FGTIDA_ORIGIN must be your public HTTPS origin without a path.')
FGTIDA_PUBLIC_DOMAIN = origin.netloc
FGTIDA_APPROVED_PRIVILEGED_USERS = [name.strip() for name in required('FGTIDA_APPROVED_PRIVILEGED_USERS').split(',') if name.strip()]
ALLOWED_HOSTS = [origin.hostname]
CSRF_TRUSTED_ORIGINS = [f'https://{origin.netloc}']
SITE_URL = f'https://{origin.netloc}'
TIME_ZONE = 'UTC'
SECURE_SSL_REDIRECT = True
# The deployment proxy must discard client-supplied Forwarded headers.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 43200
SESSION_COOKIE_NAME = '__Host-fgtida-session'
SESSION_COOKIE_DOMAIN = None
SESSION_COOKIE_PATH = '/'
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.environ.get('FGTIDA_HSTS_INCLUDE_SUBDOMAINS', 'false').lower() == 'true'
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'
CORS_ORIGIN_ALLOW_ALL = False
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = []
X_FRAME_OPTIONS = 'DENY'
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FILES = 10
PASSWORD_POLICY_ENFORCE_AT_LOGIN = True
USE_DEBUG_TOOLBAR = False
INTERNAL_IPS = []

DATABASES = {'default': {
    'ENGINE': 'django.db.backends.postgresql',
    'HOST': required('FGTIDA_DB_HOST'),
    'PORT': os.environ.get('FGTIDA_DB_PORT', '5432'),
    'NAME': required('FGTIDA_DB_NAME'),
    'USER': required('FGTIDA_DB_USER'),
    'PASSWORD': secret('FGTIDA_DB_PASSWORD'),
    'CONN_MAX_AGE': 60,
    'CONN_HEALTH_CHECKS': True,
    'OPTIONS': {'sslmode': os.environ.get('FGTIDA_DB_SSLMODE', 'verify-full')},
}}
DATABASE_ROUTERS = []
ENABLE_BLOBSTORAGE = False
BLOBDB_REPLICATION = {'ENABLED': False}
APP_API_TOKENS = {}
APP_API_TOKEN_PEPPER_BYTES = SECRET_KEY.encode()
ACCOUNT_MIGRATION_PRIVATE_KEY = None

EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
USING_DEBUG_EMAIL_SERVER = False
FGTIDA_MAIL_ENABLED = os.environ.get('FGTIDA_MAIL_ENABLED', 'false').lower() == 'true'
# Production requests only persist mail; a scheduled outbox job performs SMTP.
FGTIDA_MAIL_IMMEDIATE = False
EMAIL_HOST = required('FGTIDA_SMTP_HOST') if FGTIDA_MAIL_ENABLED else ''
EMAIL_PORT = int(os.environ.get('FGTIDA_SMTP_PORT', '587'))
EMAIL_USE_TLS = True
EMAIL_USE_SSL = False
EMAIL_TIMEOUT = 15
EMAIL_HOST_USER = required('FGTIDA_SMTP_USER') if FGTIDA_MAIL_ENABLED else ''
EMAIL_HOST_PASSWORD = secret('FGTIDA_SMTP_PASSWORD') if FGTIDA_MAIL_ENABLED else ''
if not FGTIDA_MAIL_ENABLED:
    EMAIL_BACKEND = 'django.core.mail.backends.dummy.EmailBackend'
DEFAULT_FROM_EMAIL = os.environ.get('FGTIDA_FROM_EMAIL', 'FG-TIDA Secretariat <tsbfgtida@itu.int>')
SERVER_EMAIL = DEFAULT_FROM_EMAIL
ACCOUNT_REQUEST_EMAIL = 'tsbfgtida@itu.int'
BUG_REPORT_EMAIL = 'tsbfgtida@itu.int'
ADMINS = [('FG-TIDA Secretariat', 'tsbfgtida@itu.int')]
MANAGERS = ADMINS
UTILS_FROM_EMAIL_DOMAINS = [DEFAULT_FROM_EMAIL.split('@')[-1].rstrip('>')]

FGTIDA_DATA_DIR = required('FGTIDA_DATA_DIR')
MEDIA_ROOT = FGTIDA_DATA_DIR + '/media'
MEDIA_URL = '/media/'
PHOTOS_DIRNAME = 'photo'
PHOTOS_DIR = MEDIA_ROOT + '/photo'
SERVE_CDN_PHOTOS = False
DOCUMENT_PATH_PATTERN = FGTIDA_DATA_DIR + '/documents/{doc.type_id}/'
AGENDA_PATH = FGTIDA_DATA_DIR + '/proceedings/'
MEETINGHOST_LOGO_PATH = AGENDA_PATH
INTERNET_DRAFT_PATH = FGTIDA_DATA_DIR + '/internet-drafts/'
CHARTER_PATH = FGTIDA_DATA_DIR + '/charters/'
RFC_PATH = FGTIDA_DATA_DIR + '/rfc/'
STATIC_ROOT = FGTIDA_DATA_DIR + '/static'
STATIC_URL = '/static/'
# Keep upstream fonts/vendor assets on the existing read-only asset origin.
STATIC_IETF_ORG = 'https://static.ietf.org'
STATIC_IETF_ORG_INTERNAL = STATIC_IETF_ORG
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
CELERY_BROKER_URL = (None if os.environ.get('FGTIDA_HOSTING') == 'render'
                     else required('FGTIDA_CELERY_BROKER_URL'))
CACHES = {alias: {
    'BACKEND': 'django.core.cache.backends.memcached.PyMemcacheCache',
    'LOCATION': os.environ.get('MEMCACHED_SERVICE_HOST', 'cache') + ':' + os.environ.get('MEMCACHED_SERVICE_PORT', '11211'),
    'KEY_PREFIX': 'fgtida:' + alias,
} for alias in ('default', 'agenda', 'proceedings', 'sessions', 'htmlized', 'slowpages', 'celery-results')}
DJANGO_VITE = {'default': {'manifest_path': str(Path(__file__).resolve().parents[1] / 'static/dist-neue/manifest.json')}}
