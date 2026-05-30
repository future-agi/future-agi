import uuid

from django.utils import timezone

from accounts.serializers.onboarding import ActivationStateResponseSerializer
from accounts.services.onboarding.constants import (
    ACTIVATION_SCHEMA_VERSION,
    EMAIL_CONTEXT_STATUSES,
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
from accounts.services.onboarding.journey_plan import resolve_journey_plan
from accounts.services.onboarding.lifecycle_eligibility import (
    evaluate_lifecycle_decision,
    lifecycle_preview_from_decision,
)
from accounts.services.onboarding.recommendations import (
    WRITE_STAGES,
    resolve_recommended_action,
)
from accounts.services.onboarding.route_availability import resolve_route_availability
from accounts.services.onboarding.sample_project import get_sample_project_state
from accounts.services.onboarding.signal_resolver import (
    OnboardingSignals,
    collect_onboarding_signals,
)


def _empty_signals():
    return OnboardingSignals(first_checks={})


def _base_stage(context, flags, signals):
    return resolve_stage_from_config(context=context, flags=flags, signals=signals)


def _stage_for_context(context, flags, signals):
    stage = _base_stage(context, flags, signals)
    if context.permissions["permission_limited"] and stage in WRITE_STAGES:
        return "permission_limited"
    return stage


def _available_paths(context, flags, routes, sample_project):
    selected_path = context.primary_path
    path_ids = ["observe", "sample"]
    if selected_path and selected_path not in path_ids:
        path_ids.insert(0, selected_path)

    paths = []
    for path_id in path_ids:
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
    sample_project = get_sample_project_state(
        user=context.user,
        organization=context.organization,
        workspace=context.workspace,
        is_enabled=bool(flags.get("onboarding_sample_project")),
        can_create=context.permissions["can_write"],
    )
    stage = _stage_for_context(context, flags, signals)
    if stage == "waiting_for_first_trace_sample_available" and not sample_project.get(
        "available"
    ):
        stage = "waiting_for_first_trace"
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
        "diagnostics": None,
        "warnings": context.warnings,
    }
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


def resolve_activation_state_for_request(request):
    context = resolve_onboarding_context(request)
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
