"""Reading a transcript a person pasted or uploaded: JSON messages, "User:" /
"Agent:" text, or CSV rows. Each turn is the user's or the agent's. System and
tool messages are left out and counted. Input that cannot be read names its line.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

Format = Literal["json", "text", "csv"]
Role = Literal["user", "agent"]

FORMATS: tuple[Format, ...] = ("json", "text", "csv")
# The longest text read as one transcript, pasted, uploaded or a dataset cell.
# Only what fits the import's size budget is kept, so this bounds the work of
# reading it, not what is stored.
MAX_TRANSCRIPT_CHARS = 500_000

_USER = frozenset({"user", "human", "customer", "caller", "client"})
_AGENT = frozenset({"agent", "assistant", "ai", "bot", "model"})
# The agent's prompt and its tool traffic: not what the user and the agent said.
_LEFT_OUT = frozenset({"system", "developer", "tool", "function"})
_ROLE_COLUMNS = ("role", "speaker")
_TEXT_COLUMNS = ("text", "content", "message")
# A speaker label: one word, a colon, then a space or the line's end, so a line
# that starts with a link (https://…) or a time (10:30) is not one.
_SPEAKER = re.compile(r"([A-Za-z]{1,20}):(?:[ \t](.*))?$")
_JSON_GAP = " \t\r\n,"
# An unknown speaker is named in the refusal, cut to this: a role cell can hold
# anything, and the refusal should not repeat a transcript back.
_SPEAKER_SHOWN = 30


class TranscriptError(ValueError):
    """Input that cannot be read as a transcript, with the line it fails on."""

    def __init__(self, message: str, line: int | None = None) -> None:
        super().__init__(f"Line {line}: {message}" if line else message)
        self.line = line


@dataclass(frozen=True)
class ParsedTurn:
    role: Role
    text: str


@dataclass(frozen=True)
class ParsedTranscript:
    turns: tuple[ParsedTurn, ...]
    # System and tool messages left out.
    left_out: int


def parse_transcript(text: str, form: Format) -> ParsedTranscript:
    """``text`` read as ``form``. Raises TranscriptError, naming the line where
    one can be told."""
    text = text.removeprefix("\ufeff")
    if not text.strip():
        raise TranscriptError("The transcript is empty.")
    turns, left_out = _READERS[form](text)
    if not turns:
        raise TranscriptError("No message from the user or the agent was found.")
    return ParsedTranscript(tuple(turns), left_out)


def _role(value: object, line: int | None) -> Role | None:
    """The turn's role, or None for a message that is left out."""
    name = str(value or "").strip()
    if not name:
        raise TranscriptError("No speaker is given.", line)
    if name.lower() in _USER:
        return "user"
    if name.lower() in _AGENT:
        return "agent"
    if name.lower() in _LEFT_OUT:
        return None
    shown = name if len(name) <= _SPEAKER_SHOWN else f"{name[:_SPEAKER_SHOWN]}…"
    raise TranscriptError(f"Unknown speaker {shown!r}: use User or Agent.", line)


def _json(text: str) -> tuple[list[ParsedTurn], int]:
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise TranscriptError(f"Not valid JSON: {error.msg}.", error.lineno) from None
    except RecursionError:
        raise TranscriptError("Not valid JSON: it is nested too deeply.") from None
    messages = document.get("messages") if isinstance(document, dict) else document
    if not isinstance(messages, list):
        raise TranscriptError(
            'Expected a list of messages, or an object with a "messages" list.', 1
        )
    lines = _message_lines(text, messages, top_level=messages is document)
    turns: list[ParsedTurn] = []
    left_out = 0
    for message, line in zip(messages, lines, strict=True):
        if not isinstance(message, dict):
            raise TranscriptError(
                "Each message must be an object with a role and content.", line
            )
        role = _role(message.get("role"), line)
        content = None if role is None else _content(message, line)
        if role is None or content is None:
            left_out += 1
        # An escaped NUL (\u0000) is no text, and a database cannot store it.
        elif said := content.replace("\x00", "").strip():
            turns.append(ParsedTurn(role, said))
    return turns, left_out


def _content(message: dict, line: int | None) -> str | None:
    """A message's text; None for an agent message holding only tool calls."""
    key = next((key for key in _TEXT_COLUMNS if key in message), None)
    value = message[key] if key else None
    if value is None:
        if message.get("tool_calls"):
            return None
        raise TranscriptError("A message has no content.", line)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        # Content parts: their text, without images or audio.
        return "\n".join(
            part if isinstance(part, str) else str(part.get("text") or "")
            for part in value
            if isinstance(part, str)
            or (isinstance(part, dict) and part.get("type") == "text")
        )
    raise TranscriptError("A message's content must be text.", line)


def _message_lines(text: str, messages: list, *, top_level: bool) -> list[int | None]:
    """The line each message starts on, or None for each when it cannot be told."""
    unknown: list[int | None] = [None] * len(messages)
    if top_level:
        start = text.find("[")
    else:
        found = re.search(r'"messages"\s*:\s*\[', text)
        start = found.end() - 1 if found else -1
    if start < 0:
        return unknown
    decoder = json.JSONDecoder()
    lines: list[int | None] = []
    index, line, counted = start + 1, 1, 0
    try:
        for message in messages:
            while text[index] in _JSON_GAP:
                index += 1
            line += text.count("\n", counted, index)
            counted = index
            value, index = decoder.raw_decode(text, index)
            if value != message:
                return unknown
            lines.append(line)
    except (IndexError, ValueError):
        return unknown
    return lines


def _text(text: str) -> tuple[list[ParsedTurn], int]:
    turns: list[tuple[Role, list[str]]] = []
    left_out = 0
    # The lines of the turn being read; None inside a message that is left out.
    current: list[str] | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        found = _SPEAKER.match(line)
        if found:
            role = _role(found.group(1), number)
            if role is None:
                left_out += 1
                current = None
            else:
                current = [found.group(2) or ""]
                turns.append((role, current))
        elif current is not None:
            current.append(line)
        elif line.strip() and not turns and not left_out:
            raise TranscriptError(
                "Expected a speaker, such as 'User:' or 'Agent:', at the start of "
                "the line.",
                number,
            )
    read = [ParsedTurn(role, "\n".join(lines).strip()) for role, lines in turns]
    return [turn for turn in read if turn.text], left_out


def _csv(text: str) -> tuple[list[ParsedTurn], int]:
    reader = csv.reader(io.StringIO(text, newline=""))
    turns: list[ParsedTurn] = []
    left_out = 0
    try:
        header = [cell.strip().lower() for cell in next(reader, [])]
        role_at = next((header.index(n) for n in _ROLE_COLUMNS if n in header), None)
        text_at = next((header.index(n) for n in _TEXT_COLUMNS if n in header), None)
        if role_at is None or text_at is None:
            raise TranscriptError(
                "The first row must name a role column (role or speaker) and a "
                "text column (text, content or message).",
                1,
            )
        start = reader.line_num + 1
        for row in reader:
            # A quoted cell can span lines: name the row's first.
            line, start = start, reader.line_num + 1
            if not any(cell.strip() for cell in row):
                continue
            if len(row) <= max(role_at, text_at):
                raise TranscriptError("The row has fewer cells than the header.", line)
            role = _role(row[role_at], line)
            if role is None:
                left_out += 1
            elif row[text_at].strip():
                turns.append(ParsedTurn(role, row[text_at].strip()))
    except csv.Error as error:
        raise TranscriptError(f"Not valid CSV: {error}.", reader.line_num) from None
    return turns, left_out


_READERS: dict[Format, Callable[[str], tuple[list[ParsedTurn], int]]] = {
    "json": _json,
    "text": _text,
    "csv": _csv,
}
