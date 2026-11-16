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


_WORDING_KEYS = frozenset({"description", "title", "examples", "example"})


def _shape(schema: Any) -> Any:
    """A parameter schema without its wording: types, enums, formats, bounds and
    nesting stay, so any of them changing counts as a changed tool."""
    if isinstance(schema, Mapping):
        return {
            str(key): _shape(value)
            for key, value in sorted(schema.items(), key=lambda item: str(item[0]))
            if key not in _WORDING_KEYS
        }
    if isinstance(schema, list):
        return [_shape(value) for value in schema]
    return schema


def _signature(tool: Mapping[str, Any]) -> dict[str, Any]:
    parameters = tool.get("parameters") or {}
    if not isinstance(parameters, Mapping):
        parameters = {}
    properties = parameters.get("properties") or {}
    required = parameters.get("required") or []
    return {
        "arguments": sorted(properties),
        "required": sorted(required),
        "kind": tool.get("kind"),
        "schema": _shape(parameters),
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

    environment_version = environment.environment_version
    if environment_version is not None and environment_version.agent_version_id:
        return environment_version.agent_version
    return frozen_build_pin(environment) or environment.agent_version


def stored_document(version: AgentVersion) -> Mapping[str, Any] | None:
    """The agent document stored on a version, raw extension included; None when
    the version was never read."""
    return stored_agent(version.id, with_raw_setup=True, include_stale=True)


def environment_compatibility(
    environment: HostedHarnessJob,
    version: AgentVersion,
    *,
    document: Mapping[str, Any] | None = None,
    base_documents: dict[Any, Mapping[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """``version`` against the version ``environment`` was built from; unmeasured
    when either has no stored document. A caller judging many environments passes
    the version's ``document`` once, and a ``base_documents`` dict that keeps each
    base version's document after its first read."""
    base = built_from(environment)
    if base is None:
        return {"status": UNMEASURED, "reason": "environment_not_built", "base": None}
    if base.id == version.id:
        return {"status": CLEAN, "base": str(base.id), "same_version": True}
    if base_documents is None:
        previous = stored_document(base)
    else:
        if base.id not in base_documents:
            base_documents[base.id] = stored_document(base)
        previous = base_documents[base.id]
    current = document if document is not None else stored_document(version)
    if previous is None or current is None:
        return {
            "status": UNMEASURED,
            "reason": ("base_not_read" if previous is None else "version_not_read"),
            "base": str(base.id),
        }
    return {**compare_documents(previous, current), "base": str(base.id)}
