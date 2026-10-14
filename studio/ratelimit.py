"""Distributed rate limiting and provider budget controls for Telugu Reel Studio (Phase 3/M9)."""
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import redis
from django.db import connection, connections
from django.conf import settings as django_settings
from .config import settings

def _get_db_conn():
    if 'admin' in django_settings.DATABASES:
        return connections['admin']
    elif 'job_writer' in django_settings.DATABASES:
        return connections['job_writer']
    return connection


DEFAULT_REDIS_CACHE_URL = 'redis://127.0.0.1:6379/2'

# Conservative default per-minute limits per provider
PROVIDER_RATE_LIMITS = {
    'gemini': 15,    # 15 requests/minute
    'pixabay': 50,   # 50 requests/minute
    'azure': 20,     # 20 requests/minute
    'edge_tts': 30,  # 30 requests/minute
    'sarvam': 30,    # 30 requests/minute
}

_redis_client: Optional[redis.Redis] = None


def get_redis_client() -> Optional[redis.Redis]:
    """Get or create singleton Redis client for caching and rate limiting (DB 2)."""
    global _redis_client
    if _redis_client is None:
        url = os.environ.get('STUDIO_CACHE_REDIS_URL') or DEFAULT_REDIS_CACHE_URL
        try:
            client = redis.Redis.from_url(url, socket_timeout=2.0, decode_responses=True)
            client.ping()
            _redis_client = client
        except Exception:
            _redis_client = None
    return _redis_client


def acquire_rate_slot(provider: str, wait_timeout: float = 5.0) -> bool:
    """Token bucket / sliding window rate limiter across distributed workers via Redis."""
    limit = PROVIDER_RATE_LIMITS.get(provider, 30)
    r = get_redis_client()
    if r is None:
        # Fall back gracefully if Redis is temporarily unreachable
        return True

    key = f'ratelimit:{provider}'
    now = time.time()
    window = 60.0  # 1 minute sliding window
    deadline = now + wait_timeout

    while time.time() <= deadline:
        try:
            p = r.pipeline()
            # Remove timestamps older than window
            p.zremrangebyscore(key, 0, now - window)
            # Count remaining requests in current window
            p.zcard(key)
            # Execute pipeline
            _, count = p.execute()

            if count < limit:
                # Add current request timestamp
                r.zadd(key, {f"{now}:{time.time_ns()}": now})
                r.expire(key, int(window) * 2)
                return True
            else:
                # Sleep briefly with slight jitter before retrying
                time.sleep(0.5 + (0.1 * (time.time() % 3)))
                now = time.time()
        except Exception:
            # On Redis error, do not block workers indefinitely
            return True
    return False


def record_usage(
    provider: str,
    estimated_units: int = 0,
    actual_units: int = 0,
    uncertain: bool = False
) -> Dict[str, Any]:
    """Record request counts, estimated/actual billable usage, and provider uncertainty with atomic upsert."""
    day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    now_dt = datetime.now(timezone.utc)

    query = """
    INSERT INTO usage (day, provider, calls, estimated_billable_units, actual_billable_units, uncertain_calls, last_call_at)
    VALUES (%s, %s, 1, %s, %s, %s, %s)
    ON CONFLICT (day, provider) DO UPDATE SET
        calls = usage.calls + 1,
        estimated_billable_units = usage.estimated_billable_units + EXCLUDED.estimated_billable_units,
        actual_billable_units = usage.actual_billable_units + EXCLUDED.actual_billable_units,
        uncertain_calls = usage.uncertain_calls + EXCLUDED.uncertain_calls,
        last_call_at = EXCLUDED.last_call_at
    RETURNING day, provider, calls, estimated_billable_units, actual_billable_units, uncertain_calls, last_call_at;
    """
    with _get_db_conn().cursor() as cursor:
        cursor.execute(query, (day, provider, estimated_units, actual_units, 1 if uncertain else 0, now_dt))
        row = cursor.fetchone()
        return {
            'day': row[0],
            'provider': row[1],
            'calls': row[2],
            'estimated_billable_units': row[3],
            'actual_billable_units': row[4],
            'uncertain_calls': row[5],
            'last_call_at': row[6].isoformat() if row[6] else None
        }


def get_usage_summary(provider: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve usage stats across providers for today."""
    day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    query = """
    SELECT day, provider, calls, estimated_billable_units, actual_billable_units, uncertain_calls, last_call_at
    FROM usage
    WHERE day = %s
    """
    params: List[Any] = [day]
    if provider:
        query += " AND provider = %s"
        params.append(provider)

    with _get_db_conn().cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [{
            'day': r[0],
            'provider': r[1],
            'calls': r[2],
            'estimated_billable_units': r[3],
            'actual_billable_units': r[4],
            'uncertain_calls': r[5],
            'last_call_at': r[6].isoformat() if r[6] else None
        } for r in rows]
