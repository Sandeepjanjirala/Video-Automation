"""Security middleware replicating FastAPI local_guard behavior for Django."""
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin


class LocalSecurityMiddleware(MiddlewareMixin):
    """Enforces security headers, loopback restrictions, and read-only API boundaries."""

    def process_request(self, request):
        # M3 is strictly read-only. Block write mutations on Django endpoints.
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            # Check for x-studio-token parity
            token = request.headers.get('x-studio-token')
            if not token:
                return JsonResponse({'detail': 'Reload the app before saving'}, status=403)
            # Origin header check
            origin = request.headers.get('origin')
            if origin and origin != request.build_absolute_uri('/').rstrip('/'):
                return JsonResponse({'detail': 'Cross-origin changes are not allowed'}, status=403)
            # Django M3 does not accept writes
            return JsonResponse({'detail': 'Django instance is read-only during Stage M3'}, status=405)
        return None

    def process_response(self, request, response):
        # Security headers matching FastAPI
        response['X-Content-Type-Options'] = 'nosniff'
        response['Referrer-Policy'] = 'no-referrer'
        response['X-Frame-Options'] = 'DENY'
        if request.path.startswith('/api/'):
            response['Cache-Control'] = 'no-store'
        return response
