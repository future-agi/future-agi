import uuid
from dataclasses import replace

from django.utils import timezone

from accounts.serializers.onboarding import ActivationStateResponseSerializer
from accounts.services.onboarding.constants import (
    ACTIVATION_SCHEMA_VERSION,
    EMAIL_CONTEXT_STATUSES,
    PRODUCT_PATHS,
)
from accounts.services.onboarding.context import resolve_onboarding_context
from accounts.services.onboarding.daily_quality import resolve_daily_quality_state
from accounts.services.onboarding.feature_flags import get_onboarding_flags
from accounts.services.onboarding.flow_config import (
    configured_goal_options,
    configured_path,
    configured_stage_copy,
    configured_stage_home_mode,
    configured_stage_progress,
    resolve_stage_from_config,
)
from accounts.services.onboarding.goals import resolve_goal_for_context
from accounts.services.onboarding.journey_plan import resolve_journey_plan
from accounts.services.onboarding.lifecycle_eligibility import (
    evaluate_lifecycle_decision,
    lifecycle_preview_from_decision,
)
from accounts.services.onboarding.org_completion import (
    organization_has_completed_product_setup,
)
from accounts.services.onboarding.recommendations import (
    WRITE_STAGES,
    resolve_recommended_action,
)
from accounts.services.onboarding.route_availability import resolve_route_availability
from accounts.services.onboarding.sample_project import ensure_sample_project_ready
from accounts.services.onboarding.signal_resolver import (
    OnboardingSignals,
    collect_onboarding_signals,
)

DISCOVERY_PATH_IDS = (
    "observe",
    "prompt",
    "agent",
    "gateway",
    "evals",
    "voice",
    "sample",
)


def _discovery_path_ids(selected_path):
    path_ids = []
    for path_id in (selected_path, *DISCOVERY_PATH_IDS):
        if path_id and path_id in PRODUCT_PATHS and path_id not in path_ids:
            path_ids.append(path_id)
    return path_ids


def _empty_signals():
    return OnboardingSignals(first_checks={})


def _base_stage(context, flags, signals):
    return resolve_stage_from_config(context=context, flags=flags, signals=signals)


def _stage_for_context(context, flags, signals):
    stage = _base_stage(context, flags, signals)
    if stage not in {
        "feature_disabled",
        "workspace_missing",
        "activated",
        "daily_review",
    } and organization_has_completed_product_setup(
        context.organization,
        user=context.user,
        workspace=context.workspace,
    ):
        # This workspace has not completed its own loop; activation is inherited
        # from organization-level setup completion (the "suppress forced setup
        # for later users" gate). Flag the source so the activated home can show
        # honest, non-personal copy instead of implying this user personally
        # shipped a workflow.
        return "activated", "organization"
    if context.permissions["permission_limited"] and stage in WRITE_STAGES:
        return "permission_limited", None
    return stage, None


def _available_paths(context, flags, routes, sample_project):
    selected_path = context.primary_path

    paths = []
    for path_id in _discovery_path_ids(selected_path):
        path_config = configured_path(path_id)
        route = routes.get(f"path_{path_id}", {})
        is_available = bool(route.get("is_available"))
        status = "available" if is_available else "hidden"
        if path_id == selected_path and is_available:
            status = "selected"
        elif path_id == "sample" and (
            not flags.get("onboarding_sample_project")
            or sample_project.get("is_hidden")
            or not sample_project.get("available")
        ):
            status = "hidden"
            is_available = False
        paths.append(
            {
                "id": path_id,
                "label": path_config["label"],
                "description": path_config["description"],
                "status": status,
                "href": route.get("href") or f"/dashboard/home?path={path_id}",
                "is_available": is_available,
                "blocked_reason": None if is_available else route.get("reason"),
                "requires_permission": path_config["requires_permission"],
                "first_action_id": path_config["first_action_id"],
            }
        )
    return paths


def _email_eligibility(stage, flags, now, lifecycle=None):
    if lifecycle:
        suppressed = bool(lifecycle["suppressed"])
        return {
            "eligible": lifecycle["status"] == "eligible",
            "suppressed": suppressed,
            "suppression_reason": (
                lifecycle["suppression_reason"] if suppressed else None
            ),
            "next_email_key": lifecycle["template_key"],
            "next_email_after": lifecycle["eligible_at"],
            "digest_eligible": lifecycle.get("next_campaign_key")
            in {"first_loop_complete_next", "daily_quality_open_actions"},
            "last_email_sent_at": None,
            "frequency_cap_remaining": 0 if suppressed else 1,
            "dry_run_only": True,
        }

    suppressed = stage in {"feature_disabled", "workspace_missing", "activated"}
    reason = None
    if stage == "feature_disabled":
        reason = "feature_disabled"
    elif stage == "workspace_missing":
        reason = "workspace_suppressed"
    elif stage == "activated":
        reason = "activated"
    return {
        "eligible": not suppressed,
        "suppressed": suppressed,
        "suppression_reason": reason,
        "next_email_key": None if suppressed else f"{stage}_next",
        "next_email_after": None if suppressed else now,
        "digest_eligible": stage in {"activated", "daily_review"},
        "last_email_sent_at": None,
        "frequency_cap_remaining": 2,
        "dry_run_only": not bool(flags.get("onboarding_lifecycle_send_enabled")),
    }


def _lifecycle_preview(context, flags, payload, now):
    decision = evaluate_lifecycle_decision(
        user=context.user,
        organization=context.organization,
        workspace=context.workspace,
        activation_state=payload,
        flags=flags,
        now=now,
        source="activation_state_preview",
    )
    return lifecycle_preview_from_decision(decision, flags=flags)


def _apply_daily_quality_email_guardrail(email_eligibility, daily_quality):
    if not daily_quality:
        return email_eligibility
    if daily_quality["digest_eligible"]:
        return {
            **email_eligibility,
            "digest_eligible": True,
        }
    return {
        **email_eligibility,
        "eligible": False,
        "suppressed": True,
        "suppression_reason": daily_quality["digest_suppression_reason"],
        "next_email_key": None,
        "next_email_after": None,
        "digest_eligible": False,
        "frequency_cap_remaining": 0,
    }


def _last_event_payload(event):
    if not event:
        return None
    return {
        "name": event.event_name,
        "occurred_at": event.occurred_at,
        "is_sample": event.is_sample,
        "path": event.product_path or None,
        "metadata": event.metadata or {},
    }


_VALUE_SIGNAL_STAGES = frozenset({"review_first_trace", "activated", "daily_review"})
_VALUE_SIGNAL_KEYS = ("latency_ms", "cost", "total_tokens")


def _normalize_value_signal(raw):
    if not isinstance(raw, dict):
        return None
    if not all(key in raw for key in _VALUE_SIGNAL_KEYS):
        return None
    try:
        return {
            "latency_ms": int(raw["latency_ms"]),
            "cost": float(raw["cost"]),
            "total_tokens": int(raw["total_tokens"]),
            "kind": "observe_trace_metrics",
        }
    except (TypeError, ValueError):
        return None


def _value_signal_payload(organization, workspace):
    """Look up the most-recent real observe value signal for this workspace.

    The activated observe user's last meaningful event is
    ``first_quality_loop_completed`` (in evals), which carries no signal, so we
    look back to the real ``trace_reviewed`` event that stamped the observed
    trace's latency/cost/tokens. One cheap, scoped query. Returns the
    normalized signal dict or None on any issue (the panel falls back to the
    event label honestly)."""
    if organization is None or workspace is None:
        return None
    try:
        from accounts.services.onboarding.activation_events import (
            events_for_workspace,
        )

        events = events_for_workspace(
            organization=organization,
            workspace=workspace,
            event_names=["trace_reviewed"],
            is_sample=False,
            limit=20,
        )
        for event in events:
            signal = _normalize_value_signal((event.metadata or {}).get("signal"))
            if signal is not None:
                return signal
        return None
    except Exception:
        return None


def _pluralize(count, singular, plural=None):
    return singular if count == 1 else (plural or f"{singular}s")


def _metric(label, value):
    return {
        "label": label,
        "value": str(value),
    }


def _count(value, minimum=0):
    try:
        return max(minimum, int(value or 0))
    except (TypeError, ValueError):
        return minimum


def _safe_money(value):
    if value in {None, ""}:
        return None
    try:
        return f"${float(value):.4f}"
    except (TypeError, ValueError):
        return None


def _prompt_value_signal_payload(signals):
    prompt_signals = getattr(signals, "prompt_signals", None)
    if not prompt_signals or not prompt_signals.first_loop_completed:
        return None

    compared_versions = max(prompt_signals.comparable_version_count or 0, 2)
    quality_checks = max(prompt_signals.next_loop_action_count or 0, 1)
    version_label = _pluralize(compared_versions, "version")
    check_label = _pluralize(quality_checks, "quality check")
    return {
        "kind": "prompt_quality_loop",
        "headline": "Prompt comparison complete",
        "summary": (
            f"{compared_versions} {version_label} compared · "
            f"{quality_checks} {check_label} ready"
        ),
        "metrics": [
            {
                "label": "Versions compared",
                "value": str(compared_versions),
            },
            {
                "label": "Quality checks ready",
                "value": str(quality_checks),
            },
        ],
    }


def _agent_value_signal_payload(signals):
    agent_signals = getattr(signals, "agent_signals", None)
    if not agent_signals or not agent_signals.first_loop_completed:
        return None

    runs = _count(agent_signals.run_count, minimum=1)
    scenarios = _count(agent_signals.scenario_count, minimum=1)
    evals_ready = 1 if agent_signals.has_eval_coverage else 0
    return {
        "kind": "agent_quality_loop",
        "headline": "Agent scenario covered",
        "summary": (
            f"{runs} {_pluralize(runs, 'run')} reviewed · "
            f"{evals_ready} {_pluralize(evals_ready, 'eval')} ready"
        ),
        "metrics": [
            _metric("Runs reviewed", runs),
            _metric("Scenarios covered", scenarios),
            _metric("Evals ready", evals_ready),
        ],
    }


def _gateway_value_signal_payload(signals):
    gateway_signals = getattr(signals, "gateway_signals", None)
    if not gateway_signals or not gateway_signals.first_loop_completed:
        return None

    requests = _count(gateway_signals.request_count, minimum=1)
    policies = _count(gateway_signals.policy_count, minimum=1)
    metrics = [
        _metric("Requests routed", requests),
        _metric("Policies ready", policies),
    ]
    latency_ms = _count(gateway_signals.request_latency_ms)
    if latency_ms:
        metrics.append(_metric("Last latency", f"{latency_ms} ms"))
    cost = _safe_money(gateway_signals.request_cost)
    if cost:
        metrics.append(_metric("Last cost", cost))

    return {
        "kind": "gateway_routing_loop",
        "headline": "Gateway request controlled",
        "summary": (
            f"{requests} {_pluralize(requests, 'request')} routed · "
            f"{policies} {_pluralize(policies, 'policy', 'policies')} ready"
        ),
        "metrics": metrics,
    }


def _eval_value_signal_payload(signals):
    eval_signals = getattr(signals, "eval_signals", None)
    if not eval_signals or not eval_signals.first_loop_completed:
        return None

    runs = _count(eval_signals.run_count, minimum=1)
    failures = _count(eval_signals.failure_count)
    fix_actions = 1 if eval_signals.has_failure_action else 0
    if failures:
        outcome = (
            f"{failures} {_pluralize(failures, 'failure')} reviewed · "
            f"{fix_actions} {_pluralize(fix_actions, 'fix action')} ready"
        )
    else:
        outcome = "0 failures found"
    return {
        "kind": "eval_quality_loop",
        "headline": "Quality run reviewed",
        "summary": f"{runs} {_pluralize(runs, 'run')} reviewed · {outcome}",
        "metrics": [
            _metric("Runs reviewed", runs),
            _metric("Failures found", failures),
            _metric("Fix actions", fix_actions),
        ],
    }


def _voice_value_signal_payload(signals):
    voice_signals = getattr(signals, "voice_signals", None)
    if not voice_signals or not voice_signals.first_loop_completed:
        return None

    calls = _count(voice_signals.call_count, minimum=1)
    success_checks = 1 if voice_signals.has_success_criteria else 0
    interruptions = _count(voice_signals.call_interruption_count)
    metrics = [
        _metric("Calls reviewed", calls),
        _metric("Success checks ready", success_checks),
    ]
    if voice_signals.call_response_time_ms is not None:
        metrics.append(
            _metric(
                "Response time",
                f"{_count(voice_signals.call_response_time_ms)} ms",
            )
        )
    metrics.append(_metric("Interruptions caught", interruptions))
    return {
        "kind": "voice_quality_loop",
        "headline": "Voice call reviewed",
        "summary": (
            f"{calls} {_pluralize(calls, 'call')} reviewed · "
            f"{success_checks} {_pluralize(success_checks, 'success check')} ready"
        ),
        "metrics": metrics,
    }


def _path_value_signal_payload(primary_path, signals):
    if primary_path == "prompt":
        return _prompt_value_signal_payload(signals)
    if primary_path == "agent":
        return _agent_value_signal_payload(signals)
    if primary_path == "gateway":
        return _gateway_value_signal_payload(signals)
    if primary_path == "evals":
        return _eval_value_signal_payload(signals)
    if primary_path == "voice":
        return _voice_value_signal_payload(signals)
    return None


def _safe_internal_href(value):
    if not isinstance(value, str) or not value:
        return None
    if not value.startswith("/") or value.startswith("//"):
        return None
    return value


def _action_href(action):
    if not action:
        return None
    return _safe_internal_href(action.get("href")) or _safe_internal_href(
        action.get("fallback_href")
    )


def _email_status_to_context_status(status):
    if status in EMAIL_CONTEXT_STATUSES:
        return status
    if status in {"clicked", "fresh", "sent"}:
        return "current"
    if status == "missing":
        return "invalid"
    return None


def _resolve_email_context(*, context, stage, recommended_action, fallback_action):
    raw_context = context.email_context or {}
    if not raw_context:
        return None

    target_route = _safe_internal_href(raw_context.get("target_route"))
    email_status = raw_context.get("email_status")
    context_status = _email_status_to_context_status(
        raw_context.get("context_status") or email_status
    )
    stale_reason = raw_context.get("stale_reason")
    target_stage = raw_context.get("target_stage")

    if raw_context.get("target_route") and target_route is None:
        context_status = "route_unavailable"
        stale_reason = stale_reason or "route_unavailable"
    elif stale_reason and context_status in {None, "current"}:
        context_status = "stale"
    elif target_stage and target_stage != stage:
        context_status = "stale"
        stale_reason = stale_reason or "stage_changed"
    elif context_status is None:
        context_status = "current"

    resolved_href = (
        target_route
        if context_status == "current" and target_route
        else _action_href(recommended_action)
        or _action_href(fallback_action)
        or "/dashboard/home"
    )

    email_context = {
        "campaign_key": raw_context.get("campaign_key"),
        "email_key": raw_context.get("email_key"),
        "send_log_id": raw_context.get("send_log_id"),
        "email_status": email_status,
        "link_issued_at": raw_context.get("link_issued_at"),
        "target_stage": target_stage,
        "target_event": raw_context.get("target_event"),
        "target_route": target_route,
        "context_status": context_status,
        "stale_reason": stale_reason,
        "resolved_href": resolved_href,
    }
    return email_context


def _append_email_context_warning(payload, email_context):
    if not email_context:
        return
    status = email_context.get("context_status")
    if not status or status == "current":
        return
    warning = f"email_context_{status}"
    if warning not in payload["warnings"]:
        payload["warnings"].append(warning)


def _validate_payload(payload):
    serializer = ActivationStateResponseSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def resolve_activation_state(*, context, flags, signals):
    sample_project = ensure_sample_project_ready(
        user=context.user,
        organization=context.organization,
        workspace=context.workspace,
        is_enabled=bool(flags.get("onboarding_sample_project")),
        can_create=context.permissions["can_read"],
    )
    stage, activation_source = _stage_for_context(context, flags, signals)
    if stage == "waiting_for_first_trace_sample_available" and not sample_project.get(
        "available"
    ):
        stage = "waiting_for_first_trace"
    activated_via = (activation_source or "workspace") if stage == "activated" else None
    routes = resolve_route_availability(
        context=context,
        flags=flags,
        signals=signals,
        sample_project=sample_project,
    )
    recommended_action, fallback_action = resolve_recommended_action(
        context=context,
        flags=flags,
        signals=signals,
        stage=stage,
        routes=routes,
    )
    journey_plan = resolve_journey_plan(
        primary_path=context.primary_path,
        stage=stage,
        routes=routes,
    )
    now = timezone.now()
    is_activated = stage in {"activated", "daily_review"}
    payload = {
        "schema_version": ACTIVATION_SCHEMA_VERSION,
        "request_id": f"req_{uuid.uuid4().hex}",
        "server_time": now,
        "workspace_id": str(context.workspace.id) if context.workspace else None,
        "organization_id": (
            str(context.organization.id) if context.organization else None
        ),
        "user_id": str(context.user.id),
        "goal": context.selected_goal,
        "persona": context.persona,
        "primary_path": context.primary_path,
        "stage": stage,
        "stage_copy": configured_stage_copy(stage),
        "home_mode": configured_stage_home_mode(stage),
        "is_activated": is_activated,
        "activated_via": activated_via,
        "activated_at": (
            signals.last_meaningful_event.occurred_at
            if is_activated and signals.last_meaningful_event
            else None
        ),
        "recommended_action": recommended_action,
        "fallback_action": fallback_action,
        "progress": configured_stage_progress(stage),
        "signals": signals.to_payload(),
        "available_goals": configured_goal_options(),
        "available_paths": _available_paths(context, flags, routes, sample_project),
        "sample_project": sample_project,
        "prompt": (
            signals.prompt_signals.to_activation_prompt_state(stage)
            if context.primary_path == "prompt"
            else None
        ),
        "agent": (
            signals.agent_signals.to_activation_agent_state(stage)
            if context.primary_path == "agent"
            else None
        ),
        "eval": (
            signals.eval_signals.to_activation_eval_state(stage)
            if context.primary_path == "evals"
            else None
        ),
        "voice": (
            signals.voice_signals.to_activation_voice_state(stage)
            if context.primary_path == "voice"
            else None
        ),
        "gateway": (
            signals.gateway_signals.to_activation_gateway_state(stage)
            if context.primary_path == "gateway"
            else None
        ),
        "permissions": context.permissions,
        "feature_flags": flags,
        "route_availability": routes,
        "email_context": None,
        "last_meaningful_event": _last_event_payload(signals.last_meaningful_event),
        "value_signal": None,
        "diagnostics": None,
        "warnings": context.warnings,
    }
    if context.primary_path == "observe" and stage in _VALUE_SIGNAL_STAGES:
        payload["value_signal"] = _value_signal_payload(
            context.organization, context.workspace
        )
    elif context.primary_path in {
        "prompt",
        "agent",
        "gateway",
        "evals",
        "voice",
    } and stage in {
        "activated",
        "daily_review",
    }:
        payload["value_signal"] = _path_value_signal_payload(
            context.primary_path,
            signals,
        )
    if journey_plan:
        payload["journey_plan"] = journey_plan
    if payload["home_mode"] == "daily_quality":
        daily_quality = resolve_daily_quality_state(
            context=context,
            flags=flags,
            signals=signals,
            routes=payload["route_availability"],
            stage=stage,
            now=now,
        )
        payload["daily_quality"] = daily_quality.state
        payload["route_availability"].update(daily_quality.route_availability)
        if daily_quality.recommended_action:
            payload["recommended_action"] = daily_quality.recommended_action
    payload["email_context"] = _resolve_email_context(
        context=context,
        stage=stage,
        recommended_action=payload["recommended_action"],
        fallback_action=payload["fallback_action"],
    )
    _append_email_context_warning(payload, payload["email_context"])
    lifecycle = _lifecycle_preview(context, flags, payload, now)
    payload["lifecycle"] = lifecycle
    payload["email_eligibility"] = _apply_daily_quality_email_guardrail(
        _email_eligibility(stage, flags, now, lifecycle),
        payload.get("daily_quality"),
    )
    return _validate_payload(payload)


def _context_with_activation_event_scope(context, event_data=None):
    if not event_data:
        return context

    metadata = event_data.get("metadata") or {}
    goal_context = resolve_goal_for_context(
        user=context.user,
        organization=context.organization,
        workspace=context.workspace,
        requested_goal=metadata.get("quick_start_goal"),
        requested_primary_path=metadata.get("quick_start_primary_path"),
        source="setup_org",
    )
    if goal_context.get("source") != "setup_quick_start":
        return context

    return replace(
        context,
        primary_path=goal_context["primary_path"],
        selected_goal=goal_context["goal"],
        source="setup_org",
    )


def resolve_activation_state_for_request(request, event_data=None):
    context = resolve_onboarding_context(request)
    context = _context_with_activation_event_scope(context, event_data)
    flags = get_onboarding_flags(
        user=context.user,
        organization=context.organization,
        workspace=context.workspace,
    )
    if not flags.get("onboarding_activation_state_api"):
        signals = _empty_signals()
    elif not context.organization or not context.workspace:
        signals = _empty_signals()
    else:
        signals = collect_onboarding_signals(
            user=context.user,
            organization=context.organization,
            workspace=context.workspace,
        )
    return resolve_activation_state(context=context, flags=flags, signals=signals)
