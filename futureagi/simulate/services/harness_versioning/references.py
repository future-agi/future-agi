"""Which scenarios a change touches, and which changes need one another, read from the stored scenario documents.

A scenario names the tools its solution and alternatives call, the sub-goals that grade it and the actors it
includes. A change to one of those touches the scenarios that name it. A rule, a contract field, a world file or the
simulator prompt has no scenario that cites it yet, so it touches every scenario (`all`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

ALL = "all"


def _steps(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
    elif isinstance(value, list):
        for one in value:
            yield from _steps(one)


def scenario_uses(scenario: Mapping[str, Any]) -> dict[str, set[str]]:
    """The tools, sub-goals and actors one scenario names."""
    tools = {
        str(step.get("tool") or step.get("name"))
        for field in ("solution", "alternatives")
        for step in _steps(scenario.get(field))
        if step.get("tool") or step.get("name")
    }
    sub_goals = {
        str(one.get("name") if isinstance(one, Mapping) else one)
        for one in scenario.get("sub_goals") or []
        if (one.get("name") if isinstance(one, Mapping) else one)
    }
    actors = {
        str(one.get("id") if isinstance(one, Mapping) else one)
        for one in scenario.get("actors") or []
        if (one.get("id") if isinstance(one, Mapping) else one)
    }
    return {"tool": tools, "sub_goal": sub_goals, "actor": actors}


def affects(
    change: Mapping[str, Any], scenarios: Mapping[str, Mapping[str, Any]]
) -> list[str] | str:
    """The scenario keys a change touches, or `all` when nothing says which ones."""
    kind, key = change["kind"], change["key"]
    if kind == "scenario":
        return [key]
    if kind in ("tool", "sub_goal", "actor"):
        return sorted(
            name
            for name, scenario in scenarios.items()
            if key in scenario_uses(scenario)[kind]
        )
    return ALL


def depends_on(
    change: Mapping[str, Any],
    changes: list[Mapping[str, Any]],
    scenarios: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    """The other changes an added or changed scenario needs: the tools, sub-goals and actors it names that only the
    draft adds."""
    if change["kind"] != "scenario" or change["action"] == "removed":
        return []
    uses = scenario_uses(scenarios.get(change["key"]) or {})
    return sorted(
        other["id"]
        for other in changes
        if other["action"] == "added"
        and other["kind"] in uses
        and other["key"] in uses[other["kind"]]
    )
