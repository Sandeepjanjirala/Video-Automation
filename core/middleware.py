"""Security middleware replicating FastAPI local_guard behavior for Django."""
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin


class LocalSecurityMiddleware(MiddlewareMixin):
    """Enforces security headers, loopback restrictions, and M4 controlled write guards."""

    def process_request(self, request):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            # Check for x-studio-token parity
            from core.views import DJANGO_TOKEN
            token = request.headers.get('x-studio-token')
            if not token or token != DJANGO_TOKEN:
                return JsonResponse({'detail': 'Reload the app before saving'}, status=403)

            # Origin header check
            origin = request.headers.get('origin')
            if origin and origin != request.build_absolute_uri('/').rstrip('/'):
                return JsonResponse({'detail': 'Cross-origin changes are not allowed'}, status=403)

            # Stage M4 ONLY allows POST /api/assets for controlled media upload
            # All other mutating endpoints remain strictly rejected
            normalized_path = request.path.rstrip('/')
            if normalized_path != '/api/assets' or request.method != 'POST':
                return JsonResponse({'detail': 'Django instance does not accept this mutation during Stage M4'}, status=405)

            # Enforce request size limit
            limit = 220 * 1024 * 1024 if normalized_path == '/api/assets' else 1024 * 1024
            try:
                content_length = int(request.META.get('CONTENT_LENGTH', '0'))
                if content_length > limit:
                    return JsonResponse({'detail': 'Request is too large'}, status=413)
            except (ValueError, TypeError):
                pass

        return None

    def process_response(self, request, response):
        # Security headers matching FastAPI
        response['X-Content-Type-Options'] = 'nosniff'
        response['Referrer-Policy'] = 'no-referrer'
        response['X-Frame-Options'] = 'DENY'
        if request.path.startswith('/api/'):
            response['Cache-Control'] = 'no-store'
        return response
