"""MCP Server rate limiting using a Redis-backed sliding window.

Counting has to be atomic: a stateless transport serves several calls from one
organization concurrently, and a read-modify-write over the cache lets them
overwrite each other's counts and sail past the limit. Redis does the whole
decision in one script; when the cache is not Redis (locmem deployments and
tests) a process lock keeps at least a single worker's calls serialized.
"""

import datetime
import threading
import time
import uuid

import structlog
from django.core.cache import cache

from mcp_server.constants import RATE_LIMITS
from mcp_server.exceptions import RateLimitExceededError

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

_MINUTE_EXCEEDED, _DAY_EXCEEDED, _ALLOWED = 0, 1, 2

# Serializes the non-Redis fallback within one process. Only ever contended on
# locmem deployments; the Redis path never takes it.
_fallback_lock = threading.Lock()
_script = None
_script_unavailable = False


def _sliding_window_script():
    """Return the registered Lua script, or None when the cache is not Redis."""
    global _script, _script_unavailable
    if _script_unavailable:
        return None
    if _script is None:
        from django_redis import get_redis_connection

        try:
            _script = get_redis_connection("default").register_script(
                _SLIDING_WINDOW_LUA
            )
        except Exception:
            # A locmem cache has no Redis client at all. Remember that so the
            # fallback does not pay for a failed lookup on every call.
            _script_unavailable = True
            logger.info("mcp_rate_limit_no_redis_falling_back_to_locked_window")
            return None
    return _script

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


def _check_atomic(minute_key, day_key, limits, now, day_ttl):
    """Decide and record in Redis. Returns (verdict, detail) or None if absent."""
    script = _sliding_window_script()
    if script is None:
        return None
    verdict, detail = script(
        keys=[minute_key, day_key],
        args=[now, limits["per_minute"], limits["per_day"], day_ttl, uuid.uuid4().hex],
    )
    return int(verdict), float(detail or 0)


def _check_locked(minute_key, day_key, limits, now, day_ttl):
    """Read-modify-write fallback, serialized within this process."""
    with _fallback_lock:
        minute_window = [ts for ts in (cache.get(minute_key) or []) if ts > now - 60]
        if len(minute_window) >= limits["per_minute"]:
            return _MINUTE_EXCEEDED, min(minute_window)

        day_count = cache.get(day_key, 0) or 0
        if day_count >= limits["per_day"]:
            return _DAY_EXCEEDED, 0.0

        minute_window.append(now)
        cache.set(minute_key, minute_window, timeout=120)
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


def check_rate_limit(organization_id: str, tier: str) -> None:
    """Check sliding window rate limits. Raises RateLimitExceededError if exceeded."""
    limits = RATE_LIMITS.get(tier, RATE_LIMITS["free"])
    now = time.time()
    now_dt = datetime.datetime.now(datetime.timezone.utc)
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
