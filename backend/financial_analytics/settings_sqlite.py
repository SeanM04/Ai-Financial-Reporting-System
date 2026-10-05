"""
SQLite settings for local development without PostgreSQL.
Shared settings live in settings_base.
"""

import os

# Local development defaults to DEBUG on; a DEBUG value in backend/.env still wins.
os.environ.setdefault('DEBUG', 'True')

from .settings_base import *  # noqa: E402,F401,F403
from .settings_base import BASE_DIR  # noqa: E402

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}
