"""Canonical tool alias routing for Falcon and tool discovery."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

DEPRECATED_TOOL_ALIASES: dict[str, list[str]] = {
    "search_projects": ["list_projects"],
    "search projects": ["list_projects"],
    "find projects": ["list_projects"],
    "project search": ["list_projects"],
    "list_dataset_columns": ["get_dataset"],
    "list dataset columns": ["get_dataset"],
    "dataset columns": ["get_dataset"],
    "list_run_tests": [
        "get_run_test_analytics",
        "list_test_executions",
        "run_agent_test",
    ],
    "list run tests": [
        "get_run_test_analytics",
        "list_test_executions",
        "run_agent_test",
    ],
    "list agent tests": [
        "get_run_test_analytics",
        "list_test_executions",
        "run_agent_test",
    ],
    "list simulation tests": [
        "get_run_test_analytics",
        "list_test_executions",
        "run_agent_test",
    ],
}


def normalize_tool_alias(value: str) -> str:
    return (value or "").strip().lower().replace("_", " ")


def _tokens(value: str) -> list[str]:
    return [token for token in re.split(r"[^a-z0-9]+", value) if token]


def _close_token(tokens: list[str], expected: str, threshold: float = 0.75) -> bool:
    return any(SequenceMatcher(None, token, expected).ratio() >= threshold for token in tokens)


def _looks_like_run_test_query(normalized: str) -> bool:
    tokens = _tokens(normalized)
    if not tokens:
        return False
    return (
        _close_token(tokens, "run")
        or _close_token(tokens, "test")
        or _close_token(tokens, "tests")
        or _close_token(tokens, "simulation")
    )


def alias_tool_names(query: str) -> list[str]:
    normalized = normalize_tool_alias(query)
    if not normalized:
        return []

    names: list[str] = []
    for alias, tool_names in DEPRECATED_TOOL_ALIASES.items():
        alias_normalized = normalize_tool_alias(alias)
        fuzzy_match = (
            _looks_like_run_test_query(normalized)
            and SequenceMatcher(None, normalized, alias_normalized).ratio() >= 0.78
        )
        if (
            normalized == alias_normalized
            or alias_normalized in normalized
            or fuzzy_match
        ):
            for tool_name in tool_names:
                if tool_name not in names:
                    names.append(tool_name)
    return names


def canonical_tool_name(tool_name: str) -> str:
    matches = alias_tool_names(tool_name)
    return matches[0] if matches else tool_name
