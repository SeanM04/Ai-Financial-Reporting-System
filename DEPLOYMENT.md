# Deployment Guide

How to run the AI Financial Analytics System on a Linux server. For running it on your own machine, see `README_CLEAN.md`.

## What runs in production

| Piece | Role |
|---|---|
| **nginx** | Serves the built Vue app, terminates HTTPS, forwards `/api/` and `/admin/` to Gunicorn |
| **Gunicorn** | Runs Django (`backend/gunicorn.conf.py`) |
| **PostgreSQL** | All data, plus the shared cache table used for rate limits and AI quota counters |
| **Redis** (optional) | Replaces the cache table when `REDIS_URL` is set |

No Celery worker or Docker is needed. Report generation runs inside the upload request (see [Limits](#limits)).

Requirements: Ubuntu 22.04+ (or similar), Python 3.12, Node.js 22, PostgreSQL 14+, nginx, and a domain name with DNS pointing at the server.

## 1. Database

```bash
sudo -u postgres createuser --pwprompt financial_analytics
sudo -u postgres createdb -O financial_analytics financial_analytics
```

## 2. Backend

```bash
sudo mkdir -p /srv/financial-analytics && sudo chown $USER /srv/financial-analytics
git clone <repository-url> /srv/financial-analytics
cd /srv/financial-analytics/backend
python3.12 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Edit `backend/.env`. These values matter in production:

```bash
DEBUG=False
SECRET_KEY=<output of: python3 -c "import secrets; print(secrets.token_urlsafe(50))">
ALLOWED_HOSTS=reports.example.com
CORS_ALLOWED_ORIGINS=https://reports.example.com
BEHIND_PROXY=true
DB_NAME=financial_analytics
DB_USER=financial_analytics
DB_PASSWORD=<the password from step 1>
DB_HOST=localhost
OPENAI_API_KEY=<your key>
OPENAI_DAILY_QUOTA_LIMIT=1000
LOG_DIR=/var/log/financial-analytics
```

With `DEBUG=False` the app refuses to start without a real `SECRET_KEY`, and it turns on HTTPS redirects, secure cookies and HSTS (`SECURE_HSTS_SECONDS` starts at 3600; raise it to 31536000 once HTTPS is confirmed). `BEHIND_PROXY=true` is required behind nginx, or the HTTPS redirect will loop.

Then:

```bash
sudo mkdir -p /var/log/financial-analytics && sudo chown $USER /var/log/financial-analytics
venv/bin/python manage.py migrate          # also creates the cache table
venv/bin/python manage.py createsuperuser
venv/bin/python manage.py collectstatic --noinput
venv/bin/python manage.py check --deploy   # expect only the optional HSTS subdomain/preload warnings
```

## 3. Frontend

```bash
cd /srv/financial-analytics/frontend
npm ci
npm run build-only      # outputs frontend/dist; VITE_API_URL=/api from frontend/.env
```

`npm run build` also runs the TypeScript check, which currently fails on existing errors in `PromptReportSplitPane.vue`. Use `build-only` until those are fixed.

## 4. Gunicorn service

`/etc/systemd/system/financial-analytics.service`:

```ini
[Unit]
Description=AI Financial Analytics (Gunicorn)
After=network.target postgresql.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/srv/financial-analytics/backend
ExecStart=/srv/financial-analytics/backend/venv/bin/gunicorn -c gunicorn.conf.py financial_analytics.wsgi:application
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo chown -R www-data:www-data /var/log/financial-analytics
sudo systemctl daemon-reload
sudo systemctl enable --now financial-analytics
```

Gunicorn listens on `127.0.0.1:8000` with a 300-second timeout. Tune with `GUNICORN_WORKERS`, `GUNICORN_THREADS` and `GUNICORN_TIMEOUT`.

## 5. nginx and HTTPS

`/etc/nginx/sites-available/financial-analytics`:

```nginx
server {
    listen 80;
    server_name reports.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name reports.example.com;
    # certbot fills in ssl_certificate lines

    client_max_body_size 50m;          # matches the 50 MB upload limit

    root /srv/financial-analytics/frontend/dist;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /static/ {
        alias /srv/financial-analytics/backend/staticfiles/;
    }

    location ~ ^/(api|admin)/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;       # report generation can take minutes
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/financial-analytics /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d reports.example.com
```

The frontend and API must share one origin (as above). Login uses a session cookie and CSRF token, which do not work across origins.

## 6. Scheduled jobs

Add to the `www-data` crontab (`sudo crontab -u www-data -e`):

```cron
# 02:00 - delete reports past each user's retention period
0 2 * * * cd /srv/financial-analytics/backend && venv/bin/python manage.py cleanup_old_data
# 02:30 - remove expired login sessions
30 2 * * * cd /srv/financial-analytics/backend && venv/bin/python manage.py clearsessions
```

Database backup, in the `postgres` user's crontab (keeps 30 days):

```cron
0 3 * * * pg_dump financial_analytics | gzip > /var/backups/financial-analytics/db-$(date +\%F).sql.gz && find /var/backups/financial-analytics -name 'db-*.sql.gz' -mtime +30 -delete
```

Restore with `gunzip -c db-YYYY-MM-DD.sql.gz | psql financial_analytics`. Copy backups off the server as well.

## 7. Monitoring

- Health check for an uptime monitor: `GET https://reports.example.com/api/health/` returns `{"status": "healthy", "database": "connected", ...}`. It is exempt from the HTTPS redirect.
- Application log: `$LOG_DIR/django.log`. Server errors are logged in full there; the browser only gets a generic message.
- Service log: `journalctl -u financial-analytics -f`.
- AI usage against the daily quota: the AI status page, or `GET /api/ai/usage/`.

## 8. Updating

```bash
cd /srv/financial-analytics
git pull
backend/venv/bin/pip install -r backend/requirements.txt
cd backend && venv/bin/python manage.py migrate && venv/bin/python manage.py collectstatic --noinput && cd ..
cd frontend && npm ci && npm run build-only && cd ..
sudo systemctl restart financial-analytics
```

## Security checklist

- [ ] `DEBUG=False`, a generated `SECRET_KEY`, and `ALLOWED_HOSTS` / `CORS_ALLOWED_ORIGINS` set to your domain only
- [ ] `backend/.env` readable only by the service user (`chmod 600`)
- [ ] HTTPS working, then `SECURE_HSTS_SECONDS` raised
- [ ] `check --deploy` shows no errors
- [ ] Database backups running and a restore tested
- [ ] `OPENAI_DAILY_QUOTA_LIMIT` and `AI_REQUESTS_PER_USER_PER_HOUR` set to what you can afford
- [ ] An OpenAI spending limit set in the OpenAI dashboard as a second safeguard

## Limits

- **Report generation is synchronous.** An upload holds a Gunicorn thread until OpenAI answers (up to about 3 minutes in the worst case). With the defaults (up to 8 workers × 4 threads) that is fine for a small team; heavier use needs a background job queue.
- **Each report stores the whole uploaded file** in the database, so storage grows with upload size. The retention cleanup job keeps this in check.
