"""Project middleware."""

import logging

from django.conf import settings
from django.http import JsonResponse


logger = logging.getLogger(__name__)


class HideServerErrorDetailsMiddleware:
    """With DEBUG off, replace 500 JSON bodies so exception text never reaches the browser.

    Many legacy views return str(exc) in their 500 responses; the original body is
    logged here instead. 4xx and 503 responses keep their messages.
    """

    message = 'Something went wrong on the server. Please try again, or contact support if it keeps happening.'

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (
            response.status_code == 500
            and not settings.DEBUG
            and not getattr(response, 'streaming', False)
            and response.get('Content-Type', '').startswith('application/json')
        ):
            logger.error(
                'Server error on %s %s: %s',
                request.method,
                request.path,
                response.content[:2000].decode('utf-8', 'replace'),
            )
            return JsonResponse({'error': self.message}, status=500)
        return response


class Utf8JsonContentTypeMiddleware:
    """Ensure JSON API responses declare UTF-8 charset."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        content_type = response.get('Content-Type', '')
        if content_type.startswith('application/json') and 'charset=' not in content_type.lower():
            response['Content-Type'] = 'application/json; charset=utf-8'
        return response


class SecurityHeaderCleanupMiddleware:
    """Normalize headers to reduce noisy audit warnings."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Prefer explicit CSP frame-ancestors instead of legacy X-Frame-Options.
        response['Content-Security-Policy'] = "frame-ancestors 'none'"

        for header in ('X-XSS-Protection', 'X-Frame-Options', 'Expires'):
            if header in response:
                del response[header]

        return response
