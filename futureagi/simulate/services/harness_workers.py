"""Which of a multi-agent app's workers did what in one call, from its own spans and the sandbox's record of it."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, TypedDict, cast

import structlog

from simulate.types import (
    CallTurn,
    DeclaredWorker,
    HarnessWorker,
    WorkerEvent,
    WorkerSpan,
    WorkerTimeline,
)

if TYPE_CHECKING:
    from simulate.models import HostedHarnessJob

logger = structlog.get_logger(__name__)

AGENT_KIND = "agent"
TOOL_KIND = "tool"
_WORK_KINDS = {TOOL_KIND, "llm"}
_HAND_OFF_TYPES = frozenset({"delegated", "transferred"})
# What the runtime calls an agent instance, when it names one (OpenInference / traceAI graph node).
_NODE_ID = "graph.node.id"
_SUMMARY_CHARS = 500
# The fields a framework puts a task's or an answer's own text in, when it hands one over as a JSON object.
_TEXT_KEYS = (
    "description",
    "task",
    "input",
    "query",
    "prompt",
    "content",
    "output",
    "result",
    "answer",
    "text",
)
_PAYLOAD_CHARS = 2000
# The record is sent whole to the browser, so it stays bounded however large the team or the call.
_MAX_EVENTS = 500
_MAX_TURNS = 200
_PAYLOAD_BUDGET = 64_000


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_") or "worker"


def _kind(span: Mapping[str, Any]) -> str:
    return str(span.get("observation_type") or "").strip().lower()


def _said(message: Any) -> str:
    """What one message of a conversation says: a message object's `content`, else its `content='...'` text."""
    if isinstance(message, dict):
        return str(message.get("content") or "").strip()
    found = re.match(r"\s*content=(['\"])(.*?)(?<!\\)\1(?:\s|$)", str(message), re.S)
    return (found.group(2) if found else str(message)).strip()


def _summary(value: Any, *, argument: bool = False) -> str | None:
    # A result of 0 or False is an answer, not a missing one.
    text = "" if value is None else str(value).strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    # An agent span with no input or output of its own is written as an empty object.
    if parsed == {}:
        return None
    if isinstance(parsed, dict) and isinstance(parsed.get("messages"), list):
        # A worker that returns its whole conversation answers with its last message that says something.
        said = [_said(one) for one in parsed["messages"]]
        text = next((one for one in reversed(said) if one), text)
    elif argument and isinstance(parsed, dict) and len(parsed) == 1:
        # A worker run as a tool takes its task as that tool's one argument.
        text = str(next(iter(parsed.values()))).strip()
    elif isinstance(parsed, dict):
        text = next(
            (
                str(parsed[key]).strip()
                for key in _TEXT_KEYS
                if str(parsed.get(key) or "").strip()
            ),
            text,
        )
    elif parsed is None:
        text = _said(text)
    if "\\n" in text and "\n" not in text:
        # Text taken from a printed message object keeps its line breaks escaped; show them as breaks.
        text = text.replace("\\n", "\n").replace("\\'", "'")
    if len(text) > _SUMMARY_CHARS:
        # Say the text goes on, so a cut-off answer is not read as the whole of it.
        text = text[:_SUMMARY_CHARS].rstrip() + "…"
    return text or None


def _payload(value: Any) -> Any:
    """A tool call's input or output as recorded, cut with an ellipsis when it runs past the payload limit."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            pass
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) <= _PAYLOAD_CHARS:
        return value
    return text[:_PAYLOAD_CHARS].rstrip() + "…"


# Two records of one call can be stamped by different clocks; further apart than this, they are different calls.
_SAME_CALL_SECONDS = 5.0


def _moment(at: Any) -> float | None:
    if not isinstance(at, str) or not at:
        return None
    try:
        moment = datetime.fromisoformat(at)
    except ValueError:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp()


def _digest(value: Any) -> str | None:
    payload = _payload(value)
    return None if payload is None else json.dumps(payload, sort_keys=True, default=str)


def _shown_by_spans(
    spans: list[WorkerEvent], rows: list[dict[str, Any]]
) -> dict[int, WorkerEvent]:
    """The sealed rows a span already showed, by row index, with the span event that showed each; each span stands
    for at most one row.

    First, a row that names a span's id (its span id or runtime event id) is that span's call, for every row before
    any guess, and a row whose id names no span is another call. Then a row with no id is a span's call only when its
    input or time fits that span and nothing else could be paired: the row and the span are each other's only candidate,
    or every row and span that could be paired have the same input. An input recorded on one side only is no evidence.
    A row that can't be told apart is kept, so it can show next to its span, but no call is dropped.
    """
    open_ = [one for one in spans if one["type"] in ("tool_call", "delegated")]
    returns = {
        one.get("delegation_id"): one for one in spans if one["type"] == "returned"
    }
    shown: dict[int, WorkerEvent] = {}
    claimed: set[int] = set()
    for row in rows:
        if not row["ids"]:
            continue
        for at, span in enumerate(open_):
            # A hand-off from spans carries its launching call's span id, so the kind decides too.
            if (
                at not in claimed
                and span["type"] == row["kind"]
                and span.get("span_id") in row["ids"]
            ):
                claimed.add(at)
                shown[row["index"]] = span
                break

    def sig(span: WorkerEvent) -> str | None:
        return (
            _digest(span.get("input"))
            if span["type"] == "tool_call"
            else (span.get("task") or None)
        )

    def fits(row: dict[str, Any], span: WorkerEvent) -> bool:
        if span["type"] != row["kind"] or span.get("worker_id") != row["worker_id"]:
            return False
        if row["kind"] == "tool_call" and span.get("tool") != row["tool"]:
            return False
        # Who handed the work out, where the row names who made it.
        if (
            row["kind"] == "delegated"
            and row["by_known"]
            and span.get("by") != row["by"]
        ):
            return False
        checks = []
        if row["sig"] is not None and sig(span) is not None:
            checks.append(row["sig"] == sig(span))
        mine, theirs = _moment(row["at"]), _moment(span.get("at"))
        if mine is not None and theirs is not None:
            checks.append(abs(mine - theirs) <= _SAME_CALL_SECONDS)
        return bool(checks) and all(checks)

    loose = [row for row in rows if not row["ids"]]
    free = [at for at in range(len(open_)) if at not in claimed]
    edges = {row["index"]: [at for at in free if fits(row, open_[at])] for row in loose}
    # Rows and spans that could be each other's call, in groups that share no candidate.
    group: dict[Any, Any] = {}

    def root(node: Any) -> Any:
        while group.setdefault(node, node) != node:
            node = group[node]
        return node

    for index, spans_of in edges.items():
        for at in spans_of:
            group[root(("row", index))] = root(("span", at))
    members: dict[Any, tuple[list[dict[str, Any]], list[int]]] = {}
    for row in loose:
        if edges[row["index"]]:
            members.setdefault(root(("row", row["index"])), ([], []))[0].append(row)
    for at in free:
        if ("span", at) in group:
            members.setdefault(root(("span", at)), ([], []))[1].append(at)
    for paired_rows, paired_spans in members.values():
        sigs = {row["sig"] for row in paired_rows} | {
            sig(open_[at]) for at in paired_spans
        }
        alike = None not in sigs and len(sigs) == 1
        if not ((len(paired_rows) == 1 and len(paired_spans) == 1) or alike):
            continue
        # The same input is not the same call: a span stands for a row only when their outcomes agree too.
        left = list(paired_rows)
        for at in paired_spans:
            ended: Mapping[str, Any] = returns.get(open_[at].get("delegation_id"), {})
            match = next(
                (row for row in left if _same_outcome(row, open_[at], ended)), None
            )
            if match is not None:
                left.remove(match)
                shown[match["index"]] = open_[at]
    return shown


def _same_outcome(
    row: dict[str, Any], span: WorkerEvent, returned: Mapping[str, Any]
) -> bool:
    """Whether a sealed row and a span agree on how the call or hand-off ended, where both recorded it. A hand-off's
    end is its return."""
    if row["kind"] == "tool_call":
        status, mine, theirs = (
            span.get("status"),
            row.get("output"),
            _digest(span.get("output")),
        )
    else:
        status, mine, theirs = (
            returned.get("status"),
            row.get("result"),
            returned.get("result"),
        )
    if row.get("status") and status and row["status"] != status:
        return False
    return mine is None or theirs is None or mine == theirs


# Each payload a worker event can carry, with the flag that says it was left out.
_PAYLOADS: tuple[
    tuple[Literal["input", "output"], Literal["input_omitted", "output_omitted"]], ...
] = (("input", "input_omitted"), ("output", "output_omitted"))


class _Place(TypedDict, total=False):
    """Where the harness recorded an event ran: the hand-off it belongs to, or whether that is known."""

    parent_delegation_id: str
    relationship: Literal["observed", "unknown"]


def _bounded(events: list[WorkerEvent]) -> tuple[list[WorkerEvent], int]:
    """The events a record keeps, and how many it left out."""
    kept = events[:_MAX_EVENTS]
    spent = 0
    for event in kept:
        for key, omitted in _PAYLOADS:
            if event.get(key) is None:
                continue
            size = len(json.dumps(event[key], default=str))
            if spent + size > _PAYLOAD_BUDGET:
                event[key] = None
                event[omitted] = True
            else:
                spent += size
    return kept, len(events) - len(kept)


def _utc_iso(seconds: float) -> str | None:
    """Epoch seconds as a naive UTC timestamp; None for a value no date can hold."""
    try:
        return datetime.fromtimestamp(seconds, tz=UTC).replace(tzinfo=None).isoformat()
    except (OverflowError, ValueError, OSError):
        return None


def _runtime_ids(row: Mapping[str, Any]) -> Mapping[str, Any]:
    ids = row.get("runtime_ids")
    return ids if isinstance(ids, Mapping) else {}


def _status(span: Mapping[str, Any]) -> Literal["ok", "error"]:
    return "error" if str(span.get("status") or "").upper() == "ERROR" else "ok"


def _in_order(events: list[WorkerEvent]) -> None:
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


def worker_timeline(
    spans: Iterable[WorkerSpan],
    declared: Iterable[DeclaredWorker],
    *,
    run_key: str,
    traced_calls: Iterable[Mapping[str, Any]] = (),
    turns: Iterable[CallTurn] = (),
    started_at: str | None = None,
) -> WorkerTimeline:
    """Each worker (declared, observed or both) and the call's worker events in order; `traced_calls` are raw sealed rows."""
    by_id = {str(span["span_id"]): span for span in spans}
    declared_by_key: dict[str, DeclaredWorker] = {}
    for declared_worker in declared:
        for key in (declared_worker.get("id"), declared_worker.get("name")):
            if key:
                declared_by_key[_slug(str(key))] = declared_worker

    def ancestors(span: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
        # A span is never its own ancestor, so a cycle in the recorded parents ends the walk.
        seen, parent = (
            {span["span_id"]},
            by_id.get(str(span.get("parent_span_id") or "")),
        )
        while parent is not None and parent["span_id"] not in seen:
            yield parent
            seen.add(parent["span_id"])
            parent = by_id.get(str(parent.get("parent_span_id") or ""))

    def nearest_agent(
        span: Mapping[str, Any], among: Any = None
    ) -> Mapping[str, Any] | None:
        return next(
            (
                one
                for one in ancestors(span)
                if _kind(one) == AGENT_KIND
                and (among is None or one["span_id"] in among)
            ),
            None,
        )

    def launcher_of(agent: Mapping[str, Any]) -> Mapping[str, Any] | None:
        # The tool call that launched the worker, when the framework runs workers as tools.
        for one in ancestors(agent):
            if _kind(one) == AGENT_KIND:
                return None
            if _kind(one) == TOOL_KIND:
                return one
        return None

    agents = [span for span in by_id.values() if _kind(span) == AGENT_KIND]
    busy: set[str] = set()
    for span in by_id.values():
        owner = nearest_agent(span) if _kind(span) in _WORK_KINDS else None
        if owner is not None:
            busy.add(owner["span_id"])
    children = Counter(
        parent["span_id"] for parent in map(nearest_agent, agents) if parent is not None
    )
    # An agent span that only wraps one other agent, with no work of its own, is a framework's container.
    participants = {
        span["span_id"]
        for span in agents
        if span["span_id"] in busy or children[span["span_id"]] != 1
    }

    def agent_ancestor(span: Mapping[str, Any]) -> Mapping[str, Any] | None:
        return nearest_agent(span, participants)

    def name_of(agent: Mapping[str, Any]) -> str:
        return str(
            (agent.get("attributes") or {}).get(_NODE_ID)
            or agent.get("name")
            or "agent"
        )

    workers: dict[str, HarnessWorker] = {}

    def entry_for(
        local: str, name: str, match: Mapping[str, Any] | None, instance: str
    ) -> str:
        worker_id = f"{run_key}/{local}"
        entry = workers.setdefault(
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

    def worker_of(agent: Mapping[str, Any] | None) -> str | None:
        # A top-level agent span is the coordinator, not a worker.
        if agent is None or agent_ancestor(agent) is None:
            return None
        name = name_of(agent)
        match = declared_by_key.get(_slug(name))
        return entry_for(
            str(match["id"]) if match is not None else _slug(name),
            name,
            match,
            str(agent["span_id"]),
        )

    def delegation_of(agent: Mapping[str, Any]) -> str:
        return str((launcher_of(agent) or agent)["span_id"])

    events: list[WorkerEvent] = []
    # Every id a span hand-off is known by, under its delegation id.
    hand_off_ids: dict[str, set[str]] = {}
    delegating = set()
    for span in by_id.values():
        if span["span_id"] not in participants or agent_ancestor(span) is None:
            continue
        worker_id = worker_of(span)
        launcher = launcher_of(span)
        if launcher is not None:
            delegating.add(launcher["span_id"])
        delegation_id = str((launcher or span)["span_id"])
        hand_off_ids.setdefault(delegation_id, set()).update(
            {delegation_id, str(span["span_id"])}
        )
        maker = agent_ancestor(span)
        by = worker_of(maker)
        hand_off: WorkerEvent = {
            "type": "delegated",
            "worker_id": worker_id,
            "by": by,
            "delegation_id": delegation_id,
            # The span the hand-off was made in, for opening it in Traces: its launching call, else its agent.
            "span_id": delegation_id,
            "trace_id": str(span.get("trace_id") or ""),
            "task": _summary(span.get("input"))
            or _summary((launcher or {}).get("input"), argument=True),
            "at": span.get("start_time"),
            "attribution": "reported",
        }
        if by is not None and maker is not None:
            # The hand-off the maker was itself running in, read from the span tree.
            hand_off["parent_delegation_id"] = delegation_of(maker)
        events.append(hand_off)
        if span.get("end_time"):
            events.append(
                {
                    "type": "returned",
                    "worker_id": worker_id,
                    "delegation_id": delegation_id,
                    "result": _summary(span.get("output"))
                    or _summary((launcher or {}).get("output")),
                    "status": _status(span),
                    "at": span.get("end_time"),
                    "attribution": "reported",
                }
            )
    for span in by_id.values():
        if _kind(span) != TOOL_KIND or span["span_id"] in delegating:
            continue
        owner = agent_ancestor(span)
        owner_worker = worker_of(owner)
        made: WorkerEvent = {
            "type": "tool_call",
            "worker_id": owner_worker,
            "tool": str(span.get("name") or ""),
            "span_id": str(span["span_id"]),
            "trace_id": str(span.get("trace_id") or ""),
            "status": _status(span),
            "input": _payload(span.get("input")),
            "output": _payload(span.get("output")),
            "at": span.get("start_time"),
            # No agent span above the call: neither the coordinator nor a worker can be named for it.
            "attribution": "reported" if owner is not None else "unattributed",
        }
        if owner_worker is not None and owner is not None:
            # The hand-off the call ran in, read from the span tree.
            made["delegation_id"] = delegation_of(owner)
        events.append(made)
    # Each worker's own model calls: the models it used, in the order first seen, and the tokens it spent, as the
    # spans recorded them.
    for span in by_id.values():
        owner = agent_ancestor(span) if _kind(span) == "llm" else None
        worker_id = worker_of(owner) if owner is not None else None
        if worker_id is None:
            continue
        entry = workers[worker_id]
        model = str(span.get("model") or "")
        used = [one for one in str(entry.get("model") or "").split(", ") if one]
        if model and model not in used:
            entry["model"] = ", ".join([*used, model])
        entry["tokens"] = int(entry.get("tokens") or 0) + int(span.get("tokens") or 0)
    rows = [call for call in traced_calls if isinstance(call, Mapping)]

    def by_of(row: Mapping[str, Any]) -> str:
        # The runtime's stable worker id, else its label for the worker.
        return str(row.get("worker_id") or row.get("worker_label") or "").strip()

    def declared_row(row: Mapping[str, Any]) -> bool:
        # A runtime may name a worker by an opaque id and give its declared name only as the label.
        return any(
            _slug(name) in declared_by_key
            for name in (by_of(row), str(row.get("worker_label") or ""))
            if name
        )

    handed = {
        _slug(str(row["delegated_to"])) for row in rows if row.get("delegated_to")
    }
    named = {_slug(by_of(row)) for row in rows if by_of(row)}
    labels = {
        _slug(by_of(row)): str(row.get("worker_label") or by_of(row))
        for row in rows
        if by_of(row)
    }
    # A harness that records each event's kind and parent is read as recorded. A control pass is only ever the one
    # the harness recorded: never read from a tool's name or arguments.
    recorded = any(row.get("kind") for row in rows)
    transfers = {
        index: target
        for index, row in enumerate(rows)
        if row.get("kind") == "transferred"
        and (target := str(row.get("delegated_to") or "").strip())
    }
    controlled = set(transfers.values())
    # The agent that hands work out and is never handed any is the coordinator, unless it is a declared worker.
    top = (
        {_slug(by_of(row)) for row in rows if by_of(row) and not declared_row(row)}
        - handed
        - controlled
    )
    coordinators = {
        _slug(by_of(row))
        for row in rows
        if row.get("delegated_to") and _slug(by_of(row)) in top
    }
    coordinators |= {_slug(by_of(rows[index])) for index in transfers} & top
    # So is the one agent of an app that hands nothing out.
    if (
        not handed
        and len(named) == 1
        and not workers
        and not any(declared_row(row) for row in rows if by_of(row))
    ):
        coordinators = named

    def traced_worker(by: str, label: str) -> str | None:
        if _slug(by) in coordinators:
            return None
        match = declared_by_key.get(_slug(by)) or declared_by_key.get(_slug(label))
        return entry_for(
            str(match["id"]) if match is not None else _slug(by), label, match, by
        )

    def traced_id(by: str, label: str) -> str | None:
        """The worker id `traced_worker` gives, without adding the worker: matching a row against the spans
        registers nothing, so a row the spans showed adds no worker."""
        if _slug(by) in coordinators:
            return None
        match = declared_by_key.get(_slug(by)) or declared_by_key.get(_slug(label))
        return f"{run_key}/{str(match['id']) if match is not None else _slug(by)}"

    def when(value: Any) -> str | None:
        return (
            _utc_iso(value) if isinstance(value, (int, float)) and value > 0 else None
        )

    def started(row: Mapping[str, Any]) -> str | None:
        # A row stamped after the call ended carries when it was written, not when the call began.
        at, ended = row.get("at"), row.get("ended_at")
        if (
            isinstance(at, (int, float))
            and isinstance(ended, (int, float))
            and 0 < ended < at
        ):
            return None
        return when(at)

    def placed_in(row: Mapping[str, Any]) -> _Place:
        """Where the harness recorded this event ran: the hand-off it belongs to, or that it does not know."""
        if not recorded:
            return {}
        if row.get("relationship") != "observed":
            return {"relationship": "unknown"}
        # Observed with no parent: nothing handed this work out, so it sits at the top.
        parent = str(row.get("parent_span_id") or "")
        return (
            {"parent_delegation_id": parent} if parent else {"relationship": "observed"}
        )

    def put(event: WorkerEvent, place: _Place) -> WorkerEvent:
        if "parent_delegation_id" in place:
            event["parent_delegation_id"] = place["parent_delegation_id"]
        if "relationship" in place:
            event["relationship"] = place["relationship"]
        return event

    def ids_of(row: Mapping[str, Any]) -> set[str]:
        # The ids a runtime gave this call: its span, and its own event id.
        return {
            str(one)
            for one in (
                row.get("span_id"),
                _runtime_ids(row).get("event_id"),
            )
            if one
        }

    def own_id(row: Mapping[str, Any], index: int) -> str:
        return str(
            row.get("span_id") or _runtime_ids(row).get("event_id") or f"traced-{index}"
        )

    # A sealed call or hand-off a span already showed is not repeated.
    sealed: list[dict[str, Any]] = []
    for index, call in enumerate(rows):
        by, target = by_of(call), str(call.get("delegated_to") or "").strip()
        if (not by and not target) or index in transfers:
            continue
        arguments = call.get("arguments")
        sealed.append(
            {
                "index": index,
                "ids": ids_of(call),
                "kind": "delegated" if target else "tool_call",
                "worker_id": (
                    traced_id(target, target)
                    if target
                    else traced_id(by, str(call.get("worker_label") or by))
                ),
                "by": (
                    traced_id(by, str(call.get("worker_label") or by)) if by else None
                ),
                "by_known": bool(by),
                "tool": str(call.get("name") or ""),
                # A hand-off has an outcome only once it returned.
                "status": (
                    None
                    if target and not call.get("ended_at")
                    else (
                        "error"
                        if call.get("ok") is False or call.get("refused")
                        else "ok"
                    )
                ),
                "result": _summary(call.get("result")) if target else None,
                "output": _digest(
                    call.get("result")
                    if call.get("result") is not None
                    else call.get("error")
                ),
                "sig": (
                    _summary(
                        (
                            json.dumps(arguments)
                            if isinstance(arguments, dict)
                            else arguments
                        ),
                        argument=True,
                    )
                    or None
                    if target
                    else _digest(arguments)
                ),
                "at": (
                    started(call)
                    if target
                    else started(call) or when(call.get("ended_at"))
                ),
            }
        )
    # A launching call the spans read as a plain tool call (its agent run not read as a hand-off, as when the read has
    # no top-level span yet) is the sealed hand-off with its id, shown once, as the hand-off.
    handed_ids = {
        one for row in sealed if row["kind"] == "delegated" for one in row["ids"]
    }
    events[:] = [
        one
        for one in events
        if not (one["type"] == "tool_call" and one.get("span_id") in handed_ids)
    ]
    shown = _shown_by_spans(events, sealed)
    # The sealed row is the call as the harness recorded it while it ran; the span shows it, links to it and times
    # it, but who made it, where it sits and how it ended are the row's, so an incomplete span tree never moves a
    # worker's call to the coordinator or reads a refusal as a success.
    # The agent the spans read at the top, unless the rows show it being handed work, is the coordinator: a row naming
    # it is the coordinator's own call, not a worker's.
    span_top = {
        _slug(name_of(span))
        for span in by_id.values()
        if span["span_id"] in participants
        and agent_ancestor(span) is None
        and _slug(name_of(span)) not in handed
    }

    def row_worker(by: str, label: str) -> str | None:
        if _slug(by) in span_top or _slug(label) in span_top:
            return None
        return traced_worker(by, label)

    for index, span_event in shown.items():
        call = rows[index]
        by = by_of(call)
        label = str(call.get("worker_label") or by)
        if span_event["type"] == "tool_call":
            if by:
                span_event["worker_id"] = row_worker(by, label)
            span_event["status"] = (
                "error" if call.get("ok") is False or call.get("refused") else "ok"
            )
            ended = (
                call.get("result")
                if call.get("result") is not None
                else call.get("error")
            )
            if ended is not None:
                span_event["output"] = _payload(ended)
            place = placed_in(call)
            if place:
                span_event.pop("delegation_id", None)
                span_event.pop("relationship", None)
                if "parent_delegation_id" in place:
                    span_event["delegation_id"] = place["parent_delegation_id"]
                elif place.get("relationship"):
                    span_event["relationship"] = place["relationship"]
        else:
            target = str(call.get("delegated_to") or "").strip()
            if target:
                span_event["worker_id"] = traced_worker(target, target)
            if by:
                span_event["by"] = row_worker(by, label)
            for one in events:
                if one["type"] == "returned" and one.get(
                    "delegation_id"
                ) == span_event.get("delegation_id"):
                    one["worker_id"] = span_event["worker_id"]
                    if call.get("ended_at"):
                        one["status"] = (
                            "error"
                            if call.get("ok") is False or call.get("refused")
                            else "ok"
                        )
                        one["result"] = _summary(call.get("result"))
    # Sealed rows are the same in every read, so an event keeps the row it stands for: a read without spans and a
    # later one with them then join on it.
    for index, span_event in shown.items():
        span_event["row"] = index
        if span_event["type"] == "delegated":
            for one in events:
                if one["type"] == "returned" and one.get(
                    "delegation_id"
                ) == span_event.get("delegation_id"):
                    one["row"] = index
    for index, call in enumerate(rows):
        by = by_of(call)
        if (not by and not call.get("delegated_to")) or index in shown:
            continue
        # A hand-off whose launcher the runtime could not name was made by the agent the caller talks to.
        worker_id = (
            traced_worker(by, str(call.get("worker_label") or by)) if by else None
        )
        runtime_ids = _runtime_ids(call)
        status: Literal["ok", "error"] = (
            "error" if call.get("ok") is False or call.get("refused") else "ok"
        )
        if index in transfers:
            target = transfers[index]
            # Spans may have read this control pass as a hand-off, keyed on its launching call or its agent run: the
            # recorded transfer replaces it, and what ran inside it now sits under the transfer.
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
            # The link is the span the hand-off was read from, its launching call: Open in Traces lands on that call,
            # not on the agent run it started.
            for one in events:
                if one.get("delegation_id") in ids:
                    one["delegation_id"] = own_id(call, index)
                if one.get("parent_delegation_id") in ids:
                    one["parent_delegation_id"] = own_id(call, index)
            transferred: WorkerEvent = {
                "type": "transferred",
                "worker_id": traced_worker(
                    labels.get(target, target), labels.get(target, target)
                ),
                "by": worker_id,
                "delegation_id": own_id(call, index),
                "at": started(call) or when(call.get("ended_at")),
                "attribution": "reported",
                "row": index,
            }
            put(transferred, placed_in(call))
            if from_spans and from_spans[0].get("span_id"):
                transferred["span_id"] = from_spans[0]["span_id"]
            if from_spans and from_spans[0].get("trace_id"):
                transferred["trace_id"] = from_spans[0]["trace_id"]
            events.append(transferred)
            continue
        target = str(call.get("delegated_to") or "").strip()
        if target:
            # A hand-off: the call that gave the work, and its result when the worker returned.
            handed_to = traced_worker(target, target)
            arguments = call.get("arguments")
            task = _summary(
                json.dumps(arguments) if isinstance(arguments, dict) else arguments,
                argument=True,
            )
            delegation_id = own_id(call, index)
            events.append(
                put(
                    {
                        "type": "delegated",
                        "worker_id": handed_to,
                        "by": worker_id,
                        "delegation_id": delegation_id,
                        "task": task,
                        "at": started(call),
                        "attribution": "reported",
                        "row": index,
                    },
                    placed_in(call),
                )
            )
            if when(call.get("ended_at")):
                events.append(
                    {
                        "type": "returned",
                        "worker_id": handed_to,
                        "delegation_id": delegation_id,
                        "result": _summary(call.get("result")),
                        "status": status,
                        "at": when(call.get("ended_at")),
                        "attribution": "reported",
                        "row": index,
                    }
                )
            continue
        tool = str(call.get("name") or "")
        event: WorkerEvent = {
            "type": "tool_call",
            "worker_id": worker_id,
            "tool": tool,
            "status": status,
            "input": _payload(call.get("arguments")),
            "output": _payload(
                call.get("result")
                if call.get("result") is not None
                else call.get("error")
            ),
            "at": started(call) or when(call.get("ended_at")),
            "attribution": "reported",
            "row": index,
        }
        place = placed_in(call)
        if "parent_delegation_id" in place:
            event["delegation_id"] = place["parent_delegation_id"]
        elif place.get("relationship"):
            event["relationship"] = place["relationship"]
        for _key, flag in _PAYLOADS:
            if call.get(flag):
                event[flag] = True
        if call.get("task_label") or call.get("task_id"):
            event["task"] = str(call.get("task_label") or call["task_id"])
        if runtime_ids.get("event_id"):
            event["runtime_event_id"] = str(runtime_ids["event_id"])
        if runtime_ids.get("parent_event_id"):
            event["runtime_parent_event_id"] = str(runtime_ids["parent_event_id"])
        events.append(event)
    # A return sorts after the work it closes; a hand-off with no start sorts just before its worker's first step.
    _in_order(events)
    for declared_worker in declared:
        worker_id = f"{run_key}/{declared_worker['id']}"
        workers.setdefault(
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
    # The coordinator as the app names it: the top-level agent span, else the runtime's label for it.
    # A top-level agent span the sealed rows show being handed work is a worker whose launcher this read lacks, not the
    # coordinator.
    coordinator = next(
        (
            name_of(span)
            for span in by_id.values()
            if span["span_id"] in participants
            and agent_ancestor(span) is None
            and _slug(name_of(span)) not in handed
        ),
        None,
    ) or next(
        (
            str(row.get("worker_label") or by_of(row))
            for row in rows
            if _slug(by_of(row)) in coordinators
        ),
        None,
    )
    turns = [one for one in turns if isinstance(one, Mapping)]
    kept, omitted = _bounded(events)
    said = [
        one for one in turns if one.get("role") in ("user", "agent") and one.get("at")
    ]
    return {
        "workers": sorted(workers.values(), key=lambda one: one["id"]),
        "events": kept,
        "events_omitted": omitted,
        "coordinator": {"name": coordinator} if coordinator else None,
        # Whether the app's own spans reached us, or the record was built from the sandbox's record of the call alone.
        "spans_received": bool(by_id),
        "started_at": started_at,
        "messages": [
            {"role": one["role"], "text": _summary(one.get("text")), "at": one["at"]}
            for one in said[:_MAX_TURNS]
        ],
        "messages_omitted": max(len(said) - _MAX_TURNS, 0),
    }


WORKERS_KEY = "harness_workers"
# Seconds after the result; OpenTelemetry's batch exporter delivers a span within 30 s.
FIRST_READ = 35
SECOND_READ = 600
# One call's span read: at most this many spans (past it the timeline is `partial`), for at most this long.
MAX_SPANS = 5000
SPAN_READ_CAP_MS = 30_000


def _span_dict(span: Any) -> WorkerSpan:
    return {
        "span_id": str(span.id),
        "parent_span_id": span.parent_span_id or None,
        "name": span.name,
        "observation_type": span.observation_type,
        "start_time": span.start_time.isoformat() if span.start_time else None,
        "end_time": span.end_time.isoformat() if span.end_time else None,
        "status": span.status,
        "input": span.input,
        "output": span.output,
        "attributes": dict(span.attrs_string or {}),
        "trace_id": str(span.trace_id or ""),
        "model": span.model or "",
        "tokens": int(span.total_tokens or 0),
    }


def declared_workers(job: HostedHarnessJob) -> list[DeclaredWorker]:
    """The workers the contract of the version the run was pinned to declares."""
    version = job.environment_version or (
        job.environment.environment_version if job.environment else None
    )
    contract = (
        (version.documents or {}).get("contract") if version is not None else None
    ) or {}
    return [
        cast(DeclaredWorker, one)
        for one in contract.get("workers") or []
        if isinstance(one, dict) and one.get("id")
    ]


def _clock_start(call: Any, sealed: list[dict[str, Any]]) -> float | None:
    """Epoch seconds the call's `start_time_ms` offsets count from: a sealed tool call's, else the call's start."""
    for one in sealed:
        at, offset = one.get("at"), one.get("start_time_ms")
        if isinstance(at, (int, float)) and isinstance(offset, (int, float)):
            start = float(at) - offset / 1000
            if _utc_iso(start) is not None:
                return start
    return call.started_at.timestamp() if call.started_at else None


def _call_start(call: Any, sealed: list[dict[str, Any]]) -> str | None:
    start = _clock_start(call, sealed)
    return _utc_iso(start) if start is not None else None


def _call_turns(call: Any, sealed: list[dict[str, Any]]) -> list[CallTurn]:
    """The call's messages with when each was said, on the tool calls' clock; a message with no offset is left out."""
    from simulate.models import CallTranscript
    from simulate.models.chat_message import ChatMessageModel

    start = _clock_start(call, sealed)
    if start is None:
        return []
    # Only the caller and the agent under test; any other speaker is neither the request nor the reply.
    roles: dict[str, Literal["user", "agent"]] = {"user": "user", "assistant": "agent"}
    said = []
    for message in ChatMessageModel.objects.filter(call_execution=call).order_by(
        "created_at"
    ):
        for item in message.content if isinstance(message.content, list) else []:
            if (
                isinstance(item, dict)
                and isinstance(item.get("start_time_ms"), (int, float))
                and message.role in roles
            ):
                said.append(
                    (item["start_time_ms"], roles[message.role], item.get("content"))
                )
    if not said:
        said = [
            (row.start_time_ms, roles[row.speaker_role], row.content)
            for row in CallTranscript.objects.filter(
                call_execution=call, start_time_ms__isnull=False
            )
            if row.speaker_role in roles
        ]
    return [
        {"role": role, "text": text, "at": at}
        for offset, role, text in sorted(said, key=lambda one: one[0])
        if (at := _utc_iso(start + offset / 1000)) is not None
    ]


# How far a read got, best first. A record without the field says nothing about its read, so it ranks with a failed one.
_COLLECTION_RANK = {
    "complete": 3,
    "partial": 2,
    "unavailable": 1,
    None: 1,
    "no_evidence": 0,
}


def _event_keys(one: Mapping[str, Any]) -> set[tuple[Any, ...]]:
    """What says which call or hand-off an event is, across reads: its recorded id, the sealed row it stands for, and,
    with neither id, everything it recorded. Two events from two reads are one when any of these is shared.
    """
    keys: set[tuple[Any, ...]] = set()
    ident = one.get("span_id") or one.get("runtime_event_id")
    if one["type"] != "tool_call":
        ident = one.get("delegation_id") or ident
    if ident:
        keys.add((one["type"], str(ident)))
    # A launching call one read saw as a tool call is the hand-off another read saw with the same id.
    launch = (
        one.get("span_id")
        if one["type"] == "tool_call"
        else one.get("delegation_id") or one.get("span_id")
    )
    if launch and one["type"] in ("tool_call", "delegated", "transferred"):
        keys.add(("launch", str(launch)))
    if one.get("row") is not None:
        keys.add((one["type"], "row", one["row"]))
    if not ident:
        keys.add(
            (
                one["type"],
                one.get("worker_id"),
                one.get("tool"),
                one.get("at"),
                _digest(one.get("input")),
                one.get("task"),
                one.get("result"),
            )
        )
    return keys


def _has_roots(spans: list[Any]) -> bool:
    """Whether every trace read has its top-level span: a trace whose spans all name a parent lacks its root."""
    rooted: dict[str, bool] = {}
    for one in spans:
        trace = str(getattr(one, "trace_id", "") or "")
        rooted[trace] = rooted.get(trace, False) or not getattr(
            one, "parent_span_id", None
        )
    return all(rooted.values())


def kept_record(stored: Any, read: WorkerTimeline) -> WorkerTimeline:
    """What a call keeps after another read: everything either read found.

    Events are joined by their recorded ids, so no event either read kept is lost, and a worker either read observed
    stays observed. The record's other fields come from the read that found spans, then got further, then had every
    trace's top-level span; on a tie, the later read. Its `collection` is the best of the reads that found spans, if any did.
    """
    if not isinstance(stored, Mapping) or not (
        stored.get("events") or stored.get("workers")
    ):
        return read

    def weight(one: Mapping[str, Any]) -> tuple[bool, int, bool]:
        # Not how many events a read found: an incomplete span tree finds more (a launcher read twice), not better.
        return (
            bool(one.get("spans_received")),
            _COLLECTION_RANK.get(one.get("collection"), 0),
            bool(one.get("tree_complete", True)),
        )

    # On a tie, the later read: it saw every span the earlier one did, and more.
    best, other = (read, stored) if weight(read) >= weight(stored) else (stored, read)
    events = [cast(WorkerEvent, dict(one)) for one in best.get("events") or []]
    # Each held event matches at most one, so two events alike in everything they recorded stay two.
    held = [_event_keys(one) for one in events]
    for one in other.get("events") or []:
        keys = _event_keys(one)
        match = next((at for at, mine in enumerate(held) if mine & keys), None)
        if match is None:
            events.append(cast(WorkerEvent, dict(one)))
        else:
            held[match] = set()
    _in_order(events)
    omitted = max(
        int(best.get("events_omitted") or 0), int(other.get("events_omitted") or 0)
    )
    # The merged record keeps the same bounds as one read: as many events, and as much payload.
    events, cut = _bounded(events)
    omitted += cut
    workers = [dict(one) for one in best.get("workers") or []]
    by_id = {one["worker_id"]: one for one in workers}
    for one in other.get("workers") or []:
        if not one.get("observed"):
            continue
        mine = by_id.get(one["worker_id"])
        if mine is None:
            workers.append(dict(one))
        elif not mine.get("observed"):
            mine["observed"] = True
            mine["instance_ids"] = list(
                dict.fromkeys(
                    [
                        *(mine.get("instance_ids") or []),
                        *(one.get("instance_ids") or []),
                    ]
                )
            )
    traced = [one for one in (stored, read) if one.get("spans_received")] or [
        stored,
        read,
    ]
    collection = max(
        (one.get("collection") for one in traced),
        key=lambda name: _COLLECTION_RANK.get(name, 0),
    )
    return cast(
        WorkerTimeline,
        {
            **best,
            "events": events,
            "events_omitted": omitted,
            "workers": workers,
            "spans_received": any(one.get("spans_received") for one in (stored, read)),
            "collection": collection,
        },
    )


def attempt_reset(call_execution_id: Any) -> datetime | None:
    """When the call was last reset for a rerun of the call itself; None when it never was."""
    from simulate.models.test_execution import CallExecutionSnapshot

    return (
        CallExecutionSnapshot.objects.filter(
            call_execution_id=call_execution_id,
            rerun_type=CallExecutionSnapshot.RerunType.CALL_AND_EVAL,
        )
        .order_by("-snapshot_timestamp")
        .values_list("snapshot_timestamp", flat=True)
        .first()
    )


def _epoch(at: datetime) -> float:
    return (at if at.tzinfo else at.replace(tzinfo=UTC)).timestamp()


def _begun_after(spans: list[Any], since: float) -> list[Any]:
    """The spans of the traces whose first span began after `since`; a trace with no start time is kept."""
    first: dict[str, float] = {}
    for one in spans:
        if one.start_time is not None:
            trace, at = str(one.trace_id or ""), _epoch(one.start_time)
            first[trace] = min(first.get(trace, at), at)
    return [
        one for one in spans if first.get(str(one.trace_id or ""), since + 1) > since
    ]


def record_call_workers(call_execution_id: str) -> WorkerTimeline | None:
    """Keep one call's worker timeline on the call and return it; None when it has no worker or hand-off to keep."""
    from django.utils import timezone

    from simulate.models import CallExecution, HostedHarnessJob
    from simulate.services.run_traces_v3 import list_run_traces
    from simulate.services.scoring_status import locked_call_metadata_update
    from tracer.services.clickhouse.application_read_policy import (
        application_read_context,
    )
    from tracer.services.clickhouse.v2 import get_reader

    call = CallExecution.objects.select_related("test_execution").get(
        id=call_execution_id
    )
    traces = list_run_traces(call.test_execution, call_execution_id=call.id)
    sealed = [
        one
        for one in ((call.provider_call_data or {}).get("livekit") or {}).get(
            "tool_calls"
        )
        or []
        if isinstance(one, dict)
    ]
    traced_calls = [
        one
        for one in sealed
        if one.get("worker_id") or one.get("worker_label") or one.get("delegated_to")
    ]
    job = HostedHarnessJob.no_workspace_objects.filter(
        test_execution=call.test_execution
    ).first()
    declared = declared_workers(job) if job is not None else []
    # A multi-agent app whose call left no spans and no attributed rows is still recorded, with every declared
    # worker not observed, so it never reads like a single-agent call and the later read runs.
    if traces.project_id is None and not traced_calls and not declared:
        return None
    spans, partial, unavailable = [], False, False
    # A rerun keeps the call's id, so the earlier attempt's traces are found with it: only those begun after the
    # call was reset for this attempt are read.
    reset = attempt_reset(call.id)
    since = reset.timestamp() if reset is not None else None
    if traces.project_id is not None:
        with application_read_context(execution_cap_ms=SPAN_READ_CAP_MS):
            reader = get_reader()
            try:
                spans = reader.list_by_trace_ids(
                    [
                        one.trace_id
                        for one in traces.traces
                        if since is None
                        or one.start_time is None
                        or _epoch(one.start_time) > since
                    ],
                    project_id=traces.project_id,
                    include_heavy=False,
                    limit=MAX_SPANS + 1,
                )
            except Exception:
                # Kept and returned rather than raised: a retry would repeat the read on a loaded ClickHouse, and
                # a declared worker left unobserved still gets the later read.
                logger.warning(
                    "call_workers_span_read_failed",
                    call_execution_id=str(call.id),
                    exc_info=True,
                )
                unavailable = True
            finally:
                reader.close()
        if since is not None:
            spans = _begun_after(spans, since)
        if len(spans) > MAX_SPANS:
            spans, partial = spans[:MAX_SPANS], True
    timeline = worker_timeline(
        [_span_dict(one) for one in spans],
        declared,
        run_key=str(job.run_id) if job is not None else str(call.test_execution_id),
        traced_calls=traced_calls,
        turns=_call_turns(call, sealed),
        started_at=_call_start(call, sealed),
    )
    timeline["collected_at"] = timezone.now().isoformat()
    # Spans arrive as each one ends, so an early read can lack the top-level span (it ends last) or stop at the row
    # bound: such a read is ranked below a whole one and gets the later read.
    timeline["tree_complete"] = not partial and _has_roots(spans)
    # `unavailable` when the span read failed or ran out of time, `partial` when it hit its row bound,
    # `no_evidence` when neither the app's spans nor the sandbox's record had anything for this read.
    timeline["collection"] = (
        "unavailable"
        if unavailable
        else "partial"
        if partial
        else "complete"
        if (spans or traced_calls)
        else "no_evidence"
    )
    if not timeline["workers"] and not any(
        one["type"] in _HAND_OFF_TYPES for one in timeline["events"]
    ):
        return None

    kept: list[WorkerTimeline] = []

    def keep(metadata: dict) -> bool:
        # A later read adds what it found, and never takes away what an earlier one kept.
        kept.append(kept_record(metadata.get(WORKERS_KEY), timeline))
        metadata[WORKERS_KEY] = kept[-1]
        return True

    locked_call_metadata_update(call.id, keep)
    return kept[-1] if kept else timeline
