from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from django.core.exceptions import ImproperlyConfigured

from accounts.services.onboarding.constants import (
    ACTIVATION_STAGES,
    ONBOARDING_ACTIVATION_EVENTS,
)
from accounts.services.onboarding.flow_config import get_activation_flow_config
from accounts.services.onboarding.signal_resolver import OnboardingSignals

CONFIG_PATH = Path(__file__).with_name("activation_export.yml")
SAMPLE_POLICIES = {"real_only", "sample_only", "allow_sample"}


def _config_error(message: str) -> ImproperlyConfigured:
    return ImproperlyConfigured(
        f"Invalid onboarding activation export config: {message}"
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


def _validate_unique_text_list(value: Any, path: str) -> set[str]:
    items = _sequence(value, path)
    seen = set()
    for index, item in enumerate(items):
        if not isinstance(item, str) or not item.strip():
            raise _config_error(f"{path}.{index} must be a non-empty string.")
        if item in seen:
            raise _config_error(f"{path} contains duplicate value: {item}.")
        seen.add(item)
    return seen


def _validate_payload_allowlist(config: dict) -> None:
    allowlist = _mapping(config.get("payload_allowlist"), "payload_allowlist")
    safe_signal_keys = _validate_unique_text_list(
        allowlist.get("signal_keys"),
        "payload_allowlist.signal_keys",
    )
    signal_fields = {field.name for field in fields(OnboardingSignals)}
    unknown_signals = safe_signal_keys - signal_fields
    if unknown_signals:
        unknown = ", ".join(sorted(unknown_signals))
        raise _config_error(f"payload_allowlist.signal_keys unknown values: {unknown}.")

    _validate_unique_text_list(
        allowlist.get("action_keys"),
        "payload_allowlist.action_keys",
    )
    _validate_unique_text_list(
        config.get("blocked_payload_keys"), "blocked_payload_keys"
    )


def _validate_cohort_rule(rule: dict, path: str, activation_config: dict) -> None:
    _required_text(rule, "cohort_key", path)
    _required_text(rule, "description", path)
    _required_text(rule, "owner", path)
    primary_path = _required_text(rule, "primary_path", path)
    configured_paths = set(activation_config["paths"])
    if primary_path != "any" and primary_path not in configured_paths:
        raise _config_error(f"{path}.primary_path references unknown path.")

    stages = _sequence(rule.get("stages"), f"{path}.stages")
    if not stages:
        raise _config_error(f"{path}.stages cannot be empty.")
    for stage in stages:
        if stage not in ACTIVATION_STAGES:
            raise _config_error(f"{path}.stages contains unknown stage: {stage}.")

    signal_equals = _mapping(rule.get("signal_equals"), f"{path}.signal_equals")
    signal_fields = {field.name for field in fields(OnboardingSignals)}
    for signal_name in signal_equals:
        if signal_name not in signal_fields:
            raise _config_error(
                f"{path}.signal_equals references unknown signal: {signal_name}."
            )

    target_action_id = _required_text(rule, "target_action_id", path)
    if target_action_id not in activation_config["actions"]:
        raise _config_error(f"{path}.target_action_id references unknown action.")
    target_success_event = _required_text(rule, "target_success_event", path)
    if target_success_event not in ONBOARDING_ACTIVATION_EVENTS:
        raise _config_error(f"{path}.target_success_event is not supported.")
    _required_positive_int(rule, "priority", path)

    sample_policy = rule.get("sample_policy", "allow_sample")
    if sample_policy not in SAMPLE_POLICIES:
        raise _config_error(f"{path}.sample_policy is not supported.")


def _validate_config(config: dict) -> None:
    _required_text(config, "schema_version", CONFIG_PATH.name)
    _validate_unique_text_list(config.get("paid_plan_values"), "paid_plan_values")
    _validate_payload_allowlist(config)

    activation_config = get_activation_flow_config()
    rules = _sequence(config.get("cohort_rules"), "cohort_rules")
    seen = set()
    for index, rule in enumerate(rules):
        rule = _mapping(rule, f"cohort_rules.{index}")
        _validate_cohort_rule(rule, f"cohort_rules.{index}", activation_config)
        key = rule["cohort_key"]
        if key in seen:
            raise _config_error(f"Duplicate cohort_key: {key}.")
        seen.add(key)


@lru_cache(maxsize=1)
def get_activation_export_config() -> dict:
    config = _load_config_file()
    _validate_config(config)
    return config


def activation_export_paid_plan_values() -> frozenset[str]:
    return frozenset(get_activation_export_config()["paid_plan_values"])


def activation_export_safe_signal_keys() -> frozenset[str]:
    return frozenset(get_activation_export_config()["payload_allowlist"]["signal_keys"])


def activation_export_safe_action_keys() -> frozenset[str]:
    return frozenset(get_activation_export_config()["payload_allowlist"]["action_keys"])


def activation_export_blocked_payload_keys() -> frozenset[str]:
    return frozenset(get_activation_export_config()["blocked_payload_keys"])


def _rule_matches(rule: dict, activation_state: dict) -> bool:
    primary_path = activation_state.get("primary_path")
    if rule["primary_path"] != "any" and rule["primary_path"] != primary_path:
        return False
    if activation_state.get("stage") not in rule["stages"]:
        return False

    signals = activation_state.get("signals") or {}
    for signal_name, expected_value in rule["signal_equals"].items():
        if signals.get(signal_name) != expected_value:
            return False
    return True


def matching_activation_export_cohorts(activation_state: dict) -> tuple[dict, ...]:
    matches = []
    for rule in get_activation_export_config()["cohort_rules"]:
        if not _rule_matches(rule, activation_state):
            continue
        matches.append(
            {
                "cohort_key": rule["cohort_key"],
                "description": rule["description"],
                "owner": rule["owner"],
                "target_action_id": rule["target_action_id"],
                "target_success_event": rule["target_success_event"],
                "priority": rule["priority"],
            }
        )
    matches.sort(key=lambda item: (-item["priority"], item["cohort_key"]))
    return tuple(deepcopy(matches))
