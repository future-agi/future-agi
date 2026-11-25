"""MCP Server rate limiting using a Redis-backed sliding window.

Counting has to be atomic: a stateless transport serves several calls from one
organization concurrently, and a read-modify-write over the cache lets them
overwrite each other's counts and sail past the limit. Redis does the whole
decision in one script. A process-locked fallback exists only when explicitly
enabled by test settings; production fails closed if Redis cannot decide.
"""

import datetime
import threading
import time
import uuid

import structlog
from django.conf import settings
from django.core.cache import cache
from django_redis.exceptions import ConnectionInterrupted
from redis.exceptions import RedisError

from mcp_server.constants import RATE_LIMITS
from mcp_server.exceptions import (
    RateLimitBackendUnavailableError,
    RateLimitExceededError,
)

logger = structlog.get_logger(__name__)

# Prune the window, decide, and record in one round trip so no two callers can
# interleave between reading a count and writing it back.
#
# Returns {verdict, detail}: 0 = per-minute exceeded (detail is the oldest
# timestamp still in the window), 1 = per-day exceeded, 2 = allowed.
_SLIDING_WINDOW_LUA = """
local minute_key, day_key = KEYS[1], KEYS[2]
local now        = tonumber(ARGV[1])
local per_minute = tonumber(ARGV[2])
local per_day    = tonumber(ARGV[3])
local day_ttl    = tonumber(ARGV[4])
local member     = ARGV[5]

redis.call('ZREMRANGEBYSCORE', minute_key, '-inf', now - 60)
if redis.call('ZCARD', minute_key) >= per_minute then
  local oldest = redis.call('ZRANGE', minute_key, 0, 0, 'WITHSCORES')
  return {0, tostring(oldest[2] or now)}
end

if tonumber(redis.call('GET', day_key) or '0') >= per_day then
  return {1, '0'}
end

redis.call('ZADD', minute_key, now, member)
redis.call('EXPIRE', minute_key, 120)
if redis.call('INCR', day_key) == 1 then
  redis.call('EXPIRE', day_key, day_ttl)
end
return {2, '0'}
"""

# One-key variant used by endpoint-specific throttles. It keeps the same
# atomic Redis decision as the organization limiter without a daily counter.
_SINGLE_WINDOW_LUA = """
local window_key = KEYS[1]
local now        = tonumber(ARGV[1])
local limit      = tonumber(ARGV[2])
local duration   = tonumber(ARGV[3])
local member     = ARGV[4]

redis.call('ZREMRANGEBYSCORE', window_key, '-inf', now - duration)
if redis.call('ZCARD', window_key) >= limit then
  local oldest = redis.call('ZRANGE', window_key, 0, 0, 'WITHSCORES')
  return {0, tostring(oldest[2] or now)}
end

redis.call('ZADD', window_key, now, member)
redis.call('EXPIRE', window_key, math.max(duration * 2, 1))
return {1, '0'}
"""

_MINUTE_EXCEEDED, _DAY_EXCEEDED, _ALLOWED = 0, 1, 2

# Used only by the explicitly enabled test fallback. Production uses Redis Lua.
_fallback_lock = threading.Lock()
_scripts: dict[str, object] = {}


def _local_fallback_enabled() -> bool:
    return bool(getattr(settings, "MCP_RATE_LIMIT_ALLOW_LOCAL_FALLBACK", False))


def _fallback_or_raise(reason: str, cause: BaseException | None = None) -> None:
    if _local_fallback_enabled():
        logger.warning(
            "mcp_rate_limit_redis_error_falling_back_to_locked_window",
            reason=reason,
            exc_info=bool(cause),
        )
        return

    logger.error(
        "mcp_rate_limit_backend_unavailable",
        reason=reason,
        exc_info=bool(cause),
    )
    error = RateLimitBackendUnavailableError()
    if cause is not None:
        raise error from cause
    raise error


def _redis_script(lua: str):
    """Return the registered Lua script, or None when the cache is not Redis."""
    script = _scripts.get(lua)
    if script is None:
        try:
            from django.core.cache.backends.base import InvalidCacheBackendError
            from django_redis import get_redis_connection
        except ImportError as exc:
            _fallback_or_raise("redis_client_import_failed", exc)
            return None

        try:
            script = get_redis_connection("default").register_script(lua)
        except (
            InvalidCacheBackendError,
            NotImplementedError,
            AttributeError,
            ConnectionInterrupted,
            RedisError,
        ) as exc:
            _fallback_or_raise("redis_script_registration_failed", exc)
            return None
        _scripts[lua] = script
    return script


# Maps subscription tier names to rate limit tier keys
TIER_MAPPING = {
    "free": "free",
    "basic": "pro",
    "basic_yearly": "pro",
    "custom": "enterprise",
}


def get_rate_limit_tier(organization) -> str:
    """Determine rate limit tier from organization's subscription.

    When ee is absent, there is no subscription model — fall back to
    the free tier so MCP requests continue to work.
    """
    try:
        from ee.usage.models.usage import OrganizationSubscription
    except ImportError:
        return "free"

    try:
        sub = OrganizationSubscription.objects.select_related("subscription_tier").get(
            organization=organization
        )
        tier_name = sub.subscription_tier.name
        return TIER_MAPPING.get(tier_name, "free")
    except OrganizationSubscription.DoesNotExist:
        return "free"


def _seconds_until_utc_midnight(now_dt: datetime.datetime) -> int:
    midnight = (now_dt + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(int((midnight - now_dt).total_seconds()), 1)


def _prune_window(
    window_key: str,
    duration: int,
    now: float,
    cache_backend=None,
) -> list[float]:
    backend = cache if cache_backend is None else cache_backend
    return [ts for ts in (backend.get(window_key) or []) if ts > now - duration]


def _record_window(
    window_key: str,
    timestamps: list[float],
    duration: int,
    now: float,
    cache_backend=None,
) -> None:
    backend = cache if cache_backend is None else cache_backend
    timestamps.append(now)
    backend.set(window_key, timestamps, timeout=max(duration * 2, 1))


def _check_atomic(minute_key, day_key, limits, now, day_ttl):
    """Decide and record in Redis. Returns (verdict, detail) or None if absent."""
    try:
        script = _redis_script(_SLIDING_WINDOW_LUA)
        if script is None:
            return None
        verdict, detail = script(
            keys=[minute_key, day_key],
            args=[
                now,
                limits["per_minute"],
                limits["per_day"],
                day_ttl,
                uuid.uuid4().hex,
            ],
        )
        return int(verdict), float(detail or 0)
    except (ConnectionInterrupted, RedisError) as exc:
        _fallback_or_raise("redis_script_execution_failed", exc)
        return None


def _check_locked(minute_key, day_key, limits, now, day_ttl):
    """Read-modify-write fallback, serialized within this process."""
    with _fallback_lock:
        minute_window = _prune_window(minute_key, 60, now)
        if len(minute_window) >= limits["per_minute"]:
            return _MINUTE_EXCEEDED, min(minute_window)

        day_count = cache.get(day_key, 0) or 0
        if day_count >= limits["per_day"]:
            return _DAY_EXCEEDED, 0.0

        _record_window(minute_key, minute_window, 60, now)
        if day_count:
            # Preserve the remaining day TTL instead of extending the window
            # on every call, which would stop the counter ever resetting.
            try:
                cache.incr(day_key)
            except ValueError:
                # Expired between the read and the increment.
                cache.set(day_key, 1, timeout=day_ttl)
        else:
            cache.set(day_key, 1, timeout=day_ttl)
        return _ALLOWED, 0.0


def _check_single_locked(
    window_key: str,
    limit: int,
    duration: int,
    now: float,
    cache_backend=None,
) -> tuple[bool, float]:
    """Check one sliding window with the serialized non-Redis fallback."""
    with _fallback_lock:
        timestamps = _prune_window(window_key, duration, now, cache_backend)
        if len(timestamps) >= limit:
            return False, min(timestamps)

        _record_window(window_key, timestamps, duration, now, cache_backend)
        return True, 0.0


def check_sliding_window(
    window_key: str,
    limit: int,
    duration: int,
    now: float | None = None,
) -> tuple[bool, float]:
    """Atomically admit one request in a bounded sliding window.

    Redis uses a Lua script so concurrent workers cannot overwrite one
    another's request history. Non-Redis caches use the same process lock only
    under the explicit test setting; production deployments require shared
    Redis.
    Returns ``(allowed, oldest_timestamp)``.
    """
    resolved_now = time.time() if now is None else now
    try:
        script = _redis_script(_SINGLE_WINDOW_LUA)
        if script is not None:
            allowed, oldest = script(
                keys=[window_key],
                args=[resolved_now, limit, duration, uuid.uuid4().hex],
            )
            return bool(int(allowed)), float(oldest or 0)
    except (ConnectionInterrupted, RedisError) as exc:
        _fallback_or_raise("redis_script_execution_failed", exc)
    return _check_single_locked(window_key, limit, duration, resolved_now)


def check_rate_limit(organization_id: str, tier: str) -> None:
    """Check sliding window rate limits. Raises RateLimitExceededError if exceeded."""
    limits = RATE_LIMITS.get(tier, RATE_LIMITS["free"])
    now = time.time()
    now_dt = datetime.datetime.now(datetime.UTC)
    day_ttl = _seconds_until_utc_midnight(now_dt)
    minute_key = f"mcp_rl:min:{organization_id}"
    day_key = f"mcp_rl:day:{organization_id}"

    outcome = _check_atomic(minute_key, day_key, limits, now, day_ttl)
    if outcome is None:
        outcome = _check_locked(minute_key, day_key, limits, now, day_ttl)
    verdict, detail = outcome

    if verdict == _MINUTE_EXCEEDED:
        raise RateLimitExceededError(
            f"Rate limit exceeded: {limits['per_minute']} calls/minute",
            retry_after=max(int(60 - (now - (detail or now))) + 1, 1),
        )
    if verdict == _DAY_EXCEEDED:
        raise RateLimitExceededError(
            f"Rate limit exceeded: {limits['per_day']} calls/day",
            # The counter expires at midnight, so this is when it really resets.
            retry_after=day_ttl,
        )
