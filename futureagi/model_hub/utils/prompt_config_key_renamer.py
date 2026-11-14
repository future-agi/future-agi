"""
Utilities for migrating legacy camelCase prompt config keys to canonical snake_case.

This is used by management commands that perform one-off data migrations.
"""

from __future__ import annotations

from typing import Any


LEGACY_CAMELCASE_TO_SNAKE_CASE: dict[str, str] = {
    "voiceId": "voice_id",
    "responseFormat": "response_format",
    "topP": "top_p",
    "maxTokens": "max_tokens",
    "presencePenalty": "presence_penalty",
    "frequencyPenalty": "frequency_penalty",
    "modelDetail": "model_detail",
}


def rename_legacy_camelcase_keys(obj: Any) -> tuple[Any, bool]:
    """
    Recursively rename legacy camelCase keys to canonical snake_case within JSON-like data.

    Rules:
    - No-op if legacy keys are absent.
    - If both legacy and canonical keys exist in the same dict, keep canonical and drop legacy.
    - Idempotent: re-running produces the same output.
    """
    if isinstance(obj, list):
        changed = False
        new_list: list[Any] = []
        for item in obj:
            new_item, item_changed = rename_legacy_camelcase_keys(item)
            changed = changed or item_changed
            new_list.append(new_item)
        return (new_list, True) if changed else (obj, False)

    if isinstance(obj, dict):
        changed = False
        new_dict: dict[str, Any] = {}
        for key, value in obj.items():
            canonical_key = LEGACY_CAMELCASE_TO_SNAKE_CASE.get(key, key)

            # If both keys exist, prefer canonical and drop legacy
            if canonical_key != key and canonical_key in obj:
                changed = True
                continue

            if canonical_key != key:
                changed = True

            new_value, value_changed = rename_legacy_camelcase_keys(value)
            if value_changed:
                changed = True

            new_dict[canonical_key] = new_value

        return (new_dict, True) if changed else (obj, False)

    return obj, False
