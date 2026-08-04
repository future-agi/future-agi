"""MCP Server rate limiting using Redis-backed Django cache sliding window."""

import datetime
import time

import structlog
from django.core.cache import cache

from mcp_server.constants import RATE_LIMITS
from mcp_server.exceptions import RateLimitExceededError

logger = structlog.get_logger(__name__)

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


def _day_bucket(now_dt: datetime.datetime) -> str:
    """UTC calendar day the request belongs to, as ``YYYY-MM-DD``."""
    return now_dt.strftime("%Y-%m-%d")


def _next_utc_midnight(now_dt: datetime.datetime) -> datetime.datetime:
    """Start of the next UTC day — the instant the daily quota resets."""
    return (now_dt + datetime.timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def _day_key(organization_id: str, now_dt: datetime.datetime) -> str:
    """Cache key for an org's daily counter, stamped with the UTC calendar day.

    Stamping the day into the key is what makes the quota reset at midnight.
    The previous implementation used a single un-stamped key refreshed with
    ``cache.set(..., timeout=86400)`` on every call, which re-armed the TTL each
    time — so the counter only expired after 24 consecutive hours of *zero*
    traffic, and an org with steady usage stayed locked out permanently once it
    hit the limit. With the day in the key, yesterday's counter is simply never
    read again and expires on its own.
    """
    return f"mcp_rl:day:{organization_id}:{_day_bucket(now_dt)}"


def _increment_day_counter(day_key: str, ttl: int) -> None:
    """Increment the daily counter, creating it if this is the day's first call.

    ``cache.incr`` raises ``ValueError`` when the key is absent, so the first
    call of each UTC day falls back to ``cache.add``. ``add`` is a no-op when the
    key already exists, which is what makes the fallback safe: if another worker
    created the key between our ``incr`` and our ``add``, ``add`` returns False
    and we increment the value they created instead of overwriting it.
    """
    try:
        cache.incr(day_key)
    except ValueError:
        if not cache.add(day_key, 1, timeout=ttl):
            cache.incr(day_key)


def check_rate_limit(organization_id: str, tier: str) -> None:
    """Check sliding window rate limits. Raises RateLimitExceededError if exceeded."""
    limits = RATE_LIMITS.get(tier, RATE_LIMITS["free"])
    now = time.time()
    now_dt = datetime.datetime.now(datetime.timezone.utc)

    # Per-minute check (sliding window of timestamps)
    minute_key = f"mcp_rl:min:{organization_id}"
    minute_window = cache.get(minute_key, []) or []
    cutoff = now - 60
    minute_window = [ts for ts in minute_window if ts > cutoff]

    if len(minute_window) >= limits["per_minute"]:
        oldest = min(minute_window) if minute_window else now
        retry_after = int(60 - (now - oldest)) + 1
        raise RateLimitExceededError(
            f"Rate limit exceeded: {limits['per_minute']} calls/minute",
            retry_after=max(retry_after, 1),
        )

    # Per-day check (counter keyed by UTC calendar day, expiring at midnight)
    day_key = _day_key(organization_id, now_dt)
    day_count = cache.get(day_key, 0) or 0

    midnight = _next_utc_midnight(now_dt)
    seconds_to_midnight = int((midnight - now_dt).total_seconds())

    if day_count >= limits["per_day"]:
        raise RateLimitExceededError(
            f"Rate limit exceeded: {limits['per_day']} calls/day",
            retry_after=seconds_to_midnight,
        )

    # Record this call
    minute_window.append(now)
    cache.set(minute_key, minute_window, timeout=120)
    # +60s of slack so the key outlives the boundary it is bounded by; once the
    # clock rolls over, _day_key() points at a new key anyway, so a briefly
    # lingering counter for yesterday is never consulted again.
    _increment_day_counter(day_key, seconds_to_midnight + 60)
