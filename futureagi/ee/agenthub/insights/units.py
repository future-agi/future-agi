"""Turn traces, sessions and users into readable text plus metrics.

A trace reads as one exchange, a session as its turns in order, and a user as
their recent sessions. Each exchange shows what the agent did as well as what
it said: its tool calls with their arguments and results, any step that
failed, and a note where the user or a step sent an image or audio, which this
text can't show. Text is clipped on turn boundaries (the first and last
turns, every turn where a step failed, then the second and the latest ones are
kept) so long conversations stay within the extraction budget; what was left
out is marked.
An insights-agent/v2 run reads each unit's whole span graph itself
(``evidence.py``) and takes the metrics and the text from here only to show,
for units whose roots carry no input or output too (``keep_empty``, with no
text).
"""

from __future__ import annotations

import json
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime

import structlog

# Attribute keys known to hold a recording or image URL.
from ee.agenthub.insights.evidence import _MEDIA_ATTRIBUTES

# A span's text is read as Instant eval reads it: one parser for both.
from tracer.services.instant_eval_rows import message_text as _message_text

logger = structlog.get_logger(__name__)

TURN_CHARS = 700
SESSION_CHARS = 9000
USER_SESSIONS = 8
USER_TURNS_PER_SESSION = 4
USER_CHARS = 9000
EXCERPT_CHARS = 280
TRACE_CHARS = 3000
# A tool call's arguments and its result are each cut to STEP_CHARS, and a
# turn's step lines fit in STEP_BUDGET characters, the failed ones first (the
# USER_ figures in a user's sessions). STEP_READ_CHARS is how much of each is
# read to cut from.
STEP_CHARS = 300
STEP_BUDGET = 1600
USER_STEP_CHARS = 160
USER_STEP_BUDGET = 400
STEP_READ_CHARS = 1000

# What the text can't show, in the engine's own words. The reader is told
# what they mean; a quote holding one is the engine's, not the conversation's.
IMAGE_NOTE = "[image attached: not visible to this analysis]"
AUDIO_NOTE = "[audio attached: not heard by this analysis]"
UNREAD_NOTE = "[tool calls and other steps could not be read for this analysis]"
NOTE = re.compile(
    r"\[(?:[^\[\]]*\bthis analysis|… \d+ [^\[\]]* omitted …|… \d+ more turns"
    r"|\d+ earlier sessions not shown|binary data)\]"
)
# An image or audio part of a chat input (OpenAI, Anthropic and Gemini
# shapes), quoted or JSON-escaped. ClickHouse counts them in each step's input
# with the same patterns (re2 reads them as Python does).
IMAGE_PART = (
    r'\\?"(?:type\\?"\s*:\s*\\?"(?:image_url|input_image|image)'
    r'|mime_?[tT]ype\\?"\s*:\s*\\?"image/)'
)
AUDIO_PART = (
    r'\\?"(?:type\\?"\s*:\s*\\?"(?:input_audio|audio)'
    r'|mime_?[tT]ype\\?"\s*:\s*\\?"audio/)'
)
_DATA_URI = re.compile(r"data:([\w.+-]+)/[\w.+-]+;base64,[A-Za-z0-9+/=]*")
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/=]{200,}")
_TEXT_PARTS = ("text", "input_text", "output_text")


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


@dataclass
class _Turn:
    text: str
    # A step of the turn failed: kept when a long conversation is clipped.
    failed: bool = False


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _parts_text(text: str) -> str | None:
    """A chat message's content parts as text, an image or audio part as its
    note; None when ``text`` is not a list of parts."""
    if not text.lstrip().startswith("["):
        return None
    try:
        parts = json.loads(text)
    except ValueError:
        return None
    if not isinstance(parts, list) or not all(isinstance(p, dict) for p in parts):
        return None
    out = []
    for part in parts:
        kind = str(part.get("type") or "")
        if kind in _TEXT_PARTS and isinstance(part.get("text"), str):
            out.append(part["text"])
        elif re.search(IMAGE_PART, json.dumps(part)):
            out.append(IMAGE_NOTE)
        elif re.search(AUDIO_PART, json.dumps(part)):
            out.append(AUDIO_NOTE)
        else:
            return None
    return " ".join(out)


def _data_note(match) -> str:
    kind = match.group(1).lower()
    return {"image": IMAGE_NOTE, "audio": AUDIO_NOTE}.get(kind, "[binary data]")


def _scrub(text: str) -> str:
    """Inline media as its note; base64 never enters the text."""
    text = _DATA_URI.sub(_data_note, text)
    return _BASE64_RUN.sub("[binary data]", text)


def _readable(value) -> str:
    """A root's input or output as text, read with Instant eval's parser;
    content parts and inline media become notes."""
    text = _message_text(value)
    return _scrub(_parts_text(text) or text)


def _plain(text: str) -> str:
    """Text without the engine's notes, for an excerpt or a label."""
    return NOTE.sub(" ", text)


def _label(text: str, end: str) -> str:
    """A step's name or a failure's message as its line shows it: bare when
    it holds no ``end`` (what follows it on the line), else as a JSON string,
    so the line says where it stops (``own_words``)."""
    if text and end not in text and not text.startswith('"'):
        return text
    return json.dumps(text, ensure_ascii=False)


# A line of a unit's text: who speaks, or a Tool or Step and its name, then
# the unit's own words; " -> " leads to what a call returned, and a failed
# call's reads "ERROR <message>: <output>" (``_step_line``, ``_exchange``).
_STRING = r'"(?:[^"\\]|\\.)*"'
_SPEAKS = re.compile(r"[ \t]*(?:\[Turn \d+\] )?(?:User|Assistant): ")
_CALL = re.compile(rf"[ \t]*(Tool|Step) (?:{_STRING}|\S+)")
_ARROW = re.compile(r"(?= -> )")
_FAILED = re.compile(rf"ERROR(?: ({_STRING}|.+?))?(?:: |$)")


def _line_words(line: str) -> list[tuple[int, int]]:
    """Where a line holds the unit's own words: a message, a call's
    arguments, what it returned or a failure's message. Everything else is
    the engine's: who speaks, a step's name and kind, the arrow, "ERROR", a
    note, the "…" a clipped field ends with, a session's heading and a line
    cut short. Any " -> " on a step's line is taken for the arrow."""
    speaks = _SPEAKS.match(line)
    call = None if speaks else _CALL.match(line)
    if speaks:
        marks = [(0, speaks.end())]
    elif call:
        arrows = [m.start() for m in _ARROW.finditer(line, call.end())]
        # A Tool's arguments are the unit's; a Step's kind runs to its arrow.
        if call.group(1) == "Tool":
            marks = [(0, call.end())]
        else:
            marks = [(0, arrows[0] if arrows else len(line))]
        for at in arrows:
            marks.append((at, at + 4))
            failed = _FAILED.match(line, at + 4)
            if not failed:
                continue
            if failed.group(1) is None:
                marks.append((at + 4, failed.end()))
                continue
            start, end = failed.span(1)
            marks += [(at + 4, start), (end, failed.end())]
            message = failed.group(1)
            if message.startswith('"'):
                # Its quotes are the engine's, and an escaped one all of it.
                quoted = re.fullmatch(_STRING, message) and "\\" not in message
                marks += (
                    [(start, start + 1), (end - 1, end)] if quoted else [(start, end)]
                )
    else:
        return []
    marks += [m.span() for m in NOTE.finditer(line)]
    words, at = [], 0
    for start, end in [*sorted(marks), (len(line), len(line))]:
        if start > at:
            piece = line[at:start]
            first = at + len(piece) - len(piece.lstrip())
            rest = piece.rstrip()
            rest = rest[:-1].rstrip() if rest.endswith("…") else rest
            if at + len(rest) > first:
                words.append((first, at + len(rest)))
        at = max(at, end)
    return words


def own_words(content: str, start: int, end: int) -> tuple[int, int] | None:
    """The part of ``content[start:end]`` (a quote found there) that is the
    unit's own words, never the engine's around them. Across a call's parts
    the last one with words in it is kept: what the call returned, or a
    failure's message. None across lines, which joins two parts of the
    conversation, or with none of the unit's words in it."""
    first = content.rfind("\n", 0, start) + 1
    last = content.find("\n", first)
    last = len(content) if last < 0 else last
    if end > last:
        return None
    kept = None
    for a, b in _line_words(content[first:last]):
        a, b = max(first + a, start), min(first + b, end)
        if a < b and re.search(r"\w", content[a:b]):
            kept = (a, b)
    return kept


def _io(span) -> tuple[str, str]:
    attrs = span.attrs_string or {}
    text_in = _readable(attrs.get("input.value") or span.input)
    text_out = _readable(attrs.get("output.value") or span.output)
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


def _activity(
    reader, project_id: str, roots, read: bool = True
) -> dict[str, list[dict]] | None:
    """Each trace's tool calls, failed steps and steps sent media, oldest
    first (none unless ``read``); None when they couldn't be read (the text
    then says so)."""
    if not read:
        return {}
    trace_ids = sorted({str(s.trace_id) for s in roots})
    try:
        rows = reader.activity_by_trace_ids(
            trace_ids,
            project_id=str(project_id),
            media_patterns=(IMAGE_PART, AUDIO_PART),
            field_chars=STEP_READ_CHARS,
        )
    except Exception:
        logger.warning(
            "insight_unit_activity_unreadable", traces=len(trace_ids), exc_info=True
        )
        return None
    by_trace: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_trace[str(row["trace_id"])].append(row)
    return by_trace


def _step_failed(step: dict) -> bool:
    return str(step.get("status") or "").upper() == "ERROR"


def _is_shown(step: dict) -> bool:
    """A step the text shows as a line: a tool call or a failed step (one
    read only for the media in its input is not)."""
    return step.get("observation_type") == "tool" or _step_failed(step)


def _field(step: dict, key: str, chars: int) -> str:
    """A step's input or output as it was sent: a tool's arguments and
    result are data, not a chat message."""
    raw = str(step.get(key) or "")
    text = _clip(_scrub(raw), chars)
    # Read cut short, so more of it exists than the text could show.
    if (step.get(f"{key}_chars") or 0) > len(raw) and not text.endswith("…"):
        text += "…"
    return text


def _step_line(step: dict, chars: int) -> str:
    """``Tool name {args} -> result``, or ``Step name (kind) -> ERROR ...``."""
    kind = str(step.get("observation_type") or "") or "span"
    name = _label(_clip(step.get("name") or kind, 80), " ")
    if kind == "tool":
        line = f"Tool {name}"
        args = _field(step, "input", chars)
        if args:
            line += f" {args}"
    else:
        line = f"Step {name} ({kind})"
    result = _field(step, "output", chars)
    if _step_failed(step):
        message = _clip(step.get("status_message") or "", 200)
        result = (
            "ERROR"
            + (f" {_label(message, ': ')}" if message else "")
            + (f": {result}" if result else "")
        )
    return f"{line} -> {result}" if result else line


def _step_lines(steps: list[dict], *, chars: int, budget: int, total: int) -> list:
    """The steps a turn shows, in order: the failed ones, then the first and
    the last, while their lines fit ``budget`` characters; ``total`` is how
    many the trace holds."""
    ends = zip(range(len(steps)), reversed(range(len(steps))), strict=True)
    failed = [i for i, s in enumerate(steps) if _step_failed(s)]
    shown: dict[int, str] = {}
    used = 0
    for index in dict.fromkeys(failed + [i for pair in ends for i in pair]):
        line = _step_line(steps[index], chars)
        if used + len(line) <= budget:
            shown[index] = line
            used += len(line)
    lines, skipped = [], 0
    for index in range(len(steps)):
        if index not in shown:
            skipped += 1
            continue
        if skipped:
            lines.append(f"[… {skipped} steps omitted …]")
            skipped = 0
        lines.append(shown[index])
    skipped += max(0, total - len(steps))
    if skipped:
        lines.append(f"[… {skipped} steps omitted …]")
    return lines


def _exchange(
    root,
    steps: list[dict] | None,
    seen: dict[str, int],
    *,
    text_chars: int,
    step_chars: int,
    step_budget: int,
    indent: str = "",
    head: str = "",
) -> _Turn:
    """One trace as ``User:``, its steps, then ``Assistant:``. ``seen`` holds
    the most images and audio parts any earlier turn's input carried: a chat
    input repeats the history, so only a turn bringing more gets the note."""
    text_in, text_out = _io(root)
    user, answer = _clip(text_in, text_chars), _clip(text_out, text_chars)
    steps = steps or []
    raw_in = str((root.attrs_string or {}).get("input.value") or root.input or "")
    attrs = root.attrs_string or {}
    media = {
        "images": max(
            [len(re.findall(IMAGE_PART, raw_in)), *(s["images"] or 0 for s in steps)]
        ),
        "audio": max(
            [len(re.findall(AUDIO_PART, raw_in)), *(s["audio"] or 0 for s in steps)]
        ),
    }
    if any(attrs.get(k) for k, kind in _MEDIA_ATTRIBUTES.items() if kind == "image"):
        media["images"] = max(media["images"], 1)
    if any(attrs.get(k) for k, kind in _MEDIA_ATTRIBUTES.items() if kind == "audio"):
        media["audio"] = max(media["audio"], 1)
    for key, note in (("images", IMAGE_NOTE), ("audio", AUDIO_NOTE)):
        if media[key] > seen.get(key, 0) and note not in user:
            user = f"{user} {note}".strip()
        seen[key] = max(seen.get(key, 0), media[key])
    shown = [s for s in steps if _is_shown(s)]
    total = max((s.get("in_trace") or 0 for s in shown), default=0)
    lines = [f"{indent}{head}User: {user}"]
    lines += [
        f"{indent}{line}"
        for line in _step_lines(
            shown, chars=step_chars, budget=step_budget, total=max(total, len(shown))
        )
    ]
    failed = _is_error(root) or any(_step_failed(s) for s in shown)
    if _is_error(root):
        message = _clip(root.status_message or "", 200)
        lines.append(
            f"{indent}Step {_label(_clip(root.name or 'turn', 80), ' ')} (turn) -> ERROR"
            + (f" {_label(message, ': ')}" if message else "")
        )
    lines.append(f"{indent}Assistant: {answer}")
    return _Turn("\n".join(lines), failed)


def _turns_text(turns: list[_Turn], budget: int) -> str:
    """Turns in order; over budget, the first, then while they fit the last,
    every turn where a step failed (earliest first), the second and the
    latest, with the turns left out marked."""
    text = "\n\n".join(t.text for t in turns)
    if len(text) <= budget:
        return text
    keep: set[int] = {0}
    used = len(turns[0].text) + 62

    def take(index: int) -> bool:
        nonlocal used
        cost = len(turns[index].text) + 2
        if index in keep:
            return True
        if used + cost > budget:
            return False
        keep.add(index)
        used += cost
        return True

    take(len(turns) - 1)
    for index, turn in enumerate(turns):
        if turn.failed:
            take(index)
    take(1)
    for index in reversed(range(len(turns))):
        if not take(index):
            break
    out, skipped = [], 0
    for index, turn in enumerate(turns):
        if index not in keep:
            skipped += 1
            continue
        if skipped:
            out.append(f"[… {skipped} turns omitted …]")
            skipped = 0
        out.append(turn.text)
    if skipped:
        out.append(f"[… {skipped} turns omitted …]")
    return "\n\n".join(out)


def _span_metrics(spans) -> dict:
    latencies = [s.latency_ms for s in spans if s.latency_ms]
    return {
        "traces": len(spans),
        "errors": sum(1 for s in spans if _is_error(s)),
        "latency_ms": statistics.median(latencies) if latencies else None,
        "cost": round(sum(float(s.cost or 0) for s in spans), 6),
        "tokens": sum(int(s.total_tokens or 0) for s in spans),
    }


def _unread(activity) -> str:
    return f"{UNREAD_NOTE}\n\n" if activity is None else ""


def load_traces(
    project_id: str,
    trace_ids: list[str],
    *,
    keep_empty: bool = False,
    read_steps: bool = True,
) -> dict[str, UnitText]:
    from tracer.services.clickhouse.v2 import get_reader

    with get_reader() as reader:
        roots = reader.roots_by_trace_ids(
            [str(t) for t in trace_ids], project_id=str(project_id)
        )
        activity = _activity(reader, project_id, roots, read_steps)
    latest = {str(s.trace_id): s for s in roots}
    labels = _user_labels(s.end_user_id for s in latest.values())
    units = {}
    for trace_id, span in latest.items():
        text_in, text_out = _io(span)
        readable = text_in.strip() or text_out.strip()
        if not (keep_empty or readable):
            continue
        user = labels.get(str(span.end_user_id or ""), "")
        turn = _exchange(
            span,
            (activity or {}).get(trace_id),
            {},
            text_chars=TRACE_CHARS,
            step_chars=STEP_CHARS,
            step_budget=STEP_BUDGET,
        )
        units[trace_id] = UnitText(
            unit_id=trace_id,
            content=(_unread(activity) + turn.text) if readable else "",
            excerpt=_clip(_plain(text_in), EXCERPT_CHARS),
            label=span.trace_name or span.name or "",
            user_id=user,
            session_id=str(span.trace_session_id or ""),
            time=span.start_time,
            metrics={**_span_metrics([span]), "turns": 1},
            output=text_out,
        )
    return units


def load_sessions(
    project_id: str,
    session_ids: list[str],
    *,
    keep_empty: bool = False,
    read_steps: bool = True,
) -> dict[str, UnitText]:
    from tracer.services.clickhouse.v2 import get_reader

    with get_reader() as reader:
        roots = reader.roots_by_session_ids(
            [str(s) for s in session_ids], project_id=str(project_id)
        )
        activity = _activity(reader, project_id, roots, read_steps)
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
        texts = [_io(s) for s in spans]
        readable = any(u.strip() or a.strip() for u, a in texts)
        if not (keep_empty or readable):
            continue
        seen: dict[str, int] = {}
        turns = [
            _exchange(
                span,
                (activity or {}).get(str(span.trace_id)),
                seen,
                text_chars=TURN_CHARS,
                step_chars=STEP_CHARS,
                step_budget=STEP_BUDGET,
                head=f"[Turn {number}] ",
            )
            for number, span in enumerate(spans, start=1)
        ]
        first, last = spans[0], spans[-1]
        user = next(
            (labels.get(str(s.end_user_id)) for s in spans if s.end_user_id), ""
        )
        end = last.end_time or last.start_time
        units[session_id] = UnitText(
            unit_id=session_id,
            content=(
                _unread(activity) + _turns_text(turns, SESSION_CHARS)
                if readable
                else ""
            ),
            excerpt=_clip(_plain(texts[0][0]), EXCERPT_CHARS),
            label=_clip(_plain(texts[0][0]), 80),
            user_id=user or "",
            session_id=session_id,
            time=first.start_time,
            metrics={
                **_span_metrics(spans),
                "turns": len(spans),
                "duration_s": max(0.0, (end - first.start_time).total_seconds()),
            },
            output=texts[-1][1],
        )
    return units


def _sessions_of(traces: dict) -> list[list]:
    """A user's roots grouped by session, sessions and turns oldest first."""
    sessions: dict[str, list] = defaultdict(list)
    for span in sorted(traces.values(), key=lambda s: s.start_time):
        sessions[str(span.trace_session_id or span.trace_id)].append(span)
    return sorted(sessions.values(), key=lambda group: group[0].start_time)


def load_users(
    project_id: str,
    end_user_ids: list[str],
    since: datetime | None = None,
    until: datetime | None = None,
    *,
    read_steps: bool = True,
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
        grouped = {user: _sessions_of(traces) for user, traces in by_user.items()}
        # Steps are read only for the turns a user's text shows.
        activity = _activity(
            reader,
            project_id,
            [
                span
                for ordered in grouped.values()
                for group in ordered[-USER_SESSIONS:]
                for span in group[:USER_TURNS_PER_SESSION]
            ],
            read_steps,
        )
    labels = _user_labels(by_user.keys())
    units = {}
    for end_user_id, ordered in grouped.items():
        spans = sorted(by_user[end_user_id].values(), key=lambda s: s.start_time)
        recent = ordered[-USER_SESSIONS:]
        blocks = []
        for index, group in enumerate(recent, start=len(ordered) - len(recent) + 1):
            seen: dict[str, int] = {}
            shown = [
                _exchange(
                    span,
                    (activity or {}).get(str(span.trace_id)),
                    seen,
                    text_chars=300,
                    step_chars=USER_STEP_CHARS,
                    step_budget=USER_STEP_BUDGET,
                    indent="  ",
                ).text
                for span in group[:USER_TURNS_PER_SESSION]
            ]
            more = len(group) - len(shown)
            errors = sum(1 for s in group if _is_error(s))
            header = (
                f"Session {index} ({group[0].start_time:%a %d %b}, {len(group)} turns"
                f"{f', {errors} errored' if errors else ''})"
            )
            blocks.append(
                header
                + "\n"
                + "\n".join(shown)
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
            content=_unread(activity) + content,
            excerpt=_clip(_plain(first_input), EXCERPT_CHARS),
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
    read_steps: bool = True,
) -> dict[str, UnitText]:
    """Each unit's text and metrics. ``keep_empty`` keeps units whose roots
    hold no input or output (users always are); ``read_steps=False`` leaves
    out their steps (a v2 run reads them itself)."""
    if unit == "sessions":
        return load_sessions(
            project_id, unit_ids, keep_empty=keep_empty, read_steps=read_steps
        )
    if unit == "users":
        return load_users(project_id, unit_ids, since, until, read_steps=read_steps)
    return load_traces(
        project_id, unit_ids, keep_empty=keep_empty, read_steps=read_steps
    )
