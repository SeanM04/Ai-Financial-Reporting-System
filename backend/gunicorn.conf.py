"""Gunicorn settings for production (Linux).

Run from the backend directory:
    gunicorn -c gunicorn.conf.py financial_analytics.wsgi:application

Report generation calls OpenAI inside the upload request, so a request can take
a few minutes. Threads keep one slow upload from blocking a whole worker, and the
timeout is longer than the OpenAI client's worst case
(OPENAI_TIMEOUT_SECONDS * (OPENAI_MAX_RETRIES + 1), 180s by default).
nginx's proxy_read_timeout must be at least as long as `timeout` below.
"""

import multiprocessing
import os

bind = os.environ.get('GUNICORN_BIND', '127.0.0.1:8000')
workers = int(os.environ.get('GUNICORN_WORKERS', min(multiprocessing.cpu_count() * 2 + 1, 8)))
worker_class = 'gthread'
threads = int(os.environ.get('GUNICORN_THREADS', '4'))
timeout = int(os.environ.get('GUNICORN_TIMEOUT', '300'))
graceful_timeout = 30
keepalive = 5

# Recycle workers now and then to contain memory growth from large uploads.
max_requests = 500
max_requests_jitter = 50

accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('GUNICORN_LOG_LEVEL', 'info')
