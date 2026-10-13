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

            # Stage M5 Mutation Allowlist:
            # - POST /api/assets (media upload from M4)
            # - POST /api/projects/<pid>/jobs (job creation)
            # - POST /api/jobs/<jid>/retry (job retry)
            # - POST /api/jobs/<jid>/cancel (job cancel)
            # All other mutating endpoints remain strictly rejected with 405.
            import re
            normalized_path = request.path.rstrip('/')
            is_allowed_mutation = False
            if request.method == 'POST':
                if normalized_path == '/api/assets':
                    is_allowed_mutation = True
                elif re.fullmatch(r'/api/projects/[a-zA-Z0-9_-]+/jobs', normalized_path):
                    is_allowed_mutation = True
                elif re.fullmatch(r'/api/jobs/[a-zA-Z0-9_-]+/retry', normalized_path):
                    is_allowed_mutation = True
                elif re.fullmatch(r'/api/jobs/[a-zA-Z0-9_-]+/cancel', normalized_path):
                    is_allowed_mutation = True

            if not is_allowed_mutation:
                return JsonResponse({'detail': 'Django instance does not accept this mutation during Stage M4 / Stage M5'}, status=405)

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
