"""One scenario's trials in a run lined up by route: who asked whom (or which tool was called), in order, and where the
trials first went different ways. Read from each trial's worker record; nothing is stored."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from simulate.types import (
    CallWorkersRecord,
    RouteCell,
    RouteCellState,
    RouteColumn,
    RouteParty,
    RouteSplit,
    RouteSplitGroup,
    RouteTrial,
    TrialRoute,
    TrialRoutes,
    WorkerStep,
)

# The routes compared: a whole record, or a read that ran and found no hand-off. Any other trial is listed, never
# compared, as its missing steps would read as steps it did not take.
COMPARED = ("recorded", "no_hand_off")
# How a hand-off's end reads in its cell: an answer is a step, a failure failed, a worker that never came back no answer.
_HAND_OFF_CELL: dict[str, RouteCellState] = {
    "finished": "step",
    "passed": "step",
    "failed": "failed",
    "no_return": "no_answer",
    "cut_off": "cut_off",
}


@dataclass(frozen=True)
class Trial:
    """One trial as the run lists it, and its worker record; `route` says why there is no record to compare."""

    call_execution_id: str
    result: str
    record: CallWorkersRecord | None
    route: TrialRoute


def _local(record: CallWorkersRecord) -> dict[str, tuple[str, str | None]]:
    # A worker's id within the run and its name, so the same worker matches across trials.
    return {
        str(one["worker_id"]): (str(one["id"]), str(one["name"]))
        for one in record.get("workers") or []
    }


def _column(step: WorkerStep, record: CallWorkersRecord) -> RouteColumn:
    workers = _local(record)
    lead = record.get("coordinator")
    coordinator = lead["name"] if lead else None

    def who(
        worker_id: str | None, *, unknown: bool
    ) -> tuple[RouteParty, str | None, str | None]:
        """Who took part: a worker by its id within the run, the coordinator, or unknown when the record does not
        say; a worker the record does not list keeps its id and has no name."""
        if worker_id is not None:
            key = str(worker_id)
            local, name = workers[key] if key in workers else (key, None)
            return "worker", local, name
        if unknown:
            return "unknown", None, None
        return "coordinator", None, coordinator

    if step["kind"] == "handoff":
        handoff: Mapping[str, Any] = step.get("handoff") or step.get("returned") or {}
        # A hand-off the record names no maker for, and cannot place, was not shown to be the coordinator's.
        maker_kind, maker_id, maker = who(
            handoff.get("by"), unknown=step.get("relationship") == "unknown"
        )
        worker_kind, worker_id, worker = who(step["worker_id"], unknown=True)
        return {
            "key": f"handoff:{maker_kind}:{maker_id or ''}>{worker_kind}:{worker_id or ''}",
            "kind": "handoff",
            "from_kind": maker_kind,
            "from_id": maker_id,
            "from_name": maker,
            "to_kind": worker_kind,
            "to_id": worker_id,
            "to_name": worker,
            "tool": None,
        }
    call: Mapping[str, Any] = step.get("call") or {}
    caller_kind, caller_id, caller = who(
        step["worker_id"], unknown=call.get("attribution") == "unattributed"
    )
    tool = str(call.get("tool") or "")
    return {
        "key": f"tool:{caller_kind}:{caller_id or ''}:{tool}",
        "kind": "tool_call",
        "from_kind": caller_kind,
        "from_id": caller_id,
        "from_name": caller,
        "to_kind": None,
        "to_id": None,
        "to_name": None,
        "tool": tool,
    }


def _state(step: WorkerStep) -> RouteCellState:
    if step["kind"] == "handoff":
        return _HAND_OFF_CELL.get(str(step.get("end") or ""), "step")
    call: Mapping[str, Any] = step.get("call") or {}
    return "failed" if call.get("status") == "error" else "step"


def _route(
    record: CallWorkersRecord, tool_calls: bool
) -> list[tuple[RouteColumn, int, RouteCellState]]:
    """Each step of the record that is a column, in reading order: its column, its key in the record, its state."""
    return [
        (_column(step, record), step["key"], _state(step))
        for step in record.get("steps") or []
        if step["kind"] == "handoff" or tool_calls
    ]


def _lined_up(
    routes: list[list[RouteColumn]],
) -> tuple[list[RouteColumn], list[list[int]]]:
    """The columns of every trial in one order, and each trial's steps' column positions. Each trial's own order is
    kept: a step repeating the column just before it stays in it (`repeated`), and a hand-off a trial makes after a
    column that another trial made it before gets a column of its own, so two trials that made the same hand-offs in a
    different order never line up as one route."""
    merged: list[RouteColumn] = []
    placed: list[list[RouteColumn]] = []
    for route in routes:
        at = -1
        mine: list[RouteColumn] = []
        for column in route:
            if at >= 0 and merged[at]["key"] == column["key"]:
                mine.append(merged[at])
                continue
            later = next(
                (
                    j
                    for j in range(at + 1, len(merged))
                    if merged[j]["key"] == column["key"]
                ),
                None,
            )
            if later is None:
                at += 1
                merged.insert(at, RouteColumn(**column))
            else:
                at = later
            mine.append(merged[at])
        placed.append(mine)
    positions = {id(one): index for index, one in enumerate(merged)}
    # A hand-off with a second column keeps its key apart, so each column's key is its own.
    seen: dict[str, int] = {}
    for one in merged:
        seen[one["key"]] = seen.get(one["key"], 0) + 1
        if seen[one["key"]] > 1:
            one["key"] = f"{one['key']}#{seen[one['key']]}"
    return merged, [[positions[id(one)] for one in mine] for mine in placed]


def _cell(found: list[tuple[int, RouteCellState]]) -> RouteCell:
    states = [state for _, state in found]
    if not found:
        return {
            "state": "absent",
            "step": None,
            "steps": [],
            "count": 0,
            "states": states,
        }
    return {
        "state": states[0] if len(found) == 1 else "repeated",
        "step": found[0][0],
        "steps": [key for key, _ in found],
        "count": len(found),
        "states": states,
    }


def _split(columns: list[RouteColumn], trials: list[RouteTrial]) -> RouteSplit | None:
    """The first column where the compared trials did different things, and which trials did what there."""
    compared = [one for one in trials if one["cells"] is not None]
    for index, column in enumerate(columns):
        groups: list[RouteSplitGroup] = []
        for trial in compared:
            cell = (trial["cells"] or [])[index]
            # Each occurrence's own end, in order: two trials that asked twice split when the asks ended differently.
            group = next(
                (
                    one
                    for one in groups
                    if (one["state"], one["states"]) == (cell["state"], cell["states"])
                ),
                None,
            )
            if group is None:
                group = {
                    "state": cell["state"],
                    "count": cell["count"],
                    "states": cell["states"],
                    "trials": [],
                }
                groups.append(group)
            group["trials"].append(trial["number"])
        if len(groups) > 1:
            return {"column": index, "key": column["key"], "groups": groups}
    return None


def trial_routes(
    scenario_key: str, trials: list[Trial], *, tool_calls: bool = False
) -> TrialRoutes:
    """The scenario's trials lined up: columns in order, each trial's cell in each, and the first split."""
    routes: dict[int, list[tuple[RouteColumn, int, RouteCellState]]] = {
        index: _route(one.record, tool_calls) if one.record is not None else []
        for index, one in enumerate(trials)
        if one.route in COMPARED
    }
    columns, positions = _lined_up(
        [[column for column, _, _ in route] for route in routes.values()]
    )
    at = dict(zip(routes, positions, strict=True))
    lined: list[RouteTrial] = []
    for index, one in enumerate(trials):
        route = routes.get(index)
        cells = None
        if route is not None:
            found: dict[int, list[tuple[int, RouteCellState]]] = {}
            for position, (_, number, state) in zip(at[index], route, strict=True):
                found.setdefault(position, []).append((number, state))
            cells = [_cell(found.get(position, [])) for position in range(len(columns))]
        lined.append(
            {
                "number": index + 1,
                "call_execution_id": one.call_execution_id,
                "result": one.result,
                "route": one.route,
                "cells": cells,
            }
        )
    split = _split(columns, lined)
    return {
        "scenario_key": scenario_key,
        "tool_calls": tool_calls,
        "trials": lined,
        "columns": columns,
        "split": split,
        "same_route": split is None,
    }
