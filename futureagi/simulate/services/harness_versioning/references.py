"""Which scenarios a change touches, and which changes need one another, read from the stored scenario documents.

A scenario names the tools its solution and alternatives call, the sub-goals that grade it and the actors it
includes. A change to one of those touches the scenarios that name it. A rule, a contract field, a world file or the
simulator prompt has no scenario that cites it yet, so it touches every scenario (`all`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from simulate.services.harness_scenarios import actor_ids_named
from simulate.services.harness_versioning.review_items import ReviewChange, graded
from simulate.services.scenario_refs import (
    arrives_on_tool,
    fault_actions,
    on_tool_triggers,
    path_tools,
)

ALL = "all"


def triggered_on(record: Mapping[str, Any]) -> str | None:
    """The tool an actor (or a scenario's entry for one) is brought in on, when it joins `on_tool`."""
    return arrives_on_tool(record, record)


def scenario_uses(
    scenario: Mapping[str, Any],
    actors: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, set[str]]:
    """The tools, sub-goals and actors one scenario names, as ALK's schema spells them (``scenario_refs``): the tools
    its paths call, its faults act on and its actors are set going by (an entry with no trigger of its own inherits
    the actor's activation from ``actors``); its sub-goals; and every actor it names."""
    tools = path_tools(scenario) | fault_actions(scenario)
    for entry in scenario.get("actors") or []:
        if isinstance(entry, Mapping):
            tools |= on_tool_triggers(entry, (actors or {}).get(str(entry.get("id"))))
    sub_goals = {
        str(one)
        for one in scenario.get("sub_goals") or []
        if isinstance(one, str) and one
    }
    return {
        "tool": tools,
        "sub_goal": sub_goals,
        "actor": actor_ids_named(dict(scenario)),
    }


def affects(
    change: ReviewChange,
    scenarios: Mapping[str, Mapping[str, Any]],
    base: Mapping[str, Mapping[str, Any]] | None = None,
    actors: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str] | str:
    """The scenario keys a change touches, or `all` when nothing says which ones. A scenario that names the item in
    the saved suite (``base``) or in the draft is touched; an actor entry inherits its activation from ``actors``."""
    kind, key = change["kind"], change["key"]
    if kind == "scenario":
        return [key]
    if kind in ("tool", "sub_goal", "actor"):
        return sorted(
            {
                name
                for side in (scenarios, base or {})
                for name, scenario in side.items()
                if key in scenario_uses(scenario, actors)[kind]
            }
        )
    return ALL


# The suite index, when it is reviewed as one file rather than entry by entry.
WHOLE_INDEX = "file:scenarios.json"


def depends_on(
    change: ReviewChange,
    changes: Sequence[ReviewChange],
    scenarios: Mapping[str, Mapping[str, Any]],
    base: Mapping[str, Mapping[str, Any]] | None = None,
    base_actors: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    """The other changes this one needs. An added or changed scenario needs the tools, sub-goals and actors it names
    that the draft adds, and, unless it is only renamed, the ones it names that the draft changes. Removing a tool,
    sub-goal or actor needs the scenario edits and removals that stop the saved scenarios naming it; removing a tool
    also needs the edits of the saved actors (``base_actors``) brought in on it. While the suite index is reviewed as
    one file, it and every scenario change need each other: the index lists the suite, so they are decided together."""
    ids = {other["id"] for other in changes}
    if change["id"] == WHOLE_INDEX:
        return sorted(other["id"] for other in changes if other["kind"] == "scenario")
    index = [WHOLE_INDEX] if change["kind"] == "scenario" and WHOLE_INDEX in ids else []
    if (
        change["kind"] in ("tool", "sub_goal", "actor")
        and change["action"] == "removed"
    ):
        return sorted(
            other["id"]
            for other in changes
            if (
                other["kind"] == "scenario"
                and change["key"]
                in scenario_uses((base or {}).get(other["key"]) or {}, base_actors)[
                    change["kind"]
                ]
            )
            or (
                change["kind"] == "tool"
                and other["kind"] == "actor"
                and triggered_on((base_actors or {}).get(other["key"]) or {})
                == change["key"]
            )
        )
    if change["kind"] != "scenario" or change["action"] == "removed":
        return index
    # A rename grades nothing, so it needs no definition; a graded edit may rely on how a named definition changed.
    wanted = ("added", "changed") if graded(change) else ("added",)
    uses = scenario_uses(scenarios.get(change["key"]) or {}, base_actors)
    return sorted(
        index
        + [
            other["id"]
            for other in changes
            if other["action"] in wanted
            and other["kind"] in uses
            and other["key"] in uses[other["kind"]]
        ]
    )
