"""Access rules shared by the API views: login, report ownership and rate limits.

The decorators work on plain Django views and on DRF function views. On DRF views,
place them below @api_view / @permission_classes so they receive the DRF request.
"""

import math
import time
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

from ..services.report_store import user_can_access_report


def _user(request):
    user = getattr(request, 'user', None)
    return user if user is not None and user.is_authenticated else None


def api_login_required(view):
    """Return 401 JSON instead of redirecting when the caller is not logged in."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if _user(request) is None:
            return JsonResponse({'error': 'Authentication required'}, status=401)
        return view(request, *args, **kwargs)
    return wrapper


def api_staff_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        user = _user(request)
        if user is None:
            return JsonResponse({'error': 'Authentication required'}, status=401)
        if not user.is_staff:
            return JsonResponse({'error': 'Admin access required'}, status=403)
        return view(request, *args, **kwargs)
    return wrapper


def report_access_required(view):
    """Allow only the report's owner or staff. Others get 404 so report ids are not confirmed."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        user = _user(request)
        if user is None:
            return JsonResponse({'error': 'Authentication required'}, status=401)
        report_id = kwargs.get('report_id') or kwargs.get('task_id')
        if report_id and not user_can_access_report(user, str(report_id)):
            return JsonResponse({'error': 'Report not found'}, status=404)
        return view(request, *args, **kwargs)
    return wrapper


def client_ip(request):
    """Client address; X-Forwarded-For is trusted only when a proxy is configured to set it."""
    if getattr(settings, 'TRUST_X_FORWARDED_FOR', False):
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def hit_counter(key, window_seconds):
    """Increment a shared counter that expires after the window; returns the new count."""
    cache.add(key, 0, window_seconds)
    try:
        return cache.incr(key)
    except ValueError:
        # The key expired between add() and incr().
        cache.set(key, 1, window_seconds)
        return 1


def ai_rate_limited(view):
    """Cap AI-generating requests per user per hour (AI_REQUESTS_PER_USER_PER_HOUR)."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        user = _user(request)
        limit = int(getattr(settings, 'AI_REQUESTS_PER_USER_PER_HOUR', 30))
        if user is not None and limit > 0:
            window = 3600
            bucket = int(time.time() // window)
            count = hit_counter(f'ratelimit:ai:{user.pk}:{bucket}', window)
            if count > limit:
                retry_after = window - int(time.time() % window)
                response = JsonResponse({
                    'error': (
                        f'You have reached the limit of {limit} AI requests per hour. '
                        f'Try again in {math.ceil(retry_after / 60)} minutes.'
                    ),
                }, status=429)
                response['Retry-After'] = str(retry_after)
                return response
        return view(request, *args, **kwargs)
    return wrapper
