"""Security middleware replicating FastAPI local_guard behavior for Django."""
import re
from urllib.parse import quote

from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.template.loader import render_to_string
from django.utils.deprecation import MiddlewareMixin

from studio import access

SIGN_IN_PATHS = ('/login', '/logout')
# The sign-in page needs its stylesheet and icons before signing in; nothing else is public.
PUBLIC_FILE = re.compile(r'/static/(app\.css|icon\.svg|icon-\d+\.png|manifest\.webmanifest)')


class AccessMiddleware(MiddlewareMixin):
    """The studio PC needs no password; every other device signs in (studio/access.py)."""

    def process_request(self, request):
        path = request.path
        if access.allowed(request) or path in SIGN_IN_PATHS or PUBLIC_FILE.fullmatch(path):
            return None
        if not access.password_set():
            message = ('Access from other devices is off. On the studio computer, open Settings and set a '
                       'password for phone and other devices.')
            if path.startswith('/api/'):
                return JsonResponse({'detail': message}, status=403)
            return HttpResponse(render_to_string('studio/login.html', {'disabled': True}, request), status=403)
        if path.startswith('/api/'):
            return JsonResponse({'detail': 'Signed out. Reload the page and enter the studio password.'}, status=401)
        return HttpResponseRedirect('/login?next=' + quote(request.get_full_path(), safe='/?=&'))


class LocalSecurityMiddleware(MiddlewareMixin):
    """Enforces security headers, loopback restrictions, and M4 controlled write guards."""

    def process_request(self, request):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            # Check for x-studio-token parity (the sign-in form comes before the app has a token)
            from core.views import DJANGO_TOKEN
            token = request.headers.get('x-studio-token')
            if request.path not in SIGN_IN_PATHS and (not token or token != DJANGO_TOKEN):
                return JsonResponse({'detail': 'Reload the app before saving'}, status=403)

            # Origin header check
            origin = request.headers.get('origin')
            if origin and origin != request.build_absolute_uri('/').rstrip('/'):
                return JsonResponse({'detail': 'Cross-origin changes are not allowed'}, status=403)
            if request.path in SIGN_IN_PATHS:
                return None

            # Stage M6 Mutation Allowlist:
            # - POST /api/assets (media upload from M4)
            # - POST /api/projects (project create)
            # - PUT /api/projects/<pid> (project update with optimistic lock)
            # - POST /api/projects/<pid>/approve (project revision approval)
            # - POST /api/projects/<pid>/caption-timing (read-only measurement of an unsaved body)
            # - POST /api/projects/<pid>/framing-preview (read-only frame render of an unsaved scene)
            # - POST /api/projects/<pid>/caption-preview (read-only caption style frame render)
            # - PUT /api/projects/<pid>/draft (autosave draft save)
            # - DELETE /api/projects/<pid>/draft (autosave draft delete)
            # - DELETE /api/projects/<pid> (permanent project delete; library media stays)
            # - POST /api/projects/<pid>/jobs/<jid>/apply & /apply-job/<jid> (job result application)
            # - POST /api/projects/<pid>/assign-media (scene media assignment)
            # - POST /api/projects/<pid>/voice_lock (voice lock toggle)
            # - POST /api/projects/<pid>/pronunciation_dict (pronunciation dict update)
            # - POST /api/projects/<pid>/jobs (job creation from M5)
            # - POST /api/jobs/<jid>/retry (job retry from M5)
            # - POST /api/jobs/<jid>/cancel (job cancel from M5)
            # All other mutating endpoints remain strictly rejected with 405.
            import re
            normalized_path = request.path.rstrip('/')
            is_allowed_mutation = False
            if request.method == 'POST':
                if normalized_path in (
                    '/api/assets',
                    '/api/projects',
                    '/api/settings',
                    '/api/voice/normalize',
                    '/api/voice/transliterate',
                    '/api/voice/preview',
                    '/api/voice/ab_compare',
                    '/api/voice/rewrite_preview',
                ):
                    is_allowed_mutation = True
                elif re.fullmatch(r'/api/projects/[a-zA-Z0-9_-]+/jobs', normalized_path):
                    is_allowed_mutation = True
                elif re.fullmatch(r'/api/jobs/[a-zA-Z0-9_-]+/retry', normalized_path):
                    is_allowed_mutation = True
                elif re.fullmatch(r'/api/jobs/[a-zA-Z0-9_-]+/cancel', normalized_path):
                    is_allowed_mutation = True
                elif re.fullmatch(r'/api/projects/[a-zA-Z0-9_-]+/approve', normalized_path):
                    is_allowed_mutation = True
                elif re.fullmatch(r'/api/projects/[a-zA-Z0-9_-]+/caption-timing', normalized_path):
                    is_allowed_mutation = True
                elif re.fullmatch(r'/api/projects/[a-zA-Z0-9_-]+/(framing|caption)-preview', normalized_path):
                    is_allowed_mutation = True
                elif re.fullmatch(r'/api/projects/[a-zA-Z0-9_-]+/assign-media', normalized_path):
                    is_allowed_mutation = True
                elif re.fullmatch(r'/api/projects/[a-zA-Z0-9_-]+/voice_lock', normalized_path):
                    is_allowed_mutation = True
                elif re.fullmatch(r'/api/projects/[a-zA-Z0-9_-]+/pronunciation_dict', normalized_path):
                    is_allowed_mutation = True
                elif re.fullmatch(r'/api/projects/[a-zA-Z0-9_-]+/takes/[a-zA-Z0-9_-]+/accept', normalized_path):
                    is_allowed_mutation = True
                elif re.fullmatch(r'/api/projects/[a-zA-Z0-9_-]+/jobs/[a-zA-Z0-9_-]+/apply', normalized_path):
                    is_allowed_mutation = True
                elif re.fullmatch(r'/api/projects/[a-zA-Z0-9_-]+/apply-job/[a-zA-Z0-9_-]+', normalized_path):
                    is_allowed_mutation = True
            elif request.method == 'PUT':
                if re.fullmatch(r'/api/projects/[a-zA-Z0-9_-]+', normalized_path):
                    is_allowed_mutation = True
                elif re.fullmatch(r'/api/projects/[a-zA-Z0-9_-]+/draft', normalized_path):
                    is_allowed_mutation = True
            elif request.method == 'DELETE':
                if re.fullmatch(r'/api/projects/[a-zA-Z0-9_-]+/draft', normalized_path):
                    is_allowed_mutation = True
                elif re.fullmatch(r'/api/projects/[a-zA-Z0-9_-]+', normalized_path):
                    is_allowed_mutation = True

            if not is_allowed_mutation:
                return JsonResponse({'detail': 'Endpoint does not accept this mutation'}, status=405)

            # Enforce request size limit
            # Long YouTube projects (up to 400 scenes with captions) exceed 1 MB of JSON.
            if normalized_path == '/api/assets':
                limit = 220 * 1024 * 1024
            elif normalized_path.startswith('/api/projects'):
                limit = 8 * 1024 * 1024
            else:
                limit = 1024 * 1024
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
        # With no-referrer, browsers send "Origin: null" for the sign-in form, which the origin check
        # above (rightly) refuses; same-origin keeps the real origin and still sends nothing elsewhere.
        response['Referrer-Policy'] = 'same-origin' if request.path in SIGN_IN_PATHS else 'no-referrer'
        response['X-Frame-Options'] = 'DENY'
        if request.path.startswith('/api/'):
            response['Cache-Control'] = 'no-store'
        return response
