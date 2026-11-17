"""A few plain lines of an entry's recorded demo, for the pick cards (GET /v1/picks).

The recording is an asciicast v2 file (a JSON header line, then `[seconds, "o", text]`
events) holding what a publisher's program printed, so none of it reaches a card as it
is: terminal escape sequences and control characters are removed, whitespace is
collapsed, credential-shaped strings are hidden, shell prompt lines are dropped, and
each line and the whole excerpt are capped. Nothing is added that the recording did
not print. A version with no recording, or one that does not parse, has no excerpt
(None).
"""

from __future__ import annotations

import json
import re
import unicodedata

import structlog
from django.core.cache import cache

from omega_hub.services import storage
from tfc.logging import credential_scrub

logger = structlog.get_logger(__name__)

MIN_LINES = 2
MAX_LINES = 4
LINE_CHARS = 120
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
# Recordings the checker accepts hold at most 64 KiB; a far larger one is not read.
MAX_CAST_BYTES = 256 * 1024
# A version never changes, so its excerpt is kept for a week. A failed read is tried
# again a minute later.
CACHE_SECONDS = 7 * 24 * 60 * 60
RETRY_SECONDS = 60
CACHE_PREFIX = "omega_hub:demo_excerpt:v1:"

# Terminal escape sequences, in this order: OSC, DCS, SOS, PM and APC strings (to BEL or
# ST, or to the next escape when unterminated), then CSI, then any other escape.
_SEQUENCE = re.compile(
    r"(?:\x1b[\]PX^_]|[\x90\x98\x9d\x9e\x9f])[^\x07\x1b\x9c]*(?:\x07|\x1b\\|\x9c)?"
    r"|(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]"
    r"|\x1b[ -/]*[0-~]"
)
# Control, format (bidirectional overrides, zero-width), surrogate and private-use.
_UNSHOWN = frozenset({"Cc", "Cf", "Cs", "Co"})
# A shell prompt and the command typed at it: `$ omega run ...`, `user@host:~/dir$ ...`,
# `[user@host dir]$ ...`, `PS C:\dir> ...`, and the `> name@1.0.0 demo` line npm prints.
_PROMPT = re.compile(
    "(?:[$%>\N{HEAVY RIGHT-POINTING ANGLE QUOTATION MARK ORNAMENT}"
    "\N{HEAVY ROUND-TIPPED RIGHTWARDS ARROW}\N{GREEK SMALL LETTER LAMDA}]"
    r"|[\w.-]+@[\w.-]+(?:[: ]\S*?)? ?[$#%>]"
    r"|\[[^\]]*\][$#%]"
    r"|PS [^>]*>)"
    r"(?: |$)"
)
# The Hub checker's own credential patterns, for shapes the platform scrubber leaves
# alone, such as an `sk-` key shorter than 32 characters.
_CHECKER_PATTERNS = (
    ("provider-key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{24,}")),
    ("aws-access-key-id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("private-key", re.compile("-----BEGIN [A-Z ]*" + "PRIVATE KEY-----")),
)
# What a hidden credential reads as (credential_scrub.MARKER_FORMAT).
_MARKER = re.compile(r"\[hidden: [a-z0-9-]+\]")


def for_version(version) -> list[str] | None:
    """A version's excerpt: read from storage once, then served from the cache.

    A cache or storage failure gives None; it never fails the request.
    """
    if version is None or not version.cast_key:
        return None
    key = f"{CACHE_PREFIX}{version.pk}"
    try:
        cached = cache.get(key)
    except Exception as exc:
        logger.warning(
            "omega_hub_demo_excerpt_cache_failed", error=exc.__class__.__name__
        )
        return None
    if isinstance(cached, dict):
        return cached.get("lines")
    try:
        data = storage.read_bytes(version.cast_key)
    except Exception as exc:
        logger.warning(
            "omega_hub_demo_excerpt_unreadable",
            version=str(version.pk),
            error=exc.__class__.__name__,
        )
        _remember(key, None, RETRY_SECONDS)
        return None
    try:
        lines = excerpt(data)
    except Exception as exc:
        logger.warning(
            "omega_hub_demo_excerpt_failed",
            version=str(version.pk),
            error=exc.__class__.__name__,
        )
        lines = None
    _remember(key, lines, CACHE_SECONDS)
    return lines


def _remember(key: str, lines: list[str] | None, seconds: int) -> None:
    try:
        cache.set(key, {"lines": lines}, seconds)
    except Exception as exc:
        logger.warning(
            "omega_hub_demo_excerpt_cache_failed", error=exc.__class__.__name__
        )


def excerpt(data: bytes) -> list[str] | None:
    """2 to 4 plain lines from the end of a recording's output, or None."""
    output = _output(data)
    if output is None:
        return None
    shown = "\n".join(_shown(row) for row in _SEQUENCE.sub("", output).split("\n"))
    lines = [
        line
        for line in _hide_credentials(shown).split("\n")
        if line and not _PROMPT.match(line)
    ]
    if len(lines) < MIN_LINES:
        return None
    return [_cap(line) for line in lines[-MAX_LINES:]]


def _output(data: bytes) -> str | None:
    """The text of the output events of an asciicast v2 recording, or None for anything
    else (asciicast v1, plain text, a broken event, an oversized file)."""
    if len(data) > MAX_CAST_BYTES:
        return None
    try:
        rows = [row for row in data.decode("utf-8").split("\n") if row.strip()]
        header = json.loads(rows[0]) if rows else None
        if not (
            isinstance(header, dict)
            and header.get("version") == 2
            and type(header.get("width")) is int
            and type(header.get("height")) is int
        ):
            return None
        chunks = []
        for row in rows[1:]:
            event = json.loads(row)
            if not (
                isinstance(event, list)
                and len(event) >= 3
                and isinstance(event[0], int | float)
                and not isinstance(event[0], bool)
                and isinstance(event[1], str)
                and isinstance(event[2], str)
            ):
                return None
            if event[1] == "o":
                chunks.append(event[2])
    except (ValueError, RecursionError):
        return None
    return "".join(chunks)


def _shown(row: str) -> str:
    """One output line as a terminal leaves it: a carriage return starts the line again
    (a progress counter redraws itself), so the last text written after one is kept."""
    shown = ""
    for part in row.split("\r"):
        text = _plain(part)
        if text:
            shown = text
    return shown


def _plain(text: str) -> str:
    """Backspace removes the character before it, whitespace becomes one space, and
    every other control, format, surrogate or private-use character is dropped."""
    if text.isascii() and text.isprintable():
        return " ".join(text.split())
    kept: list[str] = []
    for char in text:
        if char == "\b":
            if kept:
                kept.pop()
        elif char.isspace():
            kept.append(" ")
        elif unicodedata.category(char) not in _UNSHOWN:
            kept.append(char)
    return " ".join("".join(kept).split())


def _hide_credentials(text: str) -> str:
    """The platform's credential scrubber over the whole text (a private key block spans
    lines), then the Hub checker's patterns; each match becomes `[hidden: <kind>]`."""
    text = credential_scrub.scrub_text(text)
    for kind, pattern in _CHECKER_PATTERNS:
        text = pattern.sub(credential_scrub.MARKER_FORMAT.format(kind), text)
    return text


def _cap(line: str) -> str:
    """At most LINE_CHARS characters: a longer line is cut at a word, never inside a
    `[hidden: ...]` marker, and ends with an ellipsis."""
    if len(line) <= LINE_CHARS:
        return line
    cut = LINE_CHARS - 1
    if line[cut] != " ":
        space = line.rfind(" ", 0, cut)
        if space >= LINE_CHARS // 2:
            cut = space
    for marker in _MARKER.finditer(line, 0, cut + LINE_CHARS):
        if marker.start() < cut < marker.end():
            cut = marker.start()
    return line[:cut].rstrip() + ELLIPSIS
