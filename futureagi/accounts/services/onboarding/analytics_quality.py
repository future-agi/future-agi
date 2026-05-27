from dataclasses import dataclass, field
from datetime import timedelta

from django.utils import timezone

from accounts.models import (
    OnboardingActivationEvent,
    OnboardingLifecycleEvaluationLog,
)
from accounts.services.onboarding.activation_events import SENSITIVE_METADATA_KEYS
from accounts.services.onboarding.constants import (
    ACTIVATION_STAGES,
    ONBOARDING_ACTIVATION_EVENT_ALIASES,
    ONBOARDING_ACTIVATION_EVENTS,
    ONBOARDING_GOALS,
    PRODUCT_PATHS,
    canonical_activation_event,
    canonical_goal,
    canonical_path,
)
from accounts.services.onboarding.flow_config import get_activation_flow_config

SAMPLE_EVENT_NAMES = {
    "onboarding_sample_project_opened",
    "sample_trace_available",
    "sample_signal_viewed",
    "sample_to_real_setup_clicked",
}

REAL_ACTIVATION_EVENT_NAMES = {
    "first_quality_loop_completed",
}

HARD_BLOCKER_FIELDS = (
    "missing_workspace_id",
    "missing_organization_id",
    "workspace_org_mismatch",
    "unknown_event",
    "unknown_path",
    "unknown_stage",
    "alias_leakage",
    "sensitive_metadata_events",
    "sample_event_without_sample_flag",
    "sample_activation",
    "eligible_missing_target_url",
    "eligible_completed_target",
    "route_unavailable_primary",
    "lifecycle_error",
    "duplicate_lifecycle_candidate",
    "lifecycle_workspace_org_mismatch",
    "unknown_goal",
    "unknown_recommendation",
    "unknown_target_event",
    "negative_lifecycle_duration",
)

ACTION_IDS = tuple(get_activation_flow_config()["actions"].keys())
GOAL_METADATA_KEYS = ("goal", "new_goal", "selected_goal")
RECOMMENDATION_METADATA_KEYS = ("recommended_action_id", "action_id")
OUTPUT_COUNT_FIELDS = tuple(
    sorted(
        {
            *HARD_BLOCKER_FIELDS,
            "missing_source",
        }
    )
)


@dataclass
class OnboardingAnalyticsQualityResult:
    since: object
    until: object
    events_checked: int = 0
    lifecycle_logs_checked: int = 0
    counts: dict = field(default_factory=dict)

    def count(self, key, amount=1):
        self.counts[key] = self.counts.get(key, 0) + amount

    @property
    def missing_identity_total(self):
        return (
            self.counts.get("missing_workspace_id", 0)
            + self.counts.get("missing_organization_id", 0)
            + self.counts.get("workspace_org_mismatch", 0)
        )

    @property
    def identity_rate(self):
        if self.events_checked == 0:
            return 1.0
        valid = max(0, self.events_checked - self.missing_identity_total)
        return valid / self.events_checked

    @property
    def status(self):
        hard_blocker_count = sum(
            self.counts.get(field_name, 0) for field_name in HARD_BLOCKER_FIELDS
        )
        if hard_blocker_count:
            return "fail"
        if self.identity_rate < 0.95:
            return "fail"
        return "pass"

    def to_payload(self):
        return {
            "since": self.since.isoformat() if self.since else None,
            "until": self.until.isoformat() if self.until else None,
            "events_checked": self.events_checked,
            "lifecycle_logs_checked": self.lifecycle_logs_checked,
            "identity_rate": round(self.identity_rate, 4),
            "status": self.status,
            **{key: self.counts.get(key, 0) for key in OUTPUT_COUNT_FIELDS},
            **{
                key: self.counts.get(key, 0)
                for key in sorted(set(self.counts) - set(OUTPUT_COUNT_FIELDS))
            },
        }


def _metadata_keys(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield str(key).lower()
            yield from _metadata_keys(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _metadata_keys(item)


def _has_sensitive_metadata(metadata):
    return bool(SENSITIVE_METADATA_KEYS.intersection(_metadata_keys(metadata or {})))


def _event_query(*, since, until):
    queryset = OnboardingActivationEvent.no_workspace_objects.select_related(
        "organization",
        "workspace",
    )
    if since:
        queryset = queryset.filter(occurred_at__gte=since)
    if until:
        queryset = queryset.filter(occurred_at__lt=until)
    return queryset.order_by("occurred_at", "created_at")


def _lifecycle_query(*, since, until):
    queryset = OnboardingLifecycleEvaluationLog.no_workspace_objects.select_related(
        "organization",
        "workspace",
    )
    if since:
        queryset = queryset.filter(evaluated_at__gte=since)
    if until:
        queryset = queryset.filter(evaluated_at__lt=until)
    return queryset.order_by("evaluated_at", "created_at")


def _check_activation_event(result, event):
    if not event.workspace_id:
        result.count("missing_workspace_id")
    if not event.organization_id:
        result.count("missing_organization_id")
    if (
        event.workspace_id
        and event.organization_id
        and event.workspace.organization_id != event.organization_id
    ):
        result.count("workspace_org_mismatch")

    if event.event_name not in ONBOARDING_ACTIVATION_EVENTS:
        result.count("unknown_event")
    if (
        canonical_activation_event(event.event_name) != event.event_name
        or event.event_name in ONBOARDING_ACTIVATION_EVENT_ALIASES
    ):
        result.count("alias_leakage")

    if event.product_path:
        canonical = canonical_path(event.product_path)
        if canonical not in PRODUCT_PATHS or canonical != event.product_path:
            result.count("unknown_path")
    if event.activation_stage and event.activation_stage not in ACTIVATION_STAGES:
        result.count("unknown_stage")
    for key in GOAL_METADATA_KEYS:
        goal = (event.metadata or {}).get(key)
        if goal and canonical_goal(goal) not in ONBOARDING_GOALS:
            result.count("unknown_goal")
            break
    for key in RECOMMENDATION_METADATA_KEYS:
        action_id = (event.metadata or {}).get(key)
        if action_id and action_id not in ACTION_IDS:
            result.count("unknown_recommendation")
            break
    if _has_sensitive_metadata(event.metadata):
        result.count("sensitive_metadata_events")
    if event.event_name in SAMPLE_EVENT_NAMES and not event.is_sample:
        result.count("sample_event_without_sample_flag")
    if event.event_name in REAL_ACTIVATION_EVENT_NAMES and (
        event.is_sample or event.product_path == "sample"
    ):
        result.count("sample_activation")
    if not event.source:
        result.count("missing_source")


def _target_event_complete(log):
    if not log.target_success_event:
        return False
    sample_policy = (log.registry_snapshot or {}).get("sample_policy")
    return OnboardingActivationEvent.no_workspace_objects.filter(
        organization=log.organization,
        workspace=log.workspace,
        event_name=log.target_success_event,
        is_sample=sample_policy == "sample_only",
    ).exists()


def _check_lifecycle_log(result, log):
    if (
        log.workspace_id
        and log.organization_id
        and (log.workspace.organization_id != log.organization_id)
    ):
        result.count("lifecycle_workspace_org_mismatch")
    if log.primary_path:
        canonical = canonical_path(log.primary_path)
        if canonical not in PRODUCT_PATHS or canonical != log.primary_path:
            result.count("unknown_path")
    if log.activation_stage and log.activation_stage not in ACTIVATION_STAGES:
        result.count("unknown_stage")
    if (
        log.target_success_event
        and log.target_success_event not in ONBOARDING_ACTIVATION_EVENTS
    ):
        result.count("unknown_target_event")
    for action_id in (log.recommendation_id, log.target_action_id):
        if action_id and action_id not in ACTION_IDS:
            result.count("unknown_recommendation")
            break
    if log.eligible_at and log.evaluated_at and log.eligible_at > log.evaluated_at:
        result.count("negative_lifecycle_duration")
    if log.status == OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE:
        if not log.target_url:
            result.count("eligible_missing_target_url")
        if _target_event_complete(log):
            result.count("eligible_completed_target")
    if log.suppression_reason == "route_unavailable":
        result.count("route_unavailable_primary")
    if log.status == OnboardingLifecycleEvaluationLog.STATUS_ERROR:
        result.count("lifecycle_error")


def _check_duplicate_lifecycle_candidates(result, *, since, until):
    queryset = _lifecycle_query(since=since, until=until).filter(
        status=OnboardingLifecycleEvaluationLog.STATUS_ELIGIBLE,
        campaign_key__isnull=False,
    )
    seen = set()
    for log in queryset:
        key = (log.user_id, log.workspace_id, log.campaign_key)
        if key in seen:
            result.count("duplicate_lifecycle_candidate")
        else:
            seen.add(key)


def check_onboarding_analytics_quality(*, since=None, until=None):
    since = since or (timezone.now() - timedelta(days=7))
    until = until or timezone.now()
    result = OnboardingAnalyticsQualityResult(since=since, until=until)

    for event in _event_query(since=since, until=until):
        result.events_checked += 1
        _check_activation_event(result, event)

    for log in _lifecycle_query(since=since, until=until):
        result.lifecycle_logs_checked += 1
        _check_lifecycle_log(result, log)

    _check_duplicate_lifecycle_candidates(result, since=since, until=until)

    return result
