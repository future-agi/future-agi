"""Which of a multi-agent app's workers did what in one call, from its own spans and the sandbox's record of it."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any, Literal, TypedDict

from simulate.services.harness_worker_matching import MatchRow, shown_by_spans
from simulate.services.harness_worker_text import (
    digest,
    epoch_iso,
    payload,
    row_output,
    row_started,
    row_status,
    runtime_ids,
    slug,
    span_kind,
    span_status,
    summary,
    task_text,
)
from simulate.types import (
    CallTurn,
    DeclaredWorker,
    HarnessWorker,
    SealedToolCall,
    WorkerEvent,
    WorkerSpan,
    WorkerTimeline,
)

AGENT_KIND = "agent"
TOOL_KIND = "tool"
_WORK_KINDS = {TOOL_KIND, "llm"}
HAND_OFF_TYPES = frozenset({"delegated", "transferred"})
# What the runtime calls an agent instance, when it names one (OpenInference / traceAI graph node).
_NODE_ID = "graph.node.id"
# The record is sent whole to the browser, so it stays bounded however large the team or the call.
_MAX_EVENTS = 500
_MAX_TURNS = 200
PAYLOAD_BUDGET = 64_000

# Each payload a worker event can carry, with the flag that says it was left out.
PAYLOADS: tuple[
    tuple[Literal["input", "output"], Literal["input_omitted", "output_omitted"]], ...
] = (("input", "input_omitted"), ("output", "output_omitted"))


class _Place(TypedDict, total=False):
    """Where the harness recorded an event ran: the hand-off it belongs to, or whether that is known."""

    parent_delegation_id: str
    relationship: Literal["observed", "unknown"]
    launch: Literal["async"]


def bounded(events: list[WorkerEvent]) -> tuple[list[WorkerEvent], int]:
    """The events a record keeps, and how many it left out."""
    kept = events[:_MAX_EVENTS]
    spent = 0
    for event in kept:
        for key, omitted in PAYLOADS:
            if event.get(key) is None:
                continue
            size = len(json.dumps(event[key], default=str))
            if spent + size > PAYLOAD_BUDGET:
                event[key] = None
                event[omitted] = True
            else:
                spent += size
    return kept, len(events) - len(kept)


def in_order(events: list[WorkerEvent]) -> None:
    """Sort a call's events into reading order and number them."""
    order = {"transferred": 0, "delegated": 0, "tool_call": 1, "returned": 2}
    returns: dict[str, str] = {}
    for one in events:
        ended = one.get("at")
        if one["type"] == "returned" and ended:
            returns[one["delegation_id"]] = ended

    def placed(one: Mapping[str, Any]) -> str:
        if (
            one.get("at")
            or one["type"] != "delegated"
            or one.get("delegation_id") not in returns
        ):
            return str(one.get("at") or "")
        until = returns[one["delegation_id"]]
        steps = [
            at
            for other in events
            if other["worker_id"] == one["worker_id"]
            and other["type"] == "tool_call"
            and (at := other.get("at"))
            and at <= until
        ]
        return min([until, *steps])

    events.sort(key=lambda one: (placed(one), order[one["type"]]))
    for seq, event in enumerate(events, 1):
        event["seq"] = seq


class Roster:
    """The call's workers as they are found, each under one id per run, matched to the contract's declared workers."""

    def __init__(self, declared: Iterable[DeclaredWorker], run_key: str) -> None:
        self.declared = list(declared)
        self.run_key = run_key
        self.workers: dict[str, HarnessWorker] = {}
        self.by_key: dict[str, DeclaredWorker] = {}
        for declared_worker in self.declared:
            for key in (declared_worker.get("id"), declared_worker.get("name")):
                if key:
                    self.by_key[slug(str(key))] = declared_worker

    def match(self, *names: str) -> DeclaredWorker | None:
        return next(
            (found for name in names if (found := self.by_key.get(slug(name)))), None
        )

    def worker_id(self, by: str, label: str) -> str:
        """The id `add` gives, without adding the worker."""
        match = self.match(by, label)
        return f"{self.run_key}/{str(match['id']) if match is not None else slug(by)}"

    def add(
        self, local: str, name: str, match: Mapping[str, Any] | None, instance: str
    ) -> str:
        worker_id = f"{self.run_key}/{local}"
        entry = self.workers.setdefault(
            worker_id,
            {
                "worker_id": worker_id,
                "id": local,
                "name": str(match.get("name") or name) if match is not None else name,
                "observed": True,
                "undeclared": match is None,
                "instance_ids": [],
            },
        )
        if instance not in entry["instance_ids"]:
            entry["instance_ids"].append(instance)
        return worker_id

    def add_named(self, by: str, label: str) -> str:
        match = self.match(by, label)
        return self.add(
            str(match["id"]) if match is not None else slug(by), label, match, by
        )

    def listed(self) -> list[HarnessWorker]:
        """Every worker found, and every declared one, found or not."""
        for declared_worker in self.declared:
            worker_id = f"{self.run_key}/{declared_worker['id']}"
            self.workers.setdefault(
                worker_id,
                {
                    "worker_id": worker_id,
                    "id": str(declared_worker["id"]),
                    "name": str(declared_worker.get("name") or declared_worker["id"]),
                    "observed": False,
                    "undeclared": False,
                    "instance_ids": [],
                },
            )
        return sorted(self.workers.values(), key=lambda one: one["id"])


class _SpanTree:
    """The app's spans as a tree of agents: which agent spans are participants and whom each worked for."""

    def __init__(self, spans: Iterable[WorkerSpan], roster: Roster) -> None:
        self.by_id = {str(span["span_id"]): span for span in spans}
        self.roster = roster
        agents = [span for span in self.by_id.values() if span_kind(span) == AGENT_KIND]
        busy: set[str] = set()
        for span in self.by_id.values():
            owner = (
                self._nearest_agent(span) if span_kind(span) in _WORK_KINDS else None
            )
            if owner is not None:
                busy.add(owner["span_id"])
        children = Counter(
            parent["span_id"]
            for parent in map(self._nearest_agent, agents)
            if parent is not None
        )
        # An agent span that only wraps one other agent, with no work of its own, is a framework's container.
        self.participants = {
            span["span_id"]
            for span in agents
            if span["span_id"] in busy or children[span["span_id"]] != 1
        }

    def _ancestors(self, span: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
        # A span is never its own ancestor, so a cycle in the recorded parents ends the walk.
        seen = {span["span_id"]}
        parent = self.by_id.get(str(span.get("parent_span_id") or ""))
        while parent is not None and parent["span_id"] not in seen:
            yield parent
            seen.add(parent["span_id"])
            parent = self.by_id.get(str(parent.get("parent_span_id") or ""))

    def _nearest_agent(
        self, span: Mapping[str, Any], among: Any = None
    ) -> Mapping[str, Any] | None:
        return next(
            (
                one
                for one in self._ancestors(span)
                if span_kind(one) == AGENT_KIND
                and (among is None or one["span_id"] in among)
            ),
            None,
        )

    def launcher_of(self, agent: Mapping[str, Any]) -> Mapping[str, Any] | None:
        # The tool call that launched the worker, when the framework runs workers as tools.
        for one in self._ancestors(agent):
            if span_kind(one) == AGENT_KIND:
                return None
            if span_kind(one) == TOOL_KIND:
                return one
        return None

    def agent_ancestor(self, span: Mapping[str, Any]) -> Mapping[str, Any] | None:
        return self._nearest_agent(span, self.participants)

    @staticmethod
    def name_of(agent: Mapping[str, Any]) -> str:
        return str(
            (agent.get("attributes") or {}).get(_NODE_ID)
            or agent.get("name")
            or "agent"
        )

    def worker_of(self, agent: Mapping[str, Any] | None) -> str | None:
        # A top-level agent span is the coordinator, not a worker.
        if agent is None or self.agent_ancestor(agent) is None:
            return None
        name = self.name_of(agent)
        match = self.roster.match(name)
        return self.roster.add(
            str(match["id"]) if match is not None else slug(name),
            name,
            match,
            str(agent["span_id"]),
        )

    def delegation_of(self, agent: Mapping[str, Any]) -> str:
        return str((self.launcher_of(agent) or agent)["span_id"])

    def top_names(self, handed: set[str]) -> list[str]:
        """The top-level agents the rows never hand work to, in span order."""
        return [
            self.name_of(span)
            for span in self.by_id.values()
            if span["span_id"] in self.participants
            and self.agent_ancestor(span) is None
            and slug(self.name_of(span)) not in handed
        ]


def _span_hand_offs(
    tree: _SpanTree, events: list[WorkerEvent]
) -> tuple[dict[str, set[str]], set[str]]:
    """Each worker agent span's hand-off and return; every id a hand-off is known by, and the launching calls."""
    hand_off_ids: dict[str, set[str]] = {}
    delegating: set[str] = set()
    for span in tree.by_id.values():
        if (
            span["span_id"] not in tree.participants
            or tree.agent_ancestor(span) is None
        ):
            continue
        worker_id = tree.worker_of(span)
        launcher = tree.launcher_of(span)
        if launcher is not None:
            delegating.add(launcher["span_id"])
        delegation_id = str((launcher or span)["span_id"])
        hand_off_ids.setdefault(delegation_id, set()).update(
            {delegation_id, str(span["span_id"])}
        )
        maker = tree.agent_ancestor(span)
        by = tree.worker_of(maker)
        hand_off: WorkerEvent = {
            "type": "delegated",
            "worker_id": worker_id,
            "by": by,
            "delegation_id": delegation_id,
            # The span the hand-off was made in, for opening it in Traces: its launching call, else its agent.
            "span_id": delegation_id,
            "trace_id": str(span.get("trace_id") or ""),
            "task": summary(span.get("input"))
            or summary((launcher or {}).get("input"), argument=True),
            "at": span.get("start_time"),
            "attribution": "reported",
        }
        if by is not None and maker is not None:
            # The hand-off the maker was itself running in, read from the span tree.
            hand_off["parent_delegation_id"] = tree.delegation_of(maker)
        events.append(hand_off)
        if span.get("end_time"):
            events.append(
                {
                    "type": "returned",
                    "worker_id": worker_id,
                    "delegation_id": delegation_id,
                    "result": summary(span.get("output"))
                    or summary((launcher or {}).get("output")),
                    "status": span_status(span),
                    "at": span.get("end_time"),
                    "attribution": "reported",
                }
            )
    return hand_off_ids, delegating


def _span_tool_calls(
    tree: _SpanTree, events: list[WorkerEvent], delegating: set[str]
) -> None:
    for span in tree.by_id.values():
        if span_kind(span) != TOOL_KIND or span["span_id"] in delegating:
            continue
        owner = tree.agent_ancestor(span)
        owner_worker = tree.worker_of(owner)
        made: WorkerEvent = {
            "type": "tool_call",
            "worker_id": owner_worker,
            "tool": str(span.get("name") or ""),
            "span_id": str(span["span_id"]),
            "trace_id": str(span.get("trace_id") or ""),
            "status": span_status(span),
            "input": payload(span.get("input")),
            "output": payload(span.get("output")),
            "at": span.get("start_time"),
            # No agent span above the call: neither the coordinator nor a worker can be named for it.
            "attribution": "reported" if owner is not None else "unattributed",
        }
        if owner_worker is not None and owner is not None:
            # The hand-off the call ran in, read from the span tree.
            made["delegation_id"] = tree.delegation_of(owner)
        events.append(made)


def _span_usage(tree: _SpanTree) -> None:
    """Each worker's models in the order first seen and the tokens it spent, as its own model-call spans recorded."""
    for span in tree.by_id.values():
        owner = tree.agent_ancestor(span) if span_kind(span) == "llm" else None
        worker_id = tree.worker_of(owner) if owner is not None else None
        if worker_id is None:
            continue
        entry = tree.roster.workers[worker_id]
        model = str(span.get("model") or "")
        used = [one for one in str(entry.get("model") or "").split(", ") if one]
        if model and model not in used:
            entry["model"] = ", ".join([*used, model])
        entry["tokens"] = int(entry.get("tokens") or 0) + int(span.get("tokens") or 0)


def by_of(row: Mapping[str, Any]) -> str:
    # The runtime's stable worker id, else its label for the worker.
    return str(row.get("worker_id") or row.get("worker_label") or "").strip()


def label_of(row: Mapping[str, Any]) -> str:
    return str(row.get("worker_label") or by_of(row))


def ids_of(row: SealedToolCall) -> set[str]:
    # The ids a runtime gave this call: its span, and its own event id.
    return {
        str(one)
        for one in (row.get("span_id"), runtime_ids(row).get("event_id"))
        if one
    }


def own_id(row: SealedToolCall, index: int) -> str:
    return str(
        row.get("span_id") or runtime_ids(row).get("event_id") or f"traced-{index}"
    )


class _SealedRows:
    """The sandbox's sealed tool-call rows, and who they say the coordinator and the workers are."""

    def __init__(self, traced_calls: Iterable[SealedToolCall], roster: Roster) -> None:
        self.rows = [call for call in traced_calls if isinstance(call, Mapping)]
        self.roster = roster
        rows = self.rows
        self.handed = {
            slug(str(row["delegated_to"])) for row in rows if row.get("delegated_to")
        }
        self.named = {slug(by_of(row)) for row in rows if by_of(row)}
        self.labels = {slug(by_of(row)): label_of(row) for row in rows if by_of(row)}
        # A recorded kind and parent are read as recorded; a control pass is never guessed from a tool's name or args.
        self.recorded = any(row.get("kind") for row in rows)
        self.transfers = {
            index: target
            for index, row in enumerate(rows)
            if row.get("kind") == "transferred"
            and (target := str(row.get("delegated_to") or "").strip())
        }
        self.coordinators = self._coordinators()

    def declared_row(self, row: Mapping[str, Any]) -> bool:
        # A runtime may name a worker by an opaque id and give its declared name only as the label.
        return any(
            slug(name) in self.roster.by_key
            for name in (by_of(row), str(row.get("worker_label") or ""))
            if name
        )

    def _coordinators(self) -> set[str]:
        rows = self.rows
        controlled = set(self.transfers.values())
        # The agent that hands work out and is never handed any is the coordinator, unless it is a declared worker.
        top = (
            {
                slug(by_of(row))
                for row in rows
                if by_of(row) and not self.declared_row(row)
            }
            - self.handed
            - controlled
        )
        found = {
            slug(by_of(row))
            for row in rows
            if row.get("delegated_to") and slug(by_of(row)) in top
        }
        found |= {slug(by_of(rows[index])) for index in self.transfers} & top
        return found

    def single_agent(self, has_span_workers: bool) -> None:
        """The one agent of an app that hands nothing out is its coordinator too."""
        if (
            not self.handed
            and len(self.named) == 1
            and not has_span_workers
            and not any(self.declared_row(row) for row in self.rows if by_of(row))
        ):
            self.coordinators = set(self.named)

    def worker(self, by: str, label: str) -> str | None:
        if slug(by) in self.coordinators:
            return None
        return self.roster.add_named(by, label)

    def worker_id(self, by: str, label: str) -> str | None:
        """The id `worker` gives, without adding the worker: matching a row against the spans registers nothing."""
        if slug(by) in self.coordinators:
            return None
        return self.roster.worker_id(by, label)

    def placed_in(self, row: Mapping[str, Any]) -> _Place:
        """Where the harness recorded this event ran: the hand-off it belongs to, or that it does not know."""
        if not self.recorded:
            return {}
        if row.get("relationship") != "observed":
            return {"relationship": "unknown"}
        # Observed with no parent: nothing handed this work out, so it sits at the top.
        parent = str(row.get("parent_span_id") or "")
        return (
            {"parent_delegation_id": parent} if parent else {"relationship": "observed"}
        )

    def coordinator_label(self) -> str | None:
        return next(
            (
                label_of(row)
                for row in self.rows
                if slug(by_of(row)) in self.coordinators
            ),
            None,
        )


def _put(event: WorkerEvent, place: _Place) -> WorkerEvent:
    if "parent_delegation_id" in place:
        event["parent_delegation_id"] = place["parent_delegation_id"]
    if "relationship" in place:
        event["relationship"] = place["relationship"]
    if "launch" in place:
        event["launch"] = place["launch"]
    return event


def _call_place(event: WorkerEvent, place: _Place) -> None:
    """A tool call's place: the hand-off it ran in, else whether the top is known."""
    if "parent_delegation_id" in place:
        event["delegation_id"] = place["parent_delegation_id"]
    elif place.get("relationship"):
        event["relationship"] = place["relationship"]


def _match_rows(sealed: _SealedRows) -> list[MatchRow]:
    """The rows as `shown_by_spans` compares them with the span events."""
    matched: list[MatchRow] = []
    for index, call in enumerate(sealed.rows):
        by, target = by_of(call), str(call.get("delegated_to") or "").strip()
        if (not by and not target) or index in sealed.transfers:
            continue
        arguments = call.get("arguments")
        matched.append(
            {
                "index": index,
                "ids": ids_of(call),
                "kind": "delegated" if target else "tool_call",
                "worker_id": (
                    sealed.worker_id(target, target)
                    if target
                    else sealed.worker_id(by, label_of(call))
                ),
                "by": sealed.worker_id(by, label_of(call)) if by else None,
                "by_known": bool(by),
                "tool": str(call.get("name") or ""),
                # A hand-off has an outcome only once it returned.
                "status": None
                if target and not call.get("ended_at")
                else row_status(call),
                "result": summary(call.get("result")) if target else None,
                "output": digest(row_output(call)),
                "sig": (task_text(arguments) or None) if target else digest(arguments),
                "at": (
                    row_started(call)
                    if target
                    else row_started(call) or epoch_iso(call.get("ended_at"))
                ),
            }
        )
    return matched


def _returns_of(events: list[WorkerEvent], hand_off: WorkerEvent) -> list[WorkerEvent]:
    return [
        one
        for one in events
        if one["type"] == "returned"
        and one.get("delegation_id") == hand_off.get("delegation_id")
    ]


def _apply_shown(
    sealed: _SealedRows,
    shown: dict[int, WorkerEvent],
    events: list[WorkerEvent],
    span_top: set[str],
) -> None:
    """A span a sealed row also recorded takes the row's worker, outcome and place."""

    def row_worker(by: str, label: str) -> str | None:
        # The top agent the rows never hand work to is the coordinator: a row naming it is its own call.
        if slug(by) in span_top or slug(label) in span_top:
            return None
        return sealed.worker(by, label)

    for index, span_event in shown.items():
        call = sealed.rows[index]
        by, label = by_of(call), label_of(call)
        if span_event["type"] == "tool_call":
            if by:
                span_event["worker_id"] = row_worker(by, label)
            span_event["status"] = row_status(call)
            ended = row_output(call)
            if ended is not None:
                span_event["output"] = payload(ended)
            place = sealed.placed_in(call)
            if place:
                span_event.pop("delegation_id", None)
                span_event.pop("relationship", None)
                _call_place(span_event, place)
        else:
            target = str(call.get("delegated_to") or "").strip()
            if target:
                span_event["worker_id"] = sealed.worker(target, target)
            if by:
                span_event["by"] = row_worker(by, label)
            for one in _returns_of(events, span_event):
                one["worker_id"] = span_event["worker_id"]
                if call.get("ended_at"):
                    one["status"] = row_status(call)
                    one["result"] = summary(call.get("result"))
    # Sealed rows are the same in every read, so a read without spans and a later one with them join on the row.
    for index, span_event in shown.items():
        span_event["row"] = index
        if span_event["type"] == "delegated":
            for one in _returns_of(events, span_event):
                one["row"] = index


def _transfer_event(
    sealed: _SealedRows,
    index: int,
    events: list[WorkerEvent],
    hand_off_ids: dict[str, set[str]],
) -> None:
    """The recorded transfer replaces any hand-off the spans read for it; what ran inside moves under it."""
    call = sealed.rows[index]
    target = sealed.transfers[index]
    recorded_ids = ids_of(call)
    ids = recorded_ids | {
        handed for handed, known in hand_off_ids.items() if known & recorded_ids
    }
    from_spans = [
        one
        for one in events
        if one["type"] == "delegated" and one.get("delegation_id") in ids
    ]
    events[:] = [
        one
        for one in events
        if not (
            (
                one["type"] in ("delegated", "returned")
                and one.get("delegation_id") in ids
            )
            or (one["type"] == "tool_call" and one.get("span_id") in ids)
        )
    ]
    # Open in Traces lands on the launching call the hand-off was read from, not the agent run it started.
    for one in events:
        if one.get("delegation_id") in ids:
            one["delegation_id"] = own_id(call, index)
        if one.get("parent_delegation_id") in ids:
            one["parent_delegation_id"] = own_id(call, index)
    by = by_of(call)
    maker = sealed.worker(by, label_of(call)) if by else None
    transferred: WorkerEvent = {
        "type": "transferred",
        "worker_id": sealed.worker(
            sealed.labels.get(target, target), sealed.labels.get(target, target)
        ),
        "by": maker,
        "delegation_id": own_id(call, index),
        "at": row_started(call) or epoch_iso(call.get("ended_at")),
        "attribution": "reported",
        "row": index,
    }
    _put(transferred, sealed.placed_in(call))
    if from_spans and from_spans[0].get("span_id"):
        transferred["span_id"] = from_spans[0]["span_id"]
    if from_spans and from_spans[0].get("trace_id"):
        transferred["trace_id"] = from_spans[0]["trace_id"]
    events.append(transferred)


def _launched_by(sealed: _SealedRows, shown: dict[int, WorkerEvent]) -> set[int]:
    """The hand-offs a job made from the coordinator's launching call: rows naming that call as their parent event."""
    rows = sealed.rows
    by_event = {
        str(runtime_ids(row).get("event_id")): index
        for index, row in enumerate(rows)
        if runtime_ids(row).get("event_id")
    }
    launched: set[int] = set()
    for index, call in enumerate(rows):
        launcher = by_event.get(str(runtime_ids(call).get("parent_event_id") or ""))
        if (
            launcher is not None
            and launcher not in shown
            and index not in shown
            and str(call.get("delegated_to") or "").strip()
            and not str(rows[launcher].get("delegated_to") or "").strip()
            and sealed.placed_in(call).get("relationship") == "unknown"
        ):
            launched.add(index)
    return launched


def _hand_off_events(
    sealed: _SealedRows, index: int, events: list[WorkerEvent], *, launched: bool
) -> None:
    """A hand-off: the call that gave the work, and its result when the worker returned."""
    call = sealed.rows[index]
    by = by_of(call)
    # A hand-off whose launcher the runtime could not name was made by the agent the caller talks to.
    maker = sealed.worker(by, label_of(call)) if by else None
    target = str(call.get("delegated_to") or "").strip()
    handed_to = sealed.worker(target, target)
    delegation_id = own_id(call, index)
    events.append(
        _put(
            {
                "type": "delegated",
                "worker_id": handed_to,
                "by": maker,
                "delegation_id": delegation_id,
                "task": task_text(call.get("arguments")),
                "at": row_started(call),
                "attribution": "reported",
                "row": index,
            },
            # Made by the coordinator's launching call, so its place is known; the job outlives that call.
            {"launch": "async"} if launched else sealed.placed_in(call),
        )
    )
    if epoch_iso(call.get("ended_at")):
        events.append(
            {
                "type": "returned",
                "worker_id": handed_to,
                "delegation_id": delegation_id,
                "result": summary(call.get("result")),
                "status": row_status(call),
                "at": epoch_iso(call.get("ended_at")),
                "attribution": "reported",
                "row": index,
            }
        )


def _tool_call_event(sealed: _SealedRows, index: int) -> WorkerEvent:
    call = sealed.rows[index]
    by = by_of(call)
    ids = runtime_ids(call)
    event: WorkerEvent = {
        "type": "tool_call",
        "worker_id": sealed.worker(by, label_of(call)) if by else None,
        "tool": str(call.get("name") or ""),
        "status": row_status(call),
        "input": payload(call.get("arguments")),
        "output": payload(row_output(call)),
        "at": row_started(call) or epoch_iso(call.get("ended_at")),
        "attribution": "reported",
        "row": index,
    }
    if call.get("refusal_code"):
        # Why the world refused it, as its action log coded it.
        event["refusal_code"] = str(call["refusal_code"])
    _call_place(event, sealed.placed_in(call))
    for _key, flag in PAYLOADS:
        if call.get(flag):
            event[flag] = True
    if call.get("task_label") or call.get("task_id"):
        event["task"] = str(call.get("task_label") or call["task_id"])
    if ids.get("event_id"):
        event["runtime_event_id"] = str(ids["event_id"])
    if ids.get("parent_event_id"):
        event["runtime_parent_event_id"] = str(ids["parent_event_id"])
    return event


def finished_timeline(
    events: list[WorkerEvent],
    roster: Roster,
    *,
    coordinator: str | None,
    spans_received: bool,
    turns: Iterable[CallTurn],
    started_at: str | None,
) -> WorkerTimeline:
    """The record: events in reading order within the bounds, every worker, and the call's messages."""
    # A return sorts after the work it closes; a hand-off with no start sorts just before its worker's first step.
    in_order(events)
    workers = roster.listed()
    kept, omitted = bounded(events)
    said = [
        one
        for one in turns
        if isinstance(one, Mapping)
        and one.get("role") in ("user", "agent")
        and one.get("at")
    ]
    return {
        "workers": workers,
        "events": kept,
        "events_omitted": omitted,
        "coordinator": {"name": coordinator} if coordinator else None,
        # Whether the app's own spans reached us, or the record was built from the sandbox's record of the call alone.
        "spans_received": spans_received,
        "started_at": started_at,
        "messages": [
            {"role": one["role"], "text": summary(one.get("text")), "at": one["at"]}
            for one in said[:_MAX_TURNS]
        ],
        "messages_omitted": max(len(said) - _MAX_TURNS, 0),
    }


def worker_timeline(
    spans: Iterable[WorkerSpan],
    declared: Iterable[DeclaredWorker],
    *,
    run_key: str,
    traced_calls: Iterable[SealedToolCall] = (),
    turns: Iterable[CallTurn] = (),
    started_at: str | None = None,
) -> WorkerTimeline:
    """Each worker (declared, observed or both) and the call's worker events in order; `traced_calls` are raw sealed rows."""
    roster = Roster(declared, run_key)
    tree = _SpanTree(spans, roster)
    events: list[WorkerEvent] = []
    hand_off_ids, delegating = _span_hand_offs(tree, events)
    _span_tool_calls(tree, events, delegating)
    _span_usage(tree)
    sealed = _SealedRows(traced_calls, roster)
    sealed.single_agent(bool(roster.workers))
    matched = _match_rows(sealed)
    # A launching call read as a plain tool call (no top-level span yet) is the sealed hand-off: shown once, as that.
    handed_ids = {
        one for row in matched if row["kind"] == "delegated" for one in row["ids"]
    }
    events[:] = [
        one
        for one in events
        if not (one["type"] == "tool_call" and one.get("span_id") in handed_ids)
    ]
    shown = shown_by_spans(events, matched)
    top_names = tree.top_names(sealed.handed)
    _apply_shown(sealed, shown, events, {slug(name) for name in top_names})
    launched = _launched_by(sealed, shown)
    for index, call in enumerate(sealed.rows):
        if (not by_of(call) and not call.get("delegated_to")) or index in shown:
            continue
        if index in sealed.transfers:
            _transfer_event(sealed, index, events, hand_off_ids)
        elif str(call.get("delegated_to") or "").strip():
            _hand_off_events(sealed, index, events, launched=index in launched)
        else:
            events.append(_tool_call_event(sealed, index))
    # A top-level agent span the rows show being handed work is a worker whose launcher this read lacks.
    coordinator = next(iter(top_names), None) or sealed.coordinator_label()
    return finished_timeline(
        events,
        roster,
        coordinator=coordinator,
        spans_received=bool(tree.by_id),
        turns=turns,
        started_at=started_at,
    )
