from accounts.services.onboarding.flow_config import (
    configured_action,
    configured_stage,
    configured_write_stages,
)

WRITE_STAGES = configured_write_stages()


def _route(routes, key):
    return routes.get(key, {"href": "", "is_available": False, "reason": "missing_id"})


def _action(
    *,
    action_id,
    kind,
    title,
    description,
    route_key,
    routes,
    cta_label,
    priority,
    fallback_href,
    estimated_minutes=None,
    requires_permission=None,
    completion_event=None,
    is_sample=False,
    target_path=None,
    source="home",
    blocked_reason=None,
):
    route = _route(routes, route_key)
    route_available = bool(route["is_available"])
    blocked = bool(blocked_reason) or not route_available
    reason = blocked_reason or route.get("reason")
    href = route["href"] if route_available else None
    return {
        "id": action_id,
        "kind": kind,
        "title": title,
        "description": description,
        "href": href,
        "cta_label": cta_label,
        "estimated_minutes": estimated_minutes,
        "priority": priority,
        "blocked": blocked,
        "blocked_reason": reason if blocked else None,
        "requires_permission": requires_permission,
        "completion_event": completion_event,
        "is_sample": is_sample,
        "route_available": route_available,
        "fallback_href": fallback_href,
        "analytics": {
            "event_name": "onboarding_recommended_action_clicked",
            "source": source,
            "target_path": target_path,
        },
    }


def configured_activation_action(action_id, routes):
    config = configured_action(action_id)
    fallback_route_key = config.get("fallback_route_key") or "get_started"
    return _action(
        action_id=action_id,
        kind=config["kind"],
        title=config["title"],
        description=config["description"],
        route_key=config["route_key"],
        routes=routes,
        cta_label=config["cta_label"],
        priority=config["priority"],
        fallback_href=_route(routes, fallback_route_key)["href"],
        estimated_minutes=config.get("estimated_minutes"),
        requires_permission=config.get("requires_permission"),
        completion_event=config.get("completion_event"),
        is_sample=bool(config.get("is_sample")),
        target_path=config.get("target_path"),
        source=config.get("source", "home"),
        blocked_reason=config.get("blocked_reason"),
    )


def _fallback_for_stage(stage, flags, routes):
    stage_config = configured_stage(stage)
    for flag, action_id in stage_config.get("flagged_fallback_actions", {}).items():
        if flags.get(flag):
            candidate = configured_activation_action(action_id, routes)
            if not candidate["blocked"]:
                return candidate
    return configured_activation_action(stage_config["fallback_action"], routes)


def resolve_recommended_action(*, context, flags, signals, stage, routes):
    if stage == "activated" and context.primary_path == "prompt":
        return (
            configured_activation_action("open_prompt_metrics", routes),
            configured_activation_action("open_prompt_workbench", routes),
        )
    if stage == "activated" and context.primary_path == "agent":
        return (
            configured_activation_action("open_agent_quality", routes),
            configured_activation_action("open_agent_playground", routes),
        )
    if stage == "activated" and context.primary_path == "gateway":
        return (
            configured_activation_action("open_gateway_logs", routes),
            configured_activation_action("open_gateway_overview", routes),
        )
    if stage == "activated" and context.primary_path == "evals":
        if (
            signals.eval_has_failures
            and not signals.eval_has_failure_action
            and context.permissions["can_write"]
        ):
            return (
                configured_activation_action("fix_eval_source", routes),
                configured_activation_action("open_eval_usage", routes),
            )
        return (
            configured_activation_action("open_eval_usage", routes),
            configured_activation_action("open_evals", routes),
        )
    if stage == "activated" and context.primary_path == "voice":
        return (
            configured_activation_action("voice_monitor_calls", routes),
            configured_activation_action("open_voice_agents", routes),
        )
    fallback = _fallback_for_stage(stage, flags, routes)
    action_id = configured_stage(stage)["recommended_action"]
    action = configured_activation_action(action_id, routes)
    return action, fallback
