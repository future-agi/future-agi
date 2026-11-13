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
    """The tools, sub-goals and actors one scenario names; actors wherever the scenario names one (its actors, pre-run
    sessions and event start)."""
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
    from simulate.services.harness_scenarios import actor_ids_named

    actors = actor_ids_named(dict(scenario))
    return {"tool": tools, "sub_goal": sub_goals, "actor": actors}


def affects(
    change: Mapping[str, Any],
    scenarios: Mapping[str, Mapping[str, Any]],
    base: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str] | str:
    """The scenario keys a change touches, or `all` when nothing says which ones. A scenario that names the item in
    the saved suite (``base``) or in the draft is touched."""
    kind, key = change["kind"], change["key"]
    if kind == "scenario":
        return [key]
    if kind in ("tool", "sub_goal", "actor"):
        return sorted(
            {
                name
                for side in (scenarios, base or {})
                for name, scenario in side.items()
                if key in scenario_uses(scenario)[kind]
            }
        )
    return ALL


def depends_on(
    change: Mapping[str, Any],
    changes: list[Mapping[str, Any]],
    scenarios: Mapping[str, Mapping[str, Any]],
    base: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    """The other changes this one needs. An added or changed scenario needs the tools, sub-goals and actors it names
    that the draft adds, and, unless it is only renamed, the ones it names that the draft changes. Removing a tool, sub-goal or actor needs the scenario edits and removals that stop the
    saved scenarios naming it."""
    if (
        change["kind"] in ("tool", "sub_goal", "actor")
        and change["action"] == "removed"
    ):
        return sorted(
            other["id"]
            for other in changes
            if other["kind"] == "scenario"
            and change["key"]
            in scenario_uses((base or {}).get(other["key"]) or {})[change["kind"]]
        )
    if change["kind"] != "scenario" or change["action"] == "removed":
        return []
    from simulate.services.harness_versioning.review_items import graded

    # A rename grades nothing, so it needs no definition; a graded edit may rely on how a named definition changed.
    wanted = ("added", "changed") if graded(change) else ("added",)
    uses = scenario_uses(scenarios.get(change["key"]) or {})
    return sorted(
        other["id"]
        for other in changes
        if other["action"] in wanted
        and other["kind"] in uses
        and other["key"] in uses[other["kind"]]
    )
