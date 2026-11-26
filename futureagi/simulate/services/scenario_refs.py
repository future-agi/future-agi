"""What a scenario document names, read the way ALK's scenario schema spells it.

ALK owns the schema (agent-learning-kit ``fi/alk/harness``): a solution or alternative step names its tool as
``Step.tool``; a fault names its world action as ``Fault.action`` and an approval it waits on as
``Fault.approval``; a scenario's actor entry is brought in by ``trigger``, else by the actor's own
``activation.trigger``, and each of its scheduled ``actions`` has its own ``trigger`` (``actor_dispatch``'s order).
Every platform reader of these fields uses this module.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import Any

ON_TOOL = "on_tool"


def step_tools(steps: Any) -> Iterator[str]:
    """The tool each step of a solution or alternative path calls."""
    if not isinstance(steps, Iterable) or isinstance(steps, str | bytes | Mapping):
        return
    for step in steps:
        tool = step.get("tool") if isinstance(step, Mapping) else None
        if isinstance(tool, str) and tool:
            yield tool


def path_tools(document: Mapping[str, Any]) -> set[str]:
    """Every tool the scenario's solution and alternative paths call."""
    tools = set(step_tools(document.get("solution")))
    for path in document.get("alternatives") or []:
        tools |= set(step_tools(path))
    return tools


def fault_actions(document: Mapping[str, Any]) -> set[str]:
    """The world actions the scenario's faults are set on, and the actions an approval fault waits on."""
    found: set[str] = set()
    for fault in document.get("faults") or []:
        if not isinstance(fault, Mapping):
            continue
        for name in ("action", "approval"):
            value = fault.get(name)
            if isinstance(value, str) and value:
                found.add(value)
    return found


def _on_tool(trigger: Any) -> str | None:
    if isinstance(trigger, Mapping) and trigger.get("kind") == ON_TOOL:
        tool = trigger.get("tool")
        if isinstance(tool, str) and tool:
            return tool
    return None


def arrival_trigger(
    entry: Mapping[str, Any], actor: Mapping[str, Any] | None = None
) -> Any:
    """What brings a scenario's actor in: its entry's ``trigger``, else the actor's own ``activation.trigger``."""
    if entry.get("trigger") is not None:
        return entry.get("trigger")
    activation = actor.get("activation") if isinstance(actor, Mapping) else None
    return activation.get("trigger") if isinstance(activation, Mapping) else None


def arrives_on_tool(
    entry: Mapping[str, Any], actor: Mapping[str, Any] | None = None
) -> str | None:
    """The tool whose call brings the actor in, when it arrives ``on_tool``."""
    return _on_tool(arrival_trigger(entry, actor))


def on_tool_triggers(
    entry: Mapping[str, Any], actor: Mapping[str, Any] | None = None
) -> set[str]:
    """Every tool whose call sets the actor going: its arrival, and each of its scheduled actions."""
    tools = {
        tool
        for action in entry.get("actions") or []
        if isinstance(action, Mapping) and (tool := _on_tool(action.get("trigger")))
    }
    arrival = arrives_on_tool(entry, actor)
    if arrival:
        tools.add(arrival)
    return tools
