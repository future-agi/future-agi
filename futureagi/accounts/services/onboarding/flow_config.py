from __future__ import annotations

from collections import Counter
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from django.core.exceptions import ImproperlyConfigured

from accounts.services.onboarding.feature_flag_contract import (
    SUPPORTED_ONBOARDING_FLAG_NAMES,
)
from accounts.services.onboarding.route_contract import route_keys_for_paths
from accounts.services.onboarding.signal_contract import (
    SUPPORTED_ONBOARDING_STAGE_RULE_SIGNALS,
)

CONFIG_PATH = Path(__file__).with_name("activation_flow.yml")

HOME_MODES = {"first_run", "daily_quality", "fallback"}
JOURNEY_COPY_MAX_LENGTH = {
    "eyebrow": 48,
    "title": 120,
    "description": 240,
    "label": 64,
}
UNSAFE_COPY_MARKERS = ("{{", "{%", "://", "<script", "$(")
PROGRESS_STATES = {
    "not_started",
    "available",
    "selected",
    "in_progress",
    "blocked",
    "complete",
    "sample_only",
}
JOURNEY_LIFECYCLE_POLICIES = {
    "campaign_required",
    "sample_only",
    "post_activation",
    "in_product_only",
}

CONDITION_KEYS = {
    "always",
    "all",
    "any",
    "not",
    "flag_enabled",
    "flag_disabled",
    "signal",
    "signal_not",
    "missing",
    "missing_any",
    "context_equals",
    "context_not_equals",
}
CONTEXT_FIELDS = {
    "user",
    "organization",
    "workspace",
    "organization_role",
    "workspace_role",
    "organization_level",
    "workspace_level",
    "selected_goal",
    "primary_path",
    "persona",
    "source",
    "email_context",
    "permissions",
    "warnings",
}


def _config_error(message: str) -> ImproperlyConfigured:
    return ImproperlyConfigured(f"Invalid onboarding activation flow config: {message}")


def _mapping(value: Any, path: str) -> dict:
    if not isinstance(value, dict):
        raise _config_error(f"{path} must be a mapping.")
    return value


def _sequence(value: Any, path: str) -> list:
    if not isinstance(value, list):
        raise _config_error(f"{path} must be a list.")
    return value


def _required_text(mapping: dict, key: str, path: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _config_error(f"{path}.{key} must be a non-empty string.")
    return value


def _optional_text(mapping: dict, key: str, path: str) -> str | None:
    value = mapping.get(key)
    if value in {None, ""}:
        return None
    if not isinstance(value, str):
        raise _config_error(f"{path}.{key} must be a string.")
    return value


def _validate_supported_flag(flag: str, path: str) -> None:
    if not isinstance(flag, str) or not flag:
        raise _config_error(f"{path} must be a non-empty string.")
    if flag not in SUPPORTED_ONBOARDING_FLAG_NAMES:
        raise _config_error(f"{path} references unknown feature flag.")


def _validate_context_field(field: str, path: str) -> None:
    if not isinstance(field, str) or not field:
        raise _config_error(f"{path} must be a non-empty string.")
    if field not in CONTEXT_FIELDS:
        raise _config_error(f"{path} references unknown context field.")


def _validate_signal_name(signal: str, path: str) -> None:
    if not isinstance(signal, str) or not signal:
        raise _config_error(f"{path} must be a non-empty string.")
    if signal not in SUPPORTED_ONBOARDING_STAGE_RULE_SIGNALS:
        raise _config_error(f"{path} references unknown signal.")


def _validate_safe_copy(value: str, path: str, *, max_length: int) -> None:
    if len(value) > max_length:
        raise _config_error(f"{path} must be {max_length} characters or fewer.")
    normalized = value.lower()
    if any(marker in normalized for marker in UNSAFE_COPY_MARKERS):
        raise _config_error(f"{path} contains unsupported template or URL syntax.")


def _load_config_file() -> dict:
    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _config_error(f"{CONFIG_PATH.name} could not be read.") from exc
    except yaml.YAMLError as exc:
        raise _config_error(f"{CONFIG_PATH.name} is not valid YAML.") from exc

    return _mapping(raw, CONFIG_PATH.name)


def _merge_stage_defaults(config: dict) -> None:
    defaults = deepcopy(_mapping(config.get("stage_defaults"), "stage_defaults"))
    stages = _mapping(config.get("stages"), "stages")

    merged = {}
    for stage_id, stage_config in stages.items():
        merged_stage = deepcopy(defaults)
        merged_stage.update(_mapping(stage_config, f"stages.{stage_id}"))
        if "copy" in defaults or "copy" in stage_config:
            copy = deepcopy(defaults.get("copy", {}))
            copy.update(stage_config.get("copy") or {})
            merged_stage["copy"] = copy
        if "progress" in defaults or "progress" in stage_config:
            progress = deepcopy(defaults.get("progress", {}))
            progress.update(stage_config.get("progress") or {})
            merged_stage["progress"] = progress
        merged[stage_id] = merged_stage

    config["stages"] = merged


def _validate_goals(config: dict) -> None:
    goals = _mapping(config.get("goals"), "goals")
    paths = _mapping(config.get("paths"), "paths")
    default_goal_id = _optional_text(config, "default_goal_id", "activation_flow")
    if default_goal_id and default_goal_id not in goals:
        raise _config_error("default_goal_id references unknown goal.")

    for goal_id, goal_config in goals.items():
        path = f"goals.{goal_id}"
        goal_config = _mapping(goal_config, path)
        primary_path = _required_text(goal_config, "primary_path", path)
        if primary_path not in paths:
            raise _config_error(f"{path}.primary_path references unknown path.")
        _required_text(goal_config, "label", path)
        _required_text(goal_config, "description", path)
        outcome_preview = _required_text(goal_config, "outcome_preview", path)
        _validate_safe_copy(
            outcome_preview,
            f"{path}.outcome_preview",
            max_length=160,
        )
        minutes = goal_config.get("estimated_minutes")
        if minutes is not None and (not isinstance(minutes, int) or minutes < 1):
            raise _config_error(f"{path}.estimated_minutes must be a positive integer.")

    aliases = _mapping(config.get("goal_aliases", {}), "goal_aliases")
    for alias, canonical in aliases.items():
        if canonical not in goals:
            raise _config_error(f"goal_aliases.{alias} references unknown goal.")


def _validate_paths(config: dict) -> None:
    paths = _mapping(config.get("paths"), "paths")
    actions = _mapping(config.get("actions"), "actions")

    for path_id, path_config in paths.items():
        path = f"paths.{path_id}"
        path_config = _mapping(path_config, path)
        _required_text(path_config, "label", path)
        _required_text(path_config, "description", path)
        reactivation_label = _optional_text(
            path_config,
            "lifecycle_reactivation_label",
            path,
        )
        if reactivation_label:
            _validate_safe_copy(
                reactivation_label,
                f"{path}.lifecycle_reactivation_label",
                max_length=64,
            )
        first_action_id = _optional_text(path_config, "first_action_id", path)
        if first_action_id and first_action_id not in actions:
            raise _config_error(f"{path}.first_action_id references unknown action.")

    aliases = _mapping(config.get("path_aliases", {}), "path_aliases")
    for alias, canonical in aliases.items():
        if canonical not in paths:
            raise _config_error(f"path_aliases.{alias} references unknown path.")


def _validate_actions(config: dict) -> None:
    action_kinds = set(_sequence(config.get("action_kinds"), "action_kinds"))
    actions = _mapping(config.get("actions"), "actions")
    paths = _mapping(config.get("paths"), "paths")
    route_keys = route_keys_for_paths(paths)

    for action_id, action_config in actions.items():
        path = f"actions.{action_id}"
        action_config = _mapping(action_config, path)
        kind = _required_text(action_config, "kind", path)
        if kind not in action_kinds:
            raise _config_error(f"{path}.kind references unknown action kind.")
        _required_text(action_config, "title", path)
        _required_text(action_config, "description", path)
        route_key = _required_text(action_config, "route_key", path)
        if route_key not in route_keys:
            raise _config_error(f"{path}.route_key references unknown route.")
        _required_text(action_config, "cta_label", path)
        fallback_route_key = _required_text(action_config, "fallback_route_key", path)
        if fallback_route_key not in route_keys:
            raise _config_error(f"{path}.fallback_route_key references unknown route.")
        priority = action_config.get("priority")
        if not isinstance(priority, int):
            raise _config_error(f"{path}.priority must be an integer.")
        minutes = action_config.get("estimated_minutes")
        if minutes is not None and (not isinstance(minutes, int) or minutes < 1):
            raise _config_error(f"{path}.estimated_minutes must be a positive integer.")
        target_path = _optional_text(action_config, "target_path", path)
        if target_path and target_path not in paths:
            raise _config_error(f"{path}.target_path references unknown path.")


def _validate_stage(config: dict, stage_id: str, stage_config: dict) -> None:
    path = f"stages.{stage_id}"
    actions = _mapping(config.get("actions"), "actions")
    stages = _mapping(config.get("stages"), "stages")
    loop_steps = _mapping(config.get("product_loop_steps"), "product_loop_steps")

    home_mode = _required_text(stage_config, "home_mode", path)
    if home_mode not in HOME_MODES:
        raise _config_error(f"{path}.home_mode references unknown home mode.")

    copy = _mapping(stage_config.get("copy"), f"{path}.copy")
    _required_text(copy, "eyebrow", f"{path}.copy")
    _required_text(copy, "title", f"{path}.copy")
    _required_text(copy, "description", f"{path}.copy")

    progress = _mapping(stage_config.get("progress"), f"{path}.progress")
    if set(progress) != set(loop_steps):
        raise _config_error(f"{path}.progress must include every product loop step.")
    invalid_states = set(progress.values()) - PROGRESS_STATES
    if invalid_states:
        raise _config_error(f"{path}.progress uses unknown progress states.")

    for key in ("recommended_action", "fallback_action"):
        action_id = _required_text(stage_config, key, path)
        if action_id not in actions:
            raise _config_error(f"{path}.{key} references unknown action.")

    flagged = _mapping(
        stage_config.get("flagged_fallback_actions", {}),
        f"{path}.flagged_fallback_actions",
    )
    for flag, action_id in flagged.items():
        if not isinstance(flag, str) or not flag:
            raise _config_error(f"{path}.flagged_fallback_actions has invalid flag.")
        _validate_supported_flag(flag, f"{path}.flagged_fallback_actions.{flag}")
        if action_id not in actions:
            raise _config_error(
                f"{path}.flagged_fallback_actions references unknown action."
            )

    for next_stage in _sequence(stage_config.get("next", []), f"{path}.next"):
        if next_stage not in stages:
            raise _config_error(f"{path}.next references unknown stage.")


def _validate_stages(config: dict) -> None:
    stages = _mapping(config.get("stages"), "stages")
    if not stages:
        raise _config_error("stages cannot be empty.")
    for stage_id, stage_config in stages.items():
        _validate_stage(config, stage_id, _mapping(stage_config, f"stages.{stage_id}"))


def _validate_stage_rules(config: dict) -> None:
    stages = _mapping(config.get("stages"), "stages")
    rules = _sequence(config.get("stage_rules"), "stage_rules")
    for index, rule in enumerate(rules):
        path = f"stage_rules.{index}"
        rule = _mapping(rule, path)
        stage = _required_text(rule, "stage", path)
        if stage not in stages:
            raise _config_error(f"{path}.stage references unknown stage.")
        _validate_stage_rule_condition(_mapping(rule.get("when"), f"{path}.when"), path)


def _validate_stage_rule_condition(condition: dict, path: str) -> None:
    unknown = set(condition) - CONDITION_KEYS
    if unknown:
        names = ", ".join(sorted(unknown))
        raise _config_error(f"{path}.when contains unsupported condition: {names}.")
    if not condition:
        raise _config_error(f"{path}.when cannot be empty.")

    if "all" in condition:
        for index, nested in enumerate(_sequence(condition["all"], f"{path}.when.all")):
            _validate_stage_rule_condition(
                _mapping(nested, f"{path}.when.all.{index}"),
                f"{path}.when.all.{index}",
            )
    if "any" in condition:
        for index, nested in enumerate(_sequence(condition["any"], f"{path}.when.any")):
            _validate_stage_rule_condition(
                _mapping(nested, f"{path}.when.any.{index}"),
                f"{path}.when.any.{index}",
            )
    if "not" in condition:
        _validate_stage_rule_condition(
            _mapping(condition["not"], f"{path}.when.not"),
            f"{path}.when.not",
        )
    if "flag_enabled" in condition:
        _validate_supported_flag(condition["flag_enabled"], f"{path}.when.flag_enabled")
    if "flag_disabled" in condition:
        _validate_supported_flag(
            condition["flag_disabled"], f"{path}.when.flag_disabled"
        )
    if "signal" in condition:
        _validate_signal_name(condition["signal"], f"{path}.when.signal")
    if "signal_not" in condition:
        _validate_signal_name(condition["signal_not"], f"{path}.when.signal_not")
    if "missing" in condition:
        _validate_context_field(condition["missing"], f"{path}.when.missing")
    if "missing_any" in condition:
        for index, field in enumerate(
            _sequence(condition["missing_any"], f"{path}.when.missing_any")
        ):
            _validate_context_field(field, f"{path}.when.missing_any.{index}")
    for key in ("context_equals", "context_not_equals"):
        if key in condition:
            expected = _mapping(condition[key], f"{path}.when.{key}")
            if not expected:
                raise _config_error(f"{path}.when.{key} cannot be empty.")
            for field in expected:
                _validate_context_field(field, f"{path}.when.{key}.{field}")


def _validate_activation_events(config: dict) -> None:
    events = _mapping(config.get("activation_events"), "activation_events")
    names = _sequence(events.get("names"), "activation_events.names")
    if not names or not all(isinstance(name, str) and name for name in names):
        raise _config_error("activation_events.names must contain event names.")
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        raise _config_error(
            f"activation_events.names contains duplicate events: {', '.join(duplicates)}."
        )
    aliases = _mapping(events.get("aliases", {}), "activation_events.aliases")
    for alias, canonical in aliases.items():
        if canonical not in names:
            raise _config_error(
                f"activation_events.aliases.{alias} references unknown event."
            )


def _validate_journeys(config: dict) -> None:
    journeys = _mapping(config.get("journeys"), "journeys")
    paths = _mapping(config.get("paths"), "paths")
    stages = _mapping(config.get("stages"), "stages")
    actions = _mapping(config.get("actions"), "actions")
    events = set(
        _sequence(config["activation_events"]["names"], "activation_events.names")
    )
    anchors = set(_sequence(config.get("tour_anchors", []), "tour_anchors"))
    path_to_journey = {}

    if not journeys:
        raise _config_error("journeys cannot be empty.")

    for journey_id, journey_config in journeys.items():
        path = f"journeys.{journey_id}"
        journey_config = _mapping(journey_config, path)
        primary_path = _required_text(journey_config, "primary_path", path)
        if primary_path not in paths:
            raise _config_error(f"{path}.primary_path references unknown path.")
        if primary_path in path_to_journey:
            raise _config_error(
                f"{path}.primary_path duplicates {path_to_journey[primary_path]}."
            )
        path_to_journey[primary_path] = journey_id

        for copy_key in ("eyebrow", "title", "description"):
            value = _required_text(journey_config, copy_key, path)
            _validate_safe_copy(
                value,
                f"{path}.{copy_key}",
                max_length=JOURNEY_COPY_MAX_LENGTH[copy_key],
            )

        chips = _sequence(journey_config.get("chips", []), f"{path}.chips")
        for index, chip in enumerate(chips):
            if not isinstance(chip, str) or not chip.strip():
                raise _config_error(f"{path}.chips.{index} must be a non-empty string.")
            _validate_safe_copy(chip, f"{path}.chips.{index}", max_length=24)

        steps = _sequence(journey_config.get("steps"), f"{path}.steps")
        if not steps:
            raise _config_error(f"{path}.steps cannot be empty.")
        step_ids = []
        step_stages = []
        for index, step in enumerate(steps):
            step_path = f"{path}.steps.{index}"
            step = _mapping(step, step_path)
            step_id = _required_text(step, "id", step_path)
            step_ids.append(step_id)
            stage = _required_text(step, "stage", step_path)
            step_stages.append(stage)
            if stage not in stages:
                raise _config_error(f"{step_path}.stage references unknown stage.")
            for active_index, active_stage in enumerate(
                _sequence(step.get("active_stages", []), f"{step_path}.active_stages")
            ):
                if active_stage not in stages:
                    raise _config_error(
                        f"{step_path}.active_stages.{active_index} references unknown stage."
                    )
            action_id = _required_text(step, "action_id", step_path)
            if action_id not in actions:
                raise _config_error(f"{step_path}.action_id references unknown action.")
            success_event = _optional_text(step, "success_event", step_path)
            if success_event and success_event not in events:
                raise _config_error(
                    f"{step_path}.success_event references unknown activation event."
                )
            tour_anchor = _optional_text(step, "tour_anchor", step_path)
            if tour_anchor and tour_anchor not in anchors:
                raise _config_error(
                    f"{step_path}.tour_anchor references unknown tour anchor."
                )
            lifecycle_policy = _optional_text(step, "lifecycle_policy", step_path)
            if lifecycle_policy and lifecycle_policy not in JOURNEY_LIFECYCLE_POLICIES:
                raise _config_error(
                    f"{step_path}.lifecycle_policy references unknown policy."
                )
            for copy_key in ("label", "description"):
                value = _required_text(step, copy_key, step_path)
                _validate_safe_copy(
                    value,
                    f"{step_path}.{copy_key}",
                    max_length=JOURNEY_COPY_MAX_LENGTH[copy_key],
                )

        duplicate_step_ids = sorted(
            step_id for step_id, count in Counter(step_ids).items() if count > 1
        )
        if duplicate_step_ids:
            raise _config_error(
                f"{path}.steps contains duplicate ids: {', '.join(duplicate_step_ids)}."
            )
        duplicate_step_stages = sorted(
            stage for stage, count in Counter(step_stages).items() if count > 1
        )
        if duplicate_step_stages:
            raise _config_error(
                f"{path}.steps contains duplicate stages: {', '.join(duplicate_step_stages)}."
            )


def _validate_config(config: dict) -> None:
    _mapping(config.get("product_loop_steps"), "product_loop_steps")
    _validate_actions(config)
    _validate_paths(config)
    _validate_goals(config)
    _validate_stages(config)
    _validate_stage_rules(config)
    _validate_activation_events(config)
    _validate_journeys(config)


@lru_cache(maxsize=1)
def get_activation_flow_config() -> dict:
    config = _load_config_file()
    _merge_stage_defaults(config)
    _validate_config(config)
    return config


def configured_goal_ids() -> tuple[str, ...]:
    return tuple(get_activation_flow_config()["goals"].keys())


def configured_goal_aliases() -> dict[str, str]:
    return dict(get_activation_flow_config().get("goal_aliases", {}))


def configured_default_goal_id() -> str | None:
    return get_activation_flow_config().get("default_goal_id")


def configured_goal_primary_paths() -> dict[str, str]:
    return {
        goal_id: goal_config["primary_path"]
        for goal_id, goal_config in get_activation_flow_config()["goals"].items()
    }


def configured_goal_options() -> list[dict]:
    options = []
    for goal_id, goal_config in get_activation_flow_config()["goals"].items():
        options.append(
            {
                "id": goal_id,
                "goal": goal_id,
                "primary_path": goal_config["primary_path"],
                "label": goal_config["label"],
                "description": goal_config["description"],
                "outcome_preview": goal_config["outcome_preview"],
                "estimated_minutes": goal_config.get("estimated_minutes"),
                "disabled": False,
                "disabled_reason": None,
            }
        )
    return options


def configured_product_paths() -> tuple[str, ...]:
    return tuple(get_activation_flow_config()["paths"].keys())


def configured_path_aliases() -> dict[str, str]:
    return dict(get_activation_flow_config().get("path_aliases", {}))


def configured_path(path_id: str) -> dict:
    return deepcopy(get_activation_flow_config()["paths"][path_id])


def configured_stage_ids() -> tuple[str, ...]:
    return tuple(get_activation_flow_config()["stages"].keys())


def configured_stage(stage_id: str) -> dict:
    return deepcopy(get_activation_flow_config()["stages"][stage_id])


def configured_stage_copy(stage_id: str) -> dict:
    return deepcopy(get_activation_flow_config()["stages"][stage_id]["copy"])


def configured_stage_home_mode(stage_id: str) -> str:
    return get_activation_flow_config()["stages"][stage_id]["home_mode"]


def configured_stage_progress(stage_id: str) -> dict:
    return deepcopy(get_activation_flow_config()["stages"][stage_id]["progress"])


def configured_write_stages() -> set[str]:
    return {
        stage_id
        for stage_id, stage in get_activation_flow_config()["stages"].items()
        if stage.get("requires_write") is True
    }


def configured_action_kinds() -> tuple[str, ...]:
    return tuple(get_activation_flow_config()["action_kinds"])


def configured_action(action_id: str) -> dict:
    return deepcopy(get_activation_flow_config()["actions"][action_id])


def configured_journey_for_path(path_id: str | None) -> dict | None:
    if not path_id:
        return None
    for journey_id, journey_config in get_activation_flow_config()["journeys"].items():
        if journey_config["primary_path"] == path_id:
            return {"id": journey_id, **deepcopy(journey_config)}
    return None


def configured_activation_events() -> tuple[str, ...]:
    return tuple(get_activation_flow_config()["activation_events"]["names"])


def configured_activation_event_aliases() -> dict[str, str]:
    return dict(get_activation_flow_config()["activation_events"].get("aliases", {}))


def _context_value(context, field: str):
    return getattr(context, field)


def _is_missing(value) -> bool:
    return value is None or value == "" or value == []


def _condition_matches(condition: dict, *, context, flags: dict, signals) -> bool:
    results = []
    if "always" in condition:
        results.append(bool(condition["always"]))
    if "all" in condition:
        results.append(
            all(
                _condition_matches(
                    _mapping(item, "stage rule condition"),
                    context=context,
                    flags=flags,
                    signals=signals,
                )
                for item in _sequence(condition["all"], "stage rule all")
            )
        )
    if "any" in condition:
        results.append(
            any(
                _condition_matches(
                    _mapping(item, "stage rule condition"),
                    context=context,
                    flags=flags,
                    signals=signals,
                )
                for item in _sequence(condition["any"], "stage rule any")
            )
        )
    if "not" in condition:
        results.append(
            not _condition_matches(
                _mapping(condition["not"], "stage rule not"),
                context=context,
                flags=flags,
                signals=signals,
            )
        )
    if "flag_enabled" in condition:
        results.append(bool(flags.get(condition["flag_enabled"])))
    if "flag_disabled" in condition:
        results.append(not bool(flags.get(condition["flag_disabled"])))
    if "signal" in condition:
        results.append(bool(getattr(signals, condition["signal"])))
    if "signal_not" in condition:
        results.append(not bool(getattr(signals, condition["signal_not"])))
    if "missing" in condition:
        results.append(_is_missing(_context_value(context, condition["missing"])))
    if "missing_any" in condition:
        results.append(
            any(
                _is_missing(_context_value(context, field))
                for field in _sequence(
                    condition["missing_any"], "stage rule missing_any"
                )
            )
        )
    if "context_equals" in condition:
        expected = _mapping(condition["context_equals"], "stage rule context_equals")
        results.append(
            all(
                _context_value(context, field) == value
                for field, value in expected.items()
            )
        )
    if "context_not_equals" in condition:
        expected = _mapping(
            condition["context_not_equals"],
            "stage rule context_not_equals",
        )
        results.append(
            any(
                _context_value(context, field) != value
                for field, value in expected.items()
            )
        )

    if not results:
        raise _config_error("stage rule condition is empty or unsupported.")
    return all(results)


def resolve_stage_from_config(*, context, flags: dict, signals) -> str:
    for rule in get_activation_flow_config()["stage_rules"]:
        if _condition_matches(
            rule["when"], context=context, flags=flags, signals=signals
        ):
            return rule["stage"]
    raise _config_error("stage_rules did not resolve a stage.")
