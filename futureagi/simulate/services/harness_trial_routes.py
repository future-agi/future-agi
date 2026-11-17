"""One scenario's trials in a run lined up by route: who asked whom (or which tool was called), in order, and where the
trials first went different ways. Read from each trial's worker record; nothing is stored."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
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
    coordinator = (record.get("coordinator") or {}).get("name")

    def who(
        worker_id: str | None, *, unknown: bool
    ) -> tuple[RouteParty, str | None, str | None]:
        """Who took part: a worker by its id within the run, the coordinator, or unknown when the record does not
        say; a worker the record does not list keeps its id and has no name."""
        if worker_id is not None:
            local, name = workers.get(str(worker_id), (str(worker_id), None))
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


def _merged(routes: Iterable[list[RouteColumn]]) -> list[RouteColumn]:
    """The columns of every trial in one order: each trial's own order kept, a column first seen in a later trial
    placed after the column it followed there."""
    merged: list[RouteColumn] = []
    for route in routes:
        at = -1
        for column in route:
            keys = [one["key"] for one in merged]
            if column["key"] in keys:
                at = max(at, keys.index(column["key"]))
            else:
                at += 1
                merged.insert(at, column)
    return merged


def _cell(found: list[tuple[int, RouteCellState]]) -> RouteCell:
    if not found:
        return {"state": "absent", "step": None, "steps": [], "count": 0}
    if len(found) == 1:
        key, state = found[0]
        return {"state": state, "step": key, "steps": [key], "count": 1}
    return {
        "state": "repeated",
        "step": found[0][0],
        "steps": [key for key, _ in found],
        "count": len(found),
    }


def _split(columns: list[RouteColumn], trials: list[RouteTrial]) -> RouteSplit | None:
    """The first column where the compared trials did different things, and which trials did what there."""
    compared = [one for one in trials if one["cells"] is not None]
    for index, column in enumerate(columns):
        groups: list[RouteSplitGroup] = []
        for trial in compared:
            cell = (trial["cells"] or [])[index]
            group = next(
                (
                    one
                    for one in groups
                    if (one["state"], one["count"]) == (cell["state"], cell["count"])
                ),
                None,
            )
            if group is None:
                group = {"state": cell["state"], "count": cell["count"], "trials": []}
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
    columns = _merged([column for column, _, _ in route] for route in routes.values())
    lined: list[RouteTrial] = []
    for index, one in enumerate(trials):
        route = routes.get(index)
        cells = None
        if route is not None:
            found: dict[str, list[tuple[int, RouteCellState]]] = {}
            for column, number, state in route:
                found.setdefault(column["key"], []).append((number, state))
            cells = [_cell(found.get(column["key"], [])) for column in columns]
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
