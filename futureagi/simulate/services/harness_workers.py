"""Which of a multi-agent app's workers did what in one call, from its own spans and the sandbox's record of it."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, cast

from simulate.types import (
    CallTurn,
    DeclaredWorker,
    HarnessWorker,
    TurnSummary,
    WorkerEvent,
    WorkerSpan,
    WorkerTimeline,
)

if TYPE_CHECKING:
    from simulate.models import CallExecution, HostedHarnessJob

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


def _bounded(events: list[WorkerEvent]) -> tuple[list[WorkerEvent], int]:
    """The events a record keeps, and how many it left out."""
    kept = events[:_MAX_EVENTS]
    spent = 0
    for event in kept:
        for key in ("input", "output"):
            if event.get(key) is None:
                continue
            size = len(json.dumps(event[key], default=str))
            if spent + size > _PAYLOAD_BUDGET:
                event[key] = None
                event[f"{key}_omitted"] = True
            else:
                spent += size
    return kept, len(events) - len(kept)


def _status(span: Mapping[str, Any]) -> Literal["ok", "error"]:
    return "error" if str(span.get("status") or "").upper() == "ERROR" else "ok"


def _instant(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment.astimezone(UTC).replace(tzinfo=None) if moment.tzinfo else moment


def _turn(
    turns: list[CallTurn],
    role: str,
    *,
    before: datetime | None = None,
    after: datetime | None = None,
) -> TurnSummary | None:
    """The caller's last turn up to `before`, or the agent's first turn from `after`; None when none is known."""
    timed = [
        (when, one)
        for one in turns
        if one.get("role") == role and (when := _instant(one.get("at"))) is not None
    ]
    if before is not None:
        earlier = [pair for pair in timed if pair[0] <= before]
        pick = earlier[-1] if earlier else None
    else:
        later = [pair for pair in timed if after is not None and pair[0] >= after]
        pick = later[0] if later else None
    if pick is None:
        return None
    return {"text": _summary(pick[1].get("text")), "at": pick[1].get("at")}


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
    declared_by_key = {}
    for one in declared:
        for key in (one.get("id"), one.get("name")):
            if key:
                declared_by_key[_slug(str(key))] = one

    def ancestors(span: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
        seen, parent = set(), by_id.get(str(span.get("parent_span_id") or ""))
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
    delegating = set()
    for span in by_id.values():
        if span["span_id"] not in participants or agent_ancestor(span) is None:
            continue
        worker_id = worker_of(span)
        launcher = launcher_of(span)
        if launcher is not None:
            delegating.add(launcher["span_id"])
        delegation_id = str((launcher or span)["span_id"])
        maker = agent_ancestor(span)
        by = worker_of(maker)
        events.append(
            {
                "type": "delegated",
                "worker_id": worker_id,
                "by": by,
                "delegation_id": delegation_id,
                # The hand-off the maker was itself running in, read from the span tree.
                **(
                    {"parent_delegation_id": delegation_of(maker)}
                    if by is not None
                    else {}
                ),
                "trace_id": str(span.get("trace_id") or ""),
                "task": _summary(span.get("input"))
                or _summary((launcher or {}).get("input"), argument=True),
                "at": span.get("start_time"),
                "attribution": "reported",
            }
        )
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
        events.append(
            {
                "type": "tool_call",
                "worker_id": owner_worker,
                # The hand-off the call ran in, read from the span tree.
                **(
                    {"delegation_id": delegation_of(owner)}
                    if owner_worker is not None
                    else {}
                ),
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
        )
    # Each worker's own model calls: the model it used and the tokens it spent, as the spans recorded them.
    for span in by_id.values():
        if _kind(span) != "llm" or agent_ancestor(span) is None:
            continue
        worker_id = worker_of(nearest_agent(span))
        if worker_id is None:
            continue
        entry = workers[worker_id]
        if span.get("model"):
            entry["model"] = str(span["model"])
        entry["tokens"] = int(entry.get("tokens") or 0) + int(span.get("tokens") or 0)
    # A sealed call or hand-off a span already showed is not repeated.
    shown = Counter(
        (one["worker_id"], one["tool"]) for one in events if one["type"] == "tool_call"
    )
    shown_delegations = Counter(
        one["worker_id"] for one in events if one["type"] == "delegated"
    )
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

    def when(value: Any) -> str | None:
        if isinstance(value, (int, float)) and value > 0:
            return (
                datetime.fromtimestamp(value, tz=UTC).replace(tzinfo=None).isoformat()
            )
        return None

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

    def placed_in(row: Mapping[str, Any]) -> dict[str, str]:
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

    def own_id(row: Mapping[str, Any], index: int) -> str:
        return str(
            row.get("span_id")
            or (row.get("runtime_ids") or {}).get("event_id")
            or f"traced-{index}"
        )

    for index, call in enumerate(rows):
        by = by_of(call)
        if not by and not call.get("delegated_to"):
            continue
        # A hand-off whose launcher the runtime could not name was made by the agent the caller talks to.
        worker_id = (
            traced_worker(by, str(call.get("worker_label") or by)) if by else None
        )
        runtime_ids = call.get("runtime_ids") or {}
        status: Literal["ok", "error"] = (
            "error" if call.get("ok") is False or call.get("refused") else "ok"
        )
        if index in transfers:
            target = transfers[index]
            events.append(
                {
                    "type": "transferred",
                    "worker_id": traced_worker(
                        labels.get(target, target), labels.get(target, target)
                    ),
                    "by": worker_id,
                    "delegation_id": own_id(call, index),
                    "at": started(call) or when(call.get("ended_at")),
                    "attribution": "reported",
                    **placed_in(call),
                }
            )
            continue
        target = str(call.get("delegated_to") or "").strip()
        if target:
            # A hand-off: the call that gave the work, and its result when the worker returned.
            handed_to = traced_worker(target, target)
            if shown_delegations[handed_to]:
                shown_delegations[handed_to] -= 1
                continue
            delegation_id = own_id(call, index)
            arguments = call.get("arguments")
            events.append(
                {
                    "type": "delegated",
                    "worker_id": handed_to,
                    "by": worker_id,
                    "delegation_id": delegation_id,
                    "task": _summary(
                        json.dumps(arguments)
                        if isinstance(arguments, dict)
                        else arguments,
                        argument=True,
                    ),
                    "at": started(call),
                    "attribution": "reported",
                    **placed_in(call),
                }
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
                    }
                )
            continue
        tool = str(call.get("name") or "")
        if shown[(worker_id, tool)]:
            shown[(worker_id, tool)] -= 1
            continue
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
        }
        if call.get("refusal_code"):
            # Why the world refused it, as its action log coded it.
            event["refusal_code"] = str(call["refusal_code"])
        place = placed_in(call)
        if "parent_delegation_id" in place:
            event["delegation_id"] = place["parent_delegation_id"]
        elif place.get("relationship"):
            event["relationship"] = place["relationship"]
        for key in ("input_omitted", "output_omitted"):
            if call.get(key):
                event[key] = True
        if call.get("task_label") or call.get("task_id"):
            event["task"] = str(call.get("task_label") or call["task_id"])
        if runtime_ids.get("event_id"):
            event["runtime_event_id"] = str(runtime_ids["event_id"])
        if runtime_ids.get("parent_event_id"):
            event["runtime_parent_event_id"] = str(runtime_ids["parent_event_id"])
        events.append(event)
    # A return sorts after the work it closes; a hand-off with no start sorts just before its worker's first step.
    order = {"transferred": 0, "delegated": 0, "tool_call": 1, "returned": 2}
    returns = {
        one["delegation_id"]: one["at"]
        for one in events
        if one["type"] == "returned" and one["at"]
    }

    def placed(one: Mapping[str, Any]) -> str:
        if (
            one["at"]
            or one["type"] != "delegated"
            or one["delegation_id"] not in returns
        ):
            return str(one["at"] or "")
        until = returns[one["delegation_id"]]
        steps = [
            other["at"]
            for other in events
            if other["worker_id"] == one["worker_id"]
            and other["type"] == "tool_call"
            and other["at"]
            and other["at"] <= until
        ]
        return min([until, *steps])

    events.sort(key=lambda one: (placed(one), order[one["type"]]))
    for seq, event in enumerate(events, 1):
        event["seq"] = seq
    for one in declared:
        worker_id = f"{run_key}/{one['id']}"
        workers.setdefault(
            worker_id,
            {
                "worker_id": worker_id,
                "id": str(one["id"]),
                "name": str(one.get("name") or one["id"]),
                "observed": False,
                "undeclared": False,
                "instance_ids": [],
            },
        )
    # The coordinator as the app names it: the top-level agent span, else the runtime's label for it.
    coordinator = next(
        (
            name_of(span)
            for span in by_id.values()
            if span["span_id"] in participants and agent_ancestor(span) is None
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
    handed_at = [
        moment
        for one in events
        if one["type"] in _HAND_OFF_TYPES and (moment := _instant(one["at"]))
    ]
    closed_at = [
        moment for one in events if (moment := _instant(one["at"])) is not None
    ]
    kept, omitted = _bounded(events)
    return {
        "workers": sorted(workers.values(), key=lambda one: one["id"]),
        "events": kept,
        "events_omitted": omitted,
        "coordinator": {"name": coordinator} if coordinator else None,
        # Whether the app's own spans reached us, or the record was built from the sandbox's record of the call alone.
        "spans_received": bool(by_id),
        "request": _turn(turns, "user", before=min(handed_at)) if handed_at else None,
        "response": _turn(turns, "agent", after=max(closed_at)) if handed_at else None,
        "started_at": started_at,
        "messages": [
            {"role": one["role"], "text": _summary(one.get("text")), "at": one["at"]}
            for one in turns[:_MAX_TURNS]
            if one.get("role") in ("user", "agent") and one.get("at")
        ],
    }


WORKERS_KEY = "harness_workers"
# Seconds after the result; OpenTelemetry's batch exporter delivers a span within 30 s.
FIRST_READ = 35
SECOND_READ = 600


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


# How a trial ended, for the workers still out when it did, as the call recorded it: it ended with no return from them,
# it failed outside the agent, or it was cancelled (confirmed once the platform has stopped it). `timed_out` is only
# ever the world's own record that it refused a worker past its deadline.
_STILL_OUT = {
    "ended": "no_return",
    "infrastructure": "infrastructure",
    "cancel_requested": "cancel_requested",
    "cancelled": "cancelled",
}


def worker_states(
    timeline: WorkerTimeline, *, trial: str, past_deadline: Iterable[str] = ()
) -> WorkerTimeline:
    """The timeline with each worker's end state: `completed` or `failed` once it returned, `timed_out` when the world
    refused it past its deadline (`past_deadline`, by worker id), `not_observed` when it sent nothing, else what the trial's own ending makes it. Every
    partial event stays (MA-1: no return is invented)."""
    returned = {
        event["worker_id"]: event
        for event in timeline["events"]
        if event["type"] == "returned"
    }
    # Refused past its deadline: by the trial's world changes, or by the refusal code on its own recorded calls.
    late = set(past_deadline) | {
        str(event["worker_id"]).rsplit("/", 1)[-1]
        for event in timeline["events"]
        if event.get("refusal_code") == "deadline_passed" and event.get("worker_id")
    }
    # Only a hand-off the record shows can be still out; a worker seen only through its tool calls has no recorded
    # end, so it gets no end state rather than an invented one.
    # A control pass never returns, so only a delegated hand-off with no return can be still out.
    handed = {
        event["worker_id"]
        for event in timeline["events"]
        if event["type"] == "delegated"
    }
    workers = []
    for worker in timeline["workers"]:
        back = returned.get(worker["worker_id"])
        if not worker["observed"]:
            state = "not_observed"
        elif worker["id"] in late:
            state = "timed_out"
        elif back is not None:
            state = "failed" if back.get("status") == "error" else "completed"
        elif worker["worker_id"] in handed:
            state = _STILL_OUT.get(trial, "no_return")
        else:
            workers.append(dict(worker))
            continue
        workers.append({**worker, "state": state})
    return {**timeline, "workers": workers}


def deadline_refused(call: CallExecution) -> set[str]:
    """The workers the world refused past their deadline in this call's trial, by id, from its world changes."""
    from simulate.services.hosted_harness_ingestion import TRIAL_STATE_KEY

    trial = (call.call_metadata or {}).get(TRIAL_STATE_KEY) or {}
    return {
        str(entry["who"])[len("worker:") :]
        for entry in trial.get("state_changes") or []
        if isinstance(entry, dict)
        and str(entry.get("who") or "").startswith("worker:")
        and any(
            isinstance(one, dict) and one.get("code") == "deadline_passed"
            for one in entry.get("refused_actions") or []
        )
    }


def trial_ending(call: CallExecution, job: HostedHarnessJob | None) -> str:
    """How one call's trial ended, from the call row, its receipt and the job."""
    from simulate.models import CallExecution, HostedHarnessJob

    if call.status == CallExecution.CallStatus.CANCELLED:
        # Confirmed only once the platform has stopped the run; stopping our observation is not a confirmed cancel.
        confirmed = job is not None and job.state == HostedHarnessJob.State.CANCELED
        return "cancelled" if confirmed else "cancel_requested"
    receipt = (call.call_metadata or {}).get("hosted_harness_receipt")
    # A call the job closed without a receipt ended the way the job did.
    failure = (
        receipt.get("failure") if receipt else (getattr(job, "failure", None) or {})
    ) or {}
    return "infrastructure" if failure.get("domain") not in (None, "agent") else "ended"


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
            return float(at) - offset / 1000
    return call.started_at.timestamp() if call.started_at else None


def _call_start(call: Any, sealed: list[dict[str, Any]]) -> str | None:
    start = _clock_start(call, sealed)
    return (
        datetime.fromtimestamp(start, tz=UTC).replace(tzinfo=None).isoformat()
        if start is not None
        else None
    )


def _call_turns(call: Any, sealed: list[dict[str, Any]]) -> list[CallTurn]:
    """The call's messages with when each was said, on the tool calls' clock; a message with no offset is left out."""
    from simulate.models import CallTranscript
    from simulate.models.chat_message import ChatMessageModel

    start = _clock_start(call, sealed)
    if start is None:
        return []
    # Only the caller and the agent under test; a sub-actor or an unknown speaker is neither the request nor the reply.
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
        {
            "role": role,
            "text": text,
            "at": datetime.fromtimestamp(start + offset / 1000, tz=UTC)
            .replace(tzinfo=None)
            .isoformat(),
        }
        for offset, role, text in sorted(said, key=lambda one: one[0])
    ]


def record_call_workers(call_execution_id: str) -> WorkerTimeline | None:
    """Keep one call's worker timeline on the call and return it; None when it has no worker or hand-off to keep."""
    from django.utils import timezone

    from simulate.models import CallExecution, HostedHarnessJob
    from simulate.services.run_traces_v3 import list_run_traces
    from simulate.services.scoring_status import locked_call_metadata_update
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
    spans = []
    if traces.project_id is not None:
        reader = get_reader()
        try:
            spans = reader.list_by_trace_ids(
                [one.trace_id for one in traces.traces], project_id=traces.project_id
            )
        finally:
            reader.close()
    timeline: WorkerTimeline = {
        **worker_states(
            worker_timeline(
                [_span_dict(one) for one in spans],
                declared,
                run_key=(
                    str(job.run_id) if job is not None else str(call.test_execution_id)
                ),
                traced_calls=traced_calls,
                turns=_call_turns(call, sealed),
                started_at=_call_start(call, sealed),
            ),
            trial=trial_ending(call, job),
            past_deadline=deadline_refused(call),
        ),
        "collected_at": timezone.now().isoformat(),
        # `no_evidence` when neither the app's spans nor the sandbox's record had anything to read yet.
        "collection": "complete" if (spans or traced_calls) else "no_evidence",
    }
    if not timeline["workers"] and not any(
        one["type"] in _HAND_OFF_TYPES for one in timeline["events"]
    ):
        return None

    def keep(metadata: dict) -> bool:
        metadata[WORKERS_KEY] = timeline
        return True

    locked_call_metadata_update(call.id, keep)
    return timeline
