"""The settings a keyword-blocklist check runs on (docs-agentcc-711, item 1).

The gateway's keyword-blocklist check reads three keys of its config:
``words``, ``whole_word`` and ``max_matches`` (agentcc-gateway
internal/guardrails/blocklist/blocklist.go). Every other key used to be
saved and pushed, and the gateway ignored it. A check that named a stored
blocklist (``blocklist_id``, ``blocklist_ids``) saved with no warning and
blocked none of that list's words, because stored blocklists never reach
the gateway.

A save that adds an unknown key, or changes the value of one, is now
refused with 400 ``keyword_blocklist_unknown_key``, naming the key. A key
the stored check already holds with the same value is kept, so an org that
saved one before is not refused on saves that leave it alone. Remove the
key by sending ``null`` in a checks-map patch, or by omitting it from a
replacement checks/rules array (including guardrail policy checks).

Both write paths use this: the org-config writer (every org-config save) and
the guardrail policy serializer (policy create and update).
"""

import json
from collections.abc import Iterable, Mapping
from typing import Any

from agentcc.services.config_push import OrgConfigRejected

KEYWORD_BLOCKLIST = "keyword-blocklist"
KEYWORD_BLOCKLIST_CONFIG_KEYS = ("words", "whole_word", "max_matches")
KEYWORD_BLOCKLIST_UNKNOWN_KEY = "keyword_blocklist_unknown_key"


def unknown_key_message(param: str) -> str:
    return (
        f"{param} is not a keyword-blocklist setting. The check reads only "
        f"{', '.join(KEYWORD_BLOCKLIST_CONFIG_KEYS)}. Stored blocklists "
        "(blocklist_id) are not supported: put the words in config.words. "
        "Omit the key from the check's config in replacement checks/rules "
        "arrays (including guardrail policy checks). For a checks-map patch, "
        "send the key as null to remove it."
    )


class KeywordBlocklistUnknownKey(OrgConfigRejected):
    """A save would store a keyword-blocklist key the gateway never reads."""

    code = KEYWORD_BLOCKLIST_UNKNOWN_KEY

    def __init__(self, param: str):
        self.param = param
        super().__init__(unknown_key_message(param))


Located = list[tuple[str, Mapping[str, Any]]]


def section_blocklist_configs(guardrails: Any) -> Located:
    """(path, config) of each keyword-blocklist check of a guardrails
    section, in each shape it is stored in: a checks map (update-config), a
    checks list (a policy sync) and a rules list."""
    found: Located = []
    if not isinstance(guardrails, Mapping):
        return found
    checks = guardrails.get("checks")
    if isinstance(checks, Mapping):
        check = checks.get(KEYWORD_BLOCKLIST)
        if isinstance(check, Mapping):
            found.append((f"guardrails.checks.{KEYWORD_BLOCKLIST}", check))
    elif isinstance(checks, list):
        found.extend(_listed(checks, "guardrails.checks"))
    rules = guardrails.get("rules")
    if isinstance(rules, list):
        found.extend(_listed(rules, "guardrails.rules"))
    return _configs(found)


def policy_blocklist_configs(checks: Any) -> Located:
    """(path, config) of each keyword-blocklist check of a policy's checks."""
    if not isinstance(checks, list):
        return []
    return _configs(_listed(checks, "checks"))


def reject_new_unknown_keys(configs: Located, stored: Located) -> None:
    """Raise ``KeywordBlocklistUnknownKey`` for the first unknown key of
    ``configs`` that ``stored`` does not hold with the same value."""
    held = {pair for _, config in stored for pair in _unknown(config)}
    for path, config in configs:
        for key, value in _unknown(config):
            if (key, value) not in held:
                raise KeywordBlocklistUnknownKey(f"{path}.{key}")


def _listed(items: Iterable[Any], prefix: str) -> Located:
    return [
        (f"{prefix}.{index}", item)
        for index, item in enumerate(items)
        if isinstance(item, Mapping) and item.get("name") == KEYWORD_BLOCKLIST
    ]


def _configs(checks: Located) -> Located:
    return [
        (f"{path}.config", check["config"])
        for path, check in checks
        if isinstance(check.get("config"), Mapping)
    ]


def _unknown(config: Mapping[str, Any]) -> list[tuple[str, str]]:
    return [
        (key, json.dumps(value, sort_keys=True, default=str))
        for key, value in config.items()
        if key not in KEYWORD_BLOCKLIST_CONFIG_KEYS
    ]
