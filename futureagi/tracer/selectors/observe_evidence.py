"""What Observe recorded for the sessions or calls a person picked, read to
build a regression test from: the conversation, the tool calls with their
inputs and outputs, and the labels people gave it.

Every part says when it could not be read in full. Anything the user deleted
stays out (``retained_spans``), so evidence never outlives its source.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

import structlog
from django.db.models import Q

from model_hub.models.score import Score
from simulate.utils.session_comparison import parse_voice_span_transcripts
from tracer.services.analysis_retention import retained_spans
from tracer.services.clickhouse.v2.span_reader import CHSpan, merge_span_attributes
from tracer.services.instant_eval_rows import message_text

logger = structlog.get_logger(__name__)

SourceKind = Literal["session", "call"]

# A turn's text and a tool call's input and output are each cut to these
# characters; a source keeps its first MAX_TURNS turns and MAX_TOOL_CALLS calls.
TURN_CHARS = 2000
TOOL_FIELD_CHARS = 2000
MAX_TURNS = 100
MAX_TOOL_CALLS = 100

# A character class that matches nothing. The activity read counts image and
# audio parts with a pair of patterns; evidence reads no media, so it counts none.
_NO_MEDIA = (r"[^\s\S]", r"[^\s\S]")
_DATA_URI = re.compile(r"data:[\w.+-]+/[\w.+-]+;base64,[A-Za-z0-9+/=]*")
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/=]{200,}")
_AGENT_ROLES = ("assistant", "ai", "agent")


@dataclass(frozen=True)
class Turn:
    role: Literal["user", "agent"]
    text: str
    truncated: bool
    trace_id: str
    # When the turn's trace started; None for a voice turn, whose own time the
    # call does not record.
    at: datetime | None


@dataclass(frozen=True)
class ToolCall:
    span_id: str
    trace_id: str
    name: str
    at: datetime
    failed: bool
    error: str
    input: str
    output: str
    input_truncated: bool
    output_truncated: bool


@dataclass(frozen=True)
class Label:
    name: str
    kind: str
    value: object
    notes: str
    target: Literal["session", "trace", "span"]


@dataclass(frozen=True)
class SourceEvidence:
    """One session or call as Observe recorded it. ``tool_calls`` is None when
    the calls could not be read; the ``*_truncated`` flags say a cap cut a part."""

    source_id: str
    trace_ids: tuple[str, ...]
    started_at: datetime
    ended_at: datetime
    turns: tuple[Turn, ...]
    turns_truncated: bool
    tool_calls: tuple[ToolCall, ...] | None
    tool_calls_truncated: bool
    labels: tuple[Label, ...]


def observe_evidence(
    project_id: str, source_kind: SourceKind, source_ids: list[str]
) -> dict[str, SourceEvidence]:
    """Evidence for each id asked that the project still holds, keyed by that
    id as given. An id is absent when nothing of it is retained in the project,
    or when it names a trace that is not a call: a trace is never read as
    another kind of source."""
    from tracer.services.clickhouse.v2 import get_reader

    asked = {_canonical(source_id): source_id for source_id in source_ids}
    with get_reader() as reader:
        if source_kind == "session":
            roots = reader.roots_by_session_ids(
                list(asked.values()), project_id=project_id
            )
        else:
            roots = [
                root
                for root in reader.roots_by_trace_ids(
                    list(asked.values()), project_id=project_id, include_heavy=True
                )
                if root.observation_type == "conversation"
            ]
        roots = retained_spans(project_id, roots)
        activity = _activity(reader, project_id, roots)

    by_source: dict[str, dict[str, CHSpan]] = defaultdict(dict)
    for root in roots:
        key = root.trace_session_id if source_kind == "session" else root.trace_id
        source_id = asked.get(_canonical(key))
        if source_id is not None:
            # Roots come oldest version first; a later version of a root wins.
            by_source[source_id][str(root.trace_id)] = root
    labels = _labels(source_kind, by_source)
    return {
        source_id: _evidence(
            source_id,
            source_kind,
            sorted(spans.values(), key=lambda span: span.start_time),
            activity,
            labels.get(source_id, ()),
        )
        for source_id, spans in by_source.items()
    }


def _canonical(value: object) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError):
        return str(value)


def _activity(reader, project_id: str, roots) -> dict[str, list[dict]] | None:
    """Each trace's tool calls, oldest first, and whether a trace held more
    than the read returned; None when they could not be read."""
    trace_ids = sorted({str(root.trace_id) for root in roots})
    if not trace_ids:
        return {}
    try:
        rows = reader.activity_by_trace_ids(
            trace_ids,
            project_id=project_id,
            media_patterns=_NO_MEDIA,
            field_chars=TOOL_FIELD_CHARS,
            per_trace=MAX_TOOL_CALLS,
        )
    except Exception:
        logger.warning(
            "observe_evidence_activity_unreadable", traces=len(trace_ids), exc_info=True
        )
        return None
    by_trace: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_trace[str(row["trace_id"])].append(row)
    return by_trace


def _evidence(
    source_id: str,
    source_kind: SourceKind,
    roots: list[CHSpan],
    activity: dict[str, list[dict]] | None,
    labels: tuple[Label, ...],
) -> SourceEvidence:
    turns = _session_turns(roots) if source_kind == "session" else _call_turns(roots)
    calls, calls_truncated = _tool_calls(roots, activity)
    return SourceEvidence(
        source_id=source_id,
        trace_ids=tuple(str(root.trace_id) for root in roots),
        started_at=roots[0].start_time,
        ended_at=max(root.end_time or root.start_time for root in roots),
        turns=tuple(turns[:MAX_TURNS]),
        turns_truncated=len(turns) > MAX_TURNS,
        tool_calls=calls,
        tool_calls_truncated=calls_truncated,
        labels=labels,
    )


def _session_turns(roots: list[CHSpan]) -> list[Turn]:
    """A text session: each trace is one exchange, the user's input then the
    agent's output, as Insights reads a session."""
    turns = []
    for root in roots:
        attrs = root.attrs_string or {}
        for role, value in (
            ("user", message_text(attrs.get("input.value") or root.input)),
            ("agent", _agent_text(attrs.get("output.value") or root.output)),
        ):
            turn = _turn(role, value, str(root.trace_id), root.start_time)
            if turn is not None:
                turns.append(turn)
    return turns


def _call_turns(roots: list[CHSpan]) -> list[Turn]:
    """A call: the transcript its conversation span recorded, without the
    tool messages (those are read as tool calls)."""
    turns = []
    for root in roots:
        attrs = merge_span_attributes(
            root.attrs_string, root.attrs_number, root.attrs_bool, root.attributes_extra
        )
        # Collector ingest keeps the provider transcript as a JSON string in
        # attrs_string; the parser reads only a list, and would otherwise drop
        # a call where only the caller spoke (a voicemail, a hang-up).
        transcript = attrs.get("provider_transcript")
        if isinstance(transcript, str):
            try:
                attrs["provider_transcript"] = json.loads(transcript)
            except ValueError:
                pass
        for entry in parse_voice_span_transcripts(attrs):
            role = "user" if entry["role"] == "user" else "agent"
            text = " ".join(str(m) for m in entry.get("messages") or [] if m)
            turn = _turn(role, text, str(root.trace_id), None)
            if turn is not None:
                turns.append(turn)
    return turns


def _turn(role, value: str, trace_id: str, at: datetime | None) -> Turn | None:
    text = _BASE64_RUN.sub("[binary data]", _DATA_URI.sub("[media]", value or ""))
    text = text.strip()
    if not text:
        return None
    return Turn(
        role=role,
        text=text[:TURN_CHARS],
        truncated=len(text) > TURN_CHARS,
        trace_id=trace_id,
        at=at,
    )


def _agent_text(value) -> str:
    """The agent's reply in a root's output: the last assistant message of a
    chat array, or what ``message_text`` reads (which, for an array, would
    pick the user's message)."""
    text = value if isinstance(value, str) else json.dumps(value)
    if (text or "").strip().startswith("["):
        try:
            parsed = json.loads(text)
        except ValueError:
            return text
        for message in reversed(parsed if isinstance(parsed, list) else []):
            if isinstance(message, dict) and message.get("role") in _AGENT_ROLES:
                content = message.get("content")
                return content if isinstance(content, str) else json.dumps(content)
    return message_text(text)


def _tool_calls(
    roots: list[CHSpan], activity: dict[str, list[dict]] | None
) -> tuple[tuple[ToolCall, ...] | None, bool]:
    if activity is None:
        return None, False
    calls: list[ToolCall] = []
    truncated = False
    for root in roots:
        rows = activity.get(str(root.trace_id), [])
        # The read returns at most MAX_TOOL_CALLS steps of a trace; a full
        # page may have left some out.
        truncated = truncated or len(rows) >= MAX_TOOL_CALLS
        calls.extend(
            ToolCall(
                span_id=str(row["id"]),
                trace_id=str(row["trace_id"]),
                name=str(row["name"] or ""),
                at=row["start_time"],
                failed=str(row["status"] or "").upper() == "ERROR",
                error=str(row["status_message"] or ""),
                input=str(row["input"] or ""),
                output=str(row["output"] or ""),
                input_truncated=int(row["input_chars"] or 0) > TOOL_FIELD_CHARS,
                output_truncated=int(row["output_chars"] or 0) > TOOL_FIELD_CHARS,
            )
            for row in rows
            if row["observation_type"] == "tool"
        )
    calls.sort(key=lambda call: (call.at, call.span_id))
    return tuple(calls[:MAX_TOOL_CALLS]), truncated or len(calls) > MAX_TOOL_CALLS


def _labels(
    source_kind: SourceKind, by_source: dict[str, dict[str, CHSpan]]
) -> dict[str, tuple[Label, ...]]:
    """The annotations on each source, on itself or on one of its traces or
    spans: input evidence for a test, never its verdict."""
    source_of_trace = {
        _canonical(trace_id): source_id
        for source_id, spans in by_source.items()
        for trace_id in spans
    }
    if not source_of_trace:
        return {}
    match = Q(trace_id__in=list(source_of_trace))
    sessions = {_canonical(source_id): source_id for source_id in by_source}
    if source_kind == "session":
        match |= Q(trace_session_id__in=list(sessions))
    scores = (
        Score.no_workspace_objects.filter(match, deleted=False, label__deleted=False)
        .select_related("label")
        .order_by("created_at", "id")
    )
    labels: dict[str, list[Label]] = defaultdict(list)
    for score in scores:
        session = sessions.get(_canonical(score.trace_session_id))
        if source_kind == "session" and session is not None:
            source_id, target = session, "session"
        else:
            source_id = source_of_trace.get(_canonical(score.trace_id))
            target = "span" if score.observation_span_id else "trace"
        if source_id is None:
            continue
        labels[source_id].append(
            Label(
                name=score.label.name,
                kind=str(score.label.type),
                value=score.value,
                notes=score.notes or "",
                target=target,
            )
        )
    return {source_id: tuple(found) for source_id, found in labels.items()}
