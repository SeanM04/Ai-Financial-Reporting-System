"""
Settings shared by settings_postgres and settings_sqlite.

Everything except the database lives here so security settings cannot drift
between the two. Values come from the environment (backend/.env).
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / '.env')


def env_bool(name, default=False):
    return os.environ.get(name, str(default)).strip().lower() in ('1', 'true', 'yes', 'on')


def env_list(name, default=''):
    return [item.strip() for item in os.environ.get(name, default).split(',') if item.strip()]


# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env_bool('DEBUG', False)

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get('SECRET_KEY', '').strip()
_PLACEHOLDER_KEYS = {'', 'your-secret-key-here', 'your-very-secure-secret-key'}
if SECRET_KEY in _PLACEHOLDER_KEYS or SECRET_KEY.startswith('django-insecure'):
    if not DEBUG:
        raise ImproperlyConfigured(
            'Set a real SECRET_KEY in backend/.env before running with DEBUG=False. Generate one with: '
            'python -c "import secrets; print(secrets.token_urlsafe(50))"'
        )
    SECRET_KEY = SECRET_KEY or 'django-insecure-local-development-only'

ALLOWED_HOSTS = env_list('ALLOWED_HOSTS', 'localhost,127.0.0.1')

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'corsheaders',
    'analytics',
]

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'financial_analytics.middleware.Utf8JsonContentTypeMiddleware',
    'financial_analytics.middleware.SecurityHeaderCleanupMiddleware',
    'financial_analytics.middleware.HideServerErrorDetailsMiddleware',
]

ROOT_URLCONF = 'financial_analytics.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'financial_analytics.wsgi.application'

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static and media files
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# REST Framework: every endpoint requires a logged-in session unless it opts out.
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.MultiPartParser',
        'rest_framework.parsers.FormParser',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': int(os.environ.get('API_PAGE_SIZE', '20')),
}

# CORS and CSRF. Browsers send an Origin header that must be trusted for POSTs
# (the Vite dev server on :5173 proxies to Django on :8000, so it counts as cross-origin).
_DEV_ORIGINS = (
    'http://localhost:3000,http://127.0.0.1:3000,http://localhost:8080,http://127.0.0.1:8080,'
    'http://localhost:5173,http://127.0.0.1:5173,http://localhost:5174,http://127.0.0.1:5174'
)
CORS_ALLOWED_ORIGINS = env_list('CORS_ALLOWED_ORIGINS', _DEV_ORIGINS)
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = env_list('CSRF_TRUSTED_ORIGINS', ','.join(CORS_ALLOWED_ORIGINS))

# Shared cache for rate limits, AI quota counters and regeneration locks.
# It must be shared by every server process: Redis when REDIS_URL is set,
# otherwise a database table (created by `python manage.py migrate`).
REDIS_URL = os.environ.get('REDIS_URL', '').strip()
if REDIS_URL:
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.redis.RedisCache', 'LOCATION': REDIS_URL}}
else:
    CACHES = {'default': {'BACKEND': 'django.core.cache.backends.db.DatabaseCache', 'LOCATION': 'django_cache'}}

# File upload limits
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get('FILE_UPLOAD_MAX_MEMORY_SIZE', str(50 * 1024 * 1024)))
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get('DATA_UPLOAD_MAX_MEMORY_SIZE', str(50 * 1024 * 1024)))
API_PAGE_SIZE = int(os.environ.get('API_PAGE_SIZE', '20'))

# OpenAI
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')
OPENAI_MODEL = os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')
OPENAI_MAX_TOKENS = int(os.environ.get('OPENAI_MAX_TOKENS', '800'))
OPENAI_TEMPERATURE = float(os.environ.get('OPENAI_TEMPERATURE', '0.7'))
# Keep OPENAI_TIMEOUT_SECONDS * (OPENAI_MAX_RETRIES + 1) below the web server timeout (300s in gunicorn.conf.py).
OPENAI_TIMEOUT_SECONDS = float(os.environ.get('OPENAI_TIMEOUT_SECONDS', '60'))
OPENAI_MAX_RETRIES = int(os.environ.get('OPENAI_MAX_RETRIES', '2'))
OPENAI_RATE_LIMIT_DELAY = float(os.environ.get('OPENAI_RATE_LIMIT_DELAY', '1.0'))
OPENAI_QUOTA_CHECK_INTERVAL = int(os.environ.get('OPENAI_QUOTA_CHECK_INTERVAL', '300'))
# Enforced: OpenAI requests per day across all users (0 disables the cap).
OPENAI_DAILY_QUOTA_LIMIT = int(os.environ.get('OPENAI_DAILY_QUOTA_LIMIT', '1000'))
AI_CACHE_DURATION = int(os.environ.get('AI_CACHE_DURATION', '3600'))
AI_ENABLE_CACHING = env_bool('AI_ENABLE_CACHING', True)

# Rate limits
AI_REQUESTS_PER_USER_PER_HOUR = int(os.environ.get('AI_REQUESTS_PER_USER_PER_HOUR', '30'))
LOGIN_MAX_FAILURES = int(os.environ.get('LOGIN_MAX_FAILURES', '5'))
LOGIN_MAX_FAILURES_PER_IP = int(os.environ.get('LOGIN_MAX_FAILURES_PER_IP', '50'))
LOGIN_LOCKOUT_SECONDS = int(os.environ.get('LOGIN_LOCKOUT_SECONDS', '900'))

# Sessions
SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_COOKIE_AGE = 86400  # 24 hours
SESSION_SAVE_EVERY_REQUEST = True

# Security headers
SECURE_BROWSER_XSS_FILTER = False
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'same-origin'

# Set BEHIND_PROXY=true when nginx (or a load balancer) terminates HTTPS and
# sets X-Forwarded-Proto / X-Forwarded-For.
BEHIND_PROXY = env_bool('BEHIND_PROXY', False)
TRUST_X_FORWARDED_FOR = BEHIND_PROXY
if BEHIND_PROXY:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool('SECURE_SSL_REDIRECT', True)
    SECURE_REDIRECT_EXEMPT = [r'^api/health/$']
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Start low; raise to 31536000 once HTTPS is confirmed working everywhere.
    SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '3600'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', False)
    SECURE_HSTS_PRELOAD = env_bool('SECURE_HSTS_PRELOAD', False)

# Logging
LOG_DIR = Path(os.environ.get('LOG_DIR', str(BASE_DIR / 'logs')))
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'DEBUG' if DEBUG else 'INFO').upper()

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': str(LOG_DIR / 'django.log'),
            'formatter': 'verbose',
        },
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'root': {
        'handlers': ['console', 'file'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
        'analytics': {
            'handlers': ['console', 'file'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'financial_analytics': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
