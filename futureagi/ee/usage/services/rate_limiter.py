"""Plan-aware rate limiter using Redis sliding window.

Enforces API req/min and ingestion events/min limits based on the org's plan.
Reads limits from PlanEntitlement (via Entitlements service).

Usage:
    from ee.usage.services.rate_limiter import RateLimiter

    result = RateLimiter.check("org-123", "api")
    if not result.allowed:
        # Return 429 with Retry-After header
        return Response({"error": result.reason}, status=429,
                       headers={"Retry-After": str(result.retry_after)})
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import structlog

from ee.usage.services.emitter import get_redis
from ee.usage.services.entitlements import Entitlements

logger = structlog.get_logger(__name__)

# Rate limit types → PlanEntitlement feature keys
RATE_LIMIT_FEATURES = {
    "api": "api_rate_rpm",
    "ingestion": "ingestion_rate_epm",
}


@dataclass(frozen=True)
class RateLimitResult:
    """Result of a rate limit check."""

    allowed: bool
    reason: str = ""
    current: int = 0
    limit: int = 0
    retry_after: int = 60  # seconds


class RateLimiter:
    """Redis sliding window rate limiter."""

    @staticmethod
    def check(org_id: str, limit_type: str = "api") -> RateLimitResult:
        """Check if this request is within rate limits.

        Args:
            org_id: Organization ID.
            limit_type: "api" (req/min) or "ingestion" (events/min).

        Returns:
            RateLimitResult with allowed=True/False.
        """
        feature = RATE_LIMIT_FEATURES.get(limit_type)
        if not feature:
            return RateLimitResult(allowed=True)

        # Get limit from entitlements
        limit = Entitlements.get_limit(org_id, feature)
        if limit == -1 or limit == 0:
            return RateLimitResult(allowed=True)  # unlimited or not configured

        # Sliding window: count requests in the current minute
        r = get_redis()
        now = datetime.utcnow()
        window_key = f"rl:{org_id}:{limit_type}:{now.strftime('%Y%m%d%H%M')}"

        try:
            current = r.incr(window_key)
            if current == 1:
                r.expire(window_key, 120)  # 2 min TTL (covers window + buffer)

            if current > limit:
                # Over limit — calculate retry_after
                seconds_remaining = 60 - now.second
                logger.info(
                    "rate_limited",
                    org_id=org_id,
                    limit_type=limit_type,
                    current=current,
                    limit=limit,
                )
                return RateLimitResult(
                    allowed=False,
                    reason=f"Rate limit exceeded: {limit} {limit_type} requests per minute",
                    current=current,
                    limit=limit,
                    retry_after=max(1, seconds_remaining),
                )

            return RateLimitResult(allowed=True, current=current, limit=limit)

        except Exception:
            logger.exception("rate_limit_check_failed", org_id=org_id)
            return RateLimitResult(allowed=True)  # fail open
