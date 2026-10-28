"""Turn traces, sessions and users into readable text plus metrics.

A trace reads as one exchange, a session as its turns in order, and a user as
their recent sessions. Text is clipped on turn boundaries (first turns and the
last ones are kept) so long conversations stay within the extraction budget.
An insights-agent/v2 run reads each unit's whole span graph itself
(``evidence.py``) and takes the metrics and the text from here only to show,
for units whose roots carry no input or output too (``keep_empty``, with no
text).
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

# A span's text is read as Instant eval reads it: one parser for both.
from tracer.services.instant_eval_rows import message_text as _message_text

TURN_CHARS = 700
SESSION_CHARS = 9000
USER_SESSIONS = 8
USER_TURNS_PER_SESSION = 4
USER_CHARS = 9000
EXCERPT_CHARS = 280


@dataclass
class UnitText:
    unit_id: str
    content: str = ""
    excerpt: str = ""
    label: str = ""
    user_id: str = ""
    session_id: str = ""
    time: datetime | None = None
    metrics: dict = field(default_factory=dict)
    # The unit's last answer: its (last) trace's root output.
    output: str = ""


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _io(span) -> tuple[str, str]:
    attrs = span.attrs_string or {}
    text_in = _message_text(attrs.get("input.value") or span.input)
    text_out = _message_text(attrs.get("output.value") or span.output)
    return text_in, text_out


def _is_error(span) -> bool:
    return str(span.status or "").upper() == "ERROR"


def _user_labels(end_user_ids) -> dict[str, str]:
    ids = {str(e) for e in end_user_ids if e}
    if not ids:
        return {}
    try:
        from tracer.services.clickhouse.v2.end_user_dict_reader import (
            resolve_user_ids,
        )

        return {k: v for k, v in resolve_user_ids(ids).items() if v}
    except Exception:
        return {}


def _turns_text(turns: list[tuple[str, str]], budget: int) -> str:
    """Numbered turns; when over budget keep the first two and the latest."""
    rendered = [
        f"[Turn {i}] User: {_clip(u, TURN_CHARS)}\nAssistant: {_clip(a, TURN_CHARS)}"
        for i, (u, a) in enumerate(turns, start=1)
    ]
    text = "\n\n".join(rendered)
    if len(text) <= budget:
        return text
    head = rendered[:2]
    tail: list[str] = []
    used = sum(len(t) for t in head) + 60
    for turn in reversed(rendered[2:]):
        if used + len(turn) > budget:
            break
        tail.insert(0, turn)
        used += len(turn)
    skipped = len(rendered) - len(head) - len(tail)
    return "\n\n".join(head + [f"[… {skipped} turns omitted …]"] + tail)


def _span_metrics(spans) -> dict:
    latencies = [s.latency_ms for s in spans if s.latency_ms]
    return {
        "traces": len(spans),
        "errors": sum(1 for s in spans if _is_error(s)),
        "latency_ms": statistics.median(latencies) if latencies else None,
        "cost": round(sum(float(s.cost or 0) for s in spans), 6),
        "tokens": sum(int(s.total_tokens or 0) for s in spans),
    }


def load_traces(
    project_id: str, trace_ids: list[str], *, keep_empty: bool = False
) -> dict[str, UnitText]:
    from tracer.services.clickhouse.v2 import get_reader

    with get_reader() as reader:
        roots = reader.roots_by_trace_ids(
            [str(t) for t in trace_ids], project_id=str(project_id)
        )
    latest = {str(s.trace_id): s for s in roots}
    labels = _user_labels(s.end_user_id for s in latest.values())
    units = {}
    for trace_id, span in latest.items():
        text_in, text_out = _io(span)
        readable = text_in.strip() or text_out.strip()
        if not (keep_empty or readable):
            continue
        user = labels.get(str(span.end_user_id or ""), "")
        units[trace_id] = UnitText(
            unit_id=trace_id,
            content=(
                f"User: {_clip(text_in, 3500)}\n\nAssistant: {_clip(text_out, 3500)}"
                if readable
                else ""
            ),
            excerpt=_clip(text_in, EXCERPT_CHARS),
            label=span.trace_name or span.name or "",
            user_id=user,
            session_id=str(span.trace_session_id or ""),
            time=span.start_time,
            metrics={**_span_metrics([span]), "turns": 1},
            output=text_out,
        )
    return units


def load_sessions(
    project_id: str, session_ids: list[str], *, keep_empty: bool = False
) -> dict[str, UnitText]:
    from tracer.services.clickhouse.v2 import get_reader

    with get_reader() as reader:
        roots = reader.roots_by_session_ids(
            [str(s) for s in session_ids], project_id=str(project_id)
        )
    by_session: dict[str, dict[str, object]] = defaultdict(dict)
    for span in roots:
        # Ordered oldest first; a later version of the same root wins.
        by_session[str(span.trace_session_id)][str(span.trace_id)] = span
    labels = _user_labels(
        s.end_user_id for spans in by_session.values() for s in spans.values()
    )
    units = {}
    for session_id, traces in by_session.items():
        spans = sorted(traces.values(), key=lambda s: s.start_time)
        turns = [_io(s) for s in spans]
        readable = any(u.strip() or a.strip() for u, a in turns)
        if not (keep_empty or readable):
            continue
        first, last = spans[0], spans[-1]
        user = next(
            (labels.get(str(s.end_user_id)) for s in spans if s.end_user_id), ""
        )
        end = last.end_time or last.start_time
        units[session_id] = UnitText(
            unit_id=session_id,
            content=_turns_text(turns, SESSION_CHARS) if readable else "",
            excerpt=_clip(turns[0][0], EXCERPT_CHARS),
            label=_clip(turns[0][0], 80),
            user_id=user or "",
            session_id=session_id,
            time=first.start_time,
            metrics={
                **_span_metrics(spans),
                "turns": len(spans),
                "duration_s": max(0.0, (end - first.start_time).total_seconds()),
            },
            output=turns[-1][1],
        )
    return units


def load_users(
    project_id: str,
    end_user_ids: list[str],
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, UnitText]:
    from tracer.services.clickhouse.v2 import get_reader

    with get_reader() as reader:
        roots = reader.roots_by_end_user_ids(
            [str(u) for u in end_user_ids],
            project_id=str(project_id),
            since=since,
            until=until,
        )
    by_user: dict[str, dict[str, object]] = defaultdict(dict)
    for span in roots:
        by_user[str(span.end_user_id)][str(span.trace_id)] = span
    labels = _user_labels(by_user.keys())
    units = {}
    for end_user_id, traces in by_user.items():
        spans = sorted(traces.values(), key=lambda s: s.start_time)
        sessions: dict[str, list] = defaultdict(list)
        for span in spans:
            sessions[str(span.trace_session_id or span.trace_id)].append(span)
        ordered = sorted(sessions.values(), key=lambda group: group[0].start_time)
        recent = ordered[-USER_SESSIONS:]
        blocks = []
        for index, group in enumerate(recent, start=len(ordered) - len(recent) + 1):
            turns = [_io(s) for s in group]
            shown = turns[:USER_TURNS_PER_SESSION]
            lines = [
                f"  User: {_clip(u, 300)}\n  Assistant: {_clip(a, 300)}"
                for u, a in shown
            ]
            more = len(turns) - len(shown)
            errors = sum(1 for s in group if _is_error(s))
            header = (
                f"Session {index} ({group[0].start_time:%a %d %b}, {len(turns)} turns"
                f"{f', {errors} errored' if errors else ''})"
            )
            blocks.append(
                header
                + "\n"
                + "\n".join(lines)
                + (f"\n  [… {more} more turns]" if more > 0 else "")
            )
        content = "\n\n".join(blocks)
        if len(content) > USER_CHARS:
            content = content[-USER_CHARS:]
        if len(ordered) > len(recent):
            content = (
                f"[{len(ordered) - len(recent)} earlier sessions not shown]\n\n"
                + content
            )
        active_days = {s.start_time.date() for s in spans}
        label = labels.get(end_user_id, "") or end_user_id
        first_input = _io(ordered[0][0])[0]
        units[end_user_id] = UnitText(
            unit_id=end_user_id,
            content=content,
            excerpt=_clip(first_input, EXCERPT_CHARS),
            label=label,
            user_id=label,
            time=spans[-1].start_time,
            metrics={
                **_span_metrics(spans),
                "sessions": len(ordered),
                "turns": len(spans),
                "active_days": len(active_days),
                "first_seen": spans[0].start_time.isoformat(),
                "last_seen": spans[-1].start_time.isoformat(),
            },
            output=_io(spans[-1])[1],
        )
    return units


def load_units(
    unit: str,
    project_id: str,
    unit_ids: list[str],
    since: datetime | None = None,
    until: datetime | None = None,
    *,
    keep_empty: bool = False,
) -> dict[str, UnitText]:
    """Each unit's text and metrics. ``keep_empty`` keeps units whose roots
    hold no input or output (users always are)."""
    if unit == "sessions":
        return load_sessions(project_id, unit_ids, keep_empty=keep_empty)
    if unit == "users":
        return load_users(project_id, unit_ids, since, until)
    return load_traces(project_id, unit_ids, keep_empty=keep_empty)
