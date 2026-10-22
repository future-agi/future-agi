import re

from pydantic import ValidationError

from agentcc.contracts.gateway_admin import GuardrailCheck
from agentcc.guardrail_names import RULE_PROVIDER_DEFAULTS, RULE_TO_REGISTRY

SAFE_AGENTCC_NAME_REGEX = re.compile(r"^[A-Za-z0-9_-]+$")
SAFE_AGENTCC_NAME_ERROR = (
    "Name can only contain letters, numbers, hyphens, and underscores"
)


def validate_safe_agentcc_name(value: str) -> str:
    cleaned = value.strip()
    if not SAFE_AGENTCC_NAME_REGEX.fullmatch(cleaned):
        raise ValueError(SAFE_AGENTCC_NAME_ERROR)
    return cleaned


# Key metadata names a tenant may never set: the names the gateway reads as
# authority or as one of the key's own fields. The platform sets org_id from
# the key's organization and the key's own fields from the key.
# This is the gateway's list, written once on each side:
# reservedMetadataKeys in agentcc-gateway/internal/auth/keystore.go, and
# test_reserved_key_metadata_parity.py checks the two are equal.
RESERVED_KEY_METADATA = frozenset(
    {
        "org_id",
        "access_groups",
        "type",
        "key_type",
        "role",
        "allow_policy_override",
        "team",
        "id",
        "name",
        "owner",
    }
)


def reserved_key_metadata(metadata) -> list[str]:
    """The reserved entries in a key's metadata, in order."""
    if not isinstance(metadata, dict):
        return []
    return sorted(key for key in metadata if key in RESERVED_KEY_METADATA)


# Org guardrail config and the gateway's environment.
#
# Org config never reads the gateway's environment: the gateway refuses a "${"
# in the org check settings find_check_env_reference names, and Django refuses
# to save one there. A "${" in any other string is plain text and saves: a
# blocklist word, an expression, a rule name, a nested value, a list item, a
# map key. No org config needs a reference: Django injects the org's own
# FutureAGI key, and the guardrail policy serializer stores secrets encrypted.
ENV_REFERENCE_MARKER = "${"

# The gateway's built-in guardrails, which its dynamic factory builds by name
# before it reads a check's config (newDynamicGuardrailFactory in
# agentcc-gateway/cmd/agentcc/main.go). None reads the environment.
GATEWAY_BUILTIN_GUARDRAILS = frozenset(
    {
        "pii-detection",
        "content-moderation",
        "keyword-blocklist",
        "input-validation",
        "prompt-injection",
        "secret-detection",
        "topic-restriction",
        "language-detection",
        "system-prompt-protection",
        "hallucination-detection",
        "data-leakage-prevention",
    }
)

# The futureagi settings in which the gateway refuses a "${", in the order
# futureagi.New checks them (internal/guardrails/futureagi/futureagi.go).
FUTUREAGI_RESOLVED_SETTINGS = ("api_key", "base_url", "secret_key")

# The providers external.IsExternalProviderConfig accepts
# (internal/guardrails/external/external.go).
EXTERNAL_GUARDRAIL_PROVIDERS = frozenset(
    {
        "lakera",
        "azure_content_safety",
        "presidio",
        "llama_guard",
        "bedrock_guardrails",
        "hiddenlayer",
        "dynamoai",
        "enkrypt",
        "ibm_ai",
        "pangea",
        "zscaler",
        "crowdstrike",
        "aporia",
        "lasso",
        "grayswan",
    }
)


def find_check_env_reference(name, check, path):
    """The path of a setting of one guardrail check that holds "${" where the
    gateway refuses one in org config, or None.

    `check` is a check entry ({"enabled", "action", "config": {...}}) and
    `name` its console name; `path` names the entry, and the setting is
    appended as ".config.<key>". The kind is found the way the gateway's
    dynamic factory finds it: a built-in by name first (console names mapped
    to the gateway's), then the config's provider (or, when the config names
    none, the one config push gives that console name), futureagi before a
    webhook or an expression, an external provider last. It does not read
    "enabled": a save's own settings are checked on or off, and a caller that
    counts only the checks a push switches on (check_is_switched_on) filters
    them first. The path names the field and never carries its value.
    """
    if not isinstance(check, dict):
        return None
    cfg = check.get("config")
    if not isinstance(cfg, dict):
        return None
    if not isinstance(name, str):
        name = None
    if RULE_TO_REGISTRY.get(name, name) in GATEWAY_BUILTIN_GUARDRAILS:
        return None
    if "provider" in cfg:
        provider = cfg["provider"] if isinstance(cfg["provider"], str) else None
    else:
        provider = RULE_PROVIDER_DEFAULTS.get(name)

    if provider == "futureagi":
        keys = FUTUREAGI_RESOLVED_SETTINGS
    elif isinstance(cfg.get("url"), str) or isinstance(cfg.get("expression"), str):
        return None  # a webhook or an expression: never resolved
    elif provider in EXTERNAL_GUARDRAIL_PROVIDERS:
        keys = sorted(cfg, key=str)
    else:
        return None

    for key in keys:
        value = cfg.get(key)
        if isinstance(value, str) and ENV_REFERENCE_MARKER in value:
            return f"{path}.config.{key}"
    return None


def check_is_switched_on(check, in_list):
    """Whether config push sends `check` switched on, so that the gateway
    builds it. A check in a rules list or a checks list without
    "enabled" is on (the push sends enabled true); one in a checks object
    without it is off (the push sends none, and the gateway reads none as
    off). The value is read as the gateway contract reads it, so "false" is
    off; a value the contract refuses counts as on, and that version is
    refused by the contract anyway."""
    if not isinstance(check, dict):
        return False
    value = check.get("enabled", True) if in_list else check.get("enabled")
    try:
        return GuardrailCheck.model_validate({"enabled": value}).enabled is True
    except ValidationError:
        return True


def find_checks_env_reference(checks, path, switched_on_only=False):
    """find_check_env_reference over a list of named checks (a rules list, a
    checks list, a guardrail policy's checks); entries are named by index.
    With `switched_on_only`, a check the push sends switched off is skipped."""
    if not isinstance(checks, list):
        return None
    for index, check in enumerate(checks):
        if switched_on_only and not check_is_switched_on(check, in_list=True):
            continue
        name = check.get("name") if isinstance(check, dict) else None
        found = find_check_env_reference(name, check, f"{path}[{index}]")
        if found:
            return found
    return None


def find_guardrail_env_reference(guardrails, path, switched_on_only=False):
    """find_check_env_reference over a guardrails section in every shape it is
    stored or pushed in: "checks" as an object keyed by name (sorted, so the
    result is deterministic) or as a list, and "rules" as a list.

    With `switched_on_only` (activating a version, and the stored value a
    guardrail save leaves in place), a check the push sends switched off is
    skipped: the gateway never builds it, so a "${" stored there reaches
    nothing."""
    if not isinstance(guardrails, dict):
        return None
    checks = guardrails.get("checks")
    if isinstance(checks, dict):
        for name in sorted(checks, key=str):
            check = checks[name]
            if switched_on_only and not check_is_switched_on(check, in_list=False):
                continue
            found = find_check_env_reference(name, check, f"{path}.checks.{name}")
            if found:
                return found
    else:
        found = find_checks_env_reference(checks, f"{path}.checks", switched_on_only)
        if found:
            return found
    return find_checks_env_reference(
        guardrails.get("rules"), f"{path}.rules", switched_on_only
    )


def _without_enabled(check):
    if not isinstance(check, dict):
        return check
    return {key: value for key, value in check.items() if key != "enabled"}


def _find_changed_in_list(checks, before, path):
    if not isinstance(checks, list):
        return None
    held = (
        [_without_enabled(check) for check in before]
        if isinstance(before, list)
        else []
    )
    for index, check in enumerate(checks):
        if _without_enabled(check) in held:
            continue
        name = check.get("name") if isinstance(check, dict) else None
        found = find_check_env_reference(name, check, f"{path}[{index}]")
        if found:
            return found
    return None


def find_changed_guardrail_env_reference(guardrails, before, path):
    """find_check_env_reference over the checks of a guardrails section that a
    save adds or changes against `before`, the section it replaces, whether
    they are switched on or off.

    A check is unchanged when the same key of `before` ("checks" or "rules")
    holds it under the same name with the same settings, its "enabled" aside:
    switching a check on or off is not a change here, so switching a stored
    check off is never refused, and switching one on is refused by the
    switched-on check instead. Changing anything else in a check that keeps a
    "${" is refused, even in the same save that switches it off."""
    if not isinstance(guardrails, dict):
        return None
    if not isinstance(before, dict):
        before = {}
    checks = guardrails.get("checks")
    before_checks = before.get("checks")
    if isinstance(checks, dict):
        held = before_checks if isinstance(before_checks, dict) else {}
        for name in sorted(checks, key=str):
            check = checks[name]
            if name in held and _without_enabled(held[name]) == _without_enabled(check):
                continue
            found = find_check_env_reference(name, check, f"{path}.checks.{name}")
            if found:
                return found
    else:
        found = _find_changed_in_list(checks, before_checks, f"{path}.checks")
        if found:
            return found
    return _find_changed_in_list(
        guardrails.get("rules"), before.get("rules"), f"{path}.rules"
    )


def env_reference_error(path):
    """The error for a guardrail setting that holds "${": the field, no value."""
    return (
        f'{path} holds an environment reference ("${{"). The gateway never '
        "reads an org's guardrail settings from its environment; set the value "
        "itself."
    )
