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


def check_rate_limit(organization_id: str, tier: str) -> None:
    """Check sliding window rate limits. Raises RateLimitExceededError if exceeded."""
    limits = RATE_LIMITS.get(tier, RATE_LIMITS["free"])
    now = time.time()

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

    # Per-day check (simple counter with 24h TTL)
    day_key = f"mcp_rl:day:{organization_id}"
    day_count = cache.get(day_key, 0) or 0

    if day_count >= limits["per_day"]:
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        midnight = (now_dt + datetime.timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        retry_after = int((midnight - now_dt).total_seconds())
        raise RateLimitExceededError(
            f"Rate limit exceeded: {limits['per_day']} calls/day",
            retry_after=retry_after,
        )

    # Record this call
    minute_window.append(now)
    cache.set(minute_key, minute_window, timeout=120)
    cache.set(day_key, day_count + 1, timeout=86400)
