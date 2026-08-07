"""Capability decision service.

Single entry point for all feature access decisions. All HTTP, WebSocket,
Temporal, and tool entry points call through this service.

Decision order:
1. Is the feature registered? (unknown → programming error, deny)
2. Is it an OSS baseline feature? (→ allow, subject to quota)
3. Is this cloud? (→ delegate to cloud plan resolver)
4. Is EE code available? (→ if not, deny with EE_CODE_UNAVAILABLE)
5. Is a valid license present and active/grace/trial?
6. Does the license include the feature?
7. Is the feature permitted during grace?
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import structlog
from tfc.capabilities.registry import (
    FEATURE_REGISTRY,
    FeatureDefinition,
)
from tfc.licensing.types import (
    DenialReason,
    DeploymentFlavor,
    DeploymentLocation,
    LicenseSnapshot,
    LicenseState,
)

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class CapabilityDecision:
    allowed: bool
    feature_id: str
    reason_code: str | None = None
    license_state: str | None = None
    requires_network: bool | None = None
    service_available: bool | None = None
    grace_ends_at: str | None = None
    limit: int | None = None
    upgrade_cta: dict | None = None


class LicenseResolver(Protocol):
    """Protocol for license state resolution.

    Implemented by the EE licensing module (ee.licensing.state) and
    used by the capability service to get the current license snapshot.
    """

    def get_snapshot(self) -> LicenseSnapshot: ...


class CloudPlanResolver(Protocol):
    """Protocol for cloud plan/org entitlement resolution.

    Implemented by ee.usage.services.entitlements for Cloud deployments.
    """

    def has_feature(self, org_id: str, feature_id: str) -> bool: ...

    def get_upgrade_cta(self, org_id: str, feature_id: str) -> dict | None: ...


_license_resolver: LicenseResolver | None = None
_cloud_plan_resolver: CloudPlanResolver | None = None
_deployment_flavor: DeploymentFlavor = DeploymentFlavor.OSS
_deployment_location: DeploymentLocation = DeploymentLocation.SELF_HOSTED
_configured: bool = False


def configure(
    *,
    flavor: DeploymentFlavor,
    location: DeploymentLocation,
    license_resolver: LicenseResolver | None = None,
    cloud_plan_resolver: CloudPlanResolver | None = None,
) -> None:
    """Wire resolvers at application startup. Called from AppConfig.ready()."""
    global _license_resolver, _cloud_plan_resolver, _deployment_flavor, _deployment_location, _configured
    _deployment_flavor = flavor
    _deployment_location = location
    _license_resolver = license_resolver
    _cloud_plan_resolver = cloud_plan_resolver
    _configured = True


def get_deployment_flavor() -> DeploymentFlavor:
    return _deployment_flavor


def get_deployment_location() -> DeploymentLocation:
    return _deployment_location


def get_license_snapshot() -> LicenseSnapshot | None:
    if _license_resolver is None:
        return None
    return _license_resolver.get_snapshot()


def is_configured() -> bool:
    return _configured


def check(
    feature_id: str,
    *,
    org_id: str | None = None,
) -> CapabilityDecision:
    """Single entry point for all capability decisions.

    Returns a CapabilityDecision. Callers decide whether to raise an
    exception based on the result. For convenience, use check_or_raise()
    which raises CapabilityDenied on denial.
    """
    feature = FEATURE_REGISTRY.get(feature_id)

    # 1. Unknown feature → programming error, always deny
    if feature is None:
        logger.error(
            "capability_check_unknown_feature",
            extra={"feature_id": feature_id},
        )
        return CapabilityDecision(
            allowed=False,
            feature_id=feature_id,
            reason_code=DenialReason.FEATURE_UNKNOWN.value,
        )

    # 2. OSS baseline → allow (quota enforcement is separate)
    if feature.oss_baseline:
        return CapabilityDecision(allowed=True, feature_id=feature_id)

    # 3. Cloud → delegate to cloud plan resolver
    if _deployment_location == DeploymentLocation.CLOUD:
        return _check_cloud(feature, org_id)

    # 3.5 Self-hosted (OSS or EE, any license state): paid features are
    # cloud-plan products unless explicitly oss_locked — everything else
    # runs free off-cloud. Only oss_locked features fall through to the
    # license checks below.
    if not feature.oss_locked:
        return CapabilityDecision(allowed=True, feature_id=feature_id)

    # 4. EE code not available → deny
    if _deployment_flavor == DeploymentFlavor.OSS:
        return CapabilityDecision(
            allowed=False,
            feature_id=feature_id,
            reason_code=DenialReason.EE_CODE_UNAVAILABLE.value,
            requires_network=feature.required_service is not None,
        )

    # 5. Self-hosted EE: resolve license state
    return _check_self_hosted_ee(feature, org_id)


def check_or_raise(
    feature_id: str,
    *,
    org_id: str | None = None,
    activity: bool = False,
) -> CapabilityDecision:
    """check() + raise on denial. Use in views, activities, and tools."""
    decision = check(feature_id, org_id=org_id)
    if not decision.allowed:
        _raise_denied(decision, activity=activity)
    return decision


def _check_cloud(
    feature: FeatureDefinition,
    org_id: str | None,
) -> CapabilityDecision:
    if _cloud_plan_resolver is None:
        logger.warning("capability_check_no_cloud_resolver")
        return CapabilityDecision(
            allowed=False,
            feature_id=feature.id,
            reason_code=DenialReason.RESOLVER_UNAVAILABLE.value,
        )

    if org_id is None:
        return CapabilityDecision(
            allowed=False,
            feature_id=feature.id,
            reason_code=DenialReason.RESOLVER_UNAVAILABLE.value,
        )

    if _cloud_plan_resolver.has_feature(org_id, feature.id):
        return CapabilityDecision(allowed=True, feature_id=feature.id)

    cta = _cloud_plan_resolver.get_upgrade_cta(org_id, feature.id)
    return CapabilityDecision(
        allowed=False,
        feature_id=feature.id,
        reason_code=DenialReason.PLAN_FEATURE_MISSING.value,
        upgrade_cta=cta,
    )


def _check_self_hosted_ee(
    feature: FeatureDefinition,
    org_id: str | None,
) -> CapabilityDecision:
    if _license_resolver is None:
        logger.warning("capability_check_no_license_resolver")
        return CapabilityDecision(
            allowed=False,
            feature_id=feature.id,
            reason_code=DenialReason.RESOLVER_UNAVAILABLE.value,
        )

    snapshot = _license_resolver.get_snapshot()
    # Recompute the state against the clock so that a snapshot frozen at
    # process start does not keep serving paid features past expires_at /
    # grace_ends_at in long-lived workers.
    live_state = snapshot.live_state()

    # Missing or invalid license
    if live_state == LicenseState.MISSING:
        return CapabilityDecision(
            allowed=False,
            feature_id=feature.id,
            reason_code=DenialReason.LICENSE_MISSING.value,
            license_state=live_state.value,
        )

    if live_state == LicenseState.INVALID:
        return CapabilityDecision(
            allowed=False,
            feature_id=feature.id,
            reason_code=DenialReason.LICENSE_INVALID.value,
            license_state=live_state.value,
        )

    # Expired states
    if live_state == LicenseState.EXPIRED:
        return CapabilityDecision(
            allowed=False,
            feature_id=feature.id,
            reason_code=DenialReason.LICENSE_EXPIRED.value,
            license_state=live_state.value,
        )

    if live_state == LicenseState.TRIAL_EXPIRED:
        return CapabilityDecision(
            allowed=False,
            feature_id=feature.id,
            reason_code=DenialReason.LICENSE_TRIAL_EXPIRED.value,
            license_state=live_state.value,
        )

    # Active or grace: check feature inclusion
    if feature.id not in snapshot.features:
        return CapabilityDecision(
            allowed=False,
            feature_id=feature.id,
            reason_code=DenialReason.LICENSE_FEATURE_MISSING.value,
            license_state=live_state.value,
        )

    # Grace: check if feature is allowed during grace
    if live_state == LicenseState.GRACE and not feature.allowed_during_grace:
        return CapabilityDecision(
            allowed=False,
            feature_id=feature.id,
            reason_code=DenialReason.FEATURE_NOT_IN_GRACE.value,
            license_state=live_state.value,
            grace_ends_at=(
                snapshot.grace_ends_at.isoformat() if snapshot.grace_ends_at else None
            ),
        )

    # Network requirement check (informational only — actual enforcement is
    # at the managed service layer)
    requires_network = feature.required_service is not None

    return CapabilityDecision(
        allowed=True,
        feature_id=feature.id,
        license_state=live_state.value,
        requires_network=requires_network,
    )


def _raise_denied(decision: CapabilityDecision, *, activity: bool) -> None:
    logger.info(
        "capability_denied",
        extra={
            "feature": decision.feature_id,
            "reason": decision.reason_code,
            "license_state": decision.license_state,
            "activity": activity,
        },
    )
    if activity:
        from tfc.capabilities.errors import raise_capability_denied_for_temporal

        raise_capability_denied_for_temporal(
            decision.feature_id,
            decision.reason_code or "DENIED",
            detail=f"'{decision.feature_id}' is not available.",
        )

    from tfc.capabilities.errors import CapabilityDenied

    raise CapabilityDenied(
        feature_id=decision.feature_id,
        reason_code=decision.reason_code or "DENIED",
        upgrade_cta=decision.upgrade_cta,
    )
