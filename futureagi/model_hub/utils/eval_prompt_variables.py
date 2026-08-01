"""Helpers for keeping custom-eval prompt variables and required_keys aligned."""

from __future__ import annotations

from collections.abc import Iterable

from model_hub.utils.jinja_variables import extract_jinja_variables


def extract_eval_prompt_variables(config: dict | None) -> list[str]:
    """Extract input variables referenced by eval prompt fields."""
    if not isinstance(config, dict):
        return []

    texts: list[str] = []
    for key in ("rule_prompt", "system_prompt", "criteria"):
        value = config.get(key)
        if isinstance(value, str) and value:
            texts.append(value)

    messages = config.get("messages") or []
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str) and content:
                texts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        texts.append(part["text"])

    variables: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for variable in extract_jinja_variables(text):
            if variable not in seen:
                seen.add(variable)
                variables.append(variable)

    return variables


def sync_required_keys_from_prompt(
    config: dict | None,
    mapping: dict | None = None,
    extra_allowed_keys: Iterable[str] | None = None,
) -> dict | None:
    """Add mapped prompt variables to config.required_keys in place.

    Filter semantics: when either `mapping` or `extra_allowed_keys` is
    provided, prompt variables are only added if they appear in the
    combined allowed set. When both are None/empty, the filter is
    intentionally skipped and **every** prompt variable is promoted —
    this is the "allow-all" mode used by the write-side helper that
    trusts its caller to have already vetted the prompt. Callers that
    want strict filtering must pass a non-empty mapping/extras set;
    callers that want lax mode should pass nothing.
    """
    if not isinstance(config, dict):
        return config

    prompt_variables = extract_eval_prompt_variables(config)
    if not prompt_variables:
        return config

    allowed_keys = set(mapping or {})
    if extra_allowed_keys:
        allowed_keys.update(str(key) for key in extra_allowed_keys if key is not None)
    # Empty allowed_keys → allow-all (documented above). Explicit local so
    # the branch below reads intentionally instead of as a short-circuit.
    filter_by_allowed = bool(allowed_keys)

    required_keys = list(config.get("required_keys") or config.get("requiredKeys") or [])
    seen = set(required_keys)
    for variable in prompt_variables:
        if variable in seen:
            continue
        if filter_by_allowed and variable not in allowed_keys:
            continue
        required_keys.append(variable)
        seen.add(variable)

    config["required_keys"] = required_keys
    return config
