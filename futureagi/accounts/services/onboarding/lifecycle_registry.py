from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from django.core.exceptions import ImproperlyConfigured

from accounts.services.onboarding.constants import (
    ACTIVATION_STAGES,
    ONBOARDING_ACTIVATION_EVENTS,
)
from accounts.services.onboarding.feature_flag_contract import (
    SUPPORTED_ONBOARDING_FLAG_NAMES,
)
from accounts.services.onboarding.flow_config import get_activation_flow_config
from accounts.services.onboarding.lifecycle_template_contract import (
    lifecycle_template_contract_errors,
)

CONFIG_PATH = Path(__file__).with_name("lifecycle_campaigns.yml")

REQUIRED_FIELDS = (
    "campaign_key",
    "template_key",
    "template_version",
    "campaign_group",
    "email_subject",
    "email_preheader",
    "primary_path",
    "entry_stages",
    "wait_window_minutes",
    "priority",
    "target_action_id",
    "target_success_event",
    "route_strategy",
    "dry_run_flag",
    "send_flag",
    "frequency_cap_key",
    "sample_policy",
    "owner",
    "qa_fixture",
)

ROUTE_STRATEGIES = {
    "activation_recommendation",
    "home_choose_goal",
    "sample_project",
    "artifact_deep_link",
    "daily_quality",
    "cross_path_expansion",
}

SAMPLE_POLICIES = {"real_only", "sample_only", "allow_sample"}
DAILY_QUALITY_MODES = {
    "new_signal",
    "open_action",
    "no_new_signal",
    "permission_limited",
    "unavailable",
}
PRIMARY_PATHS_WITH_INTENTIONAL_ACTION_MISMATCH = {"observe_sample_bridge"}
TARGET_EVENTS_WITH_INTENTIONAL_ACTION_MISMATCH = {
    "daily_quality_open_actions",
    "observe_sample_bridge",
}
JOURNEY_TARGET_ACTION_MISMATCH_ALLOWED = {"observe_sample_bridge"}
JOURNEY_LIFECYCLE_POLICY_SKIP_REASONS = {
    "sample_only",
    "post_activation",
    "in_product_only",
}


def _config_error(message: str) -> ImproperlyConfigured:
    return ImproperlyConfigured(
        f"Invalid onboarding lifecycle campaign config: {message}"
    )


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


def _required_positive_int(mapping: dict, key: str, path: str) -> int:
    value = mapping.get(key)
    if not isinstance(value, int) or value < 0:
        raise _config_error(f"{path}.{key} must be a positive integer.")
    return value


def _load_config_file() -> dict:
    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _config_error(f"{CONFIG_PATH.name} could not be read.") from exc
    except yaml.YAMLError as exc:
        raise _config_error(f"{CONFIG_PATH.name} is not valid YAML.") from exc
    return _mapping(raw, CONFIG_PATH.name)


def _journey_steps_for_path(activation_config: dict, primary_path: str) -> list[dict]:
    for journey in activation_config["journeys"].values():
        if journey["primary_path"] == primary_path:
            return _sequence(journey.get("steps"), f"journeys.{primary_path}.steps")
    return []


def _step_matches_entry_stage(step: dict, entry_stage: str) -> bool:
    return entry_stage == step.get("stage") or entry_stage in step.get(
        "active_stages",
        [],
    )


def _validate_journey_alignment(
    campaign: dict,
    path: str,
    activation_config: dict,
) -> None:
    primary_path = campaign["primary_path"]
    if primary_path == "any" or campaign["route_strategy"] == "daily_quality":
        return

    journey_steps = _journey_steps_for_path(activation_config, primary_path)
    if not journey_steps:
        raise _config_error(f"{path}.primary_path has no configured journey.")

    entry_stages = campaign["entry_stages"]
    unmatched_stages = [
        stage
        for stage in entry_stages
        if not any(_step_matches_entry_stage(step, stage) for step in journey_steps)
    ]
    if unmatched_stages:
        raise _config_error(
            f"{path}.entry_stages are not present in the {primary_path} journey."
        )

    if campaign["campaign_key"] in JOURNEY_TARGET_ACTION_MISMATCH_ALLOWED:
        return

    reachable_actions = {
        step["action_id"]
        for step in journey_steps
        for stage in entry_stages
        if _step_matches_entry_stage(step, stage)
    }
    if campaign["target_action_id"] not in reachable_actions:
        raise _config_error(
            f"{path}.target_action_id is not reachable from its entry stages."
        )


def _validate_campaign(campaign: dict, path: str, activation_config: dict) -> None:
    for field in REQUIRED_FIELDS:
        if field not in campaign:
            raise _config_error(f"{path}.{field} is required.")

    _required_text(campaign, "campaign_key", path)
    template_key = _required_text(campaign, "template_key", path)
    if not template_key.endswith("_v1"):
        raise _config_error(f"{path}.template_key must include a version suffix.")
    _required_text(campaign, "template_version", path)
    _required_text(campaign, "campaign_group", path)
    for error in lifecycle_template_contract_errors(campaign):
        raise _config_error(f"{path}.{error}")
    primary_path = _required_text(campaign, "primary_path", path)
    configured_paths = set(activation_config["paths"])
    if primary_path not in configured_paths and primary_path != "any":
        raise _config_error(f"{path}.primary_path references unknown path.")
    _required_positive_int(campaign, "wait_window_minutes", path)
    _required_positive_int(campaign, "priority", path)
    target_action_id = _required_text(campaign, "target_action_id", path)
    actions = activation_config["actions"]
    if target_action_id not in actions:
        raise _config_error(f"{path}.target_action_id references unknown action.")
    if campaign["target_success_event"] not in ONBOARDING_ACTIVATION_EVENTS:
        raise _config_error(f"{path}.target_success_event is not supported.")
    target_action = actions[target_action_id]
    target_path = target_action.get("target_path")
    if (
        target_path
        and primary_path not in {target_path, "any"}
        and campaign["campaign_key"]
        not in PRIMARY_PATHS_WITH_INTENTIONAL_ACTION_MISMATCH
    ):
        raise _config_error(f"{path}.primary_path does not match target action path.")
    completion_event = target_action.get("completion_event")
    if (
        completion_event
        and campaign["target_success_event"] != completion_event
        and campaign["campaign_key"]
        not in TARGET_EVENTS_WITH_INTENTIONAL_ACTION_MISMATCH
    ):
        raise _config_error(
            f"{path}.target_success_event does not match target action completion."
        )
    route_strategy = _required_text(campaign, "route_strategy", path)
    if route_strategy not in ROUTE_STRATEGIES:
        raise _config_error(f"{path}.route_strategy is not supported.")
    expansion_target_paths = campaign.get("expansion_target_paths")
    if route_strategy == "cross_path_expansion":
        expansion_target_paths = _sequence(
            expansion_target_paths,
            f"{path}.expansion_target_paths",
        )
        if not expansion_target_paths:
            raise _config_error(f"{path}.expansion_target_paths cannot be empty.")
        for index, target_path in enumerate(expansion_target_paths):
            if target_path not in configured_paths:
                raise _config_error(
                    f"{path}.expansion_target_paths.{index} references unknown path."
                )
            first_action_id = activation_config["paths"][target_path].get(
                "first_action_id"
            )
            if not first_action_id or first_action_id not in actions:
                raise _config_error(
                    f"{path}.expansion_target_paths.{index} has no first action."
                )
    elif expansion_target_paths is not None:
        raise _config_error(
            f"{path}.expansion_target_paths requires cross_path_expansion."
        )
    dry_run_flag = _required_text(campaign, "dry_run_flag", path)
    if dry_run_flag not in SUPPORTED_ONBOARDING_FLAG_NAMES:
        raise _config_error(f"{path}.dry_run_flag references unknown feature flag.")
    send_flag = _required_text(campaign, "send_flag", path)
    if send_flag not in SUPPORTED_ONBOARDING_FLAG_NAMES:
        raise _config_error(f"{path}.send_flag references unknown feature flag.")
    _required_text(campaign, "frequency_cap_key", path)
    if campaign["sample_policy"] not in SAMPLE_POLICIES:
        raise _config_error(f"{path}.sample_policy is not supported.")
    _required_text(campaign, "owner", path)
    _required_text(campaign, "qa_fixture", path)

    stages = _sequence(campaign.get("entry_stages"), f"{path}.entry_stages")
    if not stages:
        raise _config_error(f"{path}.entry_stages cannot be empty.")
    for stage in stages:
        if stage not in ACTIVATION_STAGES:
            raise _config_error(f"{path}.entry_stages contains unknown stage.")
    _validate_journey_alignment(campaign, path, activation_config)

    modes = campaign.get("daily_quality_modes")
    if modes is not None:
        modes = _sequence(modes, f"{path}.daily_quality_modes")
        if not modes:
            raise _config_error(f"{path}.daily_quality_modes cannot be empty.")
        for mode in modes:
            if mode not in DAILY_QUALITY_MODES:
                raise _config_error(
                    f"{path}.daily_quality_modes contains unknown mode."
                )
    if "requires_digest_preview" in campaign and not isinstance(
        campaign["requires_digest_preview"],
        bool,
    ):
        raise _config_error(f"{path}.requires_digest_preview must be a boolean.")


def _validate_journey_campaign_coverage(
    campaigns: list[dict],
    activation_config: dict,
) -> None:
    covered = {}
    for campaign in campaigns:
        primary_path = campaign.get("primary_path")
        if (
            primary_path in {None, "any"}
            or campaign.get("route_strategy") == "daily_quality"
        ):
            continue
        for stage in campaign.get("entry_stages", []):
            covered.setdefault(primary_path, set()).add(stage)

    missing = []
    for journey_id, journey in activation_config["journeys"].items():
        primary_path = journey["primary_path"]
        for step in journey.get("steps", []):
            lifecycle_policy = step.get("lifecycle_policy", "campaign_required")
            if lifecycle_policy in JOURNEY_LIFECYCLE_POLICY_SKIP_REASONS:
                continue
            stages = {step["stage"], *step.get("active_stages", [])}
            if not stages & covered.get(primary_path, set()):
                missing.append(f"{journey_id}.{step['id']}")

    if missing:
        raise _config_error(
            "Every campaign_required journey step must have a lifecycle campaign: "
            + ", ".join(sorted(missing))
            + ". Add a matching campaign or set lifecycle_policy explicitly."
        )


def _validate_config(config: dict) -> None:
    _required_text(config, "schema_version", CONFIG_PATH.name)
    campaigns = _sequence(config.get("campaigns"), "campaigns")
    activation_config = get_activation_flow_config()
    seen = set()
    for index, campaign in enumerate(campaigns):
        campaign = _mapping(campaign, f"campaigns.{index}")
        _validate_campaign(campaign, f"campaigns.{index}", activation_config)
        key = campaign["campaign_key"]
        if key in seen:
            raise _config_error(f"Duplicate campaign_key: {key}.")
        seen.add(key)
    _validate_journey_campaign_coverage(campaigns, activation_config)


@lru_cache(maxsize=1)
def get_lifecycle_registry_config() -> dict:
    config = _load_config_file()
    _validate_config(config)
    return config


def lifecycle_campaigns() -> tuple[dict, ...]:
    return tuple(deepcopy(get_lifecycle_registry_config()["campaigns"]))


def lifecycle_campaign_by_key(campaign_key: str) -> dict | None:
    for campaign in lifecycle_campaigns():
        if campaign["campaign_key"] == campaign_key:
            return campaign
    return None
