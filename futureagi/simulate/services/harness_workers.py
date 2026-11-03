"""Which of a multi-agent app's workers did what in one call, from the spans its own process exported.

Generic over frameworks: a delegation is an agent-kind span nested under another agent-kind span, keyed by the tool call
that launched it when there is one; a tool call belongs to its nearest agent-kind ancestor. An agent-kind span that only
wraps one other agent (no model or tool call of its own) is a framework's container, such as the OpenAI Agents SDK's
workflow span, and is skipped. Declared worker names are not proof: a worker counts as observed only when its own spans
arrive. Spans are written by the customer's process, so
everything here is `reported` attribution; simulated actors never appear, because they are never in the agent's spans.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

AGENT_KIND = "agent"
TOOL_KIND = "tool"
_WORK_KINDS = {TOOL_KIND, "llm"}
# What the runtime calls an agent instance, when it names one (OpenInference / traceAI graph node).
_NODE_ID = "graph.node.id"
_SUMMARY_CHARS = 500
_PAYLOAD_CHARS = 2000
# The record is kept on the call and sent whole to the browser, so it stays bounded however large the team or the call:
# the first steps up to a count, and tool inputs and outputs up to a total size. A step past the size keeps its tool,
# worker and time, and says its payload was left out.
_MAX_EVENTS = 2000
_PAYLOAD_BUDGET = 256_000


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_") or "worker"


def _kind(span: Mapping[str, Any]) -> str:
    return str(span.get("observation_type") or "").strip().lower()


def _summary(value: Any, *, argument: bool = False) -> str | None:
    text = str(value or "").strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    # An agent span with no input or output of its own is written as an empty object; a worker run as a tool takes its
    # task as that tool's one argument.
    if parsed == {}:
        return None
    if argument and isinstance(parsed, dict) and len(parsed) == 1:
        text = str(next(iter(parsed.values()))).strip()
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


def _bounded(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
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
                event["payload_omitted"] = True
            else:
                spent += size
    return kept, len(events) - len(kept)


def _instant(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment.astimezone(UTC).replace(tzinfo=None) if moment.tzinfo else moment


def _turn(turns: list[Mapping[str, Any]], role: str, *, before: datetime | None = None, after: datetime | None = None):
    """The caller's last turn up to `before`, or the agent's first turn from `after`; None when none is known."""
    timed = [(when, one) for one in turns if one.get("role") == role and (when := _instant(one.get("at"))) is not None]
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
    spans: Iterable[Mapping[str, Any]],
    declared: Iterable[Mapping[str, Any]],
    *,
    run_key: str,
    traced_calls: Iterable[Mapping[str, Any]] = (),
    turns: Iterable[Mapping[str, Any]] = (),
    started_at: str | None = None,
) -> dict[str, Any]:
    """Each worker (declared, observed or both) and the run's worker events in order: `delegated`, the worker's
    `tool_call`s (with their input and output), `returned`. `spans` are dicts with `span_id`, `parent_span_id`, `name`,
    `observation_type`, `start_time`, `end_time`, `status`, `input`, `output` and `attributes`. `traced_calls` are the
    call's sealed tool-trace rows; one a runtime attributed to a worker (`by`, with its `task` and `runtime_ids`) is
    that worker's tool call, kept as reported, unless a span already showed the same call. `turns` are the call's
    messages (`role` "user" or "agent", `text`, `at`): the caller's request before the first hand-off and the
    coordinator's reply after the last return frame the timeline, each None when it was not seen."""
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

    def nearest_agent(span: Mapping[str, Any], among: Any = None) -> Mapping[str, Any] | None:
        return next(
            (one for one in ancestors(span) if _kind(one) == AGENT_KIND and (among is None or one["span_id"] in among)),
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
    children = Counter(parent["span_id"] for parent in map(nearest_agent, agents) if parent is not None)
    participants = {span["span_id"] for span in agents if span["span_id"] in busy or children[span["span_id"]] != 1}

    def agent_ancestor(span: Mapping[str, Any]) -> Mapping[str, Any] | None:
        return nearest_agent(span, participants)

    def name_of(agent: Mapping[str, Any]) -> str:
        return str((agent.get("attributes") or {}).get(_NODE_ID) or agent.get("name") or "agent")

    workers: dict[str, dict[str, Any]] = {}

    def worker_of(agent: Mapping[str, Any] | None) -> str | None:
        # A top-level agent span is the coordinator, not a worker.
        if agent is None or agent_ancestor(agent) is None:
            return None
        name = name_of(agent)
        match = declared_by_key.get(_slug(name))
        local = str(match["id"]) if match is not None else _slug(name)
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
        if agent["span_id"] not in entry["instance_ids"]:
            entry["instance_ids"].append(str(agent["span_id"]))
        return worker_id

    events: list[dict[str, Any]] = []
    delegating = set()
    for span in by_id.values():
        if span["span_id"] not in participants or agent_ancestor(span) is None:
            continue
        worker_id = worker_of(span)
        launcher = launcher_of(span)
        if launcher is not None:
            delegating.add(launcher["span_id"])
        delegation_id = str((launcher or span)["span_id"])
        by = worker_of(agent_ancestor(span))
        events.append(
            {
                "type": "delegated",
                "worker_id": worker_id,
                "by": by,
                "delegation_id": delegation_id,
                "task": _summary(span.get("input")) or _summary((launcher or {}).get("input"), argument=True),
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
                    "result": _summary(span.get("output")) or _summary((launcher or {}).get("output")),
                    "status": "error" if str(span.get("status") or "").upper() == "ERROR" else "ok",
                    "at": span.get("end_time"),
                    "attribution": "reported",
                }
            )
    for span in by_id.values():
        if _kind(span) != TOOL_KIND or span["span_id"] in delegating:
            continue
        owner = agent_ancestor(span)
        events.append(
            {
                "type": "tool_call",
                "worker_id": worker_of(owner),
                "tool": str(span.get("name") or ""),
                "span_id": str(span["span_id"]),
                "status": "error" if str(span.get("status") or "").upper() == "ERROR" else "ok",
                "input": _payload(span.get("input")),
                "output": _payload(span.get("output")),
                "at": span.get("start_time"),
                # No agent span above the call: neither the coordinator nor a worker can be named for it.
                "attribution": "reported" if owner is not None else "unattributed",
            }
        )
    shown = Counter((one["worker_id"], one["tool"]) for one in events if one["type"] == "tool_call")
    shown_delegations = Counter(one["worker_id"] for one in events if one["type"] == "delegated")
    rows = [call for call in traced_calls if isinstance(call, Mapping)]

    def by_of(row: Mapping[str, Any]) -> str:
        # The runtime's stable worker id, else its label for the worker.
        return str(row.get("worker_id") or row.get("worker_label") or "").strip()

    # The agent that hands work out and is never handed any is the coordinator, as a top-level agent span is, unless
    # it is a declared worker (one may run others as tools after the root passed it control); so is the one agent of
    # an app that hands nothing out.
    def declared_row(row: Mapping[str, Any]) -> bool:
        # A runtime may name a worker by an opaque id and give its declared name only as the label.
        return any(_slug(name) in declared_by_key for name in (by_of(row), str(row.get("worker_label") or "")) if name)

    handed = {_slug(str(row["delegated_to"])) for row in rows if row.get("delegated_to")}
    named = {_slug(by_of(row)) for row in rows if by_of(row)}
    labels = {_slug(by_of(row)): str(row.get("worker_label") or by_of(row)) for row in rows if by_of(row)}
    delegators = {_slug(by_of(row)) for row in rows if row.get("delegated_to") and by_of(row)}

    def passes_control(row: Mapping[str, Any]) -> str | None:
        # An agent that hands work out but was never handed any got control another way: a call by another agent
        # that names it, as an argument or at the end of the tool's name (`transfer_to_agent`, `transfer_to_<name>`).
        if row.get("delegated_to") or not by_of(row):
            return None
        arguments = row.get("arguments")
        values = {_slug(one) for one in (arguments.values() if isinstance(arguments, Mapping) else ()) if isinstance(one, str)}
        tool = _slug(str(row.get("name") or ""))
        return next(
            (name for name in sorted(delegators - handed) if name != _slug(by_of(row)) and (name in values or tool.endswith(name))),
            None,
        )

    transfers = {index: target for index, row in enumerate(rows) if (target := passes_control(row))}
    controlled = set(transfers.values())
    top = {_slug(by_of(row)) for row in rows if by_of(row) and not declared_row(row)} - handed - controlled
    coordinators = {_slug(by_of(row)) for row in rows if row.get("delegated_to") and _slug(by_of(row)) in top}
    coordinators |= {_slug(by_of(rows[index])) for index in transfers} & top
    if not handed and len(named) == 1 and not workers and not any(declared_row(row) for row in rows if by_of(row)):
        coordinators = named

    def traced_worker(by: str, label: str) -> str | None:
        if _slug(by) in coordinators:
            return None
        match = declared_by_key.get(_slug(by)) or declared_by_key.get(_slug(label))
        local = str(match["id"]) if match is not None else _slug(by)
        worker_id = f"{run_key}/{local}"
        entry = workers.setdefault(
            worker_id,
            {
                "worker_id": worker_id,
                "id": local,
                "name": str(match.get("name") or label) if match is not None else label,
                "observed": True,
                "undeclared": match is None,
                "instance_ids": [],
            },
        )
        if by not in entry["instance_ids"]:
            entry["instance_ids"].append(by)
        return worker_id

    def when(value: Any) -> str | None:
        if isinstance(value, (int, float)) and value > 0:
            return datetime.fromtimestamp(value, tz=UTC).replace(tzinfo=None).isoformat()
        return None

    def started(row: Mapping[str, Any]) -> str | None:
        # A row stamped after the call ended carries when it was written, not when the call began.
        at, ended = row.get("at"), row.get("ended_at")
        if isinstance(at, (int, float)) and isinstance(ended, (int, float)) and 0 < ended < at:
            return None
        return when(at)

    for index, call in enumerate(rows):
        by = by_of(call)
        if not by:
            continue
        worker_id = traced_worker(by, str(call.get("worker_label") or by))
        runtime_ids = call.get("runtime_ids") or {}
        status = "error" if call.get("ok") is False or call.get("refused") else "ok"
        if index in transfers:
            target = transfers[index]
            events.append(
                {
                    "type": "transferred",
                    "worker_id": traced_worker(labels.get(target, target), labels.get(target, target)),
                    "by": worker_id,
                    "delegation_id": str(runtime_ids.get("event_id") or f"traced-{index}"),
                    "at": started(call) or when(call.get("ended_at")),
                    "attribution": "reported",
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
            delegation_id = str(runtime_ids.get("event_id") or f"traced-{index}")
            arguments = call.get("arguments")
            events.append(
                {
                    "type": "delegated",
                    "worker_id": handed_to,
                    "by": worker_id,
                    "delegation_id": delegation_id,
                    "task": _summary(json.dumps(arguments) if isinstance(arguments, dict) else arguments, argument=True),
                    "at": started(call),
                    "attribution": "reported",
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
        events.append(
            {
                "type": "tool_call",
                "worker_id": worker_id,
                "tool": tool,
                "status": status,
                "input": _payload(call.get("arguments")),
                "output": _payload(call.get("result") if call.get("result") is not None else call.get("error")),
                "at": started(call) or when(call.get("ended_at")),
                "attribution": "reported",
                **({"task": str(call.get("task_label") or call["task_id"])} if call.get("task_label") or call.get("task_id") else {}),
                **({"runtime_event_id": str(runtime_ids["event_id"])} if runtime_ids.get("event_id") else {}),
                **(
                    {"runtime_parent_event_id": str(runtime_ids["parent_event_id"])}
                    if runtime_ids.get("parent_event_id")
                    else {}
                ),
            }
        )
    # A return sorts after the work it closes even when timestamps tie. A hand-off whose start is unknown sorts just
    # before its worker's first step, and never after its own return.
    order = {"transferred": 0, "delegated": 0, "tool_call": 1, "returned": 2}
    returns = {one["delegation_id"]: one["at"] for one in events if one["type"] == "returned" and one["at"]}

    def placed(one: Mapping[str, Any]) -> str:
        if one["at"] or one["type"] != "delegated" or one["delegation_id"] not in returns:
            return str(one["at"] or "")
        until = returns[one["delegation_id"]]
        steps = [
            other["at"]
            for other in events
            if other["worker_id"] == one["worker_id"] and other["type"] == "tool_call" and other["at"] and other["at"] <= until
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
    # The coordinator as the app names it: the top-level agent span, else the runtime's label for the agent that hands
    # work out; None when neither was seen.
    coordinator = next(
        (name_of(span) for span in by_id.values() if span["span_id"] in participants and agent_ancestor(span) is None),
        None,
    ) or next(
        (str(row.get("worker_label") or by_of(row)) for row in rows if _slug(by_of(row)) in coordinators),
        None,
    )
    turns = [one for one in turns if isinstance(one, Mapping)]
    handed_at = [moment for one in events if one["type"] in ("delegated", "transferred") and (moment := _instant(one["at"]))]
    closed_at = [moment for one in events if (moment := _instant(one["at"])) is not None]
    shown, omitted = _bounded(events)
    return {
        "workers": sorted(workers.values(), key=lambda one: one["id"]),
        "events": shown,
        "events_omitted": omitted,
        "coordinator": {"name": coordinator} if coordinator else None,
        "request": _turn(turns, "user", before=min(handed_at)) if handed_at else None,
        "response": _turn(turns, "agent", after=max(closed_at)) if handed_at else None,
        "started_at": started_at,
    }


WORKERS_KEY = "harness_workers"
# Spans can reach the collector after the receipt: OpenTelemetry's batch exporter waits up to 5 s and times out at
# 30 s, so a span that ended before the call did has arrived by then. A second read follows only when a declared
# worker was not seen yet.
FIRST_READ = 35
SECOND_READ = 600


def _span_dict(span: Any) -> dict[str, Any]:
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
    }


def declared_workers(job: Any) -> list[dict[str, Any]]:
    """The workers the environment's contract declares (the understand stage reads them from the app)."""
    environment = job.environment or job
    version = environment.environment_version
    contract = ((version.documents or {}).get("contract") if version is not None else None) or {}
    return [one for one in contract.get("workers") or [] if isinstance(one, dict) and one.get("id")]


def _clock_start(call: Any, sealed: list[dict[str, Any]]) -> float | None:
    """Epoch seconds the call's message and tool-call offsets (`start_time_ms`) count from: the sealed tool calls
    carry both, else the call's own start."""
    for one in sealed:
        at, offset = one.get("at"), one.get("start_time_ms")
        if isinstance(at, (int, float)) and isinstance(offset, (int, float)):
            return float(at) - offset / 1000
    return call.started_at.timestamp() if call.started_at else None


def _call_start(call: Any, sealed: list[dict[str, Any]]) -> str | None:
    start = _clock_start(call, sealed)
    return datetime.fromtimestamp(start, tz=UTC).replace(tzinfo=None).isoformat() if start is not None else None


def _call_turns(call: Any, sealed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The call's messages with when each was said, on the tool calls' clock; a message with no offset is left out."""
    from simulate.models import CallTranscript
    from simulate.models.chat_message import ChatMessageModel

    start = _clock_start(call, sealed)
    if start is None:
        return []
    # Only the caller and the agent under test; a sub-actor or an unknown speaker is neither the request nor the reply.
    roles = {"user": "user", "assistant": "agent"}
    said = []
    for message in ChatMessageModel.objects.filter(call_execution=call).order_by("created_at"):
        for item in message.content if isinstance(message.content, list) else []:
            if isinstance(item, dict) and isinstance(item.get("start_time_ms"), (int, float)) and message.role in roles:
                said.append((item["start_time_ms"], roles[message.role], item.get("content")))
    if not said:
        said = [
            (row.start_time_ms, roles[row.speaker_role], row.content)
            for row in CallTranscript.objects.filter(call_execution=call, start_time_ms__isnull=False)
            if row.speaker_role in roles
        ]
    return [
        {
            "role": role,
            "text": text,
            "at": datetime.fromtimestamp(start + offset / 1000, tz=UTC).replace(tzinfo=None).isoformat(),
        }
        for offset, role, text in sorted(said, key=lambda one: one[0])
    ]


def record_call_workers(call_execution_id: str) -> dict[str, Any] | None:
    """Read one call's spans and its runtime-attributed tool calls, and keep its worker timeline on the call; None
    when the agent reports neither to us.
    Returns the stored timeline, so the caller can decide on a second read."""
    from django.utils import timezone

    from simulate.models import CallExecution, HostedHarnessJob
    from simulate.services.run_traces_v3 import list_run_traces
    from simulate.services.scoring_status import locked_call_metadata_update
    from tracer.services.clickhouse.v2 import get_reader

    call = CallExecution.objects.select_related("test_execution").get(id=call_execution_id)
    traces = list_run_traces(call.test_execution, call_execution_id=call.id)
    sealed = [
        one
        for one in ((call.provider_call_data or {}).get("livekit") or {}).get("tool_calls") or []
        if isinstance(one, dict)
    ]
    traced_calls = [one for one in sealed if one.get("worker_id") or one.get("worker_label")]
    if traces.project_id is None and not traced_calls:
        return None
    job = HostedHarnessJob.no_workspace_objects.filter(test_execution=call.test_execution).first()
    spans = []
    if traces.project_id is not None:
        reader = get_reader()
        try:
            spans = reader.list_by_trace_ids([one.trace_id for one in traces.traces], project_id=traces.project_id)
        finally:
            reader.close()
    timeline = {
        **worker_timeline(
            [_span_dict(one) for one in spans],
            declared_workers(job) if job is not None else [],
            run_key=str(job.run_id) if job is not None else str(call.test_execution_id),
            traced_calls=traced_calls,
            turns=_call_turns(call, sealed),
            started_at=_call_start(call, sealed),
        ),
        "collected_at": timezone.now().isoformat(),
    }

    def keep(metadata: dict) -> bool:
        metadata[WORKERS_KEY] = timeline
        return True

    locked_call_metadata_update(call.id, keep)
    return timeline
