"""How a worker record reads a recorded value: names as ids, tasks and answers as short text, payloads bounded, times."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from simulate.types import SealedToolCall

SUMMARY_CHARS = 500
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
PAYLOAD_CHARS = 2000


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_") or "worker"


def span_kind(span: Mapping[str, Any]) -> str:
    return str(span.get("observation_type") or "").strip().lower()


def _said(message: Any) -> str:
    """What one message of a conversation says: a message object's `content`, else its `content='...'` text."""
    if isinstance(message, dict):
        return str(message.get("content") or "").strip()
    found = re.match(r"\s*content=(['\"])(.*?)(?<!\\)\1(?:\s|$)", str(message), re.S)
    return (found.group(2) if found else str(message)).strip()


def summary(value: Any, *, argument: bool = False) -> str | None:
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
    if len(text) > SUMMARY_CHARS:
        # Say the text goes on, so a cut-off answer is not read as the whole of it.
        text = text[:SUMMARY_CHARS].rstrip() + "…"
    return text or None


def task_text(arguments: object) -> str | None:
    """A hand-off's task from the arguments of the call that made it."""
    return summary(
        json.dumps(arguments) if isinstance(arguments, dict) else arguments,
        argument=True,
    )


def payload(value: Any) -> Any:
    """A tool call's input or output as recorded, cut with an ellipsis when it runs past the payload limit."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            pass
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    if len(text) <= PAYLOAD_CHARS:
        return value
    return text[:PAYLOAD_CHARS].rstrip() + "…"


def moment(at: Any) -> float | None:
    if not isinstance(at, str) or not at:
        return None
    try:
        parsed = datetime.fromisoformat(at)
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp()


def digest(value: Any) -> str | None:
    shown = payload(value)
    return None if shown is None else json.dumps(shown, sort_keys=True, default=str)


def utc_iso(seconds: float) -> str | None:
    """Epoch seconds as a UTC timestamp with its zone; None for a value no date can hold."""
    try:
        return datetime.fromtimestamp(seconds, tz=UTC).isoformat()
    except (OverflowError, ValueError, OSError):
        return None


def epoch_iso(value: Any) -> str | None:
    """A sealed row's epoch time as a timestamp; None when it carries none."""
    return utc_iso(value) if isinstance(value, (int, float)) and value > 0 else None


def row_started(row: Mapping[str, Any]) -> str | None:
    # A row stamped after the call ended carries when it was written, not when the call began.
    at, ended = row.get("at"), row.get("ended_at")
    if (
        isinstance(at, (int, float))
        and isinstance(ended, (int, float))
        and 0 < ended < at
    ):
        return None
    return epoch_iso(at)


def row_status(row: Mapping[str, Any]) -> Literal["ok", "error"]:
    return "error" if row.get("ok") is False or row.get("refused") else "ok"


def row_output(row: Mapping[str, Any]) -> object:
    return row.get("result") if row.get("result") is not None else row.get("error")


def runtime_ids(row: SealedToolCall) -> Mapping[str, object]:
    ids = row.get("runtime_ids")
    return ids if isinstance(ids, Mapping) else {}


def span_status(span: Mapping[str, Any]) -> Literal["ok", "error"]:
    return "error" if str(span.get("status") or "").upper() == "ERROR" else "ok"
