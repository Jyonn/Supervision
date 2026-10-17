"""Explicit production settings; importing without required configuration fails closed."""
from .settings import *
from django.core.exceptions import ImproperlyConfigured

def required(name):
    value = os.environ.get(name, '').strip()
    if not value:
        raise ImproperlyConfigured(f'{name} is required')
    return value

SECRET_KEY = required('SUPERVISION_SECRET_KEY')
if len(SECRET_KEY) < 50:
    raise ImproperlyConfigured('SUPERVISION_SECRET_KEY must have at least 50 characters')
FRONTEND_ORIGIN = 'https://supervision.qijiong.work'
PUBLIC_API_ORIGIN = 'https://api.supervision.qijiong.work'
ALLOWED_HOSTS = ['api.supervision.qijiong.work']
CSRF_TRUSTED_ORIGINS = [FRONTEND_ORIGIN]
CORS_ALLOWED_ORIGINS = [FRONTEND_ORIGIN]
MIDDLEWARE.insert(1, 'core.middleware.ExactOriginCors')
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True
# Same-site HTTPS subdomains; cookies remain host-only and HttpOnly.
SESSION_COOKIE_DOMAIN = CSRF_COOKIE_DOMAIN = None
SECURE_SSL_REDIRECT = True
# Reverse proxy must overwrite this header and keep upstream loopback-only.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
PRIVATE_DATA_DIR = Path(required('SUPERVISION_DATA_DIR'))
if not PRIVATE_DATA_DIR.is_absolute():
    raise ImproperlyConfigured('SUPERVISION_DATA_DIR must be absolute')
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': PRIVATE_DATA_DIR / 'supervision.sqlite3'}}
CALENDAR_VAULT_DIR = PRIVATE_DATA_DIR / 'calendar-vault'
FRIENDOM_ENABLED = FRIENDOM_EMAIL_ENABLED = False
CHRONELLE_ENABLED = CHRONELLE_AUTH_ENABLED = False
