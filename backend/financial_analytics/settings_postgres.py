"""
PostgreSQL settings. Used whenever DB_NAME or DATABASE_URL is set (see manage.py),
and the settings to deploy with. Shared settings live in settings_base.
"""

import os

from .settings_base import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'financial_analytics'),
        'USER': os.environ.get('DB_USER', 'postgres'),
        'PASSWORD': os.environ.get('DB_PASSWORD', ''),
        'HOST': os.environ.get('DB_HOST', 'localhost'),
        'PORT': os.environ.get('DB_PORT', '5432'),
        'OPTIONS': {
            'connect_timeout': 60,
        },
        'CONN_MAX_AGE': 60,  # Persistent connections
    }
}
