"""
ASGI config for financial_analytics project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

from django.core.asgi import get_asgi_application

from financial_analytics.env import configure_settings_module

configure_settings_module()

application = get_asgi_application()
