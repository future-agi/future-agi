"""Usage metering — pre-check before billable actions.

Usage:
    from ee.usage.services.metering import check_usage

    check = check_usage(str(org.id), "turing_large_evaluator")
    if not check.allowed:
        return Response({"error": check.reason}, status=403)

Synchronous. Redis-only (no DB queries). < 2ms.
"""

from __future__ import annotations

import os
from datetime import datetime

import structlog
from ee.usage.schemas.events import CheckResult, UpgradeCTA
from ee.usage.services.config import BillingConfig
from ee.usage.services.emitter import get_redis
from redis.exceptions import RedisError

logger = structlog.get_logger(__name__)

_lua_sha: str | None = None


def _get_lua_sha() -> str:
    """Load and cache the Lua check script SHA."""
    global _lua_sha
    if _lua_sha is None:
        script_path = os.path.join(
            os.path.dirname(__file__), "..", "lua", "check_quota.lua"
        )
        with open(script_path) as f:
            _lua_sha = get_redis().script_load(f.read())
    return _lua_sha


def _get_current_period() -> str:
    """Get current billing period as YYYY-MM string."""
    return datetime.utcnow().strftime("%Y-%m")


def _get_cached_plan(org_id: str) -> str:
    """Get org's plan from Redis cache, or fetch from DB and cache (5 min TTL)."""
    cache_key = f"plan:{org_id}"
    try:
        r = get_redis()
        cached = r.get(cache_key)
        if cached:
            return cached if isinstance(cached, str) else cached.decode()
    except RedisError:
        logger.warning("plan_cache_read_failed", org_id=org_id)
        r = None

    # Fetch from DB (one query, only on cache miss)
    from ee.usage.models.usage import OrganizationSubscription

    plan = (
        OrganizationSubscription.objects.filter(organization_id=org_id, deleted=False)
        .values_list("plan", flat=True)
        .first()
    )
    plan = plan or "free"
    if r is not None:
        try:
            r.setex(cache_key, 300, plan)  # 5 min TTL
        except RedisError:
            logger.warning("plan_cache_write_failed", org_id=org_id)
    return plan


def _get_cached_billing_status(org_id: str) -> str:
    """Get org's billing status from Redis cache, or fetch from DB and cache (5 min TTL)."""
    cache_key = f"billing_status:{org_id}"
    try:
        r = get_redis()
        cached = r.get(cache_key)
        if cached:
            return cached if isinstance(cached, str) else cached.decode()
    except RedisError:
        logger.warning("billing_status_cache_read_failed", org_id=org_id)
        r = None

    from ee.usage.models.usage import (
        OrganizationStatusChoices,
        OrganizationSubscription,
    )

    status = (
        OrganizationSubscription.objects.filter(organization_id=org_id, deleted=False)
        .values_list("status", flat=True)
        .first()
    )
    status = status or OrganizationStatusChoices.ACTIVE
    if r is not None:
        try:
            r.setex(cache_key, 300, status)
        except RedisError:
            logger.warning("billing_status_cache_write_failed", org_id=org_id)
    return status


def check_usage(org_id: str, event_type: str, amount: float = 0) -> CheckResult:
    """Pre-check: can this org perform this billable action?

    Synchronous. Redis-only. < 2ms.

    Args:
        org_id: Organization ID.
        event_type: API call type key from billing.yaml.
        amount: Override amount. If 0, uses per_call from config.

    Returns:
        CheckResult with allowed=True/False and details.
    """
    config = BillingConfig.get()

    # Resolve event_type → dimension + amount
    try:
        call_type_config = config.get_call_type(event_type)
    except KeyError:
        logger.warning(
            "check_usage_unknown_event_type", event_type=event_type, org_id=org_id
        )
        return CheckResult(
            allowed=True
        )  # Unknown type — don't block, log for investigation

    dimension = call_type_config.dimension

    # Stripe exhausted its retry window (grace period) without payment.
    # past_due stays soft — the grace period itself never blocks.
    from ee.usage.models.usage import OrganizationStatusChoices

    if _get_cached_billing_status(org_id) == OrganizationStatusChoices.UNPAID:
        return CheckResult(
            allowed=False,
            error_code="PAYMENT_REQUIRED",
            reason=(
                "Your subscription has unpaid invoices. "
                "Update your payment method to restore access."
            ),
            dimension=dimension,
        )

    # Resolve amount from config if not explicitly provided
    resolved_amount = amount
    if resolved_amount == 0:
        if call_type_config.per_call is not None:
            resolved_amount = call_type_config.per_call
        else:
            resolved_amount = 1

    # Get plan
    plan = _get_cached_plan(org_id)
    plan_config = config.get_plan(plan)
    period = _get_current_period()

    # Check free tier hard cap
    if plan_config.usage_caps == "hard":
        dim_config = config.get_dimension(dimension)
        free_allowance = config.get_free_allowance(dimension, plan)
        free_allowance_native = float(
            free_allowance * dim_config.native_to_display_divisor
        )

        if free_allowance_native > 0:
            usage_key = f"usage:{org_id}:{dimension}:{period}"
            result = get_redis().evalsha(
                _get_lua_sha(),
                1,
                usage_key,
                str(free_allowance_native),
                str(resolved_amount),
            )
            current = float(result)

            if current == -1:
                # Over limit
                actual_current = float(get_redis().get(usage_key) or 0)
                return CheckResult(
                    allowed=False,
                    error_code="FREE_TIER_LIMIT",
                    reason=(
                        f"Free tier {dim_config.display_name} limit reached "
                        f"({actual_current:,.2f} / {free_allowance_native:,.2f} {dim_config.native_unit})"
                    ),
                    dimension=dimension,
                    current_usage=actual_current,
                    limit=free_allowance_native,
                    upgrade_cta=UpgradeCTA(
                        text="Upgrade to Pay-as-you-go for unlimited usage",
                        plan="payg",
                    ),
                )

    # Check budget pause flag (set by budget enforcement in Phase 4)
    pause_key = f"pause:{org_id}:{dimension}"
    if get_redis().get(pause_key):
        return CheckResult(
            allowed=False,
            error_code="BUDGET_PAUSED",
            reason=f"Usage paused — you set a budget limit for {dimension}",
            dimension=dimension,
        )

    return CheckResult(allowed=True, dimension=dimension)


QUOTA_KEY_TTL = 3600


def publish_quota(org_id: str, dimension: str, limit_native: float) -> None:
    r = get_redis()
    key = f"quota:{org_id}:{dimension}"
    r.setex(key, QUOTA_KEY_TTL, str(limit_native))


def publish_quotas_for_org(org_id: str) -> None:
    config = BillingConfig.get()
    plan = _get_cached_plan(org_id)
    plan_config = config.get_plan(plan)

    for dim_key, dim_config in config.get_all_dimensions().items():
        if plan_config.usage_caps == "hard":
            free_allowance = config.get_free_allowance(dim_key, plan)
            limit_native = float(free_allowance * dim_config.native_to_display_divisor)
        else:
            limit_native = -1

        publish_quota(org_id, dim_key, limit_native)
