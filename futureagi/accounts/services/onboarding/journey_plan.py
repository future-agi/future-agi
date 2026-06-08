from accounts.services.onboarding.flow_config import configured_journey_for_path
from accounts.services.onboarding.recommendations import configured_activation_action

JOURNEY_STEP_COMPLETE = "complete"
JOURNEY_STEP_CURRENT = "current"
JOURNEY_STEP_QUEUED = "queued"
JOURNEY_STEP_STATUSES = (
    JOURNEY_STEP_COMPLETE,
    JOURNEY_STEP_CURRENT,
    JOURNEY_STEP_QUEUED,
)


def _active_stage_map(steps):
    mapping = {}
    for index, step in enumerate(steps):
        mapping[step["stage"]] = index
        for stage in step.get("active_stages", []):
            mapping[stage] = index
    return mapping


def _current_step_index(steps, stage):
    if stage in {"activated", "daily_review"}:
        return len(steps) - 1
    return _active_stage_map(steps).get(stage, 0)


def _step_status(index, current_index):
    if index < current_index:
        return JOURNEY_STEP_COMPLETE
    if index == current_index:
        return JOURNEY_STEP_CURRENT
    return JOURNEY_STEP_QUEUED


def resolve_journey_plan(*, primary_path, stage, routes):
    journey = configured_journey_for_path(primary_path)
    if not journey:
        return None

    steps = journey["steps"]
    current_index = _current_step_index(steps, stage)
    is_terminal_stage = stage in {"activated", "daily_review"}
    resolved_steps = []
    for index, step in enumerate(steps):
        action = configured_activation_action(step["action_id"], routes)
        href = action.get("href") or action.get("fallback_href") or "/dashboard/home"
        fallback_href = action.get("fallback_href") or href
        resolved_step = {
            "id": step["id"],
            "stage": step["stage"],
            "action_id": step["action_id"],
            "label": step["label"],
            "description": step["description"],
            "status": (
                JOURNEY_STEP_COMPLETE
                if is_terminal_stage
                else _step_status(index, current_index)
            ),
            "href": href,
            "fallback_href": fallback_href,
            "route_available": action["route_available"],
        }
        for key in (
            "success_event",
            "tour_anchor",
        ):
            value = step.get(key)
            if value:
                resolved_step[key] = value
        for key in (
            "blocked_reason",
            "requires_permission",
        ):
            value = action.get(key)
            if value:
                resolved_step[key] = value
        resolved_steps.append(resolved_step)

    current_step = resolved_steps[current_index]
    return {
        "id": journey["id"],
        "primary_path": journey["primary_path"],
        "eyebrow": journey["eyebrow"],
        "title": journey["title"],
        "description": journey["description"],
        "chips": journey.get("chips", []),
        "current_step_id": current_step["id"],
        "current_step_index": current_index,
        "steps": resolved_steps,
    }
