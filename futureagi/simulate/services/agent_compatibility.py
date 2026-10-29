"""Whether a new agent version still fits an environment built from an older one:
a pure comparison of the two stored agent documents, with no model call."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from simulate.models import AgentVersion, HostedHarnessJob
from simulate.services.agent_setup import stored_agent

CLEAN = "clean"
NEW_TOOL = "new_tool"
TOOL_REMOVED_OR_CHANGED = "tool_removed_or_changed"
NEW_RULE = "new_rule"
UNMEASURED = "unmeasured"


def _tools(document: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(tool.get("name") or ""): tool
        for tool in document.get("tools") or []
        if isinstance(tool, Mapping) and tool.get("name")
    }


def _signature(tool: Mapping[str, Any]) -> dict[str, Any]:
    parameters = tool.get("parameters") or {}
    properties = parameters.get("properties") if isinstance(parameters, Mapping) else {}
    required = parameters.get("required") if isinstance(parameters, Mapping) else []
    return {
        "arguments": sorted(properties or {}),
        "required": sorted(required or []),
        "kind": tool.get("kind"),
    }


def _contract(document: Mapping[str, Any]) -> Mapping[str, Any]:
    extension = document.get("extension") or {}
    contract = extension.get("contract") if isinstance(extension, Mapping) else None
    return contract if isinstance(contract, Mapping) else {}


def _rules(document: Mapping[str, Any]) -> set[str]:
    contract = _contract(document)
    rules = contract.get("hard_constraints") or []
    return {str(rule) for rule in rules if isinstance(rule, str)}


def _services(document: Mapping[str, Any]) -> set[str]:
    contract = _contract(document)
    found = set()
    for entry in contract.get("tool_entrypoints") or []:
        if isinstance(entry, Mapping) and entry.get("service"):
            found.add(str(entry["service"]))
    for dependency in contract.get("dependencies") or []:
        if isinstance(dependency, str):
            found.add(dependency)
        elif isinstance(dependency, Mapping) and dependency.get("name"):
            found.add(str(dependency["name"]))
    return found


def compare_documents(
    previous: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    """How ``current`` departs from ``previous`` for an environment built on it.

    ``clean``: only the prompt, model, voice or instructions moved. ``new_tool``: a
    tool or an outside service appeared. ``tool_removed_or_changed``: a tool the
    world was bound to went away or takes different arguments. ``new_rule``: a hard
    constraint appeared. The worst class wins, and every change is listed.
    """
    before, after = _tools(previous), _tools(current)
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(
        name
        for name in set(before) & set(after)
        if _signature(before[name]) != _signature(after[name])
    )
    new_services = sorted(_services(current) - _services(previous))
    new_rules = sorted(_rules(current) - _rules(previous))
    if removed or changed:
        status = TOOL_REMOVED_OR_CHANGED
    elif added or new_services:
        status = NEW_TOOL
    elif new_rules:
        status = NEW_RULE
    else:
        status = CLEAN
    return {
        "status": status,
        "tools_added": added,
        "tools_removed": removed,
        "tools_changed": [
            {
                "name": name,
                "before": _signature(before[name]),
                "after": _signature(after[name]),
            }
            for name in changed
        ],
        "services_added": new_services,
        "rules_added": new_rules,
        "prompt_changed": _instructions(previous) != _instructions(current),
        "model_changed": (previous.get("model") or None)
        != (current.get("model") or None),
    }


def _instructions(document: Mapping[str, Any]) -> list[str]:
    return [
        str(unit.get("instructions") or "")
        for unit in document.get("behaviour") or []
        if isinstance(unit, Mapping)
    ]


def built_from(environment: HostedHarnessJob) -> AgentVersion | None:
    """The agent version the environment's world was last built from."""
    from simulate.services.hosted_harness import frozen_build_pin

    return frozen_build_pin(environment) or environment.agent_version


def environment_compatibility(
    environment: HostedHarnessJob, version: AgentVersion
) -> dict[str, Any]:
    """``version`` against the version ``environment`` was built from; unmeasured
    when either has no stored document."""
    base = built_from(environment)
    if base is None:
        return {"status": UNMEASURED, "reason": "environment_not_built", "base": None}
    if base.id == version.id:
        return {"status": CLEAN, "base": str(base.id), "same_version": True}
    previous = stored_agent(base.id, with_raw_setup=True, include_stale=True)
    current = stored_agent(version.id, with_raw_setup=True, include_stale=True)
    if previous is None or current is None:
        return {
            "status": UNMEASURED,
            "reason": ("base_not_read" if previous is None else "version_not_read"),
            "base": str(base.id),
        }
    return {**compare_documents(previous, current), "base": str(base.id)}
