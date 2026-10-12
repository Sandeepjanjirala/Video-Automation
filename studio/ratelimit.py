"""Distributed rate limiting and provider budget controls for Telugu Reel Studio (Phase 3)."""
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import redis
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from .config import settings
from .db.session import session_scope
from .db.models import Usage

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

    with session_scope() as session:
        stmt = (
            pg_insert(Usage)
            .values(
                day=day,
                provider=provider,
                calls=1,
                estimated_billable_units=estimated_units,
                actual_billable_units=actual_units,
                uncertain_calls=1 if uncertain else 0,
                last_call_at=now_dt
            )
            .on_conflict_do_update(
                index_elements=['day', 'provider'],
                set_={
                    'calls': Usage.calls + 1,
                    'estimated_billable_units': Usage.estimated_billable_units + estimated_units,
                    'actual_billable_units': Usage.actual_billable_units + actual_units,
                    'uncertain_calls': Usage.uncertain_calls + (1 if uncertain else 0),
                    'last_call_at': now_dt
                }
            )
        )
        session.execute(stmt)
        session.flush()

        usage_row = session.get(Usage, (day, provider))
        return {
            'day': usage_row.day,
            'provider': usage_row.provider,
            'calls': usage_row.calls,
            'estimated_billable_units': usage_row.estimated_billable_units,
            'actual_billable_units': usage_row.actual_billable_units,
            'uncertain_calls': usage_row.uncertain_calls,
            'last_call_at': usage_row.last_call_at.isoformat() if usage_row.last_call_at else None
        }


def get_usage_summary(provider: Optional[str] = None) -> list:
    """Retrieve usage stats across providers for today."""
    day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    with session_scope() as session:
        stmt = select(Usage).where(Usage.day == day)
        if provider:
            stmt = stmt.where(Usage.provider == provider)
        rows = session.execute(stmt).scalars().all()
        return [{
            'day': r.day,
            'provider': r.provider,
            'calls': r.calls,
            'estimated_billable_units': r.estimated_billable_units,
            'actual_billable_units': r.actual_billable_units,
            'uncertain_calls': r.uncertain_calls,
            'last_call_at': r.last_call_at.isoformat() if r.last_call_at else None
        } for r in rows]
