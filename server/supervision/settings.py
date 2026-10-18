from pathlib import Path
import os
import tempfile
from django.core.management.utils import get_random_secret_key
BASE_DIR = Path(__file__).resolve().parents[2]
# Ephemeral process-only development signing key; no persistent secret is created.
SECRET_KEY = get_random_secret_key()
DEBUG = False
ALLOWED_HOSTS = ['127.0.0.1', 'localhost', 'testserver']
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'rest_framework', 'core']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'core.middleware.PreserveLegacySessionDeadline', 'core.middleware.PrivateResponses', 'django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'supervision.urls'
PRIVATE_DATA_DIR = Path(tempfile.gettempdir()) / f'supervision-local-{os.getuid()}'
PRIVATE_DATA_DIR.mkdir(mode=0o700, exist_ok=True)
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': PRIVATE_DATA_DIR / 'supervision.sqlite3'}}
AUTH_USER_MODEL = 'core.User'
AUTH_PASSWORD_VALIDATORS = [{'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'}, {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 10}}, {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'}, {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'}]
SESSION_COOKIE_NAME = 'supervision_session'
CSRF_COOKIE_NAME = 'supervision_csrf'
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SAMESITE = 'Lax'
# Loopback HTTP only. HTTPS/Secure cookie configuration is required before any deployment.
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SESSION_COOKIE_AGE = 90 * 24 * 60 * 60
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_SAVE_EVERY_REQUEST = False
REST_FRAMEWORK = {'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.SessionAuthentication'], 'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'], 'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer']}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'UTC'
USE_TZ = True
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

# Enabled only after the approved Frienden release is verified.
FRIENDOM_ENABLED = True
# Public Frienden metadata verified profile email + the exact existing callback.
FRIENDOM_EMAIL_ENABLED = True
FRIENDOM_CLIENT_ID = 'supervision-local'
FRIENDOM_CALLBACK_URL = 'http://127.0.0.1:8001/api/auth/friendom/callback/'
FRONTEND_ORIGIN = ''
CHRONELLE_ENABLED = False
CHRONELLE_AUTH_ENABLED = False
CHRONELLE_API_ORIGIN = ''
CHRONELLE_AUTHORIZE_ORIGIN = ''
CHRONELLE_APP_URL = ''
# Provider accepts HTTPS callbacks only. Empty callback uses the supported claim polling flow.
CHRONELLE_CALLBACK_URL = ''
CALENDAR_VAULT_DIR = PRIVATE_DATA_DIR / 'calendar-vault'
EMAIL_VERIFICATION_REQUIRED = True
LOGGING = {
    'version': 1, 'disable_existing_loggers': False,
    'filters': {'oauth_redaction': {'()': 'core.middleware.OAuthLogRedaction'}},
    'handlers': {'local_console': {'class': 'logging.StreamHandler', 'filters': ['oauth_redaction']}},
    'loggers': {'django.server': {'handlers': ['local_console'], 'level': 'INFO', 'propagate': False}},
}

# Frozen space OAuth contract. Off until the provider and client capability are approved.
FRIENDEN_SPACE_OAUTH_ENABLED = False
