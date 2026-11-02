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
    return text[:_SUMMARY_CHARS] or None


def worker_timeline(
    spans: Iterable[Mapping[str, Any]],
    declared: Iterable[Mapping[str, Any]],
    *,
    run_key: str,
    traced_calls: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Each worker (declared, observed or both) and the run's worker events in order: `delegated`, the worker's
    `tool_call`s, `returned`. `spans` are dicts with `span_id`, `parent_span_id`, `name`, `observation_type`,
    `start_time`, `end_time`, `status`, `input`, `output` and `attributes`. `traced_calls` are the call's sealed
    tool-trace rows; one a runtime attributed to a worker (`by`, with its `task` and `runtime_ids`) is that worker's
    tool call, kept as reported, unless a span already showed the same call."""
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
                "at": span.get("start_time"),
                # No agent span above the call: neither the coordinator nor a worker can be named for it.
                "attribution": "reported" if owner is not None else "unattributed",
            }
        )
    shown = Counter((one["worker_id"], one["tool"]) for one in events if one["type"] == "tool_call")
    for call in traced_calls:
        # The runtime's stable worker id, else its label for the worker.
        by = str(call.get("worker_id") or call.get("worker_label") or "").strip()
        if not by:
            continue
        match = declared_by_key.get(_slug(by))
        local = str(match["id"]) if match is not None else _slug(by)
        worker_id = f"{run_key}/{local}"
        entry = workers.setdefault(
            worker_id,
            {
                "worker_id": worker_id,
                "id": local,
                "name": str(match.get("name") or by) if match is not None else str(call.get("worker_label") or by),
                "observed": True,
                "undeclared": match is None,
                "instance_ids": [],
            },
        )
        if by not in entry["instance_ids"]:
            entry["instance_ids"].append(by)
        tool = str(call.get("name") or "")
        if shown[(worker_id, tool)]:
            shown[(worker_id, tool)] -= 1
            continue
        at = call.get("at")
        runtime_ids = call.get("runtime_ids") or {}
        events.append(
            {
                "type": "tool_call",
                "worker_id": worker_id,
                "tool": tool,
                "status": "error" if call.get("ok") is False or call.get("refused") else "ok",
                "at": datetime.fromtimestamp(at, tz=UTC).replace(tzinfo=None).isoformat()
                if isinstance(at, (int, float)) and at > 0
                else None,
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
    # A return sorts after the work it closes even when timestamps tie.
    order = {"delegated": 0, "tool_call": 1, "returned": 2}
    events.sort(key=lambda one: (str(one["at"] or ""), order[one["type"]]))
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
    return {"workers": sorted(workers.values(), key=lambda one: one["id"]), "events": events}


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
    traced_calls = [
        one
        for one in ((call.provider_call_data or {}).get("livekit") or {}).get("tool_calls") or []
        if isinstance(one, dict) and (one.get("worker_id") or one.get("worker_label"))
    ]
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
        ),
        "collected_at": timezone.now().isoformat(),
    }

    def keep(metadata: dict) -> bool:
        metadata[WORKERS_KEY] = timeline
        return True

    locked_call_metadata_update(call.id, keep)
    return timeline
