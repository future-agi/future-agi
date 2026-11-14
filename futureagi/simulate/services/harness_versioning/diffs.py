"""What changed between two versions of an environment, a suite or an agent."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

# Contract fields compared item by item below, or never part of a diff.
_CONTRACT_ITEM_FIELDS = frozenset(
    {"tools", "hard_constraints", "real_use_cases", "use_cases"}
)
_CONTRACT_IGNORED_FIELDS = frozenset({"chosen_evals", "amendments"})
_SCENARIO_IGNORED_FIELDS = frozenset({"scenario_key", "scenario_id"})


def _added_removed(previous: Iterable[str], current: Iterable[str]) -> dict[str, list]:
    before, after = set(previous), set(current)
    return {"added": sorted(after - before), "removed": sorted(before - after)}


def _tools(contract: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(tool.get("name")): tool
        for tool in contract.get("tools") or []
        if isinstance(tool, Mapping) and tool.get("name")
    }


def _strings(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if value is not None]


def _use_cases(contract: Mapping[str, Any]) -> list[str]:
    return _strings(contract.get("real_use_cases") or contract.get("use_cases"))


def contract_diff(
    previous: Mapping[str, Any] | None, current: Mapping[str, Any]
) -> dict[str, Any]:
    previous = previous or {}
    before_tools, after_tools = _tools(previous), _tools(current)
    tools = _added_removed(before_tools, after_tools)
    tools["changed"] = sorted(
        name
        for name in set(before_tools) & set(after_tools)
        if before_tools[name] != after_tools[name]
    )
    changed_fields = sorted(
        field
        for field in set(previous) | set(current)
        if field not in _CONTRACT_ITEM_FIELDS
        and field not in _CONTRACT_IGNORED_FIELDS
        and field != "modality"
        and previous.get(field) != current.get(field)
    )
    diff: dict[str, Any] = {
        "tools": tools,
        "rules": _added_removed(
            _strings(previous.get("hard_constraints")),
            _strings(current.get("hard_constraints")),
        ),
        "use_cases": _added_removed(_use_cases(previous), _use_cases(current)),
        "fields": changed_fields,
    }
    if previous.get("modality") != current.get("modality"):
        diff["modality"] = {
            "from": previous.get("modality"),
            "to": current.get("modality"),
        }
    return diff


def keyed_diff(
    previous: Mapping[str, str], current: Mapping[str, str]
) -> dict[str, list[str]]:
    """Added, removed and changed keys of two ``{key: digest}`` maps."""
    diff = _added_removed(previous, current)
    diff["changed"] = sorted(
        key for key in set(previous) & set(current) if previous[key] != current[key]
    )
    return diff


def environment_diff(
    *,
    previous_contract: Mapping[str, Any] | None,
    contract: Mapping[str, Any],
    previous_sub_goals: Mapping[str, str],
    sub_goals: Mapping[str, str],
    previous_files: Mapping[str, str] | None,
    files: Mapping[str, str],
) -> dict[str, Any]:
    """Contract items, sub-goal definitions and world files, each added, removed or changed.

    A parent whose world files are unknown (backfilled without its archive) has no file diff.
    """
    return {
        "contract": contract_diff(previous_contract, contract),
        "sub_goals": keyed_diff(previous_sub_goals, sub_goals),
        "files": None if previous_files is None else keyed_diff(previous_files, files),
    }


def scenario_key(document: Mapping[str, Any]) -> str:
    return str(document.get("scenario_key") or document.get("name") or "")


def suite_diff(
    previous: Iterable[Mapping[str, Any]],
    current: Iterable[Mapping[str, Any]],
    *,
    removed_reason: str = "removed by manual edit",
) -> dict[str, Any]:
    before = {scenario_key(one): one for one in previous if scenario_key(one)}
    after = {scenario_key(one): one for one in current if scenario_key(one)}
    changed: dict[str, list[str]] = {}
    for key in sorted(set(before) & set(after)):
        fields = sorted(
            field
            for field in set(before[key]) | set(after[key])
            if field not in _SCENARIO_IGNORED_FIELDS
            and before[key].get(field) != after[key].get(field)
        )
        if fields:
            changed[key] = fields
    return {
        "added": sorted(set(after) - set(before)),
        "removed": [
            {"key": key, "reason": removed_reason}
            for key in sorted(set(before) - set(after))
        ],
        "changed": changed,
    }


def agent_diff(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    """Which of source, prompt and variable names changed; never values."""
    return {
        "source": previous.get("source") != current.get("source"),
        "prompt": previous.get("prompt_hash") != current.get("prompt_hash"),
        "variables": keyed_diff(
            previous.get("credential_fingerprints") or {},
            current.get("credential_fingerprints") or {},
        ),
    }


def _count(noun: str, nouns: str, verb: str, values: object) -> str | None:
    size = len(values) if isinstance(values, (list, dict)) else 0
    if not size:
        return None
    return f"{size} {noun if size == 1 else nouns} {verb}"


def summarize_environment(diff: Mapping[str, Any] | None) -> str:
    if not diff:
        return "First version"
    contract = diff.get("contract") or {}
    tools = contract.get("tools") or {}
    rules = contract.get("rules") or {}
    use_cases = contract.get("use_cases") or {}
    sub_goals = diff.get("sub_goals") or {}
    files = diff.get("files") or {}
    parts = [
        _count("tool", "tools", "added", tools.get("added")),
        _count("tool", "tools", "removed", tools.get("removed")),
        _count("tool", "tools", "changed", tools.get("changed")),
        _count("rule", "rules", "added", rules.get("added")),
        _count("rule", "rules", "removed", rules.get("removed")),
        _count("use case", "use cases", "added", use_cases.get("added")),
        _count("use case", "use cases", "removed", use_cases.get("removed")),
        _count("contract field", "contract fields", "changed", contract.get("fields")),
        _count("sub-goal", "sub-goals", "added", sub_goals.get("added")),
        _count("sub-goal", "sub-goals", "removed", sub_goals.get("removed")),
        _count("sub-goal", "sub-goals", "changed", sub_goals.get("changed")),
        _count("world file", "world files", "added", files.get("added")),
        _count("world file", "world files", "removed", files.get("removed")),
        _count("world file", "world files", "changed", files.get("changed")),
    ]
    if "modality" in contract:
        modality = contract["modality"]
        parts.append(f"modality {modality['from']} → {modality['to']}")
    text = ", ".join(part for part in parts if part)
    return text or "No contract, sub-goal or world change"


def summarize_suite(diff: Mapping[str, Any] | None) -> str:
    if not diff:
        return "First version"
    parts = [
        _count("scenario", "scenarios", "added", diff.get("added")),
        _count("scenario", "scenarios", "removed", diff.get("removed")),
        _count("scenario", "scenarios", "changed", diff.get("changed")),
    ]
    return ", ".join(part for part in parts if part) or "No scenario change"
