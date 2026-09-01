"""Entitlements service — check what an org can access based on plan.

Usage:
    from ee.usage.services.entitlements import Entitlements

    limit = Entitlements.get_limit("org-123", "monitors")  # 3, 15, or -1
    can = Entitlements.has_feature("org-123", "has_knowledge_base")  # True/False
    result = Entitlements.can_create("org-123", "monitors", current_count=3)

Three-level lookup: org override → plan default in DB → billing.yaml config.
Cached in Redis (5 min TTL).
"""

from __future__ import annotations

from typing import Any

import structlog
from ee.usage.deployment import DeploymentMode
from ee.usage.schemas.events import CheckResult, UpgradeCTA
from ee.usage.services.config import BillingConfig
from ee.usage.services.emitter import get_redis
from ee.usage.services.metering import _get_cached_plan
from redis.exceptions import RedisError

logger = structlog.get_logger(__name__)

CACHE_TTL = 300  # 5 min
CACHE_MISS = object()


def _cache_get(key: str) -> Any:
    """Best-effort Redis read.

    Entitlement checks are on the request path. Redis is only a cache here, so
    a cache outage should fall through to DB/config resolution instead of
    turning feature-gated product actions into 500s.
    """
    try:
        return get_redis().get(key)
    except RedisError:
        logger.warning("entitlement_cache_read_failed", cache_key=key)
        return CACHE_MISS


def _cache_setex(key: str, ttl: int, value: str) -> None:
    """Best-effort Redis write; entitlement source of truth is DB/config."""
    try:
        get_redis().setex(key, ttl, value)
    except RedisError:
        logger.warning("entitlement_cache_write_failed", cache_key=key)


RESOURCE_DISPLAY_NAMES = {
    "monitors": "monitors & alerts",
    "queues": "annotation queues",
    "gateway_email_alerts": "gateway email alerts",
    "gateway_webhooks": "gateway webhooks",
    "shadow_experiments": "shadow experiments",
    "automation_rules": "automation rules",
}


def _display_name(resource: str) -> str:
    return RESOURCE_DISPLAY_NAMES.get(resource, resource.replace("_", " "))


class Entitlements:
    """Static methods for entitlement checks. All cached in Redis."""

    @staticmethod
    def get_entitlement(org_id: str, feature: str) -> int | bool | None:
        """Get entitlement value for an org+feature. Three-level resolution.

        Returns int (for numeric), bool (for boolean), or None (not configured).
        """
        cache_key = f"ent:{org_id}:{feature}"

        # 1. Redis cache
        cached = _cache_get(cache_key)
        if cached is not CACHE_MISS and cached is not None:
            cached_str = cached if isinstance(cached, str) else cached.decode()
            if cached_str == "True":
                return True
            if cached_str == "False":
                return False
            if cached_str == "__none__":
                return None
            try:
                return int(cached_str)
            except (ValueError, TypeError):
                pass

        # 2. DB org override
        from ee.usage.models.usage import PlanEntitlement

        override = (
            PlanEntitlement.objects.filter(
                feature=feature, organization_id=org_id, deleted=False
            )
            .values("value_int", "value_bool")
            .first()
        )
        if override:
            val = (
                override["value_int"]
                if override["value_int"] is not None
                else override["value_bool"]
            )
            _cache_setex(
                cache_key, CACHE_TTL, str(val) if val is not None else "__none__"
            )
            return val

        # 3. DB plan default
        plan = _get_cached_plan(org_id)
        default = (
            PlanEntitlement.objects.filter(
                feature=feature,
                plan=plan,
                organization__isnull=True,
                deleted=False,
            )
            .values("value_int", "value_bool")
            .first()
        )
        if default:
            val = (
                default["value_int"]
                if default["value_int"] is not None
                else default["value_bool"]
            )
            _cache_setex(
                cache_key, CACHE_TTL, str(val) if val is not None else "__none__"
            )
            return val

        # 4. billing.yaml fallback
        config = BillingConfig.get()
        val = config.get_entitlement_default(feature, plan)
        _cache_setex(cache_key, CACHE_TTL, str(val) if val is not None else "__none__")
        return val

    @staticmethod
    def get_limit(org_id: str, feature: str) -> int:
        """Get numeric limit. Returns -1 for unlimited, 0 if not configured."""
        val = Entitlements.get_entitlement(org_id, feature)
        if isinstance(val, int):
            return val
        return 0

    @staticmethod
    def has_feature(org_id: str, feature: str) -> bool:
        """Check if a boolean feature is enabled."""
        val = Entitlements.get_entitlement(org_id, feature)
        if isinstance(val, bool):
            return val
        return False

    @staticmethod
    def can_create(org_id: str, resource: str, current_count: int) -> CheckResult:
        """Check if org can create another resource (monitor, queue, KB, etc.).

        Args:
            org_id: Organization ID.
            resource: Resource key matching PlanEntitlement feature (e.g., "monitors").
            current_count: How many the org currently has.

        Returns:
            CheckResult with allowed=True/False.
        """
        # Count limits are a cloud-plan concept: self-hosted is uncapped.
        if not DeploymentMode.is_cloud():
            return CheckResult(allowed=True)

        limit = Entitlements.get_limit(org_id, resource)

        if limit == -1:
            return CheckResult(allowed=True)

        if limit is None:
            # Unconfigured resource on cloud: quota is billing — fail closed
            # with a clear denial instead of a TypeError on the comparison.
            return CheckResult(
                allowed=False,
                error_code="ENTITLEMENT_DENIED",
                reason=(
                    f"{_display_name(resource).title()} is not configured "
                    "for your plan"
                ),
                upgrade_cta=_find_upgrade_cta(org_id, resource),
            )

        if limit == 0:
            # Feature not available on this plan
            return CheckResult(
                allowed=False,
                error_code="ENTITLEMENT_DENIED",
                reason=f"{_display_name(resource).title()} is not available on your plan",
                upgrade_cta=_find_upgrade_cta(org_id, resource),
            )

        if current_count >= limit:
            return CheckResult(
                allowed=False,
                error_code="ENTITLEMENT_LIMIT",
                reason=f"You've reached the {limit} {_display_name(resource)} limit",
                current_usage=current_count,
                limit=limit,
                upgrade_cta=_find_upgrade_cta(org_id, resource),
            )

        return CheckResult(allowed=True)

    @staticmethod
    def has_feature_unified(org_id: str, feature: str) -> bool:
        """Resolve a registered capability through the canonical service."""
        from tfc.capabilities import service
        from tfc.capabilities.registry import is_registered

        if not is_registered(feature):
            return True
        return service.check(feature, org_id=org_id).allowed

    @staticmethod
    def check_feature(org_id: str, feature: str) -> CheckResult:
        """Check if a boolean feature is enabled.

        Returns CheckResult for consistency with can_create.
        """
        # Plan entitlements are a cloud concept: self-hosted deployments
        # (OSS or EE) have no plans, so plan checks pass. License-gated
        # features enforce off-cloud via tfc.capabilities, not here.
        if not DeploymentMode.is_cloud():
            return CheckResult(allowed=True)

        if Entitlements.has_feature(org_id, feature):
            return CheckResult(allowed=True)

        return CheckResult(
            allowed=False,
            error_code="ENTITLEMENT_DENIED",
            reason="This feature requires a higher plan",
            upgrade_cta=_find_upgrade_cta(org_id, feature),
        )

    @staticmethod
    def get_retention_days(org_id: str, data_type: str) -> int:
        """Get retention period in days for a data type.

        data_type: 'traces', 'gateway_logs', 'eval_results', 'sim_recordings'
        Returns -1 for unlimited retention.
        """
        feature_key = f"retention_{data_type}_days"
        days = Entitlements.get_limit(org_id, feature_key)
        return days if days != 0 else 30  # Default 30 days if not configured

    @staticmethod
    def invalidate_cache(org_id: str, feature: str | None = None) -> None:
        """Invalidate cached entitlement(s) for an org.

        With ``feature``, deletes the single ``ent:<org>:<feature>`` key.
        Without it, deletes every ``ent:<org>:*`` key — use this on plan
        changes, since every value cached against the old plan is now wrong.
        """
        try:
            r = get_redis()
            if feature is not None:
                r.delete(f"ent:{org_id}:{feature}")
                return
            for key in r.scan_iter(match=f"ent:{org_id}:*"):
                r.delete(key)
        except RedisError:
            logger.warning(
                "entitlement_cache_invalidation_failed",
                org_id=org_id,
                feature=feature,
            )


class _CloudPlanResolver:
    def has_feature(self, org_id: str, feature_id: str) -> bool:
        return Entitlements.has_feature(org_id, f"has_{feature_id}")

    def get_upgrade_cta(self, org_id: str, feature_id: str) -> dict | None:
        cta = _find_upgrade_cta(org_id, f"has_{feature_id}")
        return cta.model_dump() if cta else None


_CLOUD_PLAN_RESOLVER = _CloudPlanResolver()


def get_cloud_plan_resolver() -> _CloudPlanResolver:
    return _CLOUD_PLAN_RESOLVER


def invalidate_plan_caches(org_id: str) -> None:
    """Bust the cached plan and every cached entitlement for an org.

    Any write that changes ``OrganizationSubscription.plan`` must call this.
    Without it, the resolver serves values cached against the previous plan
    for up to ``CACHE_TTL`` (5 min), so features the customer just paid for
    keep returning 402.
    """
    get_redis().delete(f"plan:{org_id}", f"billing_status:{org_id}")
    Entitlements.invalidate_cache(str(org_id))


def _find_upgrade_cta(org_id: str, feature: str) -> UpgradeCTA | None:
    """Find the next plan that has a higher limit for this feature."""
    plan = _get_cached_plan(org_id)
    config = BillingConfig.get()

    # Plan upgrade order
    upgrade_order = ["free", "payg", "boost", "scale", "enterprise"]
    try:
        current_idx = upgrade_order.index(plan)
    except ValueError:
        return None

    current_val = config.get_entitlement_default(feature, plan)

    for next_plan in upgrade_order[current_idx + 1 :]:
        next_val = config.get_entitlement_default(feature, next_plan)
        if next_val is None:
            continue

        # Check if next plan has more
        if isinstance(next_val, bool) and next_val and not current_val:
            plan_config = config.get_plan(next_plan)
            label = (
                "Add" if next_plan in ("boost", "scale", "enterprise") else "Upgrade to"
            )
            return UpgradeCTA(
                text=f"{label} {plan_config.display_name}",
                plan=next_plan,
            )
        elif isinstance(next_val, int) and isinstance(current_val, int):
            if next_val == -1 or next_val > current_val:
                plan_config = config.get_plan(next_plan)
                limit_text = "unlimited" if next_val == -1 else str(next_val)
                label = (
                    "Add"
                    if next_plan in ("boost", "scale", "enterprise")
                    else "Upgrade to"
                )
                return UpgradeCTA(
                    text=f"{label} {plan_config.display_name} for {limit_text} {feature.replace('_', ' ')}",
                    plan=next_plan,
                )

    return None
