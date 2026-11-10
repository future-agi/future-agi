"""Findings that hold whatever the certificate says: the environment version's
contract, the agent's preflight, and the agent version's own tools against the
contract's."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from simulate.models import HarnessAgentVersionRuntime
from simulate.services.harness_readiness.catalogue import Reading, Target, targets


@dataclass(frozen=True)
class StructuralFinding:
    reason_code: Literal["production_only", "tool_unreachable"]
    reason: str
    targets: list[Target]


def _provisionable(dependency: Mapping[str, Any]) -> bool:
    """The environment stands up its own copy of a dependency only when it knows the
    engine and how the agent reaches it (the rule the build uses)."""
    reached = dependency.get("reached") or {}
    seam = any(
        reached.get(name)
        for name in (
            "dsn_env",
            "config_key",
            "host",
            "port",
            "database",
            "loader_module",
        )
    )
    return bool(dependency.get("engine")) and seam


def _compose_sibling(dependency: Mapping[str, Any]) -> bool:
    """A service the agent reaches by a single-label host name: inside its own compose app that name can only be
    another service of the same file, which the build runs with the agent."""
    host = str((dependency.get("reached") or {}).get("host") or "").strip().lower()
    return bool(host) and "." not in host and host != "localhost"


def structural_findings(
    contract: Mapping[str, Any] | None,
) -> StructuralFinding | None:
    """What the environment version's contract alone shows tool routing cannot do.

    A tool the build recorded as unreachable, or a service-backed tool naming no
    service, cannot be routed to the test world. A tool using a dependency the
    environment cannot stand up reaches the real one: production-only. The agent's
    own tools service, which a tool entrypoint names, is stood up with the agent.
    """
    contract = contract or {}
    unroutable = sorted(
        str(entry.get("tool") or "")
        for entry in contract.get("tool_entrypoints") or []
        if isinstance(entry, Mapping)
        and (
            entry.get("mode") == "unreachable"
            or (entry.get("mode") == "service" and not entry.get("service"))
        )
    )
    if unroutable:
        return StructuralFinding(
            "tool_unreachable",
            "No reachable implementation for: " + ", ".join(unroutable),
            targets("tool", unroutable, "failed"),
        )
    # A service of the agent's own source that a tool calls, whether the harness calls it or the agent's code does:
    # the build stands it up from that source, so it is never production.
    served = {
        str(entry.get("service"))
        for entry in contract.get("tool_entrypoints") or []
        if isinstance(entry, Mapping) and entry.get("service")
    }
    runtime = contract.get("runtime")
    compose = isinstance(runtime, Mapping) and runtime.get("execution") == "compose"
    live = sorted(
        {
            str(tool)
            for dependency in contract.get("dependencies") or []
            if isinstance(dependency, Mapping)
            and not _provisionable(dependency)
            and str(dependency.get("name") or "") not in served
            and not (compose and _compose_sibling(dependency))
            for tool in dependency.get("used_by") or []
        }
    )
    if live:
        verb = "reaches" if len(live) == 1 else "reach"
        return StructuralFinding(
            "production_only",
            f"Unsupported for this controlled test: {', '.join(live)} {verb} production",
            targets("tool", live, "failed"),
        )
    return None


def preflight_findings(
    runtime: HarnessAgentVersionRuntime | None,
) -> Reading | None:
    """A failed or unfinished agent preflight: the agent cannot start, whatever the
    certificate says. The reason names the checks that failed and what they miss."""
    if runtime is None:
        return None
    status = runtime.preflight_status
    if status == HarnessAgentVersionRuntime.PreflightStatus.PENDING:
        return Reading(
            "failed", "preflight_pending", "The agent's preflight has not finished"
        )
    if status != HarnessAgentVersionRuntime.PreflightStatus.FAILED:
        return None
    report = runtime.preflight_report or {}
    failed = [
        check
        for check in report.get("checks") or []
        if isinstance(check, Mapping) and check.get("status") == "failed"
    ]
    missing = sorted(
        {str(name) for check in failed for name in check.get("missing") or []}
    )
    names = ", ".join(str(check.get("label") or check.get("id")) for check in failed)
    return Reading(
        "failed",
        "preflight_failed",
        f"Agent preflight failed: {names or 'agent preflight'}"
        + (f" (missing {', '.join(missing)})" if missing else ""),
        targets("check", [str(check.get("id")) for check in failed], "failed"),
    )


def _arguments(tool: Mapping[str, Any], field: str) -> frozenset[str] | None:
    """A tool's argument names: an agent document's ``parameters.properties``, a
    contract's ``args``; ``None`` when the document does not say."""
    value = tool.get(field)
    if field == "parameters":
        value = value.get("properties") if isinstance(value, Mapping) else None
        return (
            frozenset(str(name) for name in value)
            if isinstance(value, Mapping)
            else None
        )
    return frozenset(str(name) for name in value) if isinstance(value, list) else None


def _required(tool: Mapping[str, Any]) -> frozenset[str]:
    """The argument names an agent document's ``parameters`` marks required."""
    parameters = tool.get("parameters")
    value = parameters.get("required") if isinstance(parameters, Mapping) else None
    return (
        frozenset(str(name) for name in value)
        if isinstance(value, list)
        else frozenset()
    )


def _changed(ours: Mapping[str, Any], theirs: Mapping[str, Any]) -> bool:
    """Whether the agent calls a tool in a way the world's tool does not take: an
    argument the world takes is gone from the agent (removed or renamed), or the agent
    requires one the world does not take. An extra optional argument is no change; a
    side that does not say is no change."""
    agent, world = _arguments(ours, "parameters"), _arguments(theirs, "args")
    if agent is None or world is None:
        return False
    return bool(world - agent) or bool(_required(ours) - world)


def _named(tools: Any) -> dict[str, Mapping[str, Any]]:
    return {
        str(tool["name"]): tool
        for tool in tools or []
        if isinstance(tool, Mapping) and tool.get("name")
    }


def agent_findings(
    agent_tools: list[Mapping[str, Any]] | None,
    contract: Mapping[str, Any] | None,
) -> dict[str, Reading]:
    """The agent version's tools against the environment version's, by row.

    ``agent_tools`` ``None`` means the version's tools were never read. A tool the
    agent has and the world lacks blocks every case (targets of kind ``agent_tool``);
    a world tool the agent lacks, or one it calls with other argument names, blocks
    the cases that use it. An empty list was read and shows no gap.
    """
    if agent_tools is None:
        return {
            "tool_discovery": Reading(
                "not_run",
                "agent_tools_unread",
                "This agent version's tools have not been read yet",
            )
        }
    if not agent_tools:
        return {}
    agent = _named(agent_tools)
    world = _named((contract or {}).get("tools"))
    found: dict[str, Reading] = {}
    outside = sorted(set(agent) - set(world))
    missing = sorted(set(world) - set(agent))
    if outside or missing:
        parts = []
        if outside:
            parts.append("Not in this environment: " + ", ".join(outside))
        if missing:
            parts.append("Not in this agent version: " + ", ".join(missing))
        found["tool_discovery"] = Reading(
            "failed",
            "tool_not_in_world" if outside else "tool_not_in_agent",
            "; ".join(parts),
            targets("agent_tool", outside, "failed")
            + targets("tool", missing, "failed"),
        )
    changed = sorted(
        name for name in set(agent) & set(world) if _changed(agent[name], world[name])
    )
    if changed:
        found["tool_routing"] = Reading(
            "failed",
            "tool_signature_changed",
            "Takes other arguments in this environment: " + ", ".join(changed),
            targets("tool", changed, "failed"),
        )
    return found
