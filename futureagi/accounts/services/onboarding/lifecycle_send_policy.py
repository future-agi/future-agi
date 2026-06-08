from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from django.core.exceptions import ImproperlyConfigured

CONFIG_PATH = Path(__file__).with_name("lifecycle_send_policy.yml")

SUPPORTED_PREFERENCE_FIELDS = {
    "first_action_recovery_enabled",
    "sample_bridge_enabled",
    "next_loop_enabled",
    "daily_digest_enabled",
}
SUPPORTED_CAP_SCOPES = {
    "user",
    "workspace",
    "campaign_user",
    "campaign_key",
    "campaign_group",
}
SUPPORTED_EXTERNAL_CHANNELS = {"slack", "webhook"}


def _config_error(message: str) -> ImproperlyConfigured:
    return ImproperlyConfigured(f"Invalid onboarding lifecycle send policy: {message}")


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


def _optional_text_list(mapping: dict, key: str, path: str) -> tuple[str, ...]:
    value = mapping.get(key)
    if value is None or value == "":
        return ()
    values = _sequence(value, f"{path}.{key}")
    if not values:
        raise _config_error(f"{path}.{key} cannot be empty.")
    for index, item in enumerate(values):
        if not isinstance(item, str) or not item.strip():
            raise _config_error(f"{path}.{key}.{index} must be a non-empty string.")
    return tuple(values)


def _load_config_file() -> dict:
    try:
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _config_error(f"{CONFIG_PATH.name} could not be read.") from exc
    except yaml.YAMLError as exc:
        raise _config_error(f"{CONFIG_PATH.name} is not valid YAML.") from exc
    return _mapping(raw, CONFIG_PATH.name)


def _validate_preference_groups(config: dict) -> None:
    groups = _mapping(config.get("preference_groups"), "preference_groups")
    for group, field in groups.items():
        if not isinstance(group, str) or not group.strip():
            raise _config_error("preference_groups keys must be non-empty strings.")
        if field not in SUPPORTED_PREFERENCE_FIELDS:
            raise _config_error(
                f"preference_groups.{group} references unknown preference field."
            )


def _validate_frequency_caps(config: dict, key: str) -> None:
    seen = set()
    for index, cap in enumerate(_sequence(config.get(key), key)):
        path = f"{key}.{index}"
        cap = _mapping(cap, path)
        cap_id = _required_text(cap, "id", path)
        if cap_id in seen:
            raise _config_error(f"{key} has duplicate cap id: {cap_id}.")
        seen.add(cap_id)

        scope = _required_text(cap, "scope", path)
        if scope not in SUPPORTED_CAP_SCOPES:
            raise _config_error(f"{path}.scope is not supported.")

        window_hours = cap.get("window_hours")
        if window_hours is not None and (
            not isinstance(window_hours, int) or window_hours < 1
        ):
            raise _config_error(f"{path}.window_hours must be a positive integer.")

        limit = cap.get("limit")
        if not isinstance(limit, int) or limit < 1:
            raise _config_error(f"{path}.limit must be a positive integer.")

        _required_text(cap, "reason", path)
        _optional_text_list(cap, "campaign_keys", path)
        _optional_text_list(cap, "campaign_groups", path)
        _optional_text_list(cap, "frequency_cap_keys", path)


def _validate_send_environment(config: dict) -> None:
    environment = _mapping(config.get("send_environment"), "send_environment")
    if not isinstance(environment.get("cloud_required"), bool):
        raise _config_error("send_environment.cloud_required must be a boolean.")
    _required_text(
        environment,
        "non_cloud_suppression_reason",
        "send_environment",
    )


def _validate_external_delivery(config: dict) -> None:
    delivery = _mapping(config.get("external_delivery"), "external_delivery")
    channels = _optional_text_list(delivery, "channels", "external_delivery")
    for channel in channels:
        if channel not in SUPPORTED_EXTERNAL_CHANNELS:
            raise _config_error("external_delivery.channels contains unknown channel.")
    _optional_text_list(delivery, "campaign_groups", "external_delivery")


def _validate_config(config: dict) -> None:
    _required_text(config, "schema_version", CONFIG_PATH.name)
    _validate_preference_groups(config)
    _validate_frequency_caps(config, "eligibility_frequency_caps")
    _validate_frequency_caps(config, "send_frequency_caps")
    _validate_send_environment(config)
    _validate_external_delivery(config)


@lru_cache(maxsize=1)
def get_lifecycle_send_policy() -> dict:
    config = _load_config_file()
    _validate_config(config)
    return config


def lifecycle_preference_group_field(campaign_group: str | None) -> str | None:
    if not campaign_group:
        return None
    return get_lifecycle_send_policy()["preference_groups"].get(campaign_group)


def eligibility_frequency_caps() -> tuple[dict, ...]:
    return tuple(deepcopy(get_lifecycle_send_policy()["eligibility_frequency_caps"]))


def send_frequency_caps() -> tuple[dict, ...]:
    return tuple(deepcopy(get_lifecycle_send_policy()["send_frequency_caps"]))


def send_non_cloud_suppression_reason() -> str:
    return get_lifecycle_send_policy()["send_environment"][
        "non_cloud_suppression_reason"
    ]


def external_lifecycle_delivery_channels() -> tuple[str, ...]:
    return tuple(get_lifecycle_send_policy()["external_delivery"]["channels"])


def external_lifecycle_delivery_campaign_groups() -> tuple[str, ...]:
    return tuple(get_lifecycle_send_policy()["external_delivery"]["campaign_groups"])
