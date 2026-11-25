"""Unit evidence snapshots (``insights-evidence/v1``) for the Omega reader.

A snapshot holds everything Insights may read about one unit: a trace, a
session (its traces in order) or a user (their recent sessions). It is the
span graph plus each span's fields rendered to text. Django builds it, Omega
reads it through bounded tools, and the private-corpus exporter writes the same
document to disk, so quality work on real data runs the production path.

Document: ``{"manifest": {...}, "fields": {span alias: {field: text}}}``.

- Aliases are stable within a snapshot: traces ``t1..`` by start, spans
  ``s1..`` depth first (roots and children by start, then span id), media
  ``m1..`` in span order. Real ids stay in the manifest for provenance.
- Fields are ``input``, ``output``, ``attributes``, ``events``,
  ``status_message`` and ``metadata``; empty ones are left out. Rendering is
  canonical here (Node never re-renders): chat messages become ``[role] text``
  lines, tool calls ``[tool_call name] {args}`` and tool results
  ``[tool_result name] ...``; image, audio and file content becomes an
  ``[image m3]`` marker with a media entry; other JSON is pretty-printed with
  sorted keys and long base64 becomes ``[binary N bytes]``. Raw base64, media
  URLs and URLs carrying a query string (signatures, tokens) never enter the
  text; a source cut short or not parseable has every URL inventoried. Trace
  content is data: it is kept verbatim, never interpreted.
- Reads are bounded and fail closed. A cap never drops anything silently:
  what it left out is recorded in ``manifest.limits`` and marked in the text.
- ``chars`` in the manifest count UTF-16 code units (JavaScript string
  length), the unit the Node reader slices by.
- ``manifest.digest`` is the SHA-256 of the canonical JSON of ``{"fields",
  "manifest" without "digest"}``. Canonical JSON is what ``JSON.stringify``
  writes for the same value with sorted keys: no whitespace, raw non-ASCII,
  ECMAScript number formatting. Integers past 2**53 are refused, so ids such
  as ``_version`` are written as strings.

ClickHouse access follows ``trace_detail_reads``: the run's project only,
latest versions by ``argMax(..., _version)`` with tombstones dropped, and
light identity reads before batched content reads of exact physical spans.
The identity read measures every column a content read returns, so the unit's
read budget charges all of it. Each statement asks the server to stop within
the unit's deadline, and work between reads is held to the same deadline.
"""

from __future__ import annotations

import base64
import binascii
import bisect
import copy
import hashlib
import json
import math
import re
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Protocol
from urllib.parse import urlparse

from tracer.services.clickhouse.read_budget import (
    ReadDeadlineExceeded,
    is_clickhouse_query_error,
    is_read_budget_error,
)
from tracer.services.clickhouse.v2.trace_detail_reads import PhysicalSpanIdentity

SCHEMA = "insights-evidence/v1"
UNIT_KINDS = ("trace", "session", "user")
FIELD_ORDER = ("input", "output", "attributes", "events", "status_message", "metadata")

_KIND_ALIASES = {"traces": "trace", "sessions": "session", "users": "user"}
_STATUS_MESSAGE_CHARS = 65_536
_ROW_READ_CHARS = 4_096  # a span's scalar columns, charged with its content
_STATUS_READ_CHARS = 512  # the same for a span read for its status only
_MIN_PARTIAL_CHARS = 1_024  # the least per column a part read of a span keeps
_DEADLINE_FLOOR_MS = 25
_ID_BATCH = 1_000  # ids per statement; ClickHouse parses at most 256 KiB
_MAX_KEY_CHARS = 256
_MIN_KEPT_BYTES = 2_048
_TRUNCATION_RECORD_BYTES = 192
_MAX_SAFE_INTEGER = 2**53 - 1
# The same per-statement budget as trace-detail reads.
_READ_SETTINGS = {
    "max_threads": 1,
    "max_block_size": 8192,
    "max_memory_usage": 36 * 1024 * 1024 * 1024,
    "max_bytes_to_read": 36 * 1024 * 1024 * 1024,
    "read_overflow_mode": "throw",
    "result_overflow_mode": "throw",
    "optimize_functions_to_subcolumns": 1,
}


class EvidenceUnavailable(RuntimeError):
    """A bounded read could not prove a complete, in-scope snapshot."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class EvidenceNotFound(LookupError):
    """The unit has no live spans inside the run's project."""


class _Result(Protocol):
    data: list[dict[str, Any]]


class _Analytics(Protocol):
    def execute_ch_query(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
        server_execution_cap_ms: int | None = None,
    ) -> _Result: ...


@dataclass(frozen=True)
class EvidenceLimits:
    """Caps for one unit. Exceeding one is recorded, never silent."""

    max_spans: int = 5_000
    max_field_chars: int = 2 * 1024 * 1024
    max_snapshot_bytes: int = 32 * 1024 * 1024
    user_sessions: int = 8
    max_traces: int = 1_000
    max_roster_traces: int = 20_000
    max_identity_spans: int = 50_000
    max_raw_field_chars: int = 8 * 1024 * 1024
    max_raw_chars: int = 96 * 1024 * 1024
    content_batch_spans: int = 200
    content_batch_chars: int = 16 * 1024 * 1024
    deadline_ms: int = 120_000
    query_timeout_ms: int = 30_000

    def __post_init__(self) -> None:
        for item in fields(self):
            value = getattr(self, item.name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{item.name} must be a positive integer")
        if self.max_traces < 2:
            raise ValueError("max_traces must keep at least the first and last trace")
        if self.content_batch_spans > 200:
            raise ValueError("content_batch_spans exceeds the bounded batch size")

    def manifest(self) -> dict[str, int]:
        return {
            "max_spans": self.max_spans,
            "max_field_chars": self.max_field_chars,
            "max_snapshot_bytes": self.max_snapshot_bytes,
            "max_traces": self.max_traces,
            "user_sessions": self.user_sessions,
            "max_raw_field_chars": self.max_raw_field_chars,
            "max_raw_chars": self.max_raw_chars,
        }


@dataclass(frozen=True)
class MediaSource:
    """Where a media entry's bytes live. Private: never written to a snapshot
    or a log, since a URL can carry a signature."""

    source: str
    value: str
    mime: str | None = None


@dataclass(frozen=True)
class EvidenceSnapshot:
    document: dict[str, Any]
    media_sources: Mapping[str, MediaSource]
    query_count: int = 0
    elapsed_ms: float = 0.0

    @property
    def manifest(self) -> dict[str, Any]:
        return self.document["manifest"]

    @property
    def fields(self) -> dict[str, dict[str, str]]:
        return self.document["fields"]

    @property
    def digest(self) -> str:
        return self.manifest["digest"]

    def to_bytes(self) -> bytes:
        return canonical_json(self.document).encode("utf-8")


# --------------------------------------------------------------------------- #
# Canonical JSON and digests
# --------------------------------------------------------------------------- #
def canonical_json(value: Any) -> str:
    """``JSON.stringify`` of ``value`` with sorted keys, byte for byte."""
    parts: list[str] = []
    _write_canonical(value, parts.append)
    return "".join(parts)


def _write_canonical(value: Any, out: Callable[[str], Any]) -> None:
    if value is None:
        out("null")
    elif value is True:
        out("true")
    elif value is False:
        out("false")
    elif isinstance(value, str):
        out(json.dumps(value, ensure_ascii=False))
    elif isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise ValueError("integer is not exactly representable in JavaScript")
        out(str(value))
    elif isinstance(value, float):
        out(_js_number(value))
    elif isinstance(value, Mapping):
        out("{")
        for index, key in enumerate(sorted(value)):
            if not isinstance(key, str):
                raise TypeError("canonical JSON keys must be strings")
            if index:
                out(",")
            out(json.dumps(key, ensure_ascii=False))
            out(":")
            _write_canonical(value[key], out)
        out("}")
    elif isinstance(value, list | tuple):
        out("[")
        for index, item in enumerate(value):
            if index:
                out(",")
            _write_canonical(item, out)
        out("]")
    else:
        raise TypeError(f"{type(value).__name__} is not canonical JSON")


def _js_number(value: float) -> str:
    """ECMAScript Number::toString. Python and JavaScript both print the
    shortest digits that round-trip; only the layout differs."""
    if not math.isfinite(value):
        raise ValueError("NaN and infinities are not JSON")
    if value == 0:
        return "0"
    if value.is_integer() and abs(value) <= _MAX_SAFE_INTEGER:
        return str(int(value))
    sign = "-" if value < 0 else ""
    mantissa, _, exponent = repr(abs(value)).partition("e")
    whole, _, fraction = mantissa.partition(".")
    digits = (whole + fraction).lstrip("0")
    if whole.strip("0"):
        point = len(whole.lstrip("0")) + int(exponent or 0)
    else:
        point = int(exponent or 0) - (len(fraction) - len(fraction.lstrip("0")))
    digits = digits.rstrip("0") or "0"
    count = len(digits)
    if count <= point <= 21:
        return sign + digits + "0" * (point - count)
    if 0 < point <= 21:
        return f"{sign}{digits[:point]}.{digits[point:]}"
    if -6 < point <= 0:
        return f"{sign}0.{'0' * -point}{digits}"
    power = point - 1
    tail = f".{digits[1:]}" if count > 1 else ""
    return f"{sign}{digits[0]}{tail}e{'+' if power >= 0 else '-'}{abs(power)}"


def snapshot_digest(manifest: Mapping[str, Any], fields: Mapping[str, Any]) -> str:
    body = {key: value for key, value in manifest.items() if key != "digest"}
    payload = canonical_json({"fields": fields, "manifest": body})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_snapshot(document: Mapping[str, Any]) -> bool:
    manifest = document.get("manifest") or {}
    return manifest.get("digest") == snapshot_digest(manifest, document["fields"])


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean(text: str) -> str:
    """Pair surrogates a JSON escape split and replace lone ones, so every
    rendered text encodes as UTF-8."""
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


# --------------------------------------------------------------------------- #
# Media detection
# --------------------------------------------------------------------------- #
_DATA_URI = re.compile(
    r"data:(?P<mime>[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+)?"
    r"(?:;[A-Za-z0-9.+_-]+=[^;,\s\"']*)*;base64,(?P<payload>[A-Za-z0-9+/]+={0,2})"
)
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/=]{257,}")
_BASE64_VALUE = re.compile(r"[A-Za-z0-9+/]*={0,2}")
# A URL inside free text, JSON-escaped (``\\/``, ``\\u0026``) or not. Linear:
# the body's alternatives cannot overlap.
_EMBEDDED = re.compile(
    rf"(?P<uri>{_DATA_URI.pattern})"
    r"|(?P<url>(?i:https?|gs|s3):(?://|\\/\\/)"
    r"(?:[^\s\"'<>\\]|\\/|\\u[0-9A-Fa-f]{4})+)"
)
_JSON_ESCAPE = re.compile(r"\\u([0-9A-Fa-f]{4})")
_URL_TRAILER = ".,;:!?)]}"
# The key a URL is the value of, from the text just before it:
# ``"image_url": "``, ``'recordingUrl': '``, ``\\"url\\": \\"``, ``audio=``.
_KEY_BEFORE = re.compile(r"""([A-Za-z_][\w.\-]{0,63})\\?["']?\s*[:=]\s*\\?["']?\s*$""")
# The object a generic ``url`` key sits in: ``"image_url": {"url": "``.
_CONTAINER_BEFORE = re.compile(
    r"""([A-Za-z_][\w.\-]{0,63})\\?["']?\s*:\s*\{[^{}]{0,64}$"""
)
_URL_SCHEMES = ("http://", "https://", "gs://", "s3://")
_URL_SUFFIXES = ("url", "uri", "urls", "uris", "src", "href")
_AUDIO_TOKENS = ("recording", "audio", "voice", "speech", "stereo", "mono")
_IMAGE_TOKENS = ("image", "img", "photo", "picture", "screenshot", "thumbnail")
_FILE_TOKENS = ("video", "document", "attachment", "pdf")
_PAYLOAD_KEYS = frozenset({"data", "b64json", "base64", "bytes", "audio", "image"})
# Attribute keys known to hold a media URL (semantic_conventions.py and the
# eval-mapping fallbacks in tracer/utils/eval.py).
_MEDIA_ATTRIBUTES = {
    "gen_ai.voice.recording.url": "audio",
    "gen_ai.voice.recording.stereo_url": "audio",
    "gen_ai.voice.recording.customer_url": "audio",
    "gen_ai.voice.recording.assistant_url": "audio",
    "conversation.recording.stereo": "audio",
    "conversation.recording.mono.combined": "audio",
    "conversation.recording.mono.customer": "audio",
    "conversation.recording.mono.assistant": "audio",
    "recording_url": "audio",
    "stereo_recording_url": "audio",
    "voice_recording_url": "audio",
    "customer_recording_url": "audio",
    "assistant_recording_url": "audio",
    "audio_url": "audio",
    "gen_ai.audio.url": "audio",
    "audio.url": "audio",
    "gen_ai.image.output_urls": "image",
    "image.url": "image",
    "image_url": "image",
}
# A fixed table, not ``mimetypes``: the platform's tables would make the
# digest depend on the machine.
_EXTENSION_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "svg": "image/svg+xml",
    "heic": "image/heic",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "m4a": "audio/mp4",
    "ogg": "audio/ogg",
    "oga": "audio/ogg",
    "opus": "audio/opus",
    "flac": "audio/flac",
    "aac": "audio/aac",
    "weba": "audio/webm",
    "pdf": "application/pdf",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mov": "video/quicktime",
}
_MIME_EXTENSION = {
    **{mime: ext for ext, mime in _EXTENSION_MIME.items()},
    "image/jpeg": "jpg",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/mp3": "mp3",
    "audio/x-m4a": "m4a",
    "audio/webm": "weba",
    "video/webm": "webm",
}


def _key_name(key: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (key or "").lower())


def _token_kind(name: str) -> str | None:
    for tokens, kind in (
        (_AUDIO_TOKENS, "audio"),
        (_IMAGE_TOKENS, "image"),
        (_FILE_TOKENS, "file"),
    ):
        if any(token in name for token in tokens):
            return kind
    return None


def _media_key_kind(key: str | None) -> str | None:
    """The media kind a key names outright (``recording_url``,
    ``imageUrl``...), or None."""
    if not key:
        return None
    if key in _MEDIA_ATTRIBUTES:
        return _MEDIA_ATTRIBUTES[key]
    name = _key_name(key)
    if name == "b64json":
        return "image"
    return _token_kind(name) if name.endswith(_URL_SUFFIXES) else None


def _media_hint(key: str | None) -> str | None:
    """The media kind a container key suggests for the URLs inside it
    (``recording: {mono: {combinedUrl}}``)."""
    return _token_kind(_key_name(key)) if key else None


def _without_secrets(url: str) -> str:
    """``url`` without its credentials, query string and fragment, which can
    hold signatures and tokens; the scheme, host and path stay readable."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError:
        return "[link omitted]"
    if not host:
        return "[link omitted]"
    kept = f"{parsed.scheme}://{host}{port}{parsed.path}"
    if parsed.query:
        kept += "?[query omitted]"
    if parsed.fragment:
        kept += "#[fragment omitted]"
    return kept


def _url_like_key(key: str | None) -> bool:
    return bool(key) and _key_name(key).endswith(_URL_SUFFIXES)


def _looks_like_url(value: str) -> bool:
    return value.lower().startswith(_URL_SCHEMES)


def _kind_for_mime(mime: str | None) -> str | None:
    if not mime or "/" not in mime:
        return None
    major = mime.split("/", 1)[0].lower()
    return major if major in ("image", "audio") else "file"


def _mime_for_url(url: str) -> str | None:
    try:
        path = urlparse(url).path.lower()
    except ValueError:
        return None
    ext = path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    return _EXTENSION_MIME.get(ext)


def _looks_encoded(run: str) -> bool:
    """Encoded bytes mix character classes; a ``=====`` rule or a run of one
    letter in a log line does not."""
    classes = (str.isupper, str.islower, str.isdigit)
    return sum(any(test(ch) for ch in run) for test in classes) >= 2


def _base64_size(payload: str) -> int:
    padding = len(payload) - len(payload.rstrip("="))
    return max(0, len(payload) * 3 // 4 - padding)


def default_own_storage_predicate() -> Callable[[str], bool]:
    """True for a URL of the platform's own upload bucket whose object key
    parses (``tfc.utils.storage``)."""
    from tfc.settings.settings import UPLOAD_BUCKET_NAME
    from tfc.utils.storage import is_own_storage_url
    from tfc.utils.storage_client import extract_object_key

    def predicate(url: str) -> bool:
        try:
            return bool(
                is_own_storage_url(url, UPLOAD_BUCKET_NAME)
                and extract_object_key(url, UPLOAD_BUCKET_NAME)
            )
        except Exception:
            return False

    return predicate


class _MediaRegistry:
    """Media entries of one snapshot, aliased ``m1..`` as they are found.

    No bytes are fetched here. ``accessible`` means a resolvable source exists:
    a decodable data URI, a parseable own-storage key or a syntactically valid
    https URL. The value itself goes to ``sources``, never into the text."""

    def __init__(self, own_storage: Callable[[str], bool]):
        self._own_storage = own_storage
        self.entries: list[dict[str, Any]] = []
        self.sources: dict[str, MediaSource] = {}

    def owns(self, url: str) -> bool:
        """Whether ``url`` points into the platform's own upload bucket."""
        try:
            return bool(self._own_storage(url))
        except Exception:
            return False

    def add(
        self,
        *,
        span: str,
        field: str,
        path: str,
        value: str,
        kind: str | None = None,
        mime: str | None = None,
        encoding: str = "auto",
        truncated: bool = False,
    ) -> str:
        alias = f"m{len(self.entries) + 1}"
        source, payload, mime, size, reason = self._classify(
            value, mime, encoding, truncated
        )
        if source in ("own_storage", "external") and not mime:
            mime = _mime_for_url(payload)
        kind = _kind_for_mime(mime) or kind or "file"
        self.entries.append(
            {
                "alias": alias,
                "span": span,
                "field": field,
                "path": path,
                "kind": kind,
                "mime": mime,
                "source": source,
                "bytes": size,
                "duration_s": None,
                "accessible": reason is None,
                "reason": reason,
            }
        )
        self.sources[alias] = MediaSource(source=source, value=payload, mime=mime)
        return f"[{kind} {alias}]"

    def _classify(
        self, value: str, mime: str | None, encoding: str, truncated: bool
    ) -> tuple[str, str, str | None, int | None, str | None]:
        mime = mime.strip().lower() if isinstance(mime, str) and mime.strip() else None
        if encoding == "reference":
            return "reference", value, mime, None, "provider_file_reference"
        if encoding == "base64":
            payload = re.sub(r"\s", "", value)
            valid = bool(_BASE64_VALUE.fullmatch(payload)) and len(payload) % 4 != 1
            reason = "source_truncated" if truncated else None
            if not valid and not truncated:
                reason = "invalid_base64"
            size = _base64_size(payload) if valid else None
            return "data_uri", payload, mime, size, reason
        text = value.strip()
        if text.lower().startswith("data:"):
            match = _DATA_URI.fullmatch(text)
            if match is None:
                reason = "source_truncated" if truncated else "unsupported_data_uri"
                return "data_uri", "", mime, None, reason
            payload = match.group("payload")
            mime = (match.group("mime") or "").lower() or mime
            reason = "source_truncated" if truncated else None
            return "data_uri", payload, mime, _base64_size(payload), reason
        try:
            parsed = urlparse(text)
            hostname = parsed.hostname
        except ValueError:
            return "external", text, mime, None, "invalid_url"
        scheme = parsed.scheme.lower()
        if scheme in ("http", "https"):
            if not hostname:
                return "external", text, mime, None, "invalid_url"
            source = "own_storage" if self._own_storage(text) else "external"
            if truncated:
                return source, text, mime, None, "source_truncated"
            if source == "own_storage" or scheme == "https":
                return source, text, mime, None, None
            return "external", text, mime, None, "not_https"
        reason = "unsupported_scheme" if scheme else "unsupported_reference"
        return "reference", text, mime, None, reason


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
_UNPARSED = object()
_TEXT_PARTS = frozenset(
    {"text", "input_text", "output_text", "summary_text", "reasoning_text"}
)
_RESPONSE_ITEMS = frozenset(
    {
        "message",
        "function_call",
        "function_call_output",
        "custom_tool_call",
        "custom_tool_call_output",
        "reasoning",
        "web_search_call",
        "file_search_call",
        "computer_call",
        "computer_call_output",
        "image_generation_call",
        "code_interpreter_call",
        "local_shell_call",
        "local_shell_call_output",
        "mcp_call",
        "mcp_list_tools",
    }
)
_TOOL_RESULT_ITEMS = frozenset(
    {
        "function_call_output",
        "custom_tool_call_output",
        "computer_call_output",
        "local_shell_call_output",
    }
)
_LANGCHAIN_TYPES = frozenset(
    {"human", "ai", "system", "tool", "function", "chat", "developer"}
)
_LANGCHAIN_CLASSES = {
    "HumanMessage": "human",
    "HumanMessageChunk": "human",
    "AIMessage": "ai",
    "AIMessageChunk": "ai",
    "SystemMessage": "system",
    "ToolMessage": "tool",
    "FunctionMessage": "function",
    "ChatMessage": "chat",
}
_TOOL_ROLES = frozenset({"tool", "function", "tool_result", "ipython"})
_CONTENT_KEYS = ("content", "contents", "parts", "message", "text")
# Flattened message attributes: OpenInference (``llm.input_messages.0.
# message.role``), traceAI (``gen_ai.input.messages.0.message.role``, the
# SDK's own convention) and OpenLLMetry (``gen_ai.prompt.0.role``).
_FLAT_MESSAGES = re.compile(
    r"^(?P<group>llm\.input_messages|llm\.output_messages"
    r"|gen_ai\.input\.messages|gen_ai\.output\.messages"
    r"|gen_ai\.prompt|gen_ai\.completion)\.\d+\."
)
# OpenTelemetry GenAI part modalities as media kinds.
_MODALITY_KINDS = {"image": "image", "audio": "audio", "video": "file"}


class _RawText(str):
    """Text that was cut at the source (``original`` is its full length)
    or could not be parsed. Its structure is unknown, so every URL in it is
    inventoried as media; when cut, media at its very end are marked
    truncated and the text says how much is missing."""

    original: int | None = None


def _raw_text(value: str, original: int | None = None) -> _RawText:
    text = _RawText(value)
    text.original = original
    return text


def _is_cut(value: Any) -> bool:
    return isinstance(value, _RawText) and value.original is not None


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return _UNPARSED


def _pretty(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if math.isfinite(value) and value.is_integer() and abs(value) < 1e16:
            return str(int(value))
        return repr(value)
    return str(value)


def _has_content(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _langchain_kwargs(item: Any) -> dict | None:
    if (
        isinstance(item, dict)
        and item.get("lc")
        and item.get("type") == "constructor"
        and isinstance(item.get("kwargs"), dict)
    ):
        return item["kwargs"]
    return None


def _is_message(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    if _langchain_kwargs(item) is not None:
        return True
    role = item.get("role")
    if isinstance(role, str) and role:
        return any(
            key in item
            for key in (*_CONTENT_KEYS, "tool_calls", "function_call", "audio")
        ) or ("tool_call_id" in item)
    return item.get("type") in _LANGCHAIN_TYPES and "content" in item


def _is_response_item(item: Any) -> bool:
    return isinstance(item, dict) and item.get("type") in _RESPONSE_ITEMS


def _is_message_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(_is_message(i) or _is_response_item(i) for i in value)
    )


def _is_part_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(p, dict) and isinstance(p.get("type"), str) for p in value)
    )


def _natural_key(path: str) -> tuple:
    return tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part)
        for part in path.split(".")
    )


def _unflatten(flat: Mapping[str, Any]) -> dict[str, Any]:
    """``{"0.message.role": "user"}`` -> ``{"0": {"message": {"role": ...}}}``,
    with dense numeric keys turned into lists."""
    root: dict[str, Any] = {}
    for path in sorted(flat, key=_natural_key):
        node = root
        parts = path.split(".")
        for index, part in enumerate(parts[:-1]):
            child = node.get(part)
            if child is None:
                child = node[part] = {}
            if not isinstance(child, dict):
                # A leaf already sits where this path needs a branch.
                node[".".join(parts[index:])] = flat[path]
                break
            node = child
        else:
            node[parts[-1]] = flat[path]
    return _listify(root)


def _listify(node: Any) -> Any:
    if not isinstance(node, dict):
        return node
    node = {key: _listify(value) for key, value in node.items()}
    if node and sorted(node, key=lambda k: (len(k), k)) == [
        str(i) for i in range(len(node))
    ]:
        return [node[str(i)] for i in range(len(node))]
    return node


class _Loc:
    """Where a value sits in a field, as a JSON pointer (RFC 6901) into the
    raw field. Attribute values are addressed by key (``/gen_ai.voice...``);
    flattened message attributes keep their dotted key as one token."""

    __slots__ = ("parts", "dotted")

    def __init__(self, parts: tuple[str, ...] = (), dotted: bool = False):
        self.parts = parts
        self.dotted = dotted

    def child(self, key: Any) -> _Loc:
        return _Loc((*self.parts, str(key)), self.dotted)

    def pointer(self, suffix: str = "") -> str:
        tokens = (".".join(self.parts),) if self.dotted and self.parts else self.parts
        escaped = (t.replace("~", "~0").replace("/", "~1") for t in tokens)
        return "".join(f"/{token}" for token in escaped) + suffix


class _FieldRenderer:
    """Renders one field of one span. Media found on the way are registered
    against the span and field; tool names are shared across the span's
    fields so a result in ``output`` can name a call made in ``input``."""

    def __init__(
        self,
        media: _MediaRegistry,
        span: str,
        field: str,
        tool_names: dict[str, str],
        *,
        source_truncated: bool = False,
    ):
        self.media = media
        self.span = span
        self.field = field
        self.tool_names = tool_names
        self.source_truncated = source_truncated

    # Entry points -------------------------------------------------------- #
    def payload(self, raw: str) -> str:
        stripped = raw.strip()
        if stripped[:1] in ("[", "{", '"'):
            parsed = _parse_json(stripped)
            if parsed is not _UNPARSED:
                try:
                    return self._value(parsed, _Loc())
                except RecursionError:
                    pass
            return self.text(raw, _Loc(), opaque=True)  # JSON-like, unparsed
        return self.text(raw, _Loc())

    def json_document(self, raw: str) -> str:
        parsed = _parse_json(raw.strip())
        if parsed is _UNPARSED:
            return self.text(raw, _Loc(), opaque=True)
        if not _has_content(parsed):
            return ""
        return _pretty(self._scrub(parsed, _Loc()))

    def attributes(self, attributes: Mapping[str, Any]) -> str:
        groups: dict[str, dict[str, Any]] = defaultdict(dict)
        plain: dict[str, Any] = {}
        for key, value in attributes.items():
            match = _FLAT_MESSAGES.match(key)
            if match:
                group = match.group("group")
                groups[group][key[len(group) + 1 :]] = value
            else:
                plain[key] = value
        lines = []
        for name in sorted({*groups, *plain}):
            if name in groups:
                lines.append(self._flat_messages(name, groups[name]))
            if name in plain:
                lines.append(self._attribute(name, plain[name], _Loc()))
        return "\n".join(lines)

    def events(self, raw: str) -> str:
        parsed = _parse_json(raw.strip())
        if parsed is _UNPARSED:
            return self.text(raw, _Loc(), opaque=True)
        if not _has_content(parsed):
            return ""
        if not (isinstance(parsed, list) and all(isinstance(e, dict) for e in parsed)):
            return _pretty(self._scrub(parsed, _Loc()))
        blocks = []
        for index, event in enumerate(parsed):
            loc = _Loc((str(index),))
            stamp = event.get("timestamp", event.get("time"))
            header = f"[event {event.get('name') or 'event'}"
            header += f" {_scalar(stamp)}]" if _has_content(stamp) else "]"
            lines = [header]
            attributes = event.get("attributes")
            if isinstance(attributes, dict):
                lines += [
                    self._attribute(key, attributes[key], loc.child("attributes"))
                    for key in sorted(attributes)
                ]
            for key in sorted(event):
                if key not in ("name", "timestamp", "time", "attributes"):
                    lines.append(self._attribute(key, event[key], loc))
            blocks.append("\n".join(lines))
        return "\n\n".join(blocks)

    def text(self, value: str, loc: _Loc, *, opaque: bool = False) -> str:
        """Free text with embedded data URIs, media URLs and base64 runs
        replaced.

        A URL becomes a media marker (path ``#offset``) when it names media
        (by its key or extension), carries a query string or credentials
        (signatures, tokens), or sits in ``opaque`` text: a source that was
        cut or did not parse, whose keys cannot be trusted to say what a URL
        is. Other URLs, such as a documentation link, stay as text."""
        cut = self.source_truncated or _is_cut(value)
        opaque = opaque or cut or isinstance(value, _RawText)
        rendered = str(value)
        if "base64," in rendered or "://" in rendered or ":\\/\\/" in rendered:

            def replace(match: re.Match) -> str:
                if match.group("uri") is not None:
                    return self._media(
                        match.group(0),
                        loc,
                        suffix=f"#{match.start()}",
                        truncated=cut and match.end() == len(value),
                    )
                return self._url(match, value, loc, opaque=opaque, cut=cut)

            rendered = _EMBEDDED.sub(replace, rendered)
        if len(rendered) > 256:
            rendered = _BASE64_RUN.sub(
                lambda m: (
                    f"[binary {_base64_size(m.group(0))} bytes]"
                    if _looks_encoded(m.group(0))
                    else m.group(0)
                ),
                rendered,
            )
        if _is_cut(value) and value.original > len(value):
            omitted = value.original - len(value)
            rendered += f"\n[... {omitted} chars omitted: source cap ...]"
        return rendered

    def _url(
        self, match: re.Match, value: str, loc: _Loc, *, opaque: bool, cut: bool
    ) -> str:
        raw = match.group("url")
        url = raw.rstrip(_URL_TRAILER)
        trailer = raw[len(url) :]
        clean = _JSON_ESCAPE.sub(
            lambda m: chr(int(m.group(1), 16)), url.replace("\\/", "/")
        )
        window = max(0, match.start() - 160)
        before = _KEY_BEFORE.search(value, window, match.start())
        key = before.group(1) if before else None
        kind = _media_key_kind(key)
        if kind is None and (key is None or _url_like_key(key)):
            container = _CONTAINER_BEFORE.search(value, window, match.start())
            kind = _media_hint(container.group(1)) if container else None
        kind = kind or _kind_for_mime(_mime_for_url(clean))
        try:
            parsed = urlparse(clean)
            sensitive = bool(parsed.query or parsed.username or parsed.password)
        except ValueError:
            sensitive = True
        if not (kind or sensitive or opaque):
            return raw
        truncated = cut and not trailer and match.end() == len(value)
        if kind is None and not truncated and not self.media.owns(clean):
            # A link that names no media stays readable for the analysis; only
            # what can carry a secret (query, fragment, credentials) is dropped.
            return (_without_secrets(clean) if sensitive else url) + trailer
        marker = self._media(
            clean,
            loc,
            kind=kind or "file",
            suffix=f"#{match.start()}",
            truncated=truncated,
        )
        return marker + trailer

    # Structure ----------------------------------------------------------- #
    def _media(
        self,
        value: str,
        loc: _Loc,
        *,
        kind: str | None = None,
        mime: str | None = None,
        encoding: str = "auto",
        suffix: str = "",
        truncated: bool = False,
    ) -> str:
        return self.media.add(
            span=self.span,
            field=self.field,
            path=loc.pointer(suffix),
            value=value,
            kind=kind,
            mime=mime,
            encoding=encoding,
            truncated=truncated,
        )

    def _value(self, value: Any, loc: _Loc) -> str:
        if isinstance(value, str):
            return self._string(value, loc)
        if isinstance(value, dict):
            return self._dict(value, loc)
        if _is_message_list(value):
            return "\n".join(self._items(value, loc))
        if isinstance(value, list):
            return _pretty(self._scrub(value, loc))
        return _scalar(value)

    def _dict(self, value: dict, loc: _Loc) -> str:
        if _is_message(value):
            return "\n".join(self._message(value, loc))
        lines: list[str] = []
        used: set[str] = set()
        system = value.get("system")
        if isinstance(system, str | list) and _has_content(system):
            text = self._content_text(system, loc.child("system"))
            lines.append(f"[system] {text}")
            used.add("system")
        instructions = value.get("instructions")
        if isinstance(instructions, str) and instructions.strip():
            text = self.text(instructions, loc.child("instructions"))
            lines.append(f"[instructions] {text}")
            used.add("instructions")
        for key in ("messages", "input", "output", "contents"):
            items = value.get(key)
            if (
                key == "messages"
                and isinstance(items, list)
                and any(_is_message(i) or isinstance(i, list) for i in items)
            ):
                # LangChain batches nest a list of messages per prompt.
                lines += self._items(items, loc.child(key))
                used.add(key)
            elif _is_message_list(items):
                lines += self._items(items, loc.child(key))
                used.add(key)
        if isinstance(value.get("input"), str) and "model" in value:
            lines.append(f"[user] {self._string(value['input'], loc.child('input'))}")
            used.add("input")
        if isinstance(value.get("choices"), list):
            lines += self._choices(value["choices"], loc.child("choices"))
            used.add("choices")
        if isinstance(value.get("candidates"), list):
            lines += self._candidates(value["candidates"], loc.child("candidates"))
            used.add("candidates")
        if not used:
            return _pretty(self._scrub(value, loc))
        rest = {key: item for key, item in value.items() if key not in used}
        if rest:
            lines.append("")
            lines.append(_pretty(self._scrub(rest, loc)))
        return "\n".join(lines)

    def _choices(self, choices: list, loc: _Loc) -> list[str]:
        lines = []
        for index, choice in enumerate(choices):
            choice_loc = loc.child(index)
            if not isinstance(choice, dict):
                lines.append(self._compact(choice, choice_loc))
                continue
            for key in ("message", "delta"):
                if isinstance(choice.get(key), dict):
                    message = {"role": "assistant", **choice[key]}
                    lines += self._message(message, choice_loc.child(key))
            if isinstance(choice.get("text"), str):
                text = self.text(choice["text"], choice_loc.child("text"))
                lines.append(f"[completion] {text}")
            if choice.get("finish_reason"):
                lines.append(f"[finish_reason] {choice['finish_reason']}")
            used = ("message", "delta", "text", "finish_reason", "index")
            lines += self._meta(choice, used, choice_loc)
        return lines

    def _candidates(self, candidates: list, loc: _Loc) -> list[str]:
        lines = []
        for index, candidate in enumerate(candidates):
            candidate_loc = loc.child(index)
            if not isinstance(candidate, dict):
                lines.append(self._compact(candidate, candidate_loc))
                continue
            if isinstance(candidate.get("content"), dict):
                message = {"role": "model", **candidate["content"]}
                lines += self._message(message, candidate_loc.child("content"))
            reason = candidate.get("finishReason") or candidate.get("finish_reason")
            if reason:
                lines.append(f"[finish_reason] {reason}")
            used = ("content", "finishReason", "finish_reason", "index")
            lines += self._meta(candidate, used, candidate_loc)
        return lines

    def _items(self, items: list, loc: _Loc) -> list[str]:
        lines: list[str] = []
        for index, item in enumerate(items):
            item_loc = loc.child(index)
            if isinstance(item, list):
                lines += self._items(item, item_loc)
            elif _is_message(item):
                lines += self._message(item, item_loc)
            elif _is_response_item(item):
                lines += self._response_item(item, item_loc)
            elif isinstance(item, str):
                lines.append(self._string(item, item_loc))
            else:
                lines.append(self._compact(item, item_loc))
        return lines

    def _message(self, message: dict, loc: _Loc) -> list[str]:
        kwargs = _langchain_kwargs(message)
        if kwargs is not None:
            ids = message.get("id")
            role = kwargs.get("type") or _LANGCHAIN_CLASSES.get(
                ids[-1] if isinstance(ids, list) and ids else "", "message"
            )
            return self._message({**kwargs, "role": role}, loc.child("kwargs"))
        role = str(message.get("role") or message.get("type") or "message")
        key = next((k for k in _CONTENT_KEYS if _has_content(message.get(k))), None)
        content = message.get(key) if key else None
        content_loc = loc.child(key) if key else loc
        used = {"role", key, "tool_calls", "function_call", "tool_call_id"}
        used |= {"audio", "refusal", "function_call_name"}
        used |= {"function_call_arguments_json"}
        if message.get("type") in (role, "message", *_LANGCHAIN_TYPES):
            used.add("type")
        call_id = message.get("tool_call_id")
        pieces = self._content_pieces(content, content_loc)
        # A tool message whose parts are tool results already (OpenTelemetry
        # ``tool_call_response``) renders them as they are.
        if (role in _TOOL_ROLES or call_id) and all(k == "text" for k, _ in pieces):
            name = (
                message.get("name")
                or message.get("function_call_name")
                or (self.tool_names.get(str(call_id)) if call_id else None)
            )
            text = "\n".join(piece for _, piece in pieces)
            return [self._tool_result(name, text)] + self._meta(
                message, used | {"name"}, loc
            )
        lines: list[str] = []
        segment: list[str] = []

        def flush() -> None:
            if segment:
                lines.append(f"[{role}] " + "\n".join(segment))
                segment.clear()

        for kind, piece in pieces:
            if kind == "text":
                segment.append(piece)
            else:
                flush()
                lines.append(piece)
        if isinstance(message.get("audio"), dict):
            segment.extend(self._audio(message["audio"], loc.child("audio")))
        refusal = message.get("refusal")
        if isinstance(refusal, str) and refusal:
            segment.append("[refusal] " + self.text(refusal, loc.child("refusal")))
        flush()
        lines += self._tool_calls(message.get("tool_calls"), loc.child("tool_calls"))
        call = message.get("function_call")
        if isinstance(call, dict):
            call_loc = loc.child("function_call").child("arguments")
            lines.append(
                self._tool_call(call.get("name"), call.get("arguments"), call_loc)
            )
        if _has_content(message.get("function_call_name")):  # flattened form
            args_key = "function_call_arguments_json"
            lines.append(
                self._tool_call(
                    message["function_call_name"],
                    message.get(args_key),
                    loc.child(args_key),
                )
            )
        return (lines or [f"[{role}]"]) + self._meta(message, used, loc)

    def _meta(self, item: dict, used: Iterable[Any], loc: _Loc) -> list[str]:
        """Whatever a message-like item holds beyond what was rendered (stop
        reasons, usage, timings...), so rendering never drops it."""
        used = set(used)
        extra = {
            key: value
            for key, value in item.items()
            if key not in used and _has_content(value) and value is not False
        }
        return [f"[meta] {self._compact(extra, loc)}"] if extra else []

    def _response_item(self, item: dict, loc: _Loc) -> list[str]:
        kind = item["type"]
        if kind == "message":
            return self._message({"role": "assistant", **item}, loc)
        used = {"type", "id", "call_id"}
        if item.get("status") == "completed":
            used.add("status")
        if kind in ("function_call", "custom_tool_call"):
            key = "arguments" if "arguments" in item else "input"
            call_id = item.get("call_id") or item.get("id")
            lines = [
                self._tool_call(
                    item.get("name"), item.get(key), loc.child(key), call_id
                )
            ]
            used |= {"name", key}
        elif kind in _TOOL_RESULT_ITEMS:
            call_id = item.get("call_id")
            name = self.tool_names.get(str(call_id)) if call_id else None
            text = self._content_text(item.get("output"), loc.child("output"))
            lines = [self._tool_result(name, text)]
            used.add("output")
        elif kind == "reasoning":
            texts = [
                self._content_text(item.get(key), loc.child(key))
                for key in ("summary", "content")
                if _has_content(item.get(key))
            ]
            lines = ["[reasoning] " + "\n".join(texts) if texts else "[reasoning]"]
            used |= {"summary", "content"}
        elif kind == "mcp_call":
            lines = [
                self._tool_call(
                    item.get("name"),
                    item.get("arguments"),
                    loc.child("arguments"),
                    item.get("id"),
                )
            ]
            if _has_content(item.get("output")):
                text = self._content_text(item["output"], loc.child("output"))
                lines.append(self._tool_result(item.get("name"), text))
            used |= {"name", "arguments", "output"}
        else:
            rest = {key: value for key, value in item.items() if key != "type"}
            return [f"[{kind}] {self._compact(rest, loc)}"]
        return lines + self._meta(item, used, loc)

    def _content_text(self, content: Any, loc: _Loc) -> str:
        return "\n".join(piece for _, piece in self._content_pieces(content, loc))

    def _content_pieces(self, content: Any, loc: _Loc) -> list[tuple[str, str]]:
        """``("text", s)`` for inline text and markers, ``("line", s)`` for a
        tool call or result that must stand on its own line."""
        if content is None:
            return []
        if isinstance(content, str):
            return [("text", self._string(content, loc))]
        if isinstance(content, dict):
            return self._part(content, loc)
        if isinstance(content, list):
            pieces: list[tuple[str, str]] = []
            for index, part in enumerate(content):
                part_loc = loc.child(index)
                if isinstance(part, dict):
                    pieces += self._part(part, part_loc)
                elif isinstance(part, list):
                    pieces += self._content_pieces(part, part_loc)
                elif isinstance(part, str):
                    pieces.append(("text", self._string(part, part_loc)))
                elif part is not None:
                    pieces.append(("text", _scalar(part)))
            return pieces
        return [("text", _scalar(content))]

    def _part(self, part: dict, loc: _Loc) -> list[tuple[str, str]]:
        if isinstance(part.get("message_content"), dict):
            return self._part(part["message_content"], loc.child("message_content"))
        kind = part.get("type")
        text = part.get("text")
        if isinstance(text, str) and (kind is None or kind in _TEXT_PARTS):
            return [("text", self.text(text, loc.child("text")))]
        otel = self._otel_part(part, loc)
        if otel is not None:
            return otel
        if kind in ("image_url", "input_image", "image", "output_image"):
            return [("text", self._image(part, loc))]
        if kind in ("input_audio", "audio", "output_audio"):
            return [("text", " ".join(self._audio(part, loc)))]
        if kind in ("file", "input_file", "document"):
            return [("text", self._file(part, loc))]
        if kind in ("tool_use", "server_tool_use", "mcp_tool_use"):
            call = self._tool_call(
                part.get("name"), part.get("input"), loc.child("input"), part.get("id")
            )
            return [("line", call)]
        if kind in ("tool_result", "web_search_tool_result", "mcp_tool_result"):
            call_id = part.get("tool_use_id")
            name = part.get("name") or (
                self.tool_names.get(str(call_id)) if call_id else None
            )
            result = self._content_text(part.get("content"), loc.child("content"))
            return [("line", self._tool_result(name, result, part.get("is_error")))]
        if kind == "thinking":
            thinking = self.text(str(part.get("thinking") or ""), loc.child("thinking"))
            return [("line", f"[thinking] {thinking}".rstrip())]
        if kind == "redacted_thinking":
            return [("line", "[thinking redacted]")]
        if kind == "refusal":
            refusal = self.text(str(part.get("refusal") or ""), loc.child("refusal"))
            return [("text", f"[refusal] {refusal}")]
        for key in ("inline_data", "inlineData"):
            if isinstance(part.get(key), dict):
                blob = part[key]
                mime = blob.get("mime_type") or blob.get("mimeType")
                data = blob.get("data")
                if isinstance(data, str):
                    marker = self._media(
                        data, loc.child(key).child("data"), mime=mime, encoding="base64"
                    )
                    return [("text", marker)]
        for key in ("file_data", "fileData"):
            if isinstance(part.get(key), dict):
                blob = part[key]
                uri = blob.get("file_uri") or blob.get("fileUri")
                uri_key = "file_uri" if "file_uri" in blob else "fileUri"
                mime = blob.get("mime_type") or blob.get("mimeType")
                if isinstance(uri, str):
                    marker = self._media(uri, loc.child(key).child(uri_key), mime=mime)
                    return [("text", marker)]
        for key in ("function_call", "functionCall"):
            if isinstance(part.get(key), dict):
                call = part[key]
                args_loc = loc.child(key).child("args")
                return [
                    (
                        "line",
                        self._tool_call(call.get("name"), call.get("args"), args_loc),
                    )
                ]
        for key in ("function_response", "functionResponse"):
            if isinstance(part.get(key), dict):
                response = part[key]
                result = self._compact(
                    response.get("response"), loc.child(key).child("response")
                )
                return [("line", self._tool_result(response.get("name"), result))]
        if isinstance(text, str):
            return [("text", self.text(text, loc.child("text")))]
        return [("text", self._compact(part, loc))]

    def _otel_part(self, part: dict, loc: _Loc) -> list[tuple[str, str]] | None:
        """OpenTelemetry GenAI message parts (``gen_ai.input.messages`` as
        JSON): text and reasoning in ``content``, tool calls and responses by
        id, inline ``blob`` bytes, ``uri`` and ``file`` references with a
        ``modality``. None for any other part."""
        kind = part.get("type")
        content = part.get("content")
        media_kind = _MODALITY_KINDS.get(str(part.get("modality") or ""), "file")
        mime = part.get("mime_type")
        if kind in _TEXT_PARTS and "text" not in part and isinstance(content, str):
            return [("text", self.text(content, loc.child("content")))]
        if kind == "reasoning" and isinstance(content, str):
            reasoning = self.text(content, loc.child("content"))
            return [("line", f"[reasoning] {reasoning}".rstrip())]
        if kind == "tool_call":
            call = self._tool_call(
                part.get("name"),
                part.get("arguments"),
                loc.child("arguments"),
                part.get("id"),
            )
            return [("line", call)]
        if kind == "tool_call_response":
            call_id = part.get("id")
            name = part.get("name") or (
                self.tool_names.get(str(call_id)) if call_id else None
            )
            result = ""
            if _has_content(part.get("response")):
                result = self._compact(part["response"], loc.child("response"))
            return [("line", self._tool_result(name, result))]
        if kind == "blob" and isinstance(content, str):
            marker = self._media(
                content,
                loc.child("content"),
                kind=media_kind,
                mime=mime,
                encoding="base64",
            )
            return [("text", marker)]
        if kind == "uri" and isinstance(part.get("uri"), str):
            marker = self._media_value(part["uri"], loc.child("uri"), media_kind, mime)
            return [("text", marker or "")]
        if kind == "file" and "modality" in part and _has_content(part.get("file_id")):
            marker = self._media(
                str(part["file_id"]),
                loc.child("file_id"),
                kind=media_kind,
                mime=mime,
                encoding="reference",
            )
            return [("text", marker)]
        return None

    def _media_value(
        self, value: Any, loc: _Loc, kind: str, mime: str | None = None
    ) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        cut = _is_cut(value)
        stripped = value.strip()
        if (
            len(stripped) > 64
            and not stripped.lower().startswith("data:")
            and _BASE64_VALUE.fullmatch(stripped)
        ):
            return self._media(
                stripped,
                loc,
                kind=kind,
                mime=mime,
                encoding="base64",
                truncated=cut,
            )
        return self._media(stripped, loc, kind=kind, mime=mime, truncated=cut)

    def _source(self, source: dict, loc: _Loc, kind: str) -> str | None:
        """Anthropic ``source`` blocks: base64, url, file or text."""
        source_type = source.get("type")
        mime = source.get("media_type")
        if source_type == "base64" and isinstance(source.get("data"), str):
            return self._media(
                source["data"],
                loc.child("data"),
                kind=kind,
                mime=mime,
                encoding="base64",
            )
        if source_type == "url":
            return self._media_value(source.get("url"), loc.child("url"), kind, mime)
        if source_type == "file" and source.get("file_id"):
            return self._media(
                str(source["file_id"]),
                loc.child("file_id"),
                kind=kind,
                mime=mime,
                encoding="reference",
            )
        if source_type == "text" and isinstance(source.get("data"), str):
            return self.text(source["data"], loc.child("data"))
        if source_type == "content":
            return self._content_text(source.get("content"), loc.child("content"))
        return None

    def _image(self, part: dict, loc: _Loc) -> str:
        image_url = part.get("image_url")
        if isinstance(image_url, dict):
            marker = self._media_value(
                image_url.get("url"), loc.child("image_url").child("url"), "image"
            )
        else:
            marker = self._media_value(image_url, loc.child("image_url"), "image")
        if marker is None and isinstance(part.get("source"), dict):
            marker = self._source(part["source"], loc.child("source"), "image")
        image = part.get("image")
        if marker is None and isinstance(image, dict):
            inner, inner_loc = image, loc.child("image")
            if isinstance(image.get("image"), dict):
                inner, inner_loc = image["image"], inner_loc.child("image")
            marker = self._media_value(
                inner.get("url"), inner_loc.child("url"), "image"
            )
        if marker is None and isinstance(image, str):
            marker = self._media_value(image, loc.child("image"), "image")
        if marker is None and part.get("file_id"):
            marker = self._media(
                str(part["file_id"]),
                loc.child("file_id"),
                kind="image",
                encoding="reference",
            )
        if marker is None:
            marker = self._media_value(part.get("url"), loc.child("url"), "image")
        return marker if marker is not None else self._compact(part, loc)

    def _audio(self, part: dict, loc: _Loc) -> list[str]:
        pieces = []
        for key in ("input_audio", "audio"):
            if isinstance(part.get(key), dict):
                part, loc = part[key], loc.child(key)
                break
        fmt = part.get("format")
        mime = f"audio/{fmt}" if isinstance(fmt, str) and fmt else None
        if isinstance(part.get("data"), str) and part["data"]:
            pieces.append(
                self._media(
                    part["data"],
                    loc.child("data"),
                    kind="audio",
                    mime=mime,
                    encoding="base64",
                )
            )
        for key in ("url", "audio_url"):
            marker = self._media_value(part.get(key), loc.child(key), "audio", mime)
            if marker:
                pieces.append(marker)
        transcript = part.get("transcript")
        if isinstance(transcript, str) and transcript:
            text = self.text(transcript, loc.child("transcript"))
            pieces.append(f"[transcript] {text}")
        return pieces or [self._compact(part, loc)]

    def _file(self, part: dict, loc: _Loc) -> str:
        if isinstance(part.get("file"), dict):
            part, loc = part["file"], loc.child("file")
        marker = None
        if isinstance(part.get("source"), dict):
            marker = self._source(part["source"], loc.child("source"), "file")
        for key in ("file_data", "file_url", "url"):
            if marker is None:
                marker = self._media_value(part.get(key), loc.child(key), "file")
        if marker is None and part.get("file_id"):
            marker = self._media(
                str(part["file_id"]),
                loc.child("file_id"),
                kind="file",
                encoding="reference",
            )
        if marker is None:
            return self._compact(part, loc)
        name = part.get("filename") or part.get("title")
        return (
            f"{marker} {self.text(str(name), loc.child('filename'))}"
            if name
            else marker
        )

    def _tool_calls(self, calls: Any, loc: _Loc) -> list[str]:
        if not isinstance(calls, list):
            return []
        lines = []
        for index, call in enumerate(calls):
            call_loc = loc.child(index)
            if not isinstance(call, dict):
                continue
            if isinstance(call.get("tool_call"), dict):  # OpenInference
                call, call_loc = call["tool_call"], call_loc.child("tool_call")
            function = call.get("function")
            if isinstance(function, dict):
                name, args = function.get("name"), function.get("arguments")
                args_loc = call_loc.child("function").child("arguments")
            else:
                key = "arguments" if "arguments" in call else "args"
                name, args, args_loc = (
                    call.get("name"),
                    call.get(key),
                    call_loc.child(key),
                )
            lines.append(self._tool_call(name, args, args_loc, call.get("id")))
        return lines

    def _tool_call(self, name: Any, args: Any, loc: _Loc, call_id: Any = None) -> str:
        name = str(name or "unknown")
        if call_id:
            self.tool_names[str(call_id)] = name
        if not _has_content(args):
            return f"[tool_call {name}]"
        return f"[tool_call {name}] {self._compact(args, loc)}"

    def _tool_result(self, name: Any, text: str, error: Any = False) -> str:
        label = (
            "tool_result" + (f" {name}" if name else "") + (" error" if error else "")
        )
        return f"[{label}] {text}" if text else f"[{label}]"

    def _flat_messages(self, group: str, flat: Mapping[str, Any]) -> str:
        tree = _unflatten(flat)
        entries = (
            enumerate(tree)
            if isinstance(tree, list)
            else sorted(tree.items(), key=lambda kv: _natural_key(kv[0]))
        )
        loc = _Loc(dotted=True).child(group)
        lines = []
        for index, entry in entries:
            entry_loc = loc.child(index)
            if isinstance(entry, dict) and isinstance(entry.get("message"), dict):
                entry, entry_loc = entry["message"], entry_loc.child("message")
            if isinstance(entry, dict):
                lines += self._message(entry, entry_loc)
            else:
                text = entry if isinstance(entry, str) else _scalar(entry)
                lines.append(self._string(text, entry_loc))
        return f"{group}:\n" + "\n".join(lines)

    def _attribute(self, key: str, value: Any, base: _Loc) -> str:
        loc = base.child(key)
        if isinstance(value, bool | int | float) or value is None:
            return f"{key}: {_scalar(value)}"
        if isinstance(value, str):
            stripped = value.strip()
            parsed = _UNPARSED
            if stripped[:1] in ("[", "{") and not isinstance(value, _RawText):
                parsed = _parse_json(stripped)
            if not isinstance(parsed, dict | list):
                return f"{key}: {self._string(value, loc, key, _media_hint(key))}"
            value = parsed
        if key == "gen_ai.system_instructions" and _is_part_list(value):
            return f"{key}:\n[system] {self._content_text(value, loc)}"
        if _is_message_list(value):
            return f"{key}:\n" + "\n".join(self._items(value, loc))
        if isinstance(value, dict) and _is_message(value):
            return f"{key}:\n" + "\n".join(self._message(value, loc))
        return f"{key}: {_pretty(self._scrub(value, loc, key))}"

    def _compact(self, value: Any, loc: _Loc) -> str:
        if isinstance(value, str):
            stripped = value.strip()
            parsed = _UNPARSED
            if stripped[:1] in ("[", "{") and not isinstance(value, _RawText):
                parsed = _parse_json(stripped)
            if parsed is _UNPARSED:
                return self._string(value, loc)
            value = parsed
        if isinstance(value, dict | list):
            return json.dumps(
                self._scrub(value, loc), sort_keys=True, ensure_ascii=False
            )
        return _scalar(value)

    def _scrub(
        self, value: Any, loc: _Loc, key: str | None = None, hint: str | None = None
    ) -> Any:
        """A JSON value with media replaced by markers and binary by sizes."""
        if isinstance(value, dict):
            inner = _media_hint(key) or hint
            return {
                str(k): self._scrub(v, loc.child(k), str(k), inner)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [
                self._scrub(v, loc.child(i), key, hint) for i, v in enumerate(value)
            ]
        if isinstance(value, str):
            return self._string(value, loc, key, hint)
        if isinstance(value, float) and not math.isfinite(value):
            return repr(value)
        return value

    def _string(
        self, value: str, loc: _Loc, key: str | None = None, hint: str | None = None
    ) -> str:
        cut = _is_cut(value)
        stripped = value.strip()
        if stripped.lower().startswith("data:") and _DATA_URI.fullmatch(stripped):
            return self._media(stripped, loc, kind=hint, truncated=cut)
        kind = _media_key_kind(key) or (hint if _url_like_key(key) else None)
        if kind and _looks_like_url(stripped):
            return self._media(stripped, loc, kind=kind, truncated=cut)
        if (
            len(stripped) > 256
            and _BASE64_VALUE.fullmatch(stripped)
            and _looks_encoded(stripped)
        ):
            if kind or (hint and _key_name(key) in _PAYLOAD_KEYS):
                return self._media(
                    stripped,
                    loc,
                    kind=kind or hint,
                    encoding="base64",
                    truncated=cut,
                )
            return f"[binary {_base64_size(stripped)} bytes]"
        return self.text(value, loc)


# --------------------------------------------------------------------------- #
# Span graph
# --------------------------------------------------------------------------- #
@dataclass
class _Trace:
    trace_id: str
    start: datetime
    session_id: str | None = None
    end: datetime | None = None
    span_count: int = 0
    error_count: int = 0


@dataclass(eq=False)
class _Node:
    trace_id: str
    span_id: str
    start: datetime
    parent_id: str
    kind: str
    status: str
    version: int
    raw_chars: int  # what reading every field costs (``_read_costs``)
    status_chars: int = 0  # what reading only the status message costs
    sizes: dict[str, int] = field(default_factory=dict)  # source sizes
    # The parts of ``raw_chars``: what any read of the span costs, and each
    # heavy column's source size (a read cuts each at its cap).
    fixed_chars: int = 0
    columns: tuple[int, ...] = ()
    parent: _Node | None = None
    children: list[_Node] = field(default_factory=list)
    order: int = -1
    depth: int = 0
    orphan: bool = False
    cycle: bool = False

    @property
    def key(self) -> tuple[str, str]:
        return self.trace_id, self.span_id

    @property
    def is_error(self) -> bool:
        return self.status.upper() == "ERROR"

    def cost_at(self, cap: int) -> int:
        """What reading every field costs with each column cut at ``cap``."""
        return self.fixed_chars + sum(min(size, cap) for size in self.columns)


_KIND_FLAGS = {
    "llm": "llm",
    "tool": "tool",
    "retriever": "retrieval",
    "reranker": "retrieval",
    "agent": "agent",
    "guardrail": "guardrail",
    "evaluator": "evaluator",
    "embedding": "embedding",
}


def _order_spans(nodes: list[_Node], traces: list[_Trace]) -> list[_Node]:
    """Depth-first order: traces by start, roots and children by (start,
    span id). A span whose parent is missing is an orphan root; spans only
    reachable through a parent cycle are walked last, from a cut point."""
    by_key = {node.key: node for node in nodes}
    by_trace: dict[str, list[_Node]] = defaultdict(list)
    for node in nodes:
        by_trace[node.trace_id].append(node)
        parent = by_key.get((node.trace_id, node.parent_id)) if node.parent_id else None
        if parent is None or parent is node:
            node.orphan = bool(node.parent_id)
        else:
            node.parent = parent
            parent.children.append(node)
    for node in nodes:
        node.children.sort(key=lambda n: (_naive_utc(n.start), n.span_id))
    ordered: list[_Node] = []
    visited: set[int] = set()

    def walk(root: _Node) -> None:
        stack = [(root, 0)]
        while stack:
            node, depth = stack.pop()
            if id(node) in visited:
                continue
            visited.add(id(node))
            node.depth, node.order = depth, len(ordered)
            ordered.append(node)
            stack += [(c, depth + 1) for c in reversed(node.children)]

    for trace in traces:
        members = sorted(
            by_trace.get(trace.trace_id, ()),
            key=lambda n: (_naive_utc(n.start), n.span_id),
        )
        for node in members:
            if node.parent is None:
                walk(node)
        for node in members:
            if id(node) not in visited:
                node.cycle, node.parent = True, None
                walk(node)
    return ordered


def _priority(nodes: list[_Node], traces: list[_Trace]) -> list[_Node]:
    """What a cap keeps first: every trace's roots, then failing spans, then
    model/tool work, then the rest; newest trace first within each tier so a
    session keeps its final exchange."""
    newest = {trace.trace_id: rank for rank, trace in enumerate(reversed(traces))}

    def tier(node: _Node) -> int:
        if node.parent is None:
            return 0
        if node.is_error:
            return 1
        return 2 if node.kind in _KIND_FLAGS else 3

    return sorted(nodes, key=lambda n: (tier(n), newest[n.trace_id], n.order))


def _select_spans(
    priority: list[_Node],
    max_spans: int,
    check: Callable[[], None] | None = None,
) -> set[int]:
    """Up to ``max_spans`` spans, each with all its ancestors, so the kept
    graph stays connected.

    Linear in the spans plus a log factor, whatever the depth. The selected
    set is closed under ancestors, so a span's missing chain is everything
    below its deepest selected ancestor: it fits in ``room`` spans exactly
    when the ancestor ``room`` levels up is selected, or the span is no
    deeper than that. ``_order_spans`` cut cycles and numbered the spans
    depth first, so that ancestor is the last span at its depth before this
    one in that order. Only a chain that fits is walked, and each span joins
    at most once."""
    if len(priority) <= max_spans:
        return {id(node) for node in priority}
    by_order = sorted(priority, key=lambda n: n.order)
    position = {id(node): index for index, node in enumerate(by_order)}
    at_depth: dict[int, list[int]] = defaultdict(list)
    for index, node in enumerate(by_order):
        at_depth[node.depth].append(index)

    def ancestor_at(node: _Node, depth: int) -> _Node:
        positions = at_depth[depth]
        before = bisect.bisect_left(positions, position[id(node)]) - 1
        return by_order[positions[before]]

    selected: set[int] = set()
    for index, node in enumerate(priority):
        if check is not None and index % 1024 == 0:
            check()
        room = max_spans - len(selected)
        if room <= 0:
            break
        if id(node) in selected:
            continue
        top = node.depth - room
        if top >= 0 and id(ancestor_at(node, top)) not in selected:
            continue  # its chain is longer than the room left, now and later
        current: _Node | None = node
        while current is not None and id(current) not in selected:
            selected.add(id(current))
            current = current.parent
    return selected


# --------------------------------------------------------------------------- #
# ClickHouse reads
# --------------------------------------------------------------------------- #
class EvidenceQueryBuilder:
    """Statements for one unit, all pinned to the run's project.

    With a ``version_cutoff`` (a ``_version``: ingest time in ns) every
    statement reads the unit as it stood then: each span's latest version
    written by the cutoff, unless a later tombstone deleted it since. Spans
    written after it are not there, so every later read of the unit (another
    pod, a rebuild) agrees with the first."""

    TABLE = "spans"
    UNIT_COLUMNS = {"session": "trace_session_id", "user": "end_user_id"}

    def __init__(self, *, project_id: str, version_cutoff: int | None = None):
        self.project_id = project_id
        self.version_cutoff = version_cutoff

    def _as_of(self, params: dict[str, Any], keyword: str = "WHERE") -> str:
        if self.version_cutoff is None:
            return ""
        params["evidence_version_cutoff"] = int(self.version_cutoff)
        return (
            f"\n                {keyword} (_version <= %(evidence_version_cutoff)s"
            " OR is_deleted = 1)"
        )

    def roster_query(
        self,
        *,
        kind: str,
        lookup_ids: Sequence[str],
        since: datetime | None,
        until: datetime | None,
        limit: int,
    ) -> tuple[str, dict[str, Any]]:
        """Root spans of the traces whose latest root names one of the ids.

        A predicate on the raw id before versions merge could keep a stale
        version, so candidates are found among all versions and membership is
        decided by each root's latest id (as ``span_reader`` does)."""
        column = self.UNIT_COLUMNS[kind]
        params: dict[str, Any] = {
            "evidence_project_id": self.project_id,
            "evidence_lookup_ids": tuple(lookup_ids),
        }
        bounds = ""
        for name, op, value in (("since", ">=", since), ("until", "<=", until)):
            if value is not None:
                params[f"evidence_{name}"] = _naive_utc(value)
                bounds += f"\n              AND start_time {op} %(evidence_{name})s"
        bounds += self._as_of(params)
        return (
            f"""
            /* insights_evidence:roster */
            SELECT
                trace_id,
                start_time,
                toString(latest_trace_session_id) AS trace_session_id,
                toString(latest_end_user_id) AS end_user_id,
                toString(latest_org_id) AS org_id
            FROM (
                SELECT
                    trace_id,
                    id,
                    start_time,
                    argMax(tuple(trace_session_id), _version).1
                        AS latest_trace_session_id,
                    argMax(tuple(end_user_id), _version).1 AS latest_end_user_id,
                    argMax(tuple(org_id), _version).1 AS latest_org_id,
                    argMax(is_deleted, _version) AS latest_is_deleted
                FROM {self.TABLE}
                PREWHERE project_id = %(evidence_project_id)s
                  AND (parent_span_id IS NULL OR parent_span_id = '')
                  AND trace_id IN (
                      SELECT trace_id
                      FROM {self.TABLE}
                      PREWHERE project_id = %(evidence_project_id)s
                        AND {column} IN %(evidence_lookup_ids)s
                  ){bounds}
                GROUP BY project_id, trace_id, id, start_time
            ) AS latest_roots
            WHERE latest_is_deleted = 0
              AND toString(latest_{column}) IN %(evidence_lookup_ids)s
            ORDER BY start_time DESC, trace_id DESC
            LIMIT {int(limit)}
            """,
            params,
        )

    def trace_stats_query(self, trace_ids: Sequence[str]) -> tuple[str, dict[str, Any]]:
        params = {
            "evidence_project_id": self.project_id,
            "evidence_trace_ids": tuple(trace_ids),
        }
        as_of = self._as_of(params)
        return (
            f"""
            /* insights_evidence:trace_stats */
            SELECT
                trace_id,
                count() AS span_count,
                countIf(upper(latest_status) = 'ERROR') AS error_count,
                min(start_time) AS trace_start,
                max(ifNull(latest_end_time, start_time)) AS trace_end
            FROM (
                SELECT
                    trace_id,
                    id,
                    start_time,
                    argMax(status, _version) AS latest_status,
                    argMax(tuple(end_time), _version).1 AS latest_end_time,
                    argMax(is_deleted, _version) AS latest_is_deleted
                FROM {self.TABLE}
                PREWHERE project_id = %(evidence_project_id)s
                  AND trace_id IN %(evidence_trace_ids)s{as_of}
                GROUP BY project_id, trace_id, id, start_time
            ) AS latest_trace_spans
            WHERE latest_is_deleted = 0
            GROUP BY trace_id
            ORDER BY trace_id ASC
            LIMIT {len(trace_ids) + 1}
            """,
            params,
        )

    def identity_query(
        self, trace_ids: Sequence[str], *, limit: int
    ) -> tuple[str, dict[str, Any]]:
        """Light physical identities of live spans plus what ordering,
        selection and read planning need: the size of every column a content
        read returns, so the read budget charges all of it. Live spans only,
        so the limit counts what the trace stats counted; the caller re-checks
        the flag."""
        input_value, output_value = (
            f"lengthUTF8(multiIf(position(attributes_extra, '\"{key}\"') = 0, '', "
            f"{_extra_value_sql('attributes_extra', key)}))"
            for key in ("input.value", "output.value")
        )
        key_bytes = " + ".join(
            f"arraySum(arrayMap(k -> length(k) + {overhead}, mapKeys({column})))"
            for column, overhead in (
                ("attrs_string", 16),
                ("attrs_number", 24),
                ("attrs_bool", 16),
            )
        )
        params = {
            "evidence_project_id": self.project_id,
            "evidence_trace_ids": tuple(trace_ids),
        }
        as_of = self._as_of(params)
        return (
            f"""
            /* insights_evidence:identity */
            SELECT
                trace_id,
                id AS span_id,
                start_time,
                argMax(tuple(parent_span_id), _version).1 AS parent_span_id,
                argMax(observation_type, _version) AS observation_type,
                argMax(status, _version) AS status,
                argMax(input_length, _version) AS input_chars,
                argMax(output_length, _version) AS output_chars,
                argMax(length(attributes_extra), _version) AS extra_bytes,
                argMax({input_value}, _version) AS input_value_chars,
                argMax({output_value}, _version) AS output_value_chars,
                argMax(length(span_events), _version) AS events_bytes,
                argMax(arraySum(arrayMap(v -> length(v), mapValues(attrs_string))),
                    _version) AS attrs_value_bytes,
                argMax({key_bytes}, _version) AS attrs_key_bytes,
                argMax(length(toJSONString(metadata)), _version) AS metadata_bytes,
                argMax(length(status_message), _version) AS status_bytes,
                argMax(is_deleted, _version) AS latest_is_deleted,
                max(_version) AS latest_version
            FROM {self.TABLE}
            PREWHERE project_id = %(evidence_project_id)s
              AND trace_id IN %(evidence_trace_ids)s{as_of}
            GROUP BY project_id, trace_id, id, start_time
            HAVING latest_is_deleted = 0
            ORDER BY trace_id ASC, start_time ASC, span_id ASC
            LIMIT {int(limit)}
            """,
            params,
        )

    def content_query(
        self,
        identities: Sequence[PhysicalSpanIdentity],
        *,
        heavy_chars: int,
        status_chars: int = _STATUS_MESSAGE_CHARS,
    ) -> tuple[str, dict[str, Any]]:
        """Latest content of exact identities.

        With ``heavy_chars`` > 0 every field is read, cut at that many chars
        in ClickHouse with its full length returned, so a cut is recorded
        rather than hidden. String attributes share one such allowance per
        span: the smaller values are kept whole first and the largest are cut
        (``attrs_string_cut`` maps each cut key to its full length).
        ``input.value`` and ``output.value`` leave ``attributes_extra``: the
        collector copies them into the input/output columns, so they come
        back as ``input_value``/``output_value`` only when they differ from
        the column (empty otherwise).

        With ``heavy_chars`` = 0 only the span's scalars and its status
        message (cut at ``status_chars``) are read; the identity read
        already measured the rest."""
        if not identities or len(identities) > 200:
            raise ValueError("evidence content batch is empty or unbounded")
        heavy = int(heavy_chars) > 0
        params = {
            "evidence_project_id": self.project_id,
            "evidence_trace_ids": tuple(dict.fromkeys(i.trace_id for i in identities)),
            "evidence_span_ids": tuple(dict.fromkeys(i.span_id for i in identities)),
            "evidence_span_dates": tuple(
                dict.fromkeys(i.start_time.date() for i in identities)
            ),
            "evidence_span_identities": tuple(
                (i.trace_id, i.span_id, i.start_us) for i in identities
            ),
            "evidence_heavy_chars": int(heavy_chars),
            "evidence_status_chars": int(status_chars),
            "evidence_content_limit": len(identities) + 1,
        }
        as_of = self._as_of(params, keyword="  AND")
        inner = derived = outer = ""
        if heavy:
            inner = _CONTENT_HEAVY_INNER
            derived = _CONTENT_HEAVY_DERIVED
            outer = _CONTENT_HEAVY_OUTER
        return (
            f"""
            /* insights_evidence:content */
            SELECT
                grouped_trace_id AS trace_id,
                grouped_id AS id,
                grouped_start_time AS start_time,
                latest_parent_span_id AS parent_span_id,
                latest_name AS name,
                latest_observation_type AS observation_type,
                latest_end_time AS end_time,
                latest_model AS model,
                latest_provider AS provider,
                latest_latency_ms AS latency_ms,
                latest_prompt_tokens AS prompt_tokens,
                latest_completion_tokens AS completion_tokens,
                latest_total_tokens AS total_tokens,
                latest_cost AS cost,
                latest_status AS status,
                substringUTF8(latest_status_message, 1, %(evidence_status_chars)s)
                    AS status_message,
                lengthUTF8(latest_status_message) AS status_message_chars,{outer}
                toString(latest_trace_session_id) AS trace_session_id,
                toString(latest_end_user_id) AS end_user_id,
                toString(latest_org_id) AS org_id,
                ifNull(latest_input_gcs_url, '') != '' AS input_offloaded,
                ifNull(latest_output_gcs_url, '') != '' AS output_offloaded
            FROM (
                SELECT *{derived}
                FROM (
                    SELECT
                        trace_id AS grouped_trace_id,
                        id AS grouped_id,
                        start_time AS grouped_start_time,
                        argMax(tuple(parent_span_id), _version).1
                            AS latest_parent_span_id,
                        argMax(name, _version) AS latest_name,
                        argMax(observation_type, _version) AS latest_observation_type,
                        argMax(tuple(end_time), _version).1 AS latest_end_time,
                        argMax(model, _version) AS latest_model,
                        argMax(provider, _version) AS latest_provider,
                        argMax(latency_ms, _version) AS latest_latency_ms,
                        argMax(prompt_tokens, _version) AS latest_prompt_tokens,
                        argMax(completion_tokens, _version)
                            AS latest_completion_tokens,
                        argMax(total_tokens, _version) AS latest_total_tokens,
                        argMax(cost, _version) AS latest_cost,
                        argMax(status, _version) AS latest_status,
                        argMax(status_message, _version)
                            AS latest_status_message,{inner}
                        argMax(tuple(trace_session_id), _version).1
                            AS latest_trace_session_id,
                        argMax(tuple(end_user_id), _version).1 AS latest_end_user_id,
                        argMax(tuple(org_id), _version).1 AS latest_org_id,
                        argMax(tuple(input_gcs_url), _version).1
                            AS latest_input_gcs_url,
                        argMax(tuple(output_gcs_url), _version).1
                            AS latest_output_gcs_url,
                        argMax(is_deleted, _version) AS latest_is_deleted
                    FROM {self.TABLE}
                    PREWHERE project_id = %(evidence_project_id)s
                      AND trace_id IN %(evidence_trace_ids)s
                      AND id IN %(evidence_span_ids)s
                    WHERE toDate(start_time) IN %(evidence_span_dates)s
                      AND (trace_id, id, toUnixTimestamp64Micro(start_time))
                            IN %(evidence_span_identities)s{as_of}
                    GROUP BY project_id, trace_id, id, start_time
                ) AS latest_evidence_spans
                WHERE latest_is_deleted = 0
            ) AS evidence_spans
            ORDER BY grouped_trace_id ASC, grouped_start_time ASC, grouped_id ASC
            LIMIT %(evidence_content_limit)s
            """,
            params,
        )


def _extra_value_sql(column: str, key: str) -> str:
    """One key of an ``attributes_extra`` JSON object as text: a string value
    unescaped, any other value as compact JSON, absent or null as ''."""
    return (
        f"multiIf(JSONType({column}, '{key}') = 'String', "
        f"JSONExtractString({column}, '{key}'), "
        f"JSONType({column}, '{key}') = 'Null', '', "
        f"JSONExtractRaw({column}, '{key}'))"
    )


_CONTENT_HEAVY_INNER = """
                        argMax(input, _version) AS latest_input,
                        argMax(output, _version) AS latest_output,
                        argMax(span_events, _version) AS latest_span_events,
                        argMax(attributes_extra, _version) AS latest_attributes_extra,
                        argMax(metadata, _version) AS latest_metadata,
                        argMax(attrs_string, _version) AS latest_attrs_string,
                        argMax(attrs_number, _version) AS latest_attrs_number,
                        argMax(attrs_bool, _version) AS latest_attrs_bool,"""

_CONTENT_HEAVY_DERIVED = f""",
                    {_extra_value_sql("latest_attributes_extra", "input.value")}
                        AS input_value_full,
                    {_extra_value_sql("latest_attributes_extra", "output.value")}
                        AS output_value_full,
                    if(
                        (position(latest_attributes_extra, '"input.value"') > 0
                         OR position(latest_attributes_extra, '"output.value"') > 0)
                        AND isValidJSON(latest_attributes_extra)
                        AND JSONType(latest_attributes_extra) = 'Object',
                        concat('{{', arrayStringConcat(arrayMap(
                            kv -> concat(toJSONString(kv.1), ':', kv.2),
                            arrayFilter(
                                kv -> kv.1 NOT IN ('input.value', 'output.value'),
                                JSONExtractKeysAndValuesRaw(latest_attributes_extra))),
                            ','), '}}'),
                        latest_attributes_extra
                    ) AS extra_rest,
                    toJSONString(latest_metadata) AS metadata_full,
                    arraySort((k, v) -> (lengthUTF8(v), k),
                        mapKeys(latest_attrs_string), mapValues(latest_attrs_string))
                        AS attr_keys,
                    arraySort((v, k) -> (lengthUTF8(v), k),
                        mapValues(latest_attrs_string), mapKeys(latest_attrs_string))
                        AS attr_values,
                    arrayMap(v -> lengthUTF8(v), attr_values) AS attr_sizes,
                    arrayMap((total, own) -> total - own, arrayCumSum(attr_sizes),
                        attr_sizes) AS attr_before"""

_CONTENT_HEAVY_OUTER = """
                substringUTF8(latest_input, 1, %(evidence_heavy_chars)s) AS input,
                lengthUTF8(latest_input) AS input_chars,
                substringUTF8(latest_output, 1, %(evidence_heavy_chars)s) AS output,
                lengthUTF8(latest_output) AS output_chars,
                if(input_value_full = latest_input, '',
                    substringUTF8(input_value_full, 1, %(evidence_heavy_chars)s))
                    AS input_value,
                if(input_value_full = latest_input, 0, lengthUTF8(input_value_full))
                    AS input_value_chars,
                if(output_value_full = latest_output, '',
                    substringUTF8(output_value_full, 1, %(evidence_heavy_chars)s))
                    AS output_value,
                if(output_value_full = latest_output, 0,
                    lengthUTF8(output_value_full)) AS output_value_chars,
                substringUTF8(latest_span_events, 1, %(evidence_heavy_chars)s)
                    AS span_events,
                lengthUTF8(latest_span_events) AS span_events_chars,
                substringUTF8(extra_rest, 1, %(evidence_heavy_chars)s)
                    AS attributes_extra,
                lengthUTF8(extra_rest) AS attributes_extra_chars,
                substringUTF8(metadata_full, 1, %(evidence_heavy_chars)s)
                    AS metadata_json,
                lengthUTF8(metadata_full) AS metadata_chars,
                mapFromArrays(attr_keys, arrayMap(
                    (v, before) -> substringUTF8(v, 1, toUInt64(greatest(
                        toInt64(%(evidence_heavy_chars)s) - toInt64(before), 0))),
                    attr_values, attr_before)) AS attrs_string,
                mapFromArrays(
                    arrayFilter((k, size, before) ->
                        size + before > %(evidence_heavy_chars)s,
                        attr_keys, attr_sizes, attr_before),
                    arrayFilter((size, before) ->
                        size + before > %(evidence_heavy_chars)s,
                        attr_sizes, attr_before)) AS attrs_string_cut,
                arraySum(attr_sizes) AS attrs_string_chars,
                latest_attrs_number AS attrs_number,
                latest_attrs_bool AS attrs_bool,"""


class _Reader:
    """Executes statements inside one wall-clock budget, failing closed.

    Each statement asks ClickHouse to stop at ``query_timeout_ms`` or at what
    is left of the unit's deadline, whichever comes first: application reads
    carry no server deadline unless the caller passes one. CPU work between
    and after reads calls ``check`` so it is held to the same deadline."""

    def __init__(self, analytics: _Analytics, limits: EvidenceLimits):
        self._analytics = analytics
        self._limits = limits
        self.started = monotonic()
        self._deadline = self.started + limits.deadline_ms / 1000
        self.query_count = 0

    def remaining_ms(self) -> int:
        return int((self._deadline - monotonic()) * 1000)

    def check(self) -> None:
        if self.remaining_ms() < _DEADLINE_FLOOR_MS:
            raise EvidenceUnavailable("deadline_exceeded")

    def rows(
        self, statement: tuple[str, dict[str, Any]], *, max_rows: int
    ) -> list[dict[str, Any]]:
        query, params = statement
        remaining_ms = self.remaining_ms()
        if remaining_ms < _DEADLINE_FLOOR_MS:
            raise EvidenceUnavailable("deadline_exceeded")
        cap_ms = min(self._limits.query_timeout_ms, remaining_ms)
        try:
            result = self._analytics.execute_ch_query(
                query,
                params,
                timeout_ms=cap_ms,
                settings={**_READ_SETTINGS, "max_result_rows": max_rows},
                server_execution_cap_ms=cap_ms,
            )
        except Exception as exc:
            if isinstance(exc, ReadDeadlineExceeded):
                raise EvidenceUnavailable("deadline_exceeded") from None
            if is_read_budget_error(exc):
                raise EvidenceUnavailable("read_budget_exceeded") from None
            if is_clickhouse_query_error(exc):
                raise EvidenceUnavailable("clickhouse_query_failed") from None
            raise
        self.query_count += 1
        rows = list(result.data or [])
        if len(rows) > max_rows:
            raise EvidenceUnavailable("result_limit_exceeded")
        return rows


def version_of(moment: datetime) -> int:
    """The ``_version`` an ingest at ``moment`` writes: UTC nanoseconds (a
    naive time is UTC)."""
    aware = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
    since = aware - datetime(1970, 1, 1, tzinfo=UTC)
    return (since.days * 86_400 + since.seconds) * 10**9 + since.microseconds * 1_000


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def _iso(value: datetime | None) -> str | None:
    if not isinstance(value, datetime):
        return None
    return _naive_utc(value).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _uuid_text(value: Any) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return None


def _chunks(items: Sequence, size: int) -> Iterable[Sequence]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def normalize_unit_kind(kind: str) -> str:
    kind = _KIND_ALIASES.get(str(kind), str(kind))
    if kind not in UNIT_KINDS:
        raise EvidenceUnavailable("invalid_unit_kind")
    return kind


def safe_unit_key(project_id: str, kind: str, key: str) -> str:
    """A file-safe, non-identifying name for a unit's corpus files."""
    kind = normalize_unit_kind(kind)
    digest = hashlib.sha256(f"{project_id}:{kind}:{key}".encode()).hexdigest()
    return f"{kind}-{digest[:24]}"


def resolve_lookup_ids(kind: str, key: str) -> list[str]:
    """The raw ids whose spans belong to a session or user, through its remap
    table, so a consolidated session reads as one (``span_reader``)."""
    kind = normalize_unit_kind(kind)
    if kind == "trace":
        return [str(key)]
    from tracer.services.clickhouse.v2 import get_reader

    table = "trace_session_id_remap" if kind == "session" else "end_user_id_remap"
    with get_reader() as reader:
        _askers, aliases = reader.resolve_ids(table, [str(key)])
    return sorted({str(key), *aliases})


# --------------------------------------------------------------------------- #
# Building a snapshot
# --------------------------------------------------------------------------- #
@dataclass
class _Plan:
    traces: list[_Trace]
    traces_total: int
    reasons: set[str] = field(default_factory=set)
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Draft:
    field: str
    text: str
    original: int | None = None
    reasons: list[str] = field(default_factory=list)


def build_unit_snapshot(
    *,
    analytics: _Analytics,
    project_id: str,
    organization_id: str,
    kind: str,
    key: str,
    lookup_ids: Sequence[str] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limits: EvidenceLimits | None = None,
    own_storage: Callable[[str], bool] | None = None,
    now: datetime | None = None,
    as_of: datetime | None = None,
) -> EvidenceSnapshot:
    """Read one unit inside the run's project and render its snapshot.

    ``lookup_ids`` are the raw session/user ids to search (``resolve_lookup_ids``);
    the key alone by default. ``since``/``until`` bound a user's traces.
    ``as_of`` reads the unit as it was ingested by then (later tombstones
    still delete), so every build of it agrees; the latest data without."""
    limits = limits or EvidenceLimits()
    kind = normalize_unit_kind(kind)
    project = _uuid_text(project_id)
    organization = _uuid_text(organization_id)
    if project is None or organization is None:
        raise EvidenceUnavailable("invalid_scope")
    key = str(key or "").strip()
    if not key or len(key) > _MAX_KEY_CHARS:
        raise EvidenceUnavailable("invalid_unit_key")
    reader = _Reader(analytics, limits)
    builder = EvidenceQueryBuilder(
        project_id=project,
        version_cutoff=None if as_of is None else version_of(as_of),
    )

    plan = _plan_unit(reader, builder, kind, key, lookup_ids, since, until, limits)
    _check_organization(plan.notes.pop("org_ids", ()), organization)
    nodes = _read_identities(reader, builder, plan, limits)
    reader.check()
    ordered = _order_spans(nodes, plan.traces)
    priority = _priority(ordered, plan.traces)
    selected = _select_spans(priority, limits.max_spans, reader.check)
    if len(selected) < len(ordered):
        plan.reasons.add("span_cap")
    kept = [node for node in priority if id(node) in selected]
    full, status_only, partial = _plan_reads(kept, limits)
    if len(full) < len(kept):
        plan.reasons.add("unit_read_cap")
    rows = _read_content(reader, builder, kept, full, status_only, partial, limits)
    _check_organization((row.get("org_id") for row in rows.values()), organization)

    document, sources = _assemble(
        kind=kind,
        key=key,
        project=project,
        plan=plan,
        ordered=ordered,
        priority=kept,
        selected=selected,
        full=full | set(partial),
        partial=set(partial),
        rows=rows,
        limits=limits,
        own_storage=own_storage or default_own_storage_predicate(),
        built_at=now or datetime.now(UTC),
        check=reader.check,
    )
    reader.check()
    return EvidenceSnapshot(
        document=document,
        media_sources=sources,
        query_count=reader.query_count,
        elapsed_ms=(monotonic() - reader.started) * 1000,
    )


def _plan_unit(
    reader: _Reader,
    builder: EvidenceQueryBuilder,
    kind: str,
    key: str,
    lookup_ids: Sequence[str] | None,
    since: datetime | None,
    until: datetime | None,
    limits: EvidenceLimits,
) -> _Plan:
    if kind == "trace":
        plan = _Plan(traces=[_Trace(trace_id=key, start=datetime.min)], traces_total=1)
    else:
        plan = _plan_roster(
            reader, builder, kind, key, lookup_ids, since, until, limits
        )
    stats: dict[str, dict[str, Any]] = {}
    for batch in _chunks([t.trace_id for t in plan.traces], _ID_BATCH):
        for row in reader.rows(
            builder.trace_stats_query(batch), max_rows=len(batch) + 1
        ):
            stats[str(row["trace_id"])] = row
    if kind == "trace" and not stats:
        raise EvidenceNotFound
    if len(stats) != len(plan.traces):
        # The roster proved a live root for every trace a moment ago.
        raise EvidenceUnavailable("inconsistent_trace_stats")
    for trace in plan.traces:
        row = stats[trace.trace_id]
        trace.span_count = int(row.get("span_count") or 0)
        trace.error_count = int(row.get("error_count") or 0)
        trace.start = row.get("trace_start") or trace.start
        trace.end = row.get("trace_end")
    plan.traces.sort(key=lambda t: (_naive_utc(t.start), t.trace_id))
    return plan


def _plan_roster(
    reader: _Reader,
    builder: EvidenceQueryBuilder,
    kind: str,
    key: str,
    lookup_ids: Sequence[str] | None,
    since: datetime | None,
    until: datetime | None,
    limits: EvidenceLimits,
) -> _Plan:
    ids = sorted({_uuid_text(v) for v in (key, *(lookup_ids or ())) if _uuid_text(v)})
    if _uuid_text(key) is None:
        raise EvidenceNotFound  # session and user ids are UUID columns
    cap = limits.max_roster_traces
    rows = reader.rows(
        builder.roster_query(
            kind=kind,
            lookup_ids=ids,
            since=since if kind == "user" else None,
            until=until if kind == "user" else None,
            limit=cap + 1,
        ),
        max_rows=cap + 1,
    )
    reasons = set()
    if len(rows) > cap:
        rows = rows[:cap]  # newest first: the oldest traces are the ones lost
        reasons.add("roster_cap")
    traces: dict[str, _Trace] = {}
    for row in rows:
        trace_id, start = str(row["trace_id"]), row["start_time"]
        trace = traces.get(trace_id)
        if trace is None or _naive_utc(start) < _naive_utc(trace.start):
            traces[trace_id] = _Trace(
                trace_id=trace_id,
                start=start,
                session_id=_uuid_text(row.get("trace_session_id")),
            )
    if not traces:
        raise EvidenceNotFound
    ordered = sorted(traces.values(), key=lambda t: (_naive_utc(t.start), t.trace_id))
    notes: dict[str, Any] = {
        "org_ids": [row.get("org_id") for row in rows],
        "roster_truncated": "roster_cap" in reasons,
    }
    if kind == "session":
        chosen = ordered
        if len(ordered) > limits.max_traces:
            head, tail = ordered[:2], ordered[-(limits.max_traces - 2) :]
            chosen = head + tail
            notes["traces_omitted"] = {
                "count": len(ordered) - len(chosen),
                "reason": "trace_cap",
                "after_trace": 2,
            }
            reasons.add("trace_cap")
        return _Plan(chosen, len(ordered), reasons, notes)

    sessions: dict[str, list[_Trace]] = defaultdict(list)
    for trace in ordered:
        sessions[trace.session_id or f"trace:{trace.trace_id}"].append(trace)
    by_start = sorted(
        sessions.values(), key=lambda s: (_naive_utc(s[0].start), s[0].trace_id)
    )
    recent = by_start[-limits.user_sessions :]
    older = by_start[: len(by_start) - len(recent)]
    chosen = sorted(
        (t for s in recent for t in s), key=lambda t: (_naive_utc(t.start), t.trace_id)
    )
    notes.update(
        sessions_total=len(by_start),
        sessions_included=len(recent),
        older_sessions_traces=sum(len(s) for s in older),
    )
    if older:
        reasons.add("session_cap")
    if len(chosen) > limits.max_traces:
        notes["traces_omitted"] = {
            "count": len(chosen) - limits.max_traces,
            "reason": "trace_cap",
            "after_trace": 0,
        }
        chosen = chosen[-limits.max_traces :]
        reasons.add("trace_cap")
    return _Plan(chosen, len(ordered), reasons, notes)


def _check_organization(org_ids: Iterable[Any], organization: str) -> None:
    for value in org_ids:
        text = _uuid_text(value) if value not in (None, "") else None
        if text is not None and text != organization:
            raise EvidenceUnavailable("organization_scope_mismatch")


def _read_identities(
    reader: _Reader, builder: EvidenceQueryBuilder, plan: _Plan, limits: EvidenceLimits
) -> list[_Node]:
    """Identities of the planned traces, newest first until the identity cap;
    traces past it are recorded as omitted."""
    budget = limits.max_identity_spans
    loaded: list[_Trace] = []
    for trace in reversed(plan.traces):
        if trace.span_count > budget:
            if not loaded:
                raise EvidenceUnavailable("span_identity_limit_exceeded")
            break
        budget -= trace.span_count
        loaded.append(trace)
    skipped = len(plan.traces) - len(loaded)
    if skipped:
        plan.reasons.add("identity_cap")
        previous = plan.notes.get("traces_omitted", {}).get("count", 0)
        plan.notes["traces_omitted"] = {
            "count": previous + skipped,
            "reason": "identity_cap",
            "after_trace": 0,
        }
    plan.traces = sorted(loaded, key=lambda t: (_naive_utc(t.start), t.trace_id))

    remaining = limits.max_identity_spans
    nodes: list[_Node] = []
    seen: set[tuple[str, str]] = set()
    for batch in _chunks([t.trace_id for t in plan.traces], _ID_BATCH):
        rows = reader.rows(
            builder.identity_query(batch, limit=remaining + 1), max_rows=remaining + 1
        )
        if len(rows) > remaining:
            raise EvidenceUnavailable("span_identity_limit_exceeded")
        remaining -= len(rows)
        for row in rows:
            if row.get("latest_is_deleted") or not isinstance(
                row.get("start_time"), datetime
            ):
                continue
            fixed, columns, status_chars, sizes = _cost_parts(row)
            node = _Node(
                trace_id=str(row["trace_id"]),
                span_id=str(row["span_id"]),
                start=row["start_time"],
                parent_id=str(row.get("parent_span_id") or ""),
                kind=str(row.get("observation_type") or ""),
                status=str(row.get("status") or ""),
                version=int(row.get("latest_version") or 0),
                raw_chars=0,
                status_chars=status_chars,
                sizes=sizes,
                fixed_chars=fixed,
                columns=columns,
            )
            node.raw_chars = node.cost_at(limits.max_raw_field_chars)
            if node.key in seen:
                # Parent links use a bare span id: either live copy could take
                # the other's children, so refuse to guess.
                raise EvidenceUnavailable("ambiguous_span_identity")
            seen.add(node.key)
            nodes.append(node)
    if not nodes:
        raise EvidenceNotFound
    return nodes


def _read_costs(
    row: Mapping[str, Any], limits: EvidenceLimits
) -> tuple[int, int, dict[str, int]]:
    """What reading a span costs, from its identity row: every column the
    content read returns, each capped as that read caps it; its status
    message alone; and each field's source size, for a span left unread."""
    fixed, columns, status, sizes = _cost_parts(row)
    cost = fixed + sum(min(size, limits.max_raw_field_chars) for size in columns)
    return cost, status, sizes


def _cost_parts(
    row: Mapping[str, Any],
) -> tuple[int, tuple[int, ...], int, dict[str, int]]:
    """The parts of a span's read cost: what any read of it costs (scalars,
    attribute keys, the status message), the source size of each column a
    read cuts at its cap, the status message alone, and each field's source
    size.

    The collector copies ``input.value``/``output.value`` out of
    ``attributes_extra`` into the input/output columns, and the content read
    returns such a copy once. A copy is recognised here by its length (the
    content read compares the text) and charged once; a value that differs
    from its column, or stands in for an empty one, is charged too."""

    def size(name: str) -> int:
        return max(0, int(row.get(name) or 0))

    extra = size("extra_bytes")
    sizes: dict[str, int] = {}
    columns: list[int] = []
    for name in ("input", "output"):
        column, value = size(f"{name}_chars"), size(f"{name}_value_chars")
        columns.append(column)
        if value:
            extra -= value + len(f'"{name}.value":"",')
            if value != column:
                columns.append(value)
        sizes[name] = column or value
    extra = max(0, extra)
    attrs = size("attrs_value_bytes")
    status = min(size("status_bytes"), _STATUS_MESSAGE_CHARS)
    columns += [extra, attrs, size("events_bytes"), size("metadata_bytes")]
    fixed = _ROW_READ_CHARS + size("attrs_key_bytes") + status
    sizes["attributes"] = _json_size(extra) + attrs + size("attrs_key_bytes")
    sizes["events"] = _json_size(size("events_bytes"))
    sizes["metadata"] = _json_size(size("metadata_bytes"))
    return fixed, tuple(columns), status, sizes


def _plan_reads(
    priority: list[_Node], limits: EvidenceLimits
) -> tuple[set[int], set[int], dict[int, int]]:
    """In priority order, inside the unit's raw read budget: the spans read
    in full, then for a span that does not fit, its status message alone if
    that fits. The rest are read for their scalar columns only.

    A failing span that does not fit whole is read in part instead, each
    column cut at the largest cap the budget left allows (``partial`` maps it
    to that cap), so the spans after it in the order, its siblings among
    them, cannot take the budget its fields need."""
    budget = limits.max_raw_chars
    full: set[int] = set()
    status_only: set[int] = set()
    partial: dict[int, int] = {}
    for node in priority:
        if node.raw_chars <= budget:
            full.add(id(node))
            budget -= node.raw_chars
            continue
        cap = _partial_cap(node, budget) if node.is_error else 0
        if cap:
            partial[id(node)] = cap
            budget -= node.cost_at(cap)
        elif node.status_chars and _STATUS_READ_CHARS + node.status_chars <= budget:
            status_only.add(id(node))
            budget -= _STATUS_READ_CHARS + node.status_chars
    return full, status_only, partial


def _partial_cap(node: _Node, budget: int) -> int:
    """The largest per-column cap whose read of ``node`` fits ``budget``, or
    0 when less than ``_MIN_PARTIAL_CHARS`` a column would fit."""
    if node.cost_at(_MIN_PARTIAL_CHARS) > budget:
        return 0
    low, high = _MIN_PARTIAL_CHARS, max(node.columns, default=0)
    while low < high:
        middle = (low + high + 1) // 2
        if node.cost_at(middle) <= budget:
            low = middle
        else:
            high = middle - 1
    return low


def _read_content(
    reader: _Reader,
    builder: EvidenceQueryBuilder,
    kept: list[_Node],
    full: set[int],
    status_only: set[int],
    partial: Mapping[int, int],
    limits: EvidenceLimits,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Content rows in batches bounded by span count and by the chars each
    tier's read charges: full reads, status-only reads, scalar-only reads,
    and a span read in part alone at its own cap."""
    tiers: list[tuple[list[_Node], int, int, Callable[[_Node], int]]] = [
        ([], limits.max_raw_field_chars, _STATUS_MESSAGE_CHARS, lambda n: n.raw_chars),
        ([], 0, _STATUS_MESSAGE_CHARS, lambda n: _STATUS_READ_CHARS + n.status_chars),
        ([], 0, 0, lambda n: 0),
    ]
    for node in sorted(kept, key=lambda n: n.order):
        if id(node) in partial:
            continue
        tier = 0 if id(node) in full else 1 if id(node) in status_only else 2
        tiers[tier][0].append(node)
    batches: list[tuple[list[_Node], int, int]] = []
    for members, heavy, status, cost_of in tiers:
        batch: list[_Node] = []
        chars = 0
        for node in members:
            cost = cost_of(node)
            if batch and (
                len(batch) >= limits.content_batch_spans
                or chars + cost > limits.content_batch_chars
            ):
                batches.append((batch, heavy, status))
                batch, chars = [], 0
            batch.append(node)
            chars += cost
        if batch:
            batches.append((batch, heavy, status))
    for node in kept:
        if id(node) in partial:
            batches.append(([node], partial[id(node)], _STATUS_MESSAGE_CHARS))

    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for batch, heavy, status in batches:
        identities = [
            PhysicalSpanIdentity(
                project_id=builder.project_id,
                trace_id=node.trace_id,
                span_id=node.span_id,
                start_time=node.start,
            )
            for node in batch
        ]
        result = reader.rows(
            builder.content_query(identities, heavy_chars=heavy, status_chars=status),
            max_rows=len(batch) + 1,
        )
        found = {(str(row["trace_id"]), str(row["id"])): row for row in result}
        if len(result) != len(batch) or set(found) != {node.key for node in batch}:
            raise EvidenceUnavailable("incomplete_content_replay")
        rows.update(found)
    return rows


@dataclass
class _Source:
    """One field's raw value as read, and its length at the source."""

    value: Any
    chars: int
    truncated: bool = False
    unread: bool = False


_RENDERERS: dict[str, Callable[[_FieldRenderer, Any], str]] = {
    "input": _FieldRenderer.payload,
    "output": _FieldRenderer.payload,
    "attributes": _FieldRenderer.attributes,
    "events": _FieldRenderer.events,
    "status_message": lambda renderer, value: renderer.text(value, _Loc()),
    "metadata": _FieldRenderer.json_document,
}


def _text_source(row: Mapping[str, Any], column: str, chars_column: str) -> _Source:
    raw = str(row.get(column) or "")
    chars = int(row.get(chars_column) or len(raw))
    return _Source(raw, chars, chars > len(raw))


def _span_sources(
    row: Mapping[str, Any], node: _Node, *, read_heavy: bool
) -> dict[str, _Source]:
    """Each field's raw value from a content row. A span outside the unit's
    read budget has its heavy fields as unread sizes (from its identity
    read), and its status message too when even that did not fit."""
    status = _text_source(row, "status_message", "status_message_chars")
    if status.chars and not status.value:
        status = _Source(status.chars, status.chars, unread=True)
    if not read_heavy:
        sources = {
            name: _Source(size, size, unread=True)
            for name, size in node.sizes.items()
            if size
        }
        return {**sources, "status_message": status}

    cut = {str(k): int(v) for k, v in (row.get("attrs_string_cut") or {}).items()}
    attributes: dict[str, Any] = {}
    for key, value in (row.get("attrs_string") or {}).items():
        key = str(key)
        cut_short = key in cut and isinstance(value, str)
        attributes[key] = _raw_text(value, cut[key]) if cut_short else value
    attributes.update((str(k), v) for k, v in (row.get("attrs_number") or {}).items())
    attributes.update(
        (str(k), bool(v)) for k, v in (row.get("attrs_bool") or {}).items()
    )
    extra = _text_source(row, "attributes_extra", "attributes_extra_chars")
    parsed = _parse_json(extra.value) if extra.value.strip() else {}
    if isinstance(parsed, dict):
        for key, value in parsed.items():
            name = str(key) if str(key) not in attributes else f"{key} (extra)"
            attributes[name] = value
    elif parsed is _UNPARSED:
        # Cut short or not JSON: kept whole under its column name, as raw text.
        original = extra.chars if extra.truncated else None
        attributes["attributes_extra"] = _raw_text(extra.value, original)
    elif _has_content(parsed):
        attributes["attributes_extra"] = parsed

    sources: dict[str, _Source] = {}
    attributes_chars = int(row.get("attrs_string_chars") or 0) + extra.chars
    attributes_cut = extra.truncated or bool(cut)
    for name in ("input", "output"):
        column = _text_source(row, name, f"{name}_chars")
        # ``input.value`` from attributes_extra, unless it repeated the column.
        copy = _text_source(row, f"{name}_value", f"{name}_value_chars")
        if copy.value and not column.value:
            column = copy  # stands in for the empty column, cut flag and all
        elif copy.value and not _same_payload(column, copy):
            key = f"{name}.value"
            key = key if key not in attributes else f"{key} (extra)"
            original = copy.chars if copy.truncated else None
            attributes[key] = (
                _raw_text(copy.value, original) if original else copy.value
            )
            attributes_chars += copy.chars
            attributes_cut = attributes_cut or copy.truncated
        sources[name] = column

    sources["attributes"] = _Source(attributes, attributes_chars, attributes_cut)
    sources["events"] = _text_source(row, "span_events", "span_events_chars")
    sources["status_message"] = status
    sources["metadata"] = _text_source(row, "metadata_json", "metadata_chars")
    return sources


def _same_payload(column: _Source, copy: _Source) -> bool:
    """Whether a kept ``input.value`` says what its column says. The content
    read already dropped exact copies; this catches the same JSON written
    another way (spacing, escapes). A cut value proves nothing, so it is
    kept."""
    if column.truncated or copy.truncated:
        return False
    left, right = column.value.strip(), copy.value.strip()
    if left == right:
        return True
    if left[:1] in ("[", "{", '"') and right[:1] in ("[", "{", '"'):
        parsed = _parse_json(left)
        return parsed is not _UNPARSED and parsed == _parse_json(right)
    return False


def _render_span(
    row: Mapping[str, Any],
    node: _Node,
    alias: str,
    media: _MediaRegistry,
    limits: EvidenceLimits,
    *,
    read_heavy: bool,
    cut_reason: str = "source_cap",
) -> list[_Draft]:
    """The span's fields in field order (media aliases follow it), each cut
    to ``max_field_chars`` with what was cut recorded. A field cut by its
    read is recorded under ``cut_reason``: ``unit_read_cap`` for a span read
    in part to fit the unit's budget."""
    sources = _span_sources(row, node, read_heavy=read_heavy)
    tool_names: dict[str, str] = {}
    drafts: list[_Draft] = []
    for name in FIELD_ORDER:
        source = sources.get(name)
        if source is None or not _has_content(source.value):
            continue
        if source.unread:
            text = f"[not read: {source.chars} source chars over the unit read cap]"
            drafts.append(_Draft(name, text, source.chars, ["unit_read_cap"]))
            continue
        if isinstance(source.value, str) and not source.value.strip():
            continue
        # Attributes carry their cuts per value (``_RawText``).
        renderer = _FieldRenderer(
            media,
            alias,
            name,
            tool_names,
            source_truncated=source.truncated and name != "attributes",
        )
        text = _clean(_RENDERERS[name](renderer, source.value))
        if not text:
            continue
        draft = _Draft(name, text)
        if source.truncated:
            draft.original, draft.reasons = source.chars, [cut_reason]
        if len(text) > limits.max_field_chars:
            if draft.original is None:
                draft.original = _utf16_len(text)
            draft.text = _keep_ends(text, limits.max_field_chars, "field cap")
            draft.reasons.append("field_cap")
        drafts.append(draft)
    return drafts


def _json_size(value: Any) -> int:
    """Source chars of a JSON column, 0 for an empty ``[]``/``{}``."""
    size = int(value or 0)
    return size if size > 2 else 0


def _keep_ends(text: str, keep: int, reason: str) -> str:
    """The first and last halves of ``keep`` chars, joined by a marker that
    says how much was cut and why."""
    keep = max(0, min(keep, len(text)))
    head = keep // 2
    tail = keep - head
    omitted = len(text) - keep
    marker = f"\n[... {omitted} chars omitted: {reason} ...]\n"
    return text[:head] + marker + (text[len(text) - tail :] if tail else "")


def _fit_bytes(text: str, budget: int, reason: str) -> str:
    size = len(text.encode("utf-8"))
    keep = max(0, budget * len(text) // max(size, 1) - 96)
    while keep > 0:
        candidate = _keep_ends(text, keep, reason)
        if len(candidate.encode("utf-8")) <= budget:
            return candidate
        keep = keep * 9 // 10
    return _keep_ends(text, 0, reason)


def _assemble(
    *,
    kind: str,
    key: str,
    project: str,
    plan: _Plan,
    ordered: list[_Node],
    priority: list[_Node],
    selected: set[int],
    full: set[int],
    rows: Mapping[tuple[str, str], Mapping[str, Any]],
    limits: EvidenceLimits,
    own_storage: Callable[[str], bool],
    built_at: datetime,
    partial: set[int] = frozenset(),
    check: Callable[[], None] = lambda: None,
) -> tuple[dict[str, Any], dict[str, MediaSource]]:
    kept = [node for node in ordered if id(node) in selected]
    span_alias = {id(node): f"s{index}" for index, node in enumerate(kept, start=1)}
    trace_alias = {
        trace.trace_id: f"t{index}" for index, trace in enumerate(plan.traces, start=1)
    }

    media = _MediaRegistry(own_storage)
    drafts: dict[int, list[_Draft]] = {}
    for node in kept:  # depth-first, so media aliases follow span order
        check()
        drafts[id(node)] = _render_span(
            rows[node.key],
            node,
            span_alias[id(node)],
            media,
            limits,
            read_heavy=id(node) in full,
            cut_reason="unit_read_cap" if id(node) in partial else "source_cap",
        )

    # The snapshot byte cap: spend on what a reader needs first. The reserve
    # covers the manifest; each truncation record is charged as it is made.
    capped = sum(1 for items in drafts.values() for d in items if d.reasons)
    reserve = (
        64 * 1024
        + 1_536 * len(kept)
        + 512 * len(media.entries)
        + _TRUNCATION_RECORD_BYTES * capped
    )
    budget = max(0, limits.max_snapshot_bytes - reserve)
    for node in priority:
        check()
        for draft in drafts[id(node)]:
            size = len(draft.text.encode("utf-8"))
            if size <= budget:
                budget -= size
                continue
            if draft.original is None:
                draft.original = _utf16_len(draft.text)
            if not draft.reasons:
                budget = max(0, budget - _TRUNCATION_RECORD_BYTES)
            draft.reasons.append("snapshot_cap")
            if budget >= _MIN_KEPT_BYTES:
                draft.text = _fit_bytes(draft.text, budget, "snapshot cap")
            else:
                draft.text = (
                    f"[omitted: {draft.original} chars over the snapshot byte cap]"
                )
            budget = max(0, budget - len(draft.text.encode("utf-8")))

    media_by_span: dict[str, list[str]] = defaultdict(list)
    for entry in media.entries:
        media_by_span[entry["span"]].append(entry["alias"])
    omitted = [node for node in ordered if id(node) not in selected]
    omitted_children = Counter(
        id(node.parent) for node in omitted if node.parent is not None
    )
    fields_doc: dict[str, dict[str, str]] = {}
    truncations: list[dict[str, Any]] = []
    spans: list[dict[str, Any]] = []
    included_by_trace: Counter[str] = Counter()
    for node in kept:
        alias = span_alias[id(node)]
        row = rows[node.key]
        included_by_trace[node.trace_id] += 1
        span_fields = {}
        for draft in drafts[id(node)]:
            fields_doc.setdefault(alias, {})[draft.field] = draft.text
            span_fields[draft.field] = {
                "chars": _utf16_len(draft.text),
                "sha256": _sha256(draft.text),
            }
            if draft.reasons:
                truncations.append(
                    {
                        "span": alias,
                        "field": draft.field,
                        "original_chars": draft.original,
                        "kept_chars": _utf16_len(draft.text),
                        "reason": "+".join(draft.reasons),
                    }
                )
        span_media = media_by_span[alias]
        flags = []
        if node.parent is None:
            flags.append("root")
        if node.is_error:
            flags.append("error")
        if node.kind in _KIND_FLAGS:
            flags.append(_KIND_FLAGS[node.kind])
        if node.orphan:
            flags.append("orphan")
        if node.cycle:
            flags.append("cycle")
        if omitted_children[id(node)]:
            flags.append("children_omitted")
        if id(node) not in full:
            flags.append("not_read")
        if any(d.reasons for d in drafts[id(node)]):
            flags.append("truncated")
        if span_media:
            flags.append("media")
        status = next(
            (d for d in drafts[id(node)] if d.field == "status_message"), None
        )
        status_len = 0
        if status is not None:
            unread = "unit_read_cap" in status.reasons
            status_len = status.original if unread else _utf16_len(status.text)
        spans.append(
            {
                "alias": alias,
                "span_id": node.span_id,
                "trace": trace_alias[node.trace_id],
                "parent": span_alias[id(node.parent)] if node.parent else None,
                "depth": node.depth,
                "name": _clean(str(row.get("name") or "")),
                "kind": node.kind or None,
                "status": node.status or None,
                "status_message_len": status_len,
                "model": _clean(str(row.get("model") or "")) or None,
                "provider": _clean(str(row.get("provider") or "")) or None,
                "start": _iso(node.start),
                "end": _iso(row.get("end_time")),
                "latency_ms": int(row.get("latency_ms") or 0),
                "tokens": {
                    "in": int(row.get("prompt_tokens") or 0),
                    "out": int(row.get("completion_tokens") or 0),
                    "total": int(row.get("total_tokens") or 0),
                },
                "cost": _finite(row.get("cost")),
                "fields": span_fields,
                "media": span_media,
                "flags": flags,
            }
        )

    roots: dict[str, str] = {}
    sessions: dict[str, str] = {}
    # A true root first; an orphan or cycle cut only when the trace has none.
    for node in sorted(kept, key=lambda n: (n.orphan or n.cycle, n.order)):
        if node.parent is None:
            roots.setdefault(node.trace_id, span_alias[id(node)])
        session = _uuid_text(rows[node.key].get("trace_session_id"))
        if session:
            sessions.setdefault(node.trace_id, session)
    traces = [
        {
            "trace_id": trace.trace_id,
            "alias": trace_alias[trace.trace_id],
            "session": trace.session_id or sessions.get(trace.trace_id),
            "start": _iso(trace.start),
            "end": _iso(trace.end),
            "root_span": roots.get(trace.trace_id),
            "span_count": trace.span_count,
            "error_count": trace.error_count,
            "spans_included": included_by_trace[trace.trace_id],
        }
        for trace in plan.traces
    ]

    reasons = set(plan.reasons)
    reasons.update(r for t in truncations for r in t["reason"].split("+"))
    ordered_reasons = [r for r in _REASON_ORDER if r in reasons]
    limits_doc: dict[str, Any] = {
        "spans_total": len(ordered),
        "spans_included": len(kept),
        "traces_total": plan.traces_total,
        "traces_included": len(plan.traces),
        "fields_truncated": truncations,
        "fields_offloaded": [
            {"span": span_alias[id(node)], "field": name}
            for node in kept
            for name in ("input", "output")
            if rows[node.key].get(f"{name}_offloaded")
        ],
        "spans_omitted": {
            "count": len(omitted),
            "by_kind": dict(
                sorted(Counter(n.kind or "unknown" for n in omitted).items())
            ),
            "by_trace": dict(
                sorted(Counter(trace_alias[n.trace_id] for n in omitted).items())
            ),
        },
        "traces_omitted": plan.notes.get("traces_omitted"),
        "truncated": bool(ordered_reasons),
        "reason": ordered_reasons[0] if ordered_reasons else None,
        "reasons": ordered_reasons,
        "caps": limits.manifest(),
    }
    if kind != "trace":
        limits_doc["roster_truncated"] = bool(plan.notes.get("roster_truncated"))
    if kind == "user":
        for name in ("sessions_total", "sessions_included", "older_sessions_traces"):
            limits_doc[name] = plan.notes.get(name, 0)

    manifest = {
        "schema": SCHEMA,
        "unit": {"kind": kind, "key": key, "project_id": project},
        "built_at": _iso(built_at),
        "source_version": str(max((node.version for node in kept), default=0)),
        "traces": traces,
        "spans": spans,
        "media": media.entries,
        "limits": limits_doc,
    }
    manifest["digest"] = snapshot_digest(manifest, fields_doc)
    return {"manifest": manifest, "fields": fields_doc}, media.sources


_REASON_ORDER = (
    "roster_cap",
    "session_cap",
    "trace_cap",
    "identity_cap",
    "span_cap",
    "unit_read_cap",
    "source_cap",
    "field_cap",
    "snapshot_cap",
)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# --------------------------------------------------------------------------- #
# Media bytes (exporter; the broker uses the same policy)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MediaFetchPolicy:
    """``own_storage_project`` holds platform-storage reads to trace media of
    that project (``own_storage_in_scope``); None reads any object key."""

    max_item_bytes: int = 25 * 1024 * 1024
    max_unit_bytes: int = 100 * 1024 * 1024
    fetch_external: bool = False
    timeout_s: float = 20.0
    own_storage_project: str | None = None


# Object keys the platform writes for trace media: the collector's uploads of
# inline images, audio and video (``tempcust/<uuid>``), evaluation audio, and
# voice-call recordings. Any other key of the bucket is another feature's file
# (a knowledge base, a dataset, an export), never a trace's media.
_TRACE_MEDIA_PREFIXES = (
    "tempcust/",
    "tempaudio/",
    "call-recordings/",
    "alk-sim/recordings/",
)
# A rehosted call recording names its project:
# call-recordings/<project>/<provider>/<call>/<kind>.<ext>.
_REHOSTED_RECORDING_PARTS = 5


def own_storage_in_scope(object_key: str, project_id: str) -> bool:
    """Whether a platform-storage object may be read for ``project_id``'s
    trace: a trace-media key, and a rehosted recording of that project."""
    key = str(object_key or "")
    if not key.startswith(_TRACE_MEDIA_PREFIXES) or ".." in key or "%" in key:
        return False
    parts = key.split("/")
    if parts[0] == "call-recordings" and len(parts) == _REHOSTED_RECORDING_PARTS:
        return parts[1] == str(project_id)
    return True


@dataclass(frozen=True)
class FetchedMedia:
    alias: str
    content: bytes
    mime: str | None
    extension: str


class MediaRefused(Exception):
    """A media fetch the policy refused; ``reason`` is its typed code."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def fetch_snapshot_media(
    snapshot: EvidenceSnapshot,
    policy: MediaFetchPolicy | None = None,
    *,
    own_storage_reader: Callable[[str], bytes] | None = None,
    external_getter: Callable[..., Any] | None = None,
    audio_duration: Callable[[bytes], float] | None = None,
) -> tuple[EvidenceSnapshot, list[FetchedMedia]]:
    """Fetch the bytes of each accessible media entry under the D6 policy.

    Data URIs decode in process; own-storage objects are read with the
    storage client; external URLs only when the policy allows it, through the
    SSRF guard, without leaving the original host. Every refusal is recorded
    on the entry as a typed reason; the digest is recomputed."""
    policy = policy or MediaFetchPolicy()
    manifest = copy.deepcopy(snapshot.manifest)
    fetched: list[FetchedMedia] = []
    spent = 0
    for entry in manifest["media"]:
        source = snapshot.media_sources.get(entry["alias"])
        if not entry["accessible"] or source is None:
            continue
        try:
            content, mime = _fetch_media(
                entry,
                source,
                policy,
                remaining=policy.max_unit_bytes - spent,
                own_storage_reader=own_storage_reader,
                external_getter=external_getter,
            )
        except MediaRefused as refused:
            entry.update(accessible=False, reason=refused.reason)
            continue
        spent += len(content)
        mime = entry["mime"] or mime or _sniff_mime(content)
        entry.update(bytes=len(content), mime=mime, accessible=True, reason=None)
        if entry["kind"] == "audio":
            entry["duration_s"] = _duration(content, audio_duration)
        extension = _MIME_EXTENSION.get(mime or "", "bin")
        fetched.append(FetchedMedia(entry["alias"], content, mime, extension))
    manifest["digest"] = snapshot_digest(manifest, snapshot.fields)
    document = {"manifest": manifest, "fields": snapshot.fields}
    return replace(snapshot, document=document), fetched


def fetch_media(
    entry: Mapping[str, Any],
    source: MediaSource,
    policy: MediaFetchPolicy | None = None,
    *,
    own_storage_reader: Callable[[str], bytes] | None = None,
    external_getter: Callable[..., Any] | None = None,
) -> tuple[bytes, str | None]:
    """One media entry's bytes and mime under the D6 policy, as the evidence
    broker serves a single view. Raises ``MediaRefused`` with a typed reason."""
    policy = policy or MediaFetchPolicy()
    content, mime = _fetch_media(
        entry,
        source,
        policy,
        remaining=policy.max_item_bytes,
        own_storage_reader=own_storage_reader,
        external_getter=external_getter,
    )
    return content, entry.get("mime") or mime or _sniff_mime(content)


def _fetch_media(
    entry: Mapping[str, Any],
    source: MediaSource,
    policy: MediaFetchPolicy,
    *,
    remaining: int,
    own_storage_reader: Callable[[str], bytes] | None,
    external_getter: Callable[..., Any] | None,
) -> tuple[bytes, str | None]:
    if remaining <= 0:
        raise MediaRefused("unit_media_budget")

    def check_size(size: int) -> None:
        if size > policy.max_item_bytes:
            raise MediaRefused("too_large")
        if size > remaining:
            raise MediaRefused("unit_media_budget")

    if source.source == "data_uri":
        check_size(_base64_size(source.value))
        try:
            return base64.b64decode(source.value, validate=True), source.mime
        except (binascii.Error, ValueError):
            raise MediaRefused("invalid_base64") from None
    if source.source == "own_storage":
        project = policy.own_storage_project
        if project is not None and not own_storage_in_scope(
            _own_storage_key(source.value), project
        ):
            raise MediaRefused("own_storage_out_of_scope")
        reader = own_storage_reader or _default_own_storage_reader(
            min(policy.max_item_bytes, remaining)
        )
        try:
            content = reader(source.value)
        except MediaRefused as refused:
            if refused.reason == "too_large" and remaining < policy.max_item_bytes:
                raise MediaRefused("unit_media_budget") from None
            raise
        except Exception:
            raise MediaRefused("own_storage_read_failed") from None
        check_size(len(content))
        return content, source.mime
    if source.source == "external":
        if not policy.fetch_external:
            raise MediaRefused("external_fetch_disabled")
        return _fetch_external(entry, source, policy, remaining, external_getter)
    raise MediaRefused(entry.get("reason") or "unsupported_source")


def _fetch_external(
    entry: Mapping[str, Any],
    source: MediaSource,
    policy: MediaFetchPolicy,
    remaining: int,
    external_getter: Callable[..., Any] | None,
) -> tuple[bytes, str | None]:
    from tfc.utils.ssrf_guard import SsrfBlocked

    getter = external_getter or _default_external_getter()
    cap = min(policy.max_item_bytes, remaining)
    try:
        response = getter(source.value, max_bytes=cap, timeout=policy.timeout_s)
    except SsrfBlocked:
        raise MediaRefused("blocked_by_ssrf_guard") from None
    except Exception as exc:
        reason = "too_large" if "byte limit" in str(exc) else "fetch_failed"
        raise MediaRefused(reason) from None
    if response.status_code != 200:
        raise MediaRefused(f"http_{response.status_code}")
    final_host = urlparse(getattr(response, "final_url", None) or source.value).hostname
    if final_host != urlparse(source.value).hostname:
        raise MediaRefused("redirected_off_host")
    content_type = str(response.headers.get("Content-Type") or "")
    mime = content_type.split(";", 1)[0].strip().lower() or None
    allowed = {
        "image": ("image/",),
        "audio": ("audio/", "video/", "application/octet-stream"),
    }.get(entry["kind"])
    if mime and allowed and not mime.startswith(allowed):
        raise MediaRefused("unexpected_content_type")
    content = bytes(response.content or b"")
    if len(content) > cap:
        raise MediaRefused("too_large")
    return content, mime


def _own_storage_key(url: str) -> str:
    from tfc.settings.settings import UPLOAD_BUCKET_NAME
    from tfc.utils.storage_client import extract_object_key

    try:
        return extract_object_key(url, UPLOAD_BUCKET_NAME)
    except (IndexError, ValueError):
        return ""


def _default_own_storage_reader(max_bytes: int) -> Callable[[str], bytes]:
    """Reads a platform-storage object with the storage client, as
    ``tfc.utils.storage._read_own_storage_object`` does, but stops at
    ``max_bytes``: a larger object is ``too_large`` before it is all held."""
    from tfc.settings.settings import UPLOAD_BUCKET_NAME
    from tfc.utils.storage import is_own_storage_url
    from tfc.utils.storage_client import get_storage_client

    def read(url: str) -> bytes:
        key = _own_storage_key(url)
        if not (is_own_storage_url(url, UPLOAD_BUCKET_NAME) and key):
            raise MediaRefused("own_storage_read_failed")
        response = get_storage_client().get_object(UPLOAD_BUCKET_NAME, key)
        chunks, size = [], 0
        try:
            while chunk := response.read(64 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise MediaRefused("too_large")
                chunks.append(chunk)
        finally:
            response.close()
            release = getattr(response, "release_conn", None)
            if callable(release):
                release()
        return b"".join(chunks)

    return read


def _default_external_getter() -> Callable[..., Any]:
    from tfc.utils.storage import _ssrf_safe_get

    return lambda url, *, max_bytes, timeout: _ssrf_safe_get(
        url, timeout=timeout, max_bytes=max_bytes
    )


def _duration(content: bytes, measure: Callable[[bytes], float] | None) -> float | None:
    try:
        if measure is None:
            from tfc.utils.storage import get_audio_duration as measure
        seconds = float(measure(content))
    except Exception:
        return None
    return round(seconds, 3) if math.isfinite(seconds) and seconds > 0 else None


def _sniff_mime(content: bytes) -> str | None:
    head = content[:16]
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"GIF8"):
        return "image/gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "audio/wav"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3"):
        return "audio/mpeg"
    if head.startswith(b"OggS"):
        return "audio/ogg"
    if head.startswith(b"fLaC"):
        return "audio/flac"
    if head.startswith(b"%PDF"):
        return "application/pdf"
    return None


__all__ = [
    "FIELD_ORDER",
    "SCHEMA",
    "UNIT_KINDS",
    "EvidenceLimits",
    "EvidenceNotFound",
    "EvidenceQueryBuilder",
    "EvidenceSnapshot",
    "EvidenceUnavailable",
    "FetchedMedia",
    "MediaFetchPolicy",
    "MediaRefused",
    "MediaSource",
    "build_unit_snapshot",
    "canonical_json",
    "default_own_storage_predicate",
    "fetch_media",
    "fetch_snapshot_media",
    "normalize_unit_kind",
    "own_storage_in_scope",
    "resolve_lookup_ids",
    "safe_unit_key",
    "snapshot_digest",
    "verify_snapshot",
    "version_of",
]
