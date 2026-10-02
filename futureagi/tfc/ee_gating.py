"""EE feature gating primitives — safe to import when ee/ is absent.

This module MUST NOT import anything from `ee.*` at module top. It is the
canonical gate that non-ee code uses to ask "is this EE feature allowed in
the current deployment?". When ee/ is absent, every `ee.*` import raises;
this module routes around that by probing `has_ee()` first and lazy-
importing only when EE is actually present.

Public surface:
    FeatureUnavailable      — DRF APIException (HTTP 402, upgrade_required)
    is_oss()                — cached; True when the deployment has no EE
    require_ee_feature(...) — decorator for DRF views, AI tools, Temporal
                              activities. No ee → deny; EE/Cloud → pass-
                              through (middleware / entitlement service
                              decides).
    check_ee_feature(...)   — imperative form of the decorator for use mid-
                              function (e.g. inside Temporal activity bodies).
    EE_FEATURES_OSS         — paid feature names mirrored from the canonical
                              capability registry.
"""

from __future__ import annotations

import functools
import inspect
import logging
from enum import Enum
from typing import Any, Callable, Optional, Union

from rest_framework import status as drf_status
from rest_framework.exceptions import APIException
from temporalio.exceptions import ApplicationError
from tfc.capabilities.registry import PAID_FEATURES
from tfc.ee_loader import has_ee

logger = logging.getLogger(__name__)


class EEFeature(str, Enum):
    """Canonical names of EE-only features.

    Mirrors the canonical paid capability registry. Pass enum members (or
    their `.value`)
    to `check_ee_feature` / `require_ee_feature`.
    """

    AGREEMENT_METRICS = "agreement_metrics"
    REQUIRED_LABELS = "required_labels"
    AUDIT_LOGS = "audit_logs"
    SCIM = "scim"
    VOICE_SIM = "voice_sim"
    SYNTHETIC_DATA = "synthetic_data"
    AGENTIC_EVAL = "agentic_eval"
    OPTIMIZATION = "optimization"
    PROJECT_RBAC = "project_rbac"
    CUSTOM_ROLES = "custom_roles"
    DATA_MASKING = "data_masking"
    EXTENDED_RETENTION = "extended_retention"
    CUSTOM_BRAND = "custom_brand"
    DEDICATED_SUPPORT = "dedicated_support"
    FALCON_AI = "falcon_ai"
    TURING_MODELS = "turing_models"
    PROTECT = "protect"
    SCENARIOS = "scenarios"
    ERROR_FEED = "error_feed"
    FIX_MY_AGENT = "fix_my_agent"


class EEResource(str, Enum):
    """Resource keys for limit-based entitlement checks (passed to
    `Entitlements.can_create(org_id, resource, current_count)`).

    These are numeric-limit resources (e.g. "max N webhooks") rather than
    boolean feature toggles — they don't appear in `EE_FEATURES`, but they
    are still EE/Cloud-only and should be denied when ee is absent.
    """

    GATEWAY_EMAIL_ALERTS = "gateway_email_alerts"
    GATEWAY_WEBHOOKS = "gateway_webhooks"
    SHADOW_EXPERIMENTS = "shadow_experiments"
    ANNOTATION_QUEUES = "queues"
    MONITORS = "monitors"
    DATASETS = "datasets"
    DATASET_ROWS = "dataset_rows"


# Compatibility alias for callers that expect the historical name.
EE_FEATURES_OSS = PAID_FEATURES

# Type alias for inputs that accept either the enum or its string value.
FeatureName = Union[EEFeature, str]


def _feature_name(feature: FeatureName) -> str:
    return feature.value if isinstance(feature, EEFeature) else feature


class FeatureUnavailable(APIException):
    """Raised when an EE-gated feature is accessed in a deployment that
    doesn't include it. Surfaces as HTTP 402 via custom_exception_handler.

    Carries an optional `upgrade_cta` (dict) threaded through from the
    entitlement service so Cloud users see their targeted upsell copy.
    """

    status_code = drf_status.HTTP_402_PAYMENT_REQUIRED
    default_detail = "This feature is not available on your current plan."
    default_code = "ENTITLEMENT_DENIED"

    def __init__(
        self,
        feature: FeatureName,
        detail: Optional[str] = None,
        code: Optional[str] = None,
        upgrade_cta: Optional[dict] = None,
        metadata: Optional[dict] = None,
    ):
        self.feature = _feature_name(feature)
        self.error_code = code or self.default_code
        self.upgrade_cta = upgrade_cta
        self.metadata = metadata or {}
        super().__init__(
            detail=detail or f"'{self.feature}' is not available. Upgrade your plan.",
            code=self.error_code,
        )


@functools.lru_cache(maxsize=1)
def is_oss() -> bool:
    """True when the deployment has no EE license and isn't Cloud.

    Cached for process lifetime. Safe to call when `ee.usage.deployment`
    does not exist.
    """
    if not has_ee("ee.usage"):
        return True
    try:
        from ee.usage.deployment import DeploymentMode

        return DeploymentMode.is_oss()
    except Exception:  # pragma: no cover — defensive, ee present but broken
        logger.warning("ee.usage.deployment import failed; assuming no ee")
        return True


def check_ee_feature(
    feature: FeatureName,
    *,
    org_id: Optional[str] = None,
    activity: bool = False,
) -> None:
    """Imperative pre-flight check. Raises on deny.

    Delegates to tfc.capabilities.service when configured (after
    AppConfig.ready). Falls back to legacy logic during early startup.

    Args:
        feature: EEFeature member, or a string present in EE_FEATURES_OSS.
            Strings outside that set are treated as non-gated and pass
            through silently — this avoids accidentally 402-ing features
            (e.g. "tracing") that aren't EE-restricted.
        org_id:  when provided and EE/Cloud is present, checks per-org
                 entitlement via Entitlements.check_feature (which returns
                 an upgrade_cta on Cloud).
        activity: when True, raise a Temporal non-retryable ApplicationError
                  instead of FeatureUnavailable (so the workflow fails once
                  with a clear marker instead of entering retry storms).

    Raises:
        FeatureUnavailable or temporalio.exceptions.ApplicationError.
    """
    feature_str = _feature_name(feature)

    # Only gate known EE features. A typo or a non-EE string should not
    # trigger a 402 — treat it as "not our concern".
    if feature_str not in EE_FEATURES_OSS:
        return

    # Try the new capability service first (available after AppConfig.ready)
    if _try_capability_service(feature_str, org_id=org_id, activity=activity):
        return

    # Legacy fallback for early startup or when capability service isn't wired
    if is_oss():
        # Mirror the service's two-tier rule: only oss_locked features
        # deny off-cloud; the rest of the paid set is free self-hosted.
        from tfc.capabilities.registry import get_feature

        definition = get_feature(feature_str)
        if definition is None or definition.oss_locked:
            _raise_denied(feature_str, activity=activity)
        return

    try:
        from ee.usage.deployment import DeploymentMode
        from ee.usage.services.entitlements import Entitlements
    except ImportError:
        logger.exception("ee_entitlements_unavailable")
        _raise_denied(
            feature_str,
            activity=activity,
            detail="Feature entitlement service is unavailable.",
        )

    if org_id is None and DeploymentMode.is_cloud():
        _raise_denied(
            feature_str,
            activity=activity,
            detail="Organization identity is required for this feature.",
        )

    try:
        allowed = Entitlements.has_feature_unified(str(org_id or ""), feature_str)
    except Exception:
        logger.exception("ee_entitlements_check_failed")
        _raise_denied(
            feature_str,
            activity=activity,
            detail="Feature entitlement service is unavailable.",
        )

    if allowed is not True:
        cta = None
        reason = None
        try:
            result = Entitlements.check_feature(str(org_id or ""), f"has_{feature_str}")
            reason = getattr(result, "reason", None)
            raw_cta = getattr(result, "upgrade_cta", None)
            if raw_cta is not None:
                cta = (
                    raw_cta.model_dump()
                    if hasattr(raw_cta, "model_dump")
                    else dict(raw_cta)
                )
        except Exception:  # pragma: no cover — best-effort CTA fetch
            logger.debug("ee_gating_upgrade_cta_lookup_failed", exc_info=True)
        _raise_denied(
            feature_str,
            activity=activity,
            detail=reason,
            upgrade_cta=cta,
        )


def _try_capability_service(
    feature_str: str,
    *,
    org_id: Optional[str] = None,
    activity: bool = False,
) -> bool:
    """Attempt to use the new capability service. Returns True if handled.

    Returns False if the service isn't configured yet (early startup),
    signalling the caller to fall back to legacy logic.
    """
    try:
        from tfc.capabilities import service
        from tfc.capabilities.registry import is_registered
    except Exception:
        return False

    if not service.is_configured():
        return False

    if not is_registered(feature_str):
        return False

    try:
        decision = service.check(feature_str, org_id=org_id)
    except Exception:
        logger.exception("capability_check_failed")
        _raise_denied(
            feature_str,
            activity=activity,
            detail="Feature entitlement service is unavailable.",
        )

    if getattr(decision, "allowed", None) is not True:
        if hasattr(decision, "feature_id") and hasattr(decision, "reason_code"):
            service._raise_denied(decision, activity=activity)
        _raise_denied(
            feature_str,
            activity=activity,
            detail="Feature entitlement service is unavailable.",
        )
    return True


ResourceName = Union[EEResource, str]


def check_ee_can_create(
    resource: ResourceName,
    *,
    org_id: str,
    current_count: int,
) -> None:
    """Limit-based counterpart to `check_ee_feature` for `EEResource` keys.

    Count limits are a cloud-plan concept: self-hosted deployments (OSS or
    EE) are uncapped — there is no billing to enforce against. Cloud calls
    `Entitlements.can_create(org_id, resource, count)` and raises
    FeatureUnavailable if `allowed=False`, threading `upgrade_cta` through
    so Cloud users see their targeted upsell.
    """
    resource_str = resource.value if isinstance(resource, EEResource) else resource
    try:
        from ee.usage.deployment import DeploymentMode
    except ImportError:
        return  # no ee code at all → self-hosted OSS → uncapped
    if not DeploymentMode.is_cloud():
        return
    if not org_id:
        raise FeatureUnavailable(
            resource_str,
            detail="Organization identity is required for this resource.",
            metadata={"resource": resource_str},
        )

    try:
        from ee.usage.services.entitlements import Entitlements
    except ImportError:
        logger.exception("ee_entitlements_unavailable")
        raise FeatureUnavailable(
            resource_str,
            detail="Feature entitlement service is unavailable.",
            metadata={"resource": resource_str},
        )

    try:
        result = Entitlements.can_create(str(org_id), resource_str, current_count)
    except Exception:
        logger.exception("ee_entitlements_create_check_failed")
        raise FeatureUnavailable(
            resource_str,
            detail="Feature entitlement service is unavailable.",
            metadata={"resource": resource_str},
        )

    if getattr(result, "allowed", None) is not True:
        raw_cta = getattr(result, "upgrade_cta", None)
        cta = None
        if raw_cta is not None:
            cta = (
                raw_cta.model_dump()
                if hasattr(raw_cta, "model_dump")
                else dict(raw_cta)
            )
        metadata = {"resource": resource_str}
        current_usage = getattr(result, "current_usage", None)
        limit = getattr(result, "limit", None)
        if current_usage is not None:
            metadata["current_usage"] = current_usage
        if limit is not None:
            metadata["limit"] = limit
        raise FeatureUnavailable(
            resource_str,
            detail=getattr(result, "reason", None),
            code=getattr(result, "error_code", None),
            upgrade_cta=cta,
            metadata=metadata,
        )


def require_ee_feature(
    feature: FeatureName,
    *,
    activity: bool = False,
    org_id_getter: Optional[Callable[..., Optional[str]]] = None,
) -> Callable:
    """Decorator for DRF views, AI tool execute() methods, and Temporal
    activities. When ee is absent, short-circuits with FeatureUnavailable
    (or Temporal ApplicationError when activity=True). In EE/Cloud, delegates
    to the entitlement service if `org_id_getter` is provided; otherwise
    passes through and lets middleware / existing view-level checks decide.

    Args:
        feature: EEFeature member (strings outside EE_FEATURES_OSS pass
            through without gating — see `check_ee_feature`).
        activity: True when decorating a Temporal @activity.defn function.
        org_id_getter: callable(*args, **kwargs) -> Optional[str]. Returns
            the organization id for entitlement lookup. Example for DRF:
            `lambda self, request, *a, **kw: str(request.organization.id)`.
    """

    def _decorate(fn: Callable) -> Callable:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
                org_id = org_id_getter(*args, **kwargs) if org_id_getter else None
                check_ee_feature(feature, org_id=org_id, activity=activity)
                return await fn(*args, **kwargs)

            return _async_wrapper

        @functools.wraps(fn)
        def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            org_id = org_id_getter(*args, **kwargs) if org_id_getter else None
            check_ee_feature(feature, org_id=org_id, activity=activity)
            return fn(*args, **kwargs)

        return _sync_wrapper

    return _decorate


def _raise_denied(
    feature: str,
    *,
    activity: bool,
    detail: Optional[str] = None,
    upgrade_cta: Optional[dict] = None,
) -> None:
    """Log the denial and raise the appropriate exception type.

    Structured log lets ops/analytics see which EE features OSS/EE users
    are attempting — exactly the signal needed for upsell funnels.
    """
    msg = detail or f"'{feature}' is not available. Upgrade your plan."
    logger.info(
        "ee_feature_denied",
        extra={"feature": feature, "activity": activity, "via": "ee_gating"},
    )
    if activity:
        raise ApplicationError(
            msg,
            type="FeatureUnavailable",
            non_retryable=True,
        )
    raise FeatureUnavailable(feature, detail=detail, upgrade_cta=upgrade_cta)
