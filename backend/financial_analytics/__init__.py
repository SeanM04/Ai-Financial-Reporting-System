from .env import configure_settings_module

# Must run before anything imports Django settings (including celery below).
configure_settings_module()

from .celery import app as celery_app  # noqa: E402

__all__ = ('celery_app',)
