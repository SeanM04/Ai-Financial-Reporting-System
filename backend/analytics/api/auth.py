"""Authentication endpoints."""

import json
import logging

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login
from django.core.cache import cache
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated

from rest_framework.authentication import SessionAuthentication
from .access import client_ip, hit_counter
from ..views import login_view


logger = logging.getLogger(__name__)


def _authenticate_username_or_email(request, identifier, password):
    """Authenticate by username, or by email address when the identifier contains '@'."""
    user = authenticate(request, username=identifier, password=password)
    if user is not None or '@' not in identifier:
        return user

    # Emails are not unique in Django's user model, so try each account that uses it.
    usernames = get_user_model().objects.filter(email__iexact=identifier).values_list('username', flat=True)
    for username in usernames:
        user = authenticate(request, username=username, password=password)
        if user is not None:
            return user
    return None


def _login_failure_keys(request, identifier):
    ip = client_ip(request)
    return (
        f'ratelimit:login:account:{identifier.lower()}:{ip}',
        f'ratelimit:login:ip:{ip}',
    )


def _login_locked_out(keys):
    """True when this account+address or this address has too many recent failures."""
    account_limit = int(getattr(settings, 'LOGIN_MAX_FAILURES', 5))
    ip_limit = int(getattr(settings, 'LOGIN_MAX_FAILURES_PER_IP', 50))
    account_key, ip_key = keys
    return cache.get(account_key, 0) >= account_limit or cache.get(ip_key, 0) >= ip_limit


@csrf_exempt  # No CSRF token exists before login; Django's login() issues a fresh one.
@require_http_methods(["GET", "POST"])
def simple_login_view(request):
    """Authenticate user and create a Django session for API requests."""
    if request.method == 'GET':
        return JsonResponse({'message': 'Login endpoint is accessible'})

    try:
        data = json.loads(request.body) if request.body else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'error': 'Invalid login request'}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({'error': 'Invalid login request'}, status=400)

    username = str(data.get('username') or '').strip()
    password = data.get('password')

    if not username or not password:
        return JsonResponse({'error': 'Username and password are required'}, status=400)

    keys = _login_failure_keys(request, username)
    if _login_locked_out(keys):
        lockout = int(getattr(settings, 'LOGIN_LOCKOUT_SECONDS', 900))
        response = JsonResponse({
            'error': f'Too many failed sign-in attempts. Try again in {lockout // 60} minutes.',
        }, status=429)
        response['Retry-After'] = str(lockout)
        return response

    user = _authenticate_username_or_email(request, username, str(password))
    if user is None:
        lockout = int(getattr(settings, 'LOGIN_LOCKOUT_SECONDS', 900))
        for key in keys:
            hit_counter(key, lockout)
        logger.warning('Failed sign-in for %r from %s', username, client_ip(request))
        return JsonResponse({'error': 'Invalid credentials'}, status=401)

    cache.delete(keys[0])
    login(request, user)
    return JsonResponse({
        'success': True,
        'user': {
            'id': user.id,
            'username': user.username,
            'email': getattr(user, 'email', ''),
            'first_name': getattr(user, 'first_name', ''),
            'last_name': getattr(user, 'last_name', ''),
            'is_staff': user.is_staff,
            'is_superuser': user.is_superuser,
        },
    })


@api_view(['GET'])
@authentication_classes([SessionAuthentication])
@permission_classes([IsAuthenticated])
def current_user_view(request):
    """Return the authenticated Django session user."""
    # Make sure the browser holds a CSRF cookie for later POST requests.
    get_token(request)
    user = request.user
    return JsonResponse({
        'success': True,
        'user': {
            'id': user.id,
            'username': user.username,
            'email': getattr(user, 'email', ''),
            'first_name': getattr(user, 'first_name', ''),
            'last_name': getattr(user, 'last_name', ''),
            'is_staff': user.is_staff,
            'is_superuser': user.is_superuser,
        },
    })


__all__ = ['login_view', 'simple_login_view', 'current_user_view']
