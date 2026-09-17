from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ExecutionLocation(str, Enum):
    LOCAL = "local"
    FUTUREAGI_SERVICE = "futureagi_service"
    HYBRID = "hybrid"


class GraceBehavior(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"


class AirGapBehavior(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class FeatureDefinition:
    id: str
    display_name: str
    oss_baseline: bool
    requires_license: bool
    execution_location: ExecutionLocation
    # Two-tier gating: requires_license=True features are cloud-plan products
    # by default and run FREE on self-hosted deployments (OSS or EE, any
    # license state). Setting oss_locked=True additionally requires a valid
    # license off-cloud — reserved for managed compute and the error feed.
    oss_locked: bool = False
    required_service: str | None = None
    metering_dimension: str | None = None
    air_gap_behavior: AirGapBehavior = AirGapBehavior.AVAILABLE
    allowed_during_grace: bool = True
    quota_key: str | None = None


# --- OSS Baseline Features (always available, may have quota) ---

FEATURE_KNOWLEDGE_BASE = FeatureDefinition(
    id="knowledge_base",
    display_name="Knowledge Bases",
    oss_baseline=True,
    requires_license=False,
    execution_location=ExecutionLocation.LOCAL,
)

FEATURE_REVIEW_WORKFLOW = FeatureDefinition(
    id="review_workflow",
    display_name="Review Workflows",
    oss_baseline=True,
    requires_license=False,
    execution_location=ExecutionLocation.LOCAL,
)

FEATURE_TRACE_INGESTION = FeatureDefinition(
    id="trace_ingestion",
    display_name="Trace Ingestion",
    oss_baseline=True,
    requires_license=False,
    execution_location=ExecutionLocation.LOCAL,
    quota_key="traces_monthly",
)

FEATURE_GATEWAY_REQUESTS = FeatureDefinition(
    id="gateway_requests",
    display_name="Gateway Requests",
    oss_baseline=True,
    requires_license=False,
    execution_location=ExecutionLocation.LOCAL,
    quota_key="gateway_requests_monthly",
)

# --- Enterprise Features (require valid license) ---

FEATURE_FALCON_AI = FeatureDefinition(
    id="falcon_ai",
    oss_locked=True,
    display_name="Falcon AI",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.FUTUREAGI_SERVICE,
    required_service="falcon",
    metering_dimension="managed_ai_credits_monthly",
    air_gap_behavior=AirGapBehavior.UNAVAILABLE,
)

FEATURE_TURING_MODELS = FeatureDefinition(
    id="turing_models",
    oss_locked=True,
    display_name="Turing Models",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.FUTUREAGI_SERVICE,
    required_service="turing",
    metering_dimension="managed_ai_credits_monthly",
    air_gap_behavior=AirGapBehavior.UNAVAILABLE,
)

FEATURE_PROTECT = FeatureDefinition(
    id="protect",
    oss_locked=True,
    display_name="Protect",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.FUTUREAGI_SERVICE,
    required_service="protect",
    metering_dimension="managed_ai_credits_monthly",
    air_gap_behavior=AirGapBehavior.UNAVAILABLE,
)

FEATURE_VOICE_SIM = FeatureDefinition(
    id="voice_sim",
    display_name="Voice Simulation",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.HYBRID,
    required_service="voice",
    metering_dimension="voice_minutes_monthly",
    air_gap_behavior=AirGapBehavior.UNAVAILABLE,
)

FEATURE_AGENTIC_EVAL = FeatureDefinition(
    id="agentic_eval",
    display_name="Agentic Evaluations",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.LOCAL,
)

FEATURE_SYNTHETIC_DATA = FeatureDefinition(
    id="synthetic_data",
    display_name="Synthetic Data",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.LOCAL,
)

FEATURE_SCENARIOS = FeatureDefinition(
    id="scenarios",
    display_name="Scenarios",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.LOCAL,
)

FEATURE_ERROR_FEED = FeatureDefinition(
    id="error_feed",
    oss_locked=True,
    display_name="Error Feed",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.LOCAL,
)

FEATURE_FIX_MY_AGENT = FeatureDefinition(
    id="fix_my_agent",
    display_name="Fix My Agent",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.LOCAL,
)

FEATURE_OPTIMIZATION = FeatureDefinition(
    id="optimization",
    display_name="Optimization",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.LOCAL,
)

FEATURE_SCIM = FeatureDefinition(
    id="scim",
    display_name="SCIM",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.LOCAL,
)

FEATURE_PROJECT_RBAC = FeatureDefinition(
    id="project_rbac",
    display_name="Project RBAC",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.LOCAL,
)

FEATURE_CUSTOM_ROLES = FeatureDefinition(
    id="custom_roles",
    display_name="Custom Roles",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.LOCAL,
)

FEATURE_AUDIT_LOGS = FeatureDefinition(
    id="audit_logs",
    display_name="Audit Logs",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.LOCAL,
)

FEATURE_DATA_MASKING = FeatureDefinition(
    id="data_masking",
    display_name="Data Masking",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.LOCAL,
)

FEATURE_CUSTOM_BRAND = FeatureDefinition(
    id="custom_brand",
    display_name="Custom Branding",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.LOCAL,
)

FEATURE_EXTENDED_RETENTION = FeatureDefinition(
    id="extended_retention",
    display_name="Extended Retention",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.LOCAL,
)

FEATURE_DEDICATED_SUPPORT = FeatureDefinition(
    id="dedicated_support",
    display_name="Dedicated Support",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.LOCAL,
    air_gap_behavior=AirGapBehavior.UNAVAILABLE,
)

FEATURE_AGREEMENT_METRICS = FeatureDefinition(
    id="agreement_metrics",
    display_name="Agreement Metrics",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.LOCAL,
)

FEATURE_REQUIRED_LABELS = FeatureDefinition(
    id="required_labels",
    display_name="Required Labels",
    oss_baseline=False,
    requires_license=True,
    execution_location=ExecutionLocation.LOCAL,
)


_ALL_FEATURES: tuple[FeatureDefinition, ...] = (
    FEATURE_KNOWLEDGE_BASE,
    FEATURE_REVIEW_WORKFLOW,
    FEATURE_TRACE_INGESTION,
    FEATURE_GATEWAY_REQUESTS,
    FEATURE_FALCON_AI,
    FEATURE_TURING_MODELS,
    FEATURE_PROTECT,
    FEATURE_VOICE_SIM,
    FEATURE_AGENTIC_EVAL,
    FEATURE_SYNTHETIC_DATA,
    FEATURE_SCENARIOS,
    FEATURE_ERROR_FEED,
    FEATURE_FIX_MY_AGENT,
    FEATURE_OPTIMIZATION,
    FEATURE_SCIM,
    FEATURE_PROJECT_RBAC,
    FEATURE_CUSTOM_ROLES,
    FEATURE_AUDIT_LOGS,
    FEATURE_DATA_MASKING,
    FEATURE_CUSTOM_BRAND,
    FEATURE_EXTENDED_RETENTION,
    FEATURE_DEDICATED_SUPPORT,
    FEATURE_AGREEMENT_METRICS,
    FEATURE_REQUIRED_LABELS,
)


FEATURE_REGISTRY: dict[str, FeatureDefinition] = {f.id: f for f in _ALL_FEATURES}


OSS_BASELINE_FEATURES: frozenset[str] = frozenset(
    f.id for f in _ALL_FEATURES if f.oss_baseline
)

PAID_FEATURES: frozenset[str] = frozenset(
    f.id for f in _ALL_FEATURES if f.requires_license
)

# Paid features that stay license-gated even off-cloud (managed compute +
# the error feed). Everything else in PAID_FEATURES is free on self-hosted.
OSS_LOCKED_FEATURES: frozenset[str] = frozenset(
    f.id for f in _ALL_FEATURES if f.oss_locked
)

MANAGED_SERVICE_FEATURES: frozenset[str] = frozenset(
    f.id
    for f in _ALL_FEATURES
    if f.execution_location == ExecutionLocation.FUTUREAGI_SERVICE
)


def get_feature(feature_id: str) -> FeatureDefinition | None:
    return FEATURE_REGISTRY.get(feature_id)


def is_registered(feature_id: str) -> bool:
    return feature_id in FEATURE_REGISTRY
