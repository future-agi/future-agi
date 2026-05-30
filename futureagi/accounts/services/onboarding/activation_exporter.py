from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

from accounts.models import OnboardingPaidCloudActivationExportLog
from accounts.services.onboarding.activation_events import SENSITIVE_METADATA_KEYS
from accounts.services.onboarding.activation_plane import (
    ActivationExportDecision,
    activation_export_decision,
)
from accounts.services.onboarding.activation_state import resolve_activation_state
from accounts.services.onboarding.context import resolve_onboarding_context
from accounts.services.onboarding.feature_flags import get_onboarding_flags
from accounts.services.onboarding.lifecycle_candidates import lifecycle_candidates
from accounts.services.onboarding.signal_resolver import (
    OnboardingSignals,
    collect_onboarding_signals,
)

ACTIVATION_EXPORT_SCHEMA_VERSION = "onboarding-activation-export-2026-05-30.v1"

SAFE_SIGNAL_KEYS = {
    "provider_keys",
    "datasets",
    "evals",
    "eval_runs",
    "eval_source_count",
    "eval_scorer_count",
    "eval_group_count",
    "eval_run_count",
    "eval_failure_count",
    "eval_has_source",
    "eval_has_scorer",
    "eval_has_completed_run",
    "eval_has_failures",
    "eval_has_review",
    "eval_has_failure_action",
    "eval_first_loop_completed",
    "eval_is_sample_only",
    "eval_sample_source_count",
    "eval_permission_limited",
    "prompt_templates",
    "prompt_versions",
    "prompt_comparisons",
    "prompt_sample_templates",
    "prompt_run_exists",
    "prompt_committed_version_exists",
    "prompt_comparison_completed",
    "prompt_next_loop_action_exists",
    "prompt_first_loop_completed",
    "agents",
    "agent_prototype_runs",
    "agent_sample_count",
    "agent_has_agent",
    "agent_has_agent_version",
    "agent_has_scenario",
    "agent_has_run",
    "agent_run_failed",
    "agent_has_review",
    "agent_has_eval_coverage",
    "agent_multiple_scenarios",
    "agent_first_loop_completed",
    "agent_voice_feature_unavailable",
    "agent_permission_limited",
    "observe_projects",
    "traces",
    "trace_reviews",
    "gateway_keys",
    "gateway_requests",
    "gateway_policies",
    "gateway_available",
    "gateway_provider_count",
    "gateway_provider_model_count",
    "gateway_has_provider",
    "gateway_has_key",
    "gateway_has_request",
    "gateway_request_status_code",
    "gateway_request_is_error",
    "gateway_request_latency_ms",
    "gateway_request_cache_hit",
    "gateway_request_fallback_used",
    "gateway_request_guardrail_triggered",
    "gateway_has_review",
    "gateway_has_failure_repair",
    "gateway_has_policy",
    "gateway_policy_synced",
    "gateway_is_sample_only",
    "gateway_sample_request_count",
    "gateway_permission_limited",
    "gateway_guard_blocked",
    "gateway_first_loop_completed",
    "voice_agents",
    "voice_simulations",
    "voice_calls",
    "voice_reviews",
    "voice_has_agent",
    "voice_has_scenario",
    "voice_has_test",
    "voice_has_call",
    "voice_has_completed_call",
    "voice_call_failed",
    "voice_has_review",
    "voice_has_success_criteria",
    "voice_first_loop_completed",
    "voice_is_sample_only",
    "voice_sample_call_count",
    "voice_permission_limited",
    "team_invites",
    "dashboards",
    "alerts",
    "sample_project_opened",
    "sample_trace_available",
    "sample_signal_viewed",
    "sample_trace_reviewed",
}

SAFE_ACTION_KEYS = {
    "id",
    "kind",
    "label",
    "completion_event",
    "success_event",
    "product_path",
    "activation_stage",
    "route_key",
    "is_sample",
}

SENSITIVE_EXPORT_KEYS = SENSITIVE_METADATA_KEYS | {
    "gateway_key_prefix",
    "gateway_provider_credential_id",
    "provider_credential_id",
}


@dataclass(frozen=True)
class ActivationExportResult:
    status: str
    suppression_reason: str | None
    idempotency_key: str
    fact_payload: dict
    written: bool
    log: object | None = None


@dataclass(frozen=True)
class ActivationExportBatchResult:
    run_id: uuid.UUID
    evaluated: int
    written: int
    status_counts: dict
    suppression_counts: dict
    errors: list[dict]

    def to_payload(self):
        return {
            "run_id": str(self.run_id),
            "evaluated": self.evaluated,
            "written": self.written,
            "status_counts": self.status_counts,
            "suppression_counts": self.suppression_counts,
            "errors": self.errors,
        }


class _Request:
    def __init__(self, *, user, organization, workspace, source):
        self.user = user
        self.organization = organization
        self.workspace = workspace
        self.query_params = {"source": source}


def _json_safe(value):
    return json.loads(json.dumps(value, cls=DjangoJSONEncoder))


def _walk_keys(value):
    if isinstance(value, dict):
        for key, nested_value in value.items():
            yield str(key).lower()
            yield from _walk_keys(nested_value)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


def assert_activation_export_payload_safe(payload):
    unsafe_keys = SENSITIVE_EXPORT_KEYS.intersection(_walk_keys(payload))
    if unsafe_keys:
        raise ValidationError("Activation export payload contains sensitive keys.")
    _json_safe(payload)
    return payload


def _safe_signals(activation_state):
    signals = activation_state.get("signals") or {}
    if not isinstance(signals, dict):
        return {}
    return {
        key: _json_safe(signals[key])
        for key in sorted(SAFE_SIGNAL_KEYS)
        if key in signals
    }


def _safe_action(action):
    if not isinstance(action, dict):
        return None
    return {
        key: _json_safe(action[key])
        for key in sorted(SAFE_ACTION_KEYS)
        if key in action
    }


def _safe_route_availability(activation_state):
    routes = activation_state.get("route_availability") or {}
    if not isinstance(routes, dict):
        return {}
    safe_routes = {}
    for key, value in routes.items():
        if not isinstance(value, dict):
            continue
        safe_routes[str(key)] = {
            "is_available": bool(value.get("is_available")),
            "reason": value.get("reason"),
        }
    return _json_safe(safe_routes)


def _safe_lifecycle(activation_state):
    lifecycle = activation_state.get("lifecycle") or {}
    email_eligibility = activation_state.get("email_eligibility") or {}
    return _json_safe(
        {
            "campaign": {
                "next_campaign_key": lifecycle.get("next_campaign_key"),
                "campaign_group": lifecycle.get("campaign_group"),
                "template_key": lifecycle.get("template_key"),
                "template_version": lifecycle.get("template_version"),
                "status": lifecycle.get("status"),
                "suppression_reason": lifecycle.get("suppression_reason"),
            },
            "email_eligibility": {
                "eligible": email_eligibility.get("eligible"),
                "suppressed": email_eligibility.get("suppressed"),
                "suppression_reason": email_eligibility.get("suppression_reason"),
                "next_email_key": email_eligibility.get("next_email_key"),
                "digest_eligible": email_eligibility.get("digest_eligible"),
                "frequency_cap_remaining": email_eligibility.get(
                    "frequency_cap_remaining"
                ),
                "dry_run_only": email_eligibility.get("dry_run_only"),
            },
        }
    )


def _safe_activation(activation_state):
    return _json_safe(
        {
            "schema_version": activation_state.get("schema_version"),
            "goal": activation_state.get("goal"),
            "persona": activation_state.get("persona"),
            "primary_path": activation_state.get("primary_path"),
            "stage": activation_state.get("stage"),
            "home_mode": activation_state.get("home_mode"),
            "is_activated": activation_state.get("is_activated"),
            "activated_at": activation_state.get("activated_at"),
            "progress": activation_state.get("progress"),
            "recommended_action": _safe_action(
                activation_state.get("recommended_action")
            ),
            "fallback_action": _safe_action(activation_state.get("fallback_action")),
            "permissions": {
                "can_read": (activation_state.get("permissions") or {}).get("can_read"),
                "can_write": (activation_state.get("permissions") or {}).get(
                    "can_write"
                ),
                "can_manage_workspace": (activation_state.get("permissions") or {}).get(
                    "can_manage_workspace"
                ),
                "permission_limited": (activation_state.get("permissions") or {}).get(
                    "permission_limited"
                ),
            },
            "warnings": activation_state.get("warnings") or [],
        }
    )


def build_activation_export_payload(
    *,
    user,
    organization,
    workspace,
    activation_state,
    decision: ActivationExportDecision,
    evaluated_at,
):
    payload = {
        "schema_version": ACTIVATION_EXPORT_SCHEMA_VERSION,
        "evaluated_at": evaluated_at,
        "organization": {"id": str(organization.id)},
        "workspace": {"id": str(workspace.id)},
        "user": {"id": str(user.id)},
        "deployment": {
            "mode": decision.deployment_mode,
            "region": decision.deployment_region,
        },
        "subscription": {
            "plan_tier": decision.plan_tier,
            "status": decision.subscription_status,
        },
        "activation": _safe_activation(activation_state),
        "signals": _safe_signals(activation_state),
        "route_availability": _safe_route_availability(activation_state),
        "lifecycle": _safe_lifecycle(activation_state),
    }
    return assert_activation_export_payload_safe(_json_safe(payload))


def _suppressed_payload(*, user, organization, workspace, decision, evaluated_at):
    payload = {
        "schema_version": ACTIVATION_EXPORT_SCHEMA_VERSION,
        "evaluated_at": evaluated_at,
        "organization": {"id": str(organization.id)},
        "workspace": {"id": str(workspace.id)},
        "user": {"id": str(user.id)},
        "deployment": {
            "mode": decision.deployment_mode,
            "region": decision.deployment_region,
        },
        "subscription": {
            "plan_tier": decision.plan_tier,
            "status": decision.subscription_status,
        },
        "suppressed": {
            "reason": decision.suppression_reason,
        },
    }
    return assert_activation_export_payload_safe(_json_safe(payload))


def _event_cursor(activation_state):
    last_event = activation_state.get("last_meaningful_event") or {}
    return last_event.get("occurred_at") or activation_state.get("activated_at")


def _idempotency_key(*, user, workspace, activation_state, status, suppression_reason):
    cursor = _json_safe(_event_cursor(activation_state))
    natural_key = {
        "schema_version": ACTIVATION_EXPORT_SCHEMA_VERSION,
        "workspace_id": str(workspace.id),
        "user_id": str(user.id),
        "stage": activation_state.get("stage"),
        "primary_path": activation_state.get("primary_path"),
        "status": status,
        "suppression_reason": suppression_reason,
        "event_cursor": cursor,
    }
    digest = hashlib.sha256(
        json.dumps(natural_key, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return f"actexp:{digest}"


def export_activation_fact(
    *,
    user,
    organization,
    workspace,
    activation_state,
    run_id=None,
    source="activation_export",
    write=False,
    now=None,
):
    if organization is None or workspace is None:
        raise ValidationError("Organization and workspace are required.")
    if workspace.organization_id != organization.id:
        raise ValidationError("Workspace does not belong to organization.")

    now = now or timezone.now()
    run_id = run_id or uuid.uuid4()
    decision = activation_export_decision(organization)
    status = (
        OnboardingPaidCloudActivationExportLog.STATUS_READY
        if decision.allowed
        else OnboardingPaidCloudActivationExportLog.STATUS_SUPPRESSED
    )
    suppression_reason = None if decision.allowed else decision.suppression_reason
    idempotency_key = _idempotency_key(
        user=user,
        workspace=workspace,
        activation_state=activation_state,
        status=status,
        suppression_reason=suppression_reason,
    )
    if decision.allowed:
        fact_payload = build_activation_export_payload(
            user=user,
            organization=organization,
            workspace=workspace,
            activation_state=activation_state,
            decision=decision,
            evaluated_at=now,
        )
    else:
        fact_payload = _suppressed_payload(
            user=user,
            organization=organization,
            workspace=workspace,
            decision=decision,
            evaluated_at=now,
        )

    if not write:
        return ActivationExportResult(
            status=status,
            suppression_reason=suppression_reason,
            idempotency_key=idempotency_key,
            fact_payload=fact_payload,
            written=False,
        )

    log, _created = (
        OnboardingPaidCloudActivationExportLog.no_workspace_objects.update_or_create(
            workspace=workspace,
            idempotency_key=idempotency_key,
            defaults={
                "run_id": run_id,
                "user": user,
                "organization": organization,
                "deployment_mode": decision.deployment_mode,
                "region": decision.deployment_region,
                "plan_tier": decision.plan_tier,
                "schema_version": ACTIVATION_EXPORT_SCHEMA_VERSION,
                "event_cursor": _json_safe(_event_cursor(activation_state)) or "",
                "status": status,
                "suppression_reason": suppression_reason,
                "fact_payload": fact_payload,
                "evaluated_at": now,
                "metadata": {"source": source},
            },
        )
    )
    return ActivationExportResult(
        status=status,
        suppression_reason=suppression_reason,
        idempotency_key=idempotency_key,
        fact_payload=fact_payload,
        written=True,
        log=log,
    )


def _empty_signals():
    return OnboardingSignals(first_checks={})


def _activation_state_for_candidate(*, candidate, source):
    request = _Request(
        user=candidate.user,
        organization=candidate.organization,
        workspace=candidate.workspace,
        source=source,
    )
    context = resolve_onboarding_context(request)
    flags = get_onboarding_flags(
        user=context.user,
        organization=context.organization,
        workspace=context.workspace,
    )
    if not flags.get("onboarding_activation_state_api"):
        signals = _empty_signals()
    else:
        signals = collect_onboarding_signals(
            user=context.user,
            organization=context.organization,
            workspace=context.workspace,
        )
    activation_state = resolve_activation_state(
        context=context,
        flags=flags,
        signals=signals,
    )
    return context, activation_state


def run_onboarding_activation_export(
    *,
    limit=100,
    user_id=None,
    workspace_id=None,
    source="activation_export",
    write=False,
    run_id=None,
    now=None,
):
    now = now or timezone.now()
    run_id = run_id or uuid.uuid4()
    candidates = lifecycle_candidates(
        limit=limit,
        user_id=user_id,
        workspace_id=workspace_id,
    )

    status_counts = Counter()
    suppression_counts = Counter()
    errors = []
    written = 0

    for candidate in candidates:
        try:
            context, activation_state = _activation_state_for_candidate(
                candidate=candidate,
                source=source,
            )
            result = export_activation_fact(
                user=context.user,
                organization=context.organization,
                workspace=context.workspace,
                activation_state=activation_state,
                run_id=run_id,
                source=source,
                write=write,
                now=now,
            )
            status_counts[result.status] += 1
            if result.suppression_reason:
                suppression_counts[result.suppression_reason] += 1
            if result.written:
                written += 1
        except Exception as exc:
            status_counts["error"] += 1
            suppression_counts["activation_export_error"] += 1
            errors.append(
                {
                    "user_id": str(candidate.user.id),
                    "workspace_id": str(candidate.workspace.id),
                    "error": str(exc)[:500],
                }
            )

    return ActivationExportBatchResult(
        run_id=run_id,
        evaluated=len(candidates),
        written=written,
        status_counts=dict(status_counts),
        suppression_counts=dict(suppression_counts),
        errors=errors,
    )
