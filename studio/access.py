"""Password access for using the studio from a phone, a tablet or another computer.

Requests from the studio PC itself (loopback, not through a proxy) need no password. Every other
device signs in once with the access password (Settings, or STUDIO_PASSWORD on a server) and gets
a signed cookie valid for 30 days. With no password set, other devices are refused.
"""
import hashlib
import hmac
import os
import secrets
import threading
import time

from django.core import signing

COOKIE = 'studio_access'
MAX_AGE = 30 * 86400
ITERATIONS = 240_000
MIN_LENGTH = 8
# Wrong passwords per client address before a pause (a phone keyboard typo is fine; guessing is not).
MAX_FAILURES, FAILURE_WINDOW = 10, 15 * 60
_failures = {}
_lock = threading.Lock()


def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), ITERATIONS).hex()
    return f'pbkdf2_sha256${ITERATIONS}${salt}${digest}'


def _matches(password, stored):
    try:
        _, iterations, salt, digest = stored.split('$')
        check = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), int(iterations)).hex()
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(check, digest)


def _stored():
    """('env', password) when STUDIO_PASSWORD is set, ('settings', hash) from Settings, or None."""
    env = os.environ.get('STUDIO_PASSWORD', '')
    if env:
        return 'env', env
    from .config import settings
    stored = settings().get('access_password_hash') or ''
    return ('settings', stored) if stored else None


def password_set():
    return _stored() is not None


def verify(password):
    stored = _stored()
    if not stored or not isinstance(password, str) or not password:
        return False
    kind, value = stored
    return hmac.compare_digest(password.encode(), value.encode()) if kind == 'env' else _matches(password, value)


def _fingerprint():
    """Changes whenever the password changes, so old sign-ins stop working."""
    stored = _stored()
    return hashlib.sha256(f'{stored[0]}:{stored[1]}'.encode()).hexdigest()[:16] if stored else ''


def is_local(request):
    """The studio PC itself: a loopback connection that did not come through a proxy."""
    forwarded = any(request.META.get(h) for h in ('HTTP_X_FORWARDED_FOR', 'HTTP_FORWARDED', 'HTTP_X_REAL_IP'))
    return request.META.get('REMOTE_ADDR') in ('127.0.0.1', '::1') and not forwarded


def signed_in(request):
    token = request.COOKIES.get(COOKIE)
    if not token or not password_set():
        return False
    try:
        data = signing.loads(token, salt='studio-access', max_age=MAX_AGE)
    except signing.BadSignature:
        return False
    return isinstance(data, dict) and hmac.compare_digest(str(data.get('p', '')), _fingerprint())


def allowed(request):
    return is_local(request) or signed_in(request)


def sign_in(response, request):
    token = signing.dumps({'p': _fingerprint(), 't': int(time.time())}, salt='studio-access')
    response.set_cookie(COOKIE, token, max_age=MAX_AGE, httponly=True, samesite='Lax', secure=request.is_secure())
    return response


def sign_out(response):
    response.delete_cookie(COOKIE, samesite='Lax')
    return response


def client(request):
    return request.META.get('REMOTE_ADDR', '')


def locked_out(request):
    now = time.time()
    with _lock:
        recent = [t for t in _failures.get(client(request), []) if now - t < FAILURE_WINDOW]
        _failures[client(request)] = recent
        return len(recent) >= MAX_FAILURES


def record_failure(request):
    with _lock:
        _failures.setdefault(client(request), []).append(time.time())


def clear_failures(request):
    with _lock:
        _failures.pop(client(request), None)
