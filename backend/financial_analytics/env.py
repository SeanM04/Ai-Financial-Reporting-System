"""Choose the settings module before Django starts.

Loads backend/.env, then uses settings_postgres when DB_NAME or DATABASE_URL is
set and settings_sqlite otherwise. An explicit DJANGO_SETTINGS_MODULE always wins.
manage.py, wsgi.py, asgi.py and celery.py all go through here.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent


def configure_settings_module():
    load_dotenv(BACKEND_DIR / '.env')
    if os.environ.get('DB_NAME') or os.environ.get('DATABASE_URL'):
        default = 'financial_analytics.settings_postgres'
    else:
        default = 'financial_analytics.settings_sqlite'
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', default)
    return os.environ['DJANGO_SETTINGS_MODULE']
