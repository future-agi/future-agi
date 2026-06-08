from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from django.db.models import Q

from accounts.services.onboarding.activation_events import (
    events_for_workspace,
    latest_event,
)
from accounts.services.onboarding.quality_actions import (
    open_quality_actions_for_context,
)
from accounts.services.onboarding.route_availability import route_entry

REVIEW_EVENTS = (
    "daily_quality_item_reviewed",
    "daily_quality_top_change_reviewed",
    "daily_quality_action_completed",
)
WEEKLY_REVIEW_COMPLETED_EVENTS = ("weekly_quality_review_completed",)
WEEKLY_REVIEW_ROUTE = "/dashboard/home?mode=weekly-review"

PATH_ORDER = ("observe", "prompt", "agent", "gateway", "evals", "voice")
SUPPORTED_PATHS = set(PATH_ORDER)
SEVERITY_RANK = {"critical": 3, "warning": 2, "info": 1}

PATH_CARD_META = {
    "prompt": {"label": "Prompts", "healthy_summary": "Review prompt metrics"},
    "agent": {"label": "Agents", "healthy_summary": "Review agent runs"},
    "observe": {"label": "Observe", "healthy_summary": "Review observe"},
    "gateway": {"label": "Gateway", "healthy_summary": "Review gateway logs"},
    "evals": {"label": "Evals", "healthy_summary": "Review eval runs"},
    "voice": {"label": "Voice", "healthy_summary": "Monitor voice calls"},
}

PATH_REVIEW_ACTIONS = {
    "prompt": {
        "id": "open_prompt_metrics",
        "label": "Review prompt metrics",
        "body": "Inspect prompt test output and decide the next prompt change.",
        "route_keys": ("prompt_metrics", "prompt_workbench"),
        "source_type": "prompt",
        "source_attr": "latest_prompt_id",
    },
    "agent": {
        "id": "open_agent_quality",
        "label": "Review agent runs",
        "body": "Inspect recent agent runs and keep eval coverage current.",
        "route_keys": ("agent_quality", "agent_list"),
        "source_type": "agent",
        "source_attr": "agent_id",
    },
    "gateway": {
        "id": "open_gateway_logs",
        "label": "Review gateway logs",
        "body": "Inspect recent gateway requests, latency, cost, and routing behavior.",
        "route_keys": ("gateway_log_review", "gateway_overview"),
        "source_type": "gateway_request",
        "source_attr": "gateway_request_log_id",
    },
    "evals": {
        "id": "review_eval_runs",
        "label": "Review eval runs",
        "body": "Inspect recent eval results and pick the next source improvement.",
        "route_keys": ("eval_review_failures", "eval_list"),
        "source_type": "eval_run",
        "source_attr": "eval_run_id",
    },
    "voice": {
        "id": "monitor_voice_calls",
        "label": "Monitor voice calls",
        "body": "Review recent voice calls and keep success criteria fresh.",
        "route_keys": ("voice_monitor_calls", "voice_list"),
        "source_type": "voice_call",
        "source_attr": "voice_call_execution_id",
    },
}

SIGNAL_ACTION_LABELS = {
    "trace_failure": ("review_failed_trace", "Review trace"),
    "prompt_quality_review": ("review_prompt_quality", "Review prompt"),
    "agent_run_issue": ("review_agent_run_issue", "Review run"),
    "gateway_request_issue": ("review_gateway_request_issue", "Review request"),
    "eval_failure": ("review_eval_failures", "Review failures"),
    "voice_call_issue": ("review_voice_call_issue", "Review call"),
}


@dataclass(frozen=True)
class DailyQualityResult:
    state: dict
    recommended_action: dict | None
    route_availability: dict = field(default_factory=dict)


def _internal_route(href):
    return isinstance(href, str) and href.startswith("/") and not href.startswith("//")


def _route(routes, key, fallback="/dashboard/get-started"):
    return routes.get(key) or route_entry(
        fallback, is_available=False, reason="missing_id"
    )


def _path(context, path=None):
    return path or context.primary_path or "observe"


def _first_available_route(routes, keys, fallback="/dashboard/get-started"):
    fallback_route = route_entry(fallback, is_available=False, reason="missing_id")
    for key in keys:
        route = _route(routes, key, fallback)
        href = route.get("href")
        if not _internal_route(href):
            continue
        if route.get("is_available"):
            return route, []
        if fallback_route.get("reason") == "missing_id":
            fallback_route = route
    return fallback_route, ["route_fallback_used"]


def _route_href(route, fallback="/dashboard/get-started"):
    href = route.get("href")
    return href if _internal_route(href) else fallback


def _last_reviewed_at(context, path=None):
    if not context.organization or not context.workspace:
        return None
    event = latest_event(
        organization=context.organization,
        workspace=context.workspace,
        event_names=REVIEW_EVENTS,
        product_path=_path(context, path),
        is_sample=False,
    )
    return event.occurred_at if event else None


def _first_quality_loop_at(context, path=None):
    if not context.organization or not context.workspace:
        return None
    event = latest_event(
        organization=context.organization,
        workspace=context.workspace,
        event_names=["first_quality_loop_completed"],
        product_path=_path(context, path),
        is_sample=False,
    )
    return event.occurred_at if event else None


def _window(context, signals, now, path=None):
    last_reviewed_at = _last_reviewed_at(context, path)
    activated_at = _first_quality_loop_at(context, path)
    if not activated_at and signals.last_meaningful_event:
        activated_at = signals.last_meaningful_event.occurred_at
    earliest = now - timedelta(days=7)
    starts = [value for value in (last_reviewed_at, activated_at, earliest) if value]
    return {
        "last_reviewed_at": last_reviewed_at,
        "start_at": max(starts) if starts else earliest,
        "end_at": now,
    }


def _weekly_window(now):
    return {
        "start_at": now - timedelta(days=7),
        "end_at": now,
    }


def _recent_event_count(context, *, event_names, window, path=None):
    if not context.organization or not context.workspace:
        return 0
    events = events_for_workspace(
        organization=context.organization,
        workspace=context.workspace,
        event_names=event_names,
        product_path=_path(context, path),
        is_sample=False,
        limit=500,
    )
    return sum(
        1
        for event in events
        if window["start_at"] < event.occurred_at <= window["end_at"]
    )


def _trace_queryset(context):
    from tracer.models.trace import Trace

    return (
        Trace.no_workspace_objects.filter(
            project__organization=context.organization,
            project__workspace=context.workspace,
            project__trace_type="observe",
        )
        .exclude(project__source="sample")
        .filter(
            Q(project__metadata__is_sample__isnull=True)
            | Q(project__metadata__is_sample=False)
        )
        .filter(Q(metadata__is_sample__isnull=True) | Q(metadata__is_sample=False))
    )


def _trace_failure_signal(context, review_window):
    trace = (
        _trace_queryset(context)
        .filter(
            created_at__gt=review_window["start_at"],
            created_at__lte=review_window["end_at"],
            error__isnull=False,
        )
        .select_related("project")
        .order_by("-created_at", "id")
        .first()
    )
    if not trace or trace.error in ({}, [], ""):
        return None, {}

    route = f"/dashboard/observe/{trace.project_id}/trace/{trace.id}"
    return (
        {
            "id": f"trace_failure:{trace.id}",
            "type": "trace_failure",
            "severity": "critical",
            "title": "Review the latest failed trace",
            "body": "A real observe trace failed since the last quality review.",
            "source_type": "trace",
            "source_id": str(trace.id),
            "project_id": str(trace.project_id),
            "route": route,
            "is_sample": False,
            "created_at": trace.created_at,
        },
        {
            "daily_quality_signal": route_entry(route),
        },
    )


def _in_window(value, review_window):
    if not value:
        return False
    return review_window["start_at"] < value <= review_window["end_at"]


def _path_loop_completed(context, signals, path=None):
    path = _path(context, path)
    if _first_quality_loop_at(context, path=path):
        return True
    if path == "observe":
        return signals.first_loop_completed
    if path == "prompt":
        return signals.prompt_first_loop_completed
    if path == "agent":
        return signals.agent_first_loop_completed
    if path == "gateway":
        return signals.gateway_first_loop_completed
    if path == "evals":
        return signals.eval_first_loop_completed
    if path == "voice":
        return signals.voice_first_loop_completed
    return False


def _path_sample_only(context, signals, path=None):
    path = _path(context, path)
    if path == "observe":
        return bool(
            signals.last_meaningful_event and signals.last_meaningful_event.is_sample
        )
    if path == "agent":
        return bool(signals.agent_signals.is_sample_only)
    if path == "gateway":
        return bool(signals.gateway_is_sample_only)
    if path == "evals":
        return bool(signals.eval_is_sample_only)
    if path == "voice":
        return bool(signals.voice_is_sample_only)
    return False


def _signal_route(routes, *keys):
    route, _diagnostics = _first_available_route(routes, keys)
    if route.get("is_available") and _internal_route(route.get("href")):
        return route
    return None


def _structured_signal(
    *,
    signal_id,
    signal_type,
    severity,
    title,
    body,
    source_type,
    source_id,
    route,
    created_at,
    path,
):
    return {
        "id": signal_id,
        "path": path,
        "type": signal_type,
        "severity": severity,
        "title": title,
        "body": body,
        "source_type": source_type,
        "source_id": str(source_id),
        "project_id": None,
        "route": route,
        "is_sample": False,
        "created_at": created_at,
    }


def _latest_prompt_activity(signals):
    candidates = [
        (
            signals.prompt_signals.latest_comparison_at,
            "comparison",
            "Review the latest prompt comparison",
            "A real prompt comparison is ready to inspect since the last quality check.",
        ),
        (
            signals.prompt_signals.latest_run_at,
            "run",
            "Review the latest prompt test",
            "A real prompt test ran since the last quality check.",
        ),
        (
            signals.prompt_signals.latest_version_at,
            "version",
            "Review the latest prompt version",
            "A real prompt version changed since the last quality check.",
        ),
    ]
    candidates = [candidate for candidate in candidates if candidate[0]]
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate[0])


def _prompt_signal(signals, routes, review_window):
    activity = _latest_prompt_activity(signals)
    if not activity:
        return None, {}
    created_at, activity_kind, title, body = activity
    if not _in_window(created_at, review_window):
        return None, {}
    route = _signal_route(routes, "prompt_metrics", "prompt_workbench")
    if not route:
        return None, {}
    source_id = signals.latest_prompt_id or signals.first_prompt_id
    if not source_id:
        return None, {}
    href = route["href"]
    return (
        _structured_signal(
            signal_id=f"prompt_quality_review:{activity_kind}:{source_id}",
            signal_type="prompt_quality_review",
            severity="info",
            title=title,
            body=body,
            source_type="prompt",
            source_id=source_id,
            route=href,
            created_at=created_at,
            path="prompt",
        ),
        {"daily_quality_signal": route_entry(href)},
    )


def _agent_signal(signals, routes, review_window):
    completed_at = signals.agent_signals.run_completed_at
    if not signals.agent_run_failed or not _in_window(completed_at, review_window):
        return None, {}
    route = _signal_route(routes, "agent_review_trace", "agent_quality")
    if not route:
        return None, {}
    source_id = (
        signals.agent_execution_id
        or signals.agent_graph_execution_id
        or signals.agent_call_execution_id
        or signals.agent_id
    )
    if not source_id:
        return None, {}
    href = route["href"]
    return (
        _structured_signal(
            signal_id=f"agent_run_issue:{source_id}",
            signal_type="agent_run_issue",
            severity="warning",
            title="Review the latest agent run issue",
            body="A real agent run needs review since the last quality check.",
            source_type="agent_run",
            source_id=source_id,
            route=href,
            created_at=completed_at,
            path="agent",
        ),
        {"daily_quality_signal": route_entry(href)},
    )


def _gateway_signal(signals, routes, review_window):
    started_at = signals.gateway_signals.request_started_at
    if not signals.gateway_request_is_error or not _in_window(
        started_at, review_window
    ):
        return None, {}
    route = _signal_route(routes, "gateway_failure", "gateway_log_review")
    if not route:
        return None, {}
    source_id = signals.gateway_request_log_id or signals.gateway_request_id
    if not source_id:
        return None, {}
    href = route["href"]
    return (
        _structured_signal(
            signal_id=f"gateway_request_issue:{source_id}",
            signal_type="gateway_request_issue",
            severity=(
                "critical"
                if (signals.gateway_request_status_code or 0) >= 500
                else "warning"
            ),
            title="Review the latest failed gateway request",
            body="A real gateway request failed since the last quality review.",
            source_type="gateway_request",
            source_id=source_id,
            route=href,
            created_at=started_at,
            path="gateway",
        ),
        {"daily_quality_signal": route_entry(href)},
    )


def _eval_signal(signals, routes, review_window):
    completed_at = signals.eval_run_completed_at
    if not signals.eval_has_failures or not _in_window(completed_at, review_window):
        return None, {}
    route = _signal_route(routes, "eval_review_failures")
    if not route:
        return None, {}
    source_id = signals.eval_run_id or signals.eval_scorer_template_id
    if not source_id:
        return None, {}
    href = route["href"]
    return (
        _structured_signal(
            signal_id=f"eval_failure:{source_id}",
            signal_type="eval_failure",
            severity="warning",
            title="Review the latest eval failures",
            body="A real eval run has failures to inspect.",
            source_type="eval_run",
            source_id=source_id,
            route=href,
            created_at=completed_at,
            path="evals",
        ),
        {"daily_quality_signal": route_entry(href)},
    )


def _voice_signal(signals, routes, review_window):
    completed_at = signals.voice_call_completed_at
    if not signals.voice_call_failed or not _in_window(completed_at, review_window):
        return None, {}
    route = _signal_route(routes, "voice_review_call")
    if not route:
        return None, {}
    source_id = signals.voice_call_execution_id or signals.voice_test_execution_id
    if not source_id:
        return None, {}
    href = route["href"]
    return (
        _structured_signal(
            signal_id=f"voice_call_issue:{source_id}",
            signal_type="voice_call_issue",
            severity="warning",
            title="Review the latest voice call issue",
            body="A real voice call needs review since the last quality check.",
            source_type="voice_call",
            source_id=source_id,
            route=href,
            created_at=completed_at,
            path="voice",
        ),
        {"daily_quality_signal": route_entry(href)},
    )


def _top_signal(context, signals, routes, review_window, path=None):
    path = _path(context, path)
    if path == "observe":
        signal, route_availability = _trace_failure_signal(context, review_window)
        if signal:
            signal = {**signal, "path": "observe"}
        return signal, route_availability
    if path == "agent":
        return _agent_signal(signals, routes, review_window)
    if path == "prompt":
        return _prompt_signal(signals, routes, review_window)
    if path == "gateway":
        return _gateway_signal(signals, routes, review_window)
    if path == "evals":
        return _eval_signal(signals, routes, review_window)
    if path == "voice":
        return _voice_signal(signals, routes, review_window)
    return None, {}


def _observe_route(routes):
    for key in ("observe_project", "observe_dashboard", "get_started"):
        route = _route(routes, key)
        if route.get("is_available") and _internal_route(route.get("href")):
            return route["href"], []
    return "/dashboard/get-started", ["route_fallback_used"]


def _request_access_action(context, path=None):
    path = _path(context, path)
    route = (
        context.permissions.get("request_access_href")
        or "/dashboard/settings/user-management"
    )
    if not _internal_route(route):
        route = "/dashboard/settings/user-management"
    return {
        "id": "request_workspace_access",
        "path": path,
        "label": "Request access",
        "body": "Ask an admin for workspace access before changing the quality setup.",
        "route": route,
        "fallback_route": "/dashboard/get-started",
        "route_available": True,
        "source_type": "workspace",
        "source_id": str(context.workspace.id) if context.workspace else None,
        "success_event": None,
        "is_primary": True,
        "is_sample": False,
        "requires_permission": None,
        "activation_kind": "request_access",
    }


def _next_action(context, signals, routes, path=None):
    path = _path(context, path)
    if path != "observe":
        return _path_review_action(context, signals, routes, path=path)

    route, diagnostics = _observe_route(routes)
    source_id = signals.first_observe_id or (
        str(context.workspace.id) if context.workspace else None
    )
    write_action = None
    if not signals.evaluator_exists:
        write_action = {
            "id": "create_trace_evaluator",
            "label": "Create quality check",
            "body": "Turn reviewed traces into repeatable quality coverage.",
            "success_event": "first_quality_loop_completed",
        }
    elif not signals.alert_exists:
        write_action = {
            "id": "create_trace_alert",
            "label": "Create alert",
            "body": "Get notified when a future trace needs attention.",
            "success_event": "first_quality_loop_completed",
        }
    elif not signals.dashboard_exists:
        write_action = {
            "id": "create_trace_dashboard",
            "label": "Create dashboard",
            "body": "Give the team one place to review trace health.",
            "success_event": "first_quality_loop_completed",
        }
    elif not signals.saved_view_exists:
        write_action = {
            "id": "save_trace_view",
            "label": "Save trace view",
            "body": "Keep the recurring trace review filter one click away.",
            "success_event": "first_quality_loop_completed",
        }

    if write_action and not context.permissions["can_write"]:
        return _request_access_action(context, path=path), ["permission_limited"]

    if not write_action:
        return (
            {
                "id": "open_observe_project",
                "path": path,
                "label": "Open observe",
                "body": "Review the current observe project and recent traces.",
                "route": route,
                "fallback_route": "/dashboard/get-started",
                "route_available": True,
                "source_type": "project" if signals.first_observe_id else "workspace",
                "source_id": source_id,
                "success_event": None,
                "is_primary": True,
                "is_sample": False,
                "requires_permission": None,
                "activation_kind": "daily_quality",
            },
            diagnostics,
        )

    return (
        {
            **write_action,
            "path": path,
            "route": route,
            "fallback_route": "/dashboard/get-started",
            "route_available": True,
            "source_type": "project" if signals.first_observe_id else "workspace",
            "source_id": source_id,
            "is_primary": True,
            "is_sample": False,
            "requires_permission": "observe:write",
            "activation_kind": "daily_quality",
        },
        [*diagnostics, "route_fallback_used"],
    )


def _path_review_action(context, signals, routes, path=None):
    path = _path(context, path)
    action_config = PATH_REVIEW_ACTIONS.get(path)
    if not action_config:
        return _request_access_action(context, path=path), ["path_changed"]

    route, diagnostics = _first_available_route(routes, action_config["route_keys"])
    source_id = getattr(signals, action_config["source_attr"], None) or (
        str(context.workspace.id) if context.workspace else None
    )
    return (
        {
            "id": action_config["id"],
            "path": path,
            "label": action_config["label"],
            "body": action_config["body"],
            "route": _route_href(route),
            "fallback_route": "/dashboard/get-started",
            "route_available": bool(route.get("is_available")),
            "source_type": action_config["source_type"],
            "source_id": str(source_id) if source_id else None,
            "success_event": None,
            "is_primary": True,
            "is_sample": False,
            "requires_permission": None,
            "activation_kind": "daily_quality",
        },
        diagnostics,
    )


def _signal_action(top_signal):
    action_id, label = SIGNAL_ACTION_LABELS.get(
        top_signal["type"],
        ("review_daily_quality_signal", "Review signal"),
    )
    return {
        "id": action_id,
        "path": top_signal["path"],
        "label": label,
        "body": top_signal["body"],
        "route": top_signal["route"],
        "fallback_route": "/dashboard/get-started",
        "route_available": True,
        "source_type": top_signal["source_type"],
        "source_id": top_signal["source_id"],
        "success_event": "daily_quality_item_reviewed",
        "is_primary": True,
        "is_sample": False,
        "requires_permission": None,
        "activation_kind": "daily_quality",
    }


def _product_card(*, path, mode, top_signal, action, routes):
    if top_signal:
        route = top_signal["route"]
    elif path == "observe":
        route = _route(routes, "observe_project").get("href") or _route(
            routes, "observe_dashboard"
        ).get("href")
    else:
        route = action.get("route")
    if not _internal_route(route):
        route = "/dashboard/get-started"
    meta = PATH_CARD_META.get(path, PATH_CARD_META["observe"])
    if top_signal:
        return {
            "path": path,
            "status": "needs_review",
            "label": meta["label"],
            "summary": top_signal["title"],
            "metric": "1",
            "change": "New since last review",
            "route": route,
        }
    if mode == "permission_limited":
        return {
            "path": path,
            "status": "permission_limited",
            "label": meta["label"],
            "summary": "Access needed for the next setup action",
            "metric": "View",
            "change": "No new signal",
            "route": route,
        }
    return {
        "path": path,
        "status": "healthy",
        "label": meta["label"],
        "summary": action["label"] or meta["healthy_summary"],
        "metric": "0",
        "change": "No new signal",
        "route": route,
    }


def _activation_action(action, target_path, fallback_href="/dashboard/get-started"):
    route_available = bool(action.get("route_available", True))
    return {
        "id": action["id"],
        "kind": action.get("activation_kind") or "daily_quality",
        "title": action["label"],
        "description": action["body"],
        "href": action.get("route") if route_available else None,
        "cta_label": action["label"],
        "estimated_minutes": 4,
        "priority": 100,
        "blocked": not route_available,
        "blocked_reason": None if route_available else "route_unavailable",
        "requires_permission": action.get("requires_permission"),
        "completion_event": action.get("success_event"),
        "is_sample": bool(action.get("is_sample")),
        "route_available": route_available,
        "fallback_href": action.get("fallback_route") or fallback_href,
        "analytics": {
            "event_name": "daily_quality_action_opened",
            "source": "daily_quality_home",
            "target_path": target_path,
        },
    }


def _active_paths(context, signals):
    paths = [
        path
        for path in PATH_ORDER
        if _path_loop_completed(context, signals, path=path)
        and not _path_sample_only(context, signals, path=path)
    ]
    current_path = _path(context)
    if current_path in paths:
        return [current_path, *[path for path in paths if path != current_path]]
    return paths


def _signal_sort_key(signal):
    return (
        SEVERITY_RANK.get(signal.get("severity"), 0),
        signal.get("created_at"),
        -PATH_ORDER.index(signal["path"]) if signal["path"] in PATH_ORDER else 0,
    )


def _signals_for_paths(context, signals, routes, now, active_paths):
    signals_by_path = {}
    route_availability_by_path = {}
    for path in active_paths:
        review_window = _window(context, signals, now, path=path)
        signal, route_availability = _top_signal(
            context,
            signals,
            routes,
            review_window,
            path=path,
        )
        if signal:
            signals_by_path[path] = signal
            route_availability_by_path[path] = route_availability

    if not signals_by_path:
        return None, {}, {}

    top_signal = max(signals_by_path.values(), key=_signal_sort_key)
    return (
        top_signal,
        route_availability_by_path.get(top_signal["path"], {}),
        signals_by_path,
    )


def _unavailable(reason, now):
    return {
        "mode": "unavailable",
        "last_reviewed_at": None,
        "window": {
            "start_at": now,
            "end_at": now,
        },
        "top_signal": None,
        "primary_action": None,
        "action_cards": [],
        "product_cards": [],
        "weekly_review": None,
        "digest_eligible": False,
        "digest_suppression_reason": reason,
        "diagnostics": [reason],
    }


def _weekly_review_state(
    context,
    flags,
    *,
    top_signal,
    open_actions,
    now,
    active_paths=None,
    unresolved_signal_count=None,
):
    window = _weekly_window(now)
    paths = active_paths or [_path(context)]
    completed_count = sum(
        _recent_event_count(
            context,
            event_names=REVIEW_EVENTS,
            window=window,
            path=path,
        )
        for path in paths
    )
    signal_count = (
        unresolved_signal_count
        if unresolved_signal_count is not None
        else 1
        if top_signal
        else 0
    )
    unresolved_count = len(open_actions) + signal_count
    last_completed = latest_event(
        organization=context.organization,
        workspace=context.workspace,
        event_names=WEEKLY_REVIEW_COMPLETED_EVENTS,
        product_path=_path(context),
        is_sample=False,
    )
    last_completed_at = last_completed.occurred_at if last_completed else None

    status = "not_due"
    due = False
    summary = "No weekly team review is due right now."
    if not flags.get("onboarding_weekly_team_review"):
        status = "flag_disabled"
        summary = "Weekly team review is disabled."
    elif context.permissions["permission_limited"]:
        status = "permission_limited"
        summary = "Workspace access is needed before weekly review."
    elif last_completed_at and last_completed_at > window["start_at"]:
        status = "completed_recently"
        summary = "Weekly team review is already complete for this window."
    elif not unresolved_count and not completed_count:
        status = "no_useful_signal"
        summary = "No unresolved quality work was found for this weekly window."
    elif unresolved_count:
        status = "due"
        due = True
        summary = "Review unresolved quality work with your team."
    else:
        summary = "Recent daily quality work is current."

    return {
        "due": due,
        "status": status,
        "route": WEEKLY_REVIEW_ROUTE,
        "window": window,
        "summary": summary,
        "unresolved_count": unresolved_count,
        "completed_count": completed_count,
        "last_completed_at": last_completed_at,
        "action_label": "Open weekly review" if due else None,
    }


def resolve_daily_quality_state(*, context, flags, signals, routes, stage, now):
    path = _path(context)
    if not flags.get("onboarding_daily_quality_home"):
        return DailyQualityResult(_unavailable("flag_disabled", now), None)
    if path not in SUPPORTED_PATHS:
        return DailyQualityResult(_unavailable("path_changed", now), None)

    active_paths = _active_paths(context, signals)
    if stage not in {"activated", "daily_review"} or not active_paths:
        return DailyQualityResult(_unavailable("not_activated", now), None)
    if path in active_paths and _path_sample_only(context, signals, path=path):
        return DailyQualityResult(_unavailable("sample_only", now), None)

    review_window = _window(context, signals, now, path=path)
    open_actions = open_quality_actions_for_context(context, now)
    top_signal, route_availability, signals_by_path = _signals_for_paths(
        context,
        signals,
        routes,
        now,
        active_paths,
    )
    diagnostics = []

    if top_signal:
        mode = "new_signal"
        primary_action = _signal_action(top_signal)
        digest_eligible = bool(flags.get("onboarding_email_daily_digest_enabled"))
        digest_suppression_reason = None if digest_eligible else "flag_disabled"
    elif open_actions:
        primary_action = {
            **open_actions[0],
            "path": path,
            "is_primary": True,
        }
        open_actions = [{"path": path, **action} for action in open_actions[1:]]
        mode = "open_action"
        digest_eligible = bool(flags.get("onboarding_email_daily_digest_enabled"))
        digest_suppression_reason = None if digest_eligible else "flag_disabled"
        diagnostics = ["open_action"]
    else:
        next_action_path = path if path in active_paths else active_paths[0]
        primary_action, diagnostics = _next_action(
            context,
            signals,
            routes,
            path=next_action_path,
        )
        mode = (
            "permission_limited"
            if "permission_limited" in diagnostics
            else "no_new_signal"
        )
        digest_eligible = False
        digest_suppression_reason = (
            "permission_limited" if mode == "permission_limited" else "no_useful_signal"
        )
        diagnostics = diagnostics or ["no_new_signal"]

    state = {
        "mode": mode,
        "last_reviewed_at": review_window["last_reviewed_at"],
        "window": {
            "start_at": review_window["start_at"],
            "end_at": review_window["end_at"],
        },
        "top_signal": top_signal,
        "primary_action": primary_action,
        "action_cards": open_actions,
        "product_cards": [
            _product_card(
                path=product_path,
                mode=(
                    "new_signal"
                    if signals_by_path.get(product_path)
                    else mode
                    if product_path == primary_action.get("path")
                    else "no_new_signal"
                ),
                top_signal=signals_by_path.get(product_path),
                action=(
                    primary_action
                    if product_path == primary_action.get("path")
                    else _next_action(
                        context,
                        signals,
                        routes,
                        path=product_path,
                    )[0]
                ),
                routes=routes,
            )
            for product_path in active_paths
        ],
        "weekly_review": _weekly_review_state(
            context,
            flags,
            top_signal=top_signal,
            open_actions=(
                [primary_action, *open_actions]
                if mode == "open_action"
                else open_actions
            ),
            now=now,
            active_paths=active_paths,
            unresolved_signal_count=len(signals_by_path),
        ),
        "digest_eligible": digest_eligible,
        "digest_suppression_reason": digest_suppression_reason,
        "diagnostics": diagnostics,
    }
    return DailyQualityResult(
        state=state,
        recommended_action=_activation_action(
            primary_action,
            primary_action.get("path") or path,
        ),
        route_availability=route_availability,
    )
