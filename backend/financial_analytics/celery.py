"""
Celery configuration for Financial Analytics System.
"""

import os
import logging
from celery import Celery
from celery.schedules import crontab

from .env import configure_settings_module

configure_settings_module()

logger = logging.getLogger(__name__)

app = Celery('financial_analytics')

# Load configuration from Django settings with CELERY namespace
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks from all registered Django apps
app.autodiscover_tasks()

# Configure periodic tasks (Celery Beat)
app.conf.beat_schedule = {
    # Run data cleanup daily at 2 AM
    'cleanup-old-data-daily': {
        'task': 'analytics.tasks.cleanup_old_data_task',
        'schedule': crontab(hour=2, minute=0),  # 2:00 AM daily
        'options': {'queue': 'default'}
    },
}

# Default Celery configuration
app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes
    result_expires=3600,  # Results expire after 1 hour
)

# Optional: Configure Redis broker if available
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')

# Try to connect, but don't fail if Redis is not available
try:
    app.conf.broker_url = CELERY_BROKER_URL
    app.conf.result_backend = CELERY_RESULT_BACKEND
except Exception as e:
    logger.warning("Could not configure Celery broker: %s", e)
    logger.warning("Celery will use in-memory broker. Configure Redis for production use.")


@app.task(bind=True)
def debug_task(self):
    """Debug task for testing Celery setup."""
    logger.debug("Celery debug task request: %r", self.request)
