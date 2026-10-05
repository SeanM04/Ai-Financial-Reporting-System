"""
WSGI config for financial_analytics project.

It exposes the WSGI callable as a module-level variable named ``application``.
Run it with: gunicorn -c gunicorn.conf.py financial_analytics.wsgi:application

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

from django.core.wsgi import get_wsgi_application

from financial_analytics.env import configure_settings_module

configure_settings_module()

application = get_wsgi_application()
