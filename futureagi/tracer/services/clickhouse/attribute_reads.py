"""Bounded ClickHouse 25.3 selectors for span attribute picker APIs.

The picker surfaces in this module are discovery aids, not accounting reads.
Non-cursor compatibility reads walk a fixed one-year horizon in adjacent
half-open bands. Cursor callers can freeze a broader retained-data window;
every physical read remains capped and every selected span is replayed through
``argMax(_version)`` before accepting a key or value.  That keeps tombstones
and cleared attributes from leaking stale data even when span ids are reused.

Only the CH25 ``spans`` table is read.  Callers must perform their PostgreSQL
project ownership check before constructing the selector; telemetry never
falls back to PostgreSQL.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog
from django.conf import settings

from tracer.services.clickhouse.attribute_cursor_state import (
    ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS,
)
from tracer.services.clickhouse.client import ClickHouseClient
from tracer.services.clickhouse.read_budget import (
    ReadDeadlineExceeded,
    is_read_budget_error,
)
from tracer.utils.attribute_suggestion_contract import (
    TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES,
)
from tracer.utils.filter_operators import (
    JSON_ARRAY_FILTER_MAX_STRING_UTF8_BYTES,
    JSON_ARRAY_FILTER_MAX_TOTAL_STRING_UTF8_BYTES,
)

logger = structlog.get_logger(__name__)

AttributeType = Literal["string", "number", "boolean", "array", "map", "json"]
JsonAttributeMode = Literal["none", "scalars", "arrays", "structured", "all"]
QueryStatus = Literal["complete", "sampled", "degraded"]
AttributeKeyBrowseStatus = Literal["continuation", "exhausted", "limit_reached"]
AttributeValueBrowseStatus = Literal["continuation", "exhausted", "limit_reached"]
PhysicalSpanIdentity = tuple[str, str, str, datetime]
JsonScalar = str | int | float | bool
AttributeValue = str | int | float | bool | tuple[JsonScalar, ...]

ATTRIBUTE_READ_HORIZON_DAYS = (7, 14, 30, 180, 365)
# Attribute inventory and value reads share the reviewed, environment-backed
# filter-value wall. Every statement receives only the operation's
# remaining time; finite query-count, candidate, byte, memory, and result caps
# continue to bound the work independently of source-row volume.
ATTRIBUTE_READ_WALL_TIMEOUT_MS = settings.FILTER_VALUE_READ_TIMEOUT_MS
ATTRIBUTE_READ_QUERY_TIMEOUT_MS = settings.FILTER_VALUE_READ_TIMEOUT_MS
ATTRIBUTE_READ_EXACT_KEY_QUERY_TIMEOUT_MS = settings.FILTER_VALUE_READ_TIMEOUT_MS
# Property picker requests use the same reviewed dashboard wall across Observe,
# Tasks, Annotations, Evals, Users, and dashboard widgets. This is a per-request
# work budget, not a data or pagination limit: cursor reads publish only proven
# progress and retain the same frozen window across exact continuations.
ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS = settings.DASHBOARD_FILTER_VALUE_WALL_MS
# Keep one operation below the production low-load harness ceiling (32) even
# when a typed value page needs candidate, version-certificate, and hydration
# queries. Candidate-page caps bound discovery breadth; this separate ceiling
# bounds the actual ClickHouse attempts those pages can expand into.
ATTRIBUTE_READ_MAX_QUERY_COUNT = 30
# JSON overflow has no key skip index, but it still follows the one shared
# operation deadline rather than imposing a smaller data-dependent cutoff.
ATTRIBUTE_READ_JSON_QUERY_TIMEOUT_MS = settings.FILTER_VALUE_READ_TIMEOUT_MS
# The active-part lower bound is only a pagination accelerator: Unix epoch is
# the conservative lossless fallback. Keep this metadata probe short so it
# cannot consume the authoritative cursor read's configured wall.
ATTRIBUTE_READ_METADATA_TIMEOUT_MS = 750
ATTRIBUTE_READ_FALLBACK_RETAINED_START = datetime(1970, 1, 1, tzinfo=UTC)
# Production A/B on the largest US project showed that one-day and 12-hour
# attribute seeds exceed the picker byte envelope on historical dense windows,
# while six-hour seeds return the exact requested key/value in a
# bounded sample. Keep this below the storage-density failure threshold; the
# whole-operation query/deadline caps still bound long-window discovery.
ATTRIBUTE_READ_EXPLICIT_SEGMENT = timedelta(hours=6)
# Keep each storage-order seed small enough that dense projects stop inside the
# read envelope before ClickHouse pulls another large attribute block.  The
# extra row requested by ``_candidate_ids`` remains an explicit truncation
# sentinel, so callers never mistake this discovery sample for a complete
# distribution.
ATTRIBUTE_READ_CANDIDATE_LIMIT = 64
# Value pickers replay full typed Map values only after acquiring a finite
# identity set. Keep that acquisition deliberately tiny so a key lookup cannot
# pull another large values block before LIMIT stops the read.
ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT = 8
# Exact-key discovery may continue through a small number of deterministic
# candidate pages when a storage-order first probe replays entirely to
# cleared/tombstoned latest state. This cap is shared across adaptive bands and
# lanes; it never turns generic key inventory into an open-ended scan.
ATTRIBUTE_READ_TARGETED_CANDIDATE_PAGE_LIMIT = 6
# A stale-only value probe may use this many deterministic continuation pages.
# First probes cover all adaptive bands before these pages run round-robin, so a
# dense recent week cannot hide an older value. A first sample that already has
# usable values remains a visibly degraded sample instead of paying for a full
# global sort. The configured wall deadline remains the production cap.
ATTRIBUTE_READ_VALUE_CANDIDATE_PAGE_LIMIT = 6
ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT = 15
ATTRIBUTE_READ_MAX_KEYS = 1_000
ATTRIBUTE_READ_MAX_VALUES = 500
ATTRIBUTE_READ_MAX_KEY_BYTES = 512
ATTRIBUTE_READ_MAX_SEARCH_BYTES = 512
ATTRIBUTE_READ_MAX_PROJECTS = 64

# Cursor-mode pickers walk a caller-frozen retained-data window in small,
# newest-first physical batches.  The public page is intentionally small while
# the larger internal batch amortizes duplicate values (for example thousands
# of consecutive ``completed`` calls) without allowing one request to become
# an unbounded distinct scan.
ATTRIBUTE_VALUE_CURSOR_MAX_PAGE_SIZE = 50
# A legacy JSON array is one physical value but can contain an arbitrarily
# large selectable vocabulary. Cursor reads therefore scan only a finite slice
# of its raw JSON text per request and carry an authenticated character
# checkpoint. The limits below bound Python work, not retained results: hitting
# either one publishes a continuation at the exact unconsumed member.
ATTRIBUTE_VALUE_CURSOR_JSON_MAX_SCAN_CHARS = 256 * 1024
ATTRIBUTE_VALUE_CURSOR_JSON_MAX_SCANNED_MEMBERS = 2_048
# Any selectable string (at most 4 KiB decoded UTF-8) has a JSON token well
# below this size even when every character is a ``\\uXXXX`` escape. A token
# still open after this prefix is necessarily unselectable and can be skipped
# incrementally without materializing it.
ATTRIBUTE_VALUE_CURSOR_JSON_MAX_SELECTABLE_TOKEN_CHARS = 32 * 1024
# The opaque integer remains inside the signed list cursor. It packs a strong
# raw-value fingerprint, an exact character position, and the small lexer state
# needed to continue through an unselectable oversized/nested member. Older
# cursors used a plain <=501 unique-member offset; those restart the same array
# safely and rely on the exact seen-value set to suppress repeats.
_JSON_ARRAY_CURSOR_PREFIX = 0xA771
_JSON_ARRAY_CURSOR_VERSION = 1
_JSON_ARRAY_CURSOR_POSITION_BITS = 64
_JSON_ARRAY_CURSOR_AUX_BITS = 32
_JSON_ARRAY_CURSOR_MODE_BITS = 2
_JSON_ARRAY_CURSOR_FINGERPRINT_BITS = 256
_JSON_ARRAY_CURSOR_MODE_BOUNDARY = 0
_JSON_ARRAY_CURSOR_MODE_STRING = 1
_JSON_ARRAY_CURSOR_MODE_NESTED = 2
_JSON_ARRAY_CURSOR_MODE_PRIMITIVE = 3
_JSON_ARRAY_CURSOR_NESTED_IN_STRING = 1 << 30
_JSON_ARRAY_CURSOR_NESTED_ESCAPE = 1 << 31
# Maximum SQL/result sentinel for a continuation proof. The server-held exact
# de-duplication prefix can grow beyond this optimization ceiling; in that case
# an overflowing proof simply falls back to the ordinary exact physical walk at
# the unchanged frontier. DISTINCT state remains bounded by the independent
# read-byte, memory, statement, and wall limits below rather than
# max_rows_in_distinct, which races the SQL LIMIT.
ATTRIBUTE_VALUE_CURSOR_PROOF_MAX_RESULT_ROWS = (
    ATTRIBUTE_CURSOR_STATE_MAX_DIGESTS + ATTRIBUTE_VALUE_CURSOR_MAX_PAGE_SIZE + 1
)
ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT = 64
ATTRIBUTE_VALUE_CURSOR_MAX_CANDIDATE_LIMIT = 512
# Only ``call_id`` has representative production evidence for a larger first
# prefix: 960 exact identities yielded 40 values in 2.49s on Colektia, while
# 512 yielded 11. Every other key starts at 64 and grows only after a completed
# batch proves that its current prefix produced no new values.
ATTRIBUTE_VALUE_CURSOR_DENSE_CANDIDATE_LIMIT = 960
# One cursor request is a responsive, exact prefix read rather than a request
# to exhaust the vocabulary. Four candidate batches and their bounded
# latest-state reads cover up to 256 newest matching physical spans once the
# page has found a new value. A
# duplicate-only continuation may use the independent 30-query operation
# envelope and grow its finite replay batch up to 512 identities.  This keeps
# ordinary pages responsive while collapsing long runs of already-seen values.
ATTRIBUTE_VALUE_CURSOR_MAX_CANDIDATE_PAGES = 4
ATTRIBUTE_VALUE_CURSOR_DUPLICATE_ONLY_MAX_CANDIDATE_PAGES = (
    ATTRIBUTE_READ_MAX_QUERY_COUNT // 2
)
# Empty value probes are exact proofs that the requested key/search has no
# candidate in that half-open physical slice.  Grow only after such a proof so
# sparse retained history does not require one public cursor round-trip per
# day.  The 60-day ceiling still exhausts a frozen 365-day window in a small,
# finite number of statements, while the selector's independent configured
# 30-query ceilings remain the hard request bound.
ATTRIBUTE_VALUE_CURSOR_MAX_EMPTY_SEGMENT = timedelta(days=60)
# A widened physical candidate slice is an optional accelerator.  If
# ClickHouse cannot read it inside this short statement budget, retry the
# *same* cursor position with the ordinary six-hour slice; never publish
# speculative progress. Raw distinct proofs have their own qualified timeout
# and proactive growth telemetry below.
ATTRIBUTE_VALUE_CURSOR_SPECULATIVE_TIMEOUT_MS = 750
# A searched page or continuation can use a complete raw-value superset before
# falling back to a physical latest-state walk. Search pages advance only when
# that superset has no relevant value; continuations may also prove every
# relevant value is already in their exact digest vocabulary. Start below the
# one-hour density that can exceed the finite byte envelope, then grow
# successful proofs under the bounded speculative policy below.
# Start at the production-qualified floor: a failed five-minute probe can consume the
# request's whole read-volume allowance before the selector gets a chance to
# retry the same frontier at five seconds.  Coletia contains individual
# 30-second intervals above the bounded 1 GiB read-volume envelope.
ATTRIBUTE_VALUE_CURSOR_DISTINCT_INITIAL_SEGMENT = timedelta(seconds=5)
ATTRIBUTE_VALUE_CURSOR_DISTINCT_MIN_SEGMENT = timedelta(seconds=5)
# Five seconds is the production-qualified floor on the densest observed
# frontier.  Complete empty/seen-only proofs may grow geometrically beyond it,
# but every statement retains the finite proof timeout below and only a cheap
# success can widen the next adjacent slice. A failure moves no cursor state and
# retries the same frontier at a narrower width, so sparse history collapses
# without turning a dense interval into either a skipped range or a long chain
# of five-second HTTP pages.
ATTRIBUTE_VALUE_CURSOR_DISTINCT_MAX_SEGMENT = ATTRIBUTE_VALUE_CURSOR_MAX_EMPTY_SEGMENT
# Complete raw proofs use the production-qualified 2.5 s ceiling at every
# width. Widen only while the previous successful proof was comfortably cheap:
# this preserves logarithmic sparse traversal without using a timeout exception
# to discover the first dense width. The threshold is deliberately below the
# 658 ms successful boundary observed immediately before a 750 ms doubled-slice
# timeout, while still allowing its 438 ms predecessor to grow once.
ATTRIBUTE_VALUE_CURSOR_DISTINCT_TIMEOUT_MS = 2_500
# Load-more continuations without a search term can cheaply certify runs of
# already-published values, but exhausting a sparse year is not one HTTP
# response's job. Two complete adjacent proofs yield an exact advancing cursor
# while bounding the duplicate-only fast path independently of the wall clock.
ATTRIBUTE_VALUE_CURSOR_MAX_UNSEARCHED_CONTINUATION_PROOFS = 2
ATTRIBUTE_VALUE_CURSOR_MAX_SEARCH_PROOFS = 6
ATTRIBUTE_VALUE_CURSOR_DISTINCT_GROWTH_QUERY_TIME_MS = 500
# Retain at least four-times resource headroom before carrying the same width
# into an adjacent slice, whose density is not known yet. A proof at or above
# one quarter of either native read cap shrinks the next width; a proof below
# one eighth may still double when its time telemetry is also cheap. Values in
# between freeze the width. Missing progress telemetry preserves the legacy
# time-only policy for compatible external executors.
ATTRIBUTE_VALUE_CURSOR_DISTINCT_RESOURCE_TARGET_FRACTION = 0.25
# Dense projects can cross the ordinary byte envelope even inside the
# production-qualified six-hour seed.  A failed statement proves nothing, so
# retry its identical newest-first frontier in this smaller exact slice before
# exposing an unavailable picker response.
ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT = timedelta(seconds=5)
# The first unsearched value page used to begin with a six-hour Map scan. On a
# dense production project that single candidate statement read 6.52 GB and
# consumed 4.78 seconds before the exact replay even began. Start a brand-new
# cursor at the already-qualified five-second floor and grow only after a
# complete empty-slice proof. This changes request work, not reachability: every
# successful empty slice advances the frozen physical frontier and every wider
# failure leaves that frontier at its last proven boundary. Legacy cursors with
# a carried adaptive slice or physical checkpoint retain their exact resume
# state; a bare legacy empty frontier safely uses the new lossless seed.
ATTRIBUTE_VALUE_CURSOR_INITIAL_SEGMENT = ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT
# An unpinned physical page can require candidate, version certificate, typed
# hydration and isolated JSON hydration.  Leave that full fallback available
# when a speculative distinct proof cannot certify its slice.
ATTRIBUTE_VALUE_CURSOR_DISTINCT_QUERY_RESERVE = 4
ATTRIBUTE_VALUE_CURSOR_DISTINCT_WALL_RESERVE_MS = 3_000
# Native query cancellation and driver delivery can overshoot a statement cap
# slightly.  Keep a small scheduling margin in addition to the declared proof
# timeout so the exact physical fallback still owns its full reserve.
ATTRIBUTE_VALUE_CURSOR_DISTINCT_GUARD_MARGIN_MS = 100
# De-duplication state lives in immutable server-side chunks.  The signed URL
# therefore remains fixed width while pagination can continue until the frozen
# physical window is exhausted; there is no vocabulary-count ceiling.

# Attribute-key browse cursors use the same frozen newest-first physical walk
# as value cursors. Keep full 128-bit key identities and a lower page-size cap
# so the worst reachable continuation (including one 255-byte trace id and one
# 255-byte span id) stays below common 8 KiB request-line limits. Exact-key
# searches reuse this physical cursor and bind their key into its signed state.
ATTRIBUTE_KEY_CURSOR_MAX_PAGE_SIZE = 50
ATTRIBUTE_KEY_CURSOR_CANDIDATE_LIMIT = 64
# Duplicate-only pages may safely grow their finite replay batch after a
# successful 64-row proof.  The ceiling matches the read envelope's modeled
# block-stop point and remains small enough for one bounded latest-state
# replay.  A budget failure at an expanded size falls back to 64 without
# moving the physical cursor.
ATTRIBUTE_KEY_CURSOR_MAX_CANDIDATE_LIMIT = 512
# Empty candidate pages cost one query (there is nothing to replay), while a
# non-empty page costs a candidate/replay pair.  Let the independent 30-query
# operation ceiling decide how many of each fit: dense walks still stop after
# at most 15 pairs, but an empty historical suffix can collapse up to 29
# adjacent probes inside one API request instead of exposing empty UI pages.
ATTRIBUTE_KEY_CURSOR_MAX_CANDIDATE_PAGES = ATTRIBUTE_READ_MAX_QUERY_COUNT
# Exact lookup is cursor-backed. Eight candidate slices per response keep sparse
# missing-key discovery from issuing 29 near-duplicate statements; the signed
# next cursor resumes the already-advanced frozen window without skipping data.
ATTRIBUTE_KEY_CURSOR_EXACT_MAX_CANDIDATE_PAGES = 8
ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT = timedelta(minutes=5)
# Generic retained-key continuations can cross a historical density cliff even
# after the ordinary five-minute recut.  Keep that established width for first
# pages and sparse growth, but leave one smaller exact retry at the unchanged
# physical frontier.  Five seconds is already the qualified dense floor for
# the sibling retained-value cursor.  This is a work-slice minimum, not a
# retained-window or result limit: only a complete candidate/replay proof may
# move the signed cursor past the slice.
ATTRIBUTE_KEY_CURSOR_DENSE_RETRY_MIN_SEGMENT = timedelta(seconds=5)
# Exact-key JSON discovery starts at the same production-qualified five-minute
# slice, but a newly dense interval must not turn that soft starting point into
# an API failure. Halve an unproven exact slice down to thirty seconds before
# returning an honest bounded-read failure. Successful empty proofs grow
# geometrically again, capped at sixty days so a sparse retained year finishes
# within the 30-query operation without issuing an unbounded JSON parse/sort.
ATTRIBUTE_KEY_CURSOR_EXACT_MIN_SEGMENT = timedelta(seconds=30)
ATTRIBUTE_KEY_CURSOR_EXACT_MAX_EMPTY_SEGMENT = timedelta(days=60)
# A missing candidate proves the whole queried slice has no selectable key, so
# the next adjacent slice may widen.  Dense attribute-free intervals can still
# exceed the read envelope. On a typed budget failure, jump directly to a
# five-minute slice instead of spending the request wall on several
# doomed halvings. No cursor progress is published until that retry succeeds.
# Every successful statement therefore stays inside the same byte/time
# ceilings while sparse retained history remains reachable in practical pages.
# A generic key page starts with the same production-qualified five-minute
# slice used by exact key discovery. This keeps the newest page below the dense
# tenant latency cliff; proven empty slices still grow geometrically, while
# signed physical checkpoints preserve the same no-skip continuation contract.
# Every empty-slice growth probe wider than the ordinary six-hour ceiling is
# speculative: a short failure moves no cursor state and retries the identical
# frontier at five minutes. Sixty days
# remains the intermediate ceiling when a still-wider historical probe fails,
# so partition-pruned years can collapse quickly without sacrificing range.
ATTRIBUTE_KEY_CURSOR_EMPTY_SEGMENT_SOFT_LIMIT = timedelta(days=60)
ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS = 250
ATTRIBUTE_KEY_CURSOR_RETRY_GUARD_MARGIN_MS = 100
ATTRIBUTE_KEY_CURSOR_DIGEST_BYTES = 16
# The absolute returned-token guard covers unexpected high-entropy identity
# shapes as well as the modeled 255-byte trace/span maximum. The endpoint adds
# roughly 130 URL-encoded bytes around this token, leaving ample space below an
# 8 KiB request line.
ATTRIBUTE_KEY_CURSOR_MAX_TOKEN_BYTES = 7_500

_MIB = 1024 * 1024
ATTRIBUTE_READ_SETTINGS: dict[str, Any] = {
    "max_threads": 1,
    # None of the current spans projections covers the identity plus every
    # typed attribute-key subcolumn used by these selectors.  Letting CH25
    # consider all of them adds material planning time before a bounded read
    # can start, while never producing a usable plan for this query shape.
    "optimize_use_projections": 0,
    "allow_experimental_projection_optimization": 0,
    # A small block lets LIMIT BY stop dense candidate scans promptly instead
    # of pulling the default ~65k-row block after the 513th identity.
    "max_block_size": 8_192,
    "max_memory_usage": 36 * 1024 * _MIB,
    "max_bytes_to_read": 36 * 1024 * _MIB,
    "read_overflow_mode": "throw",
    "max_result_rows": ATTRIBUTE_READ_CANDIDATE_LIMIT + 1,
    # One retained identity can carry a wide attributes_extra payload. Keep a
    # finite response ceiling, but leave enough room to hydrate the bounded
    # candidate page on large tenants instead of turning a valid property page
    # into an empty result.
    "max_result_bytes": settings.ATTRIBUTE_READ_MAX_RESULT_BYTES,
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}

# JSON overflow has no key skip index. Its candidate phase therefore samples
# only narrow physical identities; these settings further tighten that seed
# without being required for correctness. Exact JSON key/value inspection runs
# only during latest-state hydration of the finite sampled identities.
_JSON_VALUE_CANDIDATE_SETTINGS: dict[str, Any] = {
    "max_block_size": 2_048,
    "max_bytes_to_read": 36 * 1024 * _MIB,
}

# A single indexed Map granule on the largest production tenant exceeds the
# old low read-volume guard even for a 30-second temporal slice. Cursor value
# reads remain single-threaded and share the configured wall; the common 36 GiB
# memory/read ceilings let ClickHouse finish that already-selected granule.
_ATTRIBUTE_VALUE_PROOF_MAP_SETTINGS: dict[str, Any] = {
    "max_bytes_to_read": 36 * 1024 * _MIB,
}

# The ordered fallback stops at a 65-row sentinel, but ClickHouse may have to
# admit one whole physical granule before LIMIT can fire.  Coletia has a
# very large granule before LIMIT can stop. This candidate-only byte allowance
# lets that finite keyset seed complete; latest-version replay remains bounded
# to the returned identities and the memory/byte limits are unchanged.
_ATTRIBUTE_VALUE_CANDIDATE_MAP_SETTINGS: dict[str, Any] = {
    "max_bytes_to_read": 36 * 1024 * _MIB,
}

_TYPE_PRIORITY: dict[AttributeType, int] = {
    "string": 0,
    "number": 1,
    "boolean": 2,
    "array": 3,
    "map": 4,
    "json": 5,
}

_NIL_UUID = "00000000-0000-0000-0000-000000000000"
# The storage-order generic cardinality sample can be full of non-session spans
# on a dense project even when the same project has session data.  This
# predicate gives session pickers a separate, indexed candidate lane instead of
# treating that unrelated sample as evidence that sessions do not exist.
_SESSION_CARDINALITY_CANDIDATE_PREDICATE = (
    f"isNotNull(trace_session_id) AND trace_session_id != toUUID('{_NIL_UUID}')"
)


class InvalidAttributeKey(ValueError):
    """A requested attribute key is not safe for the public picker API."""


class InvalidAttributeSearch(ValueError):
    """A requested value-search term is not safe for the public picker API."""


class IncompleteLatestStateReplay(RuntimeError):
    """A candidate set could not be fully verified at its latest state."""


class AttributeReadQueryLimitExceeded(ReadDeadlineExceeded):
    """A bounded attribute operation exhausted its ClickHouse query ceiling."""


@dataclass(frozen=True)
class AttributeQueryPage:
    data: list[dict[str, Any]]
    query_time_ms: float
    read_rows: int | None = None
    read_bytes: int | None = None


@dataclass(frozen=True)
class AttributeReadMetadata:
    query_complete: bool
    query_status: QueryStatus
    query_error_code: str | None
    query_window_start: datetime
    query_window_end: datetime
    query_count: int

    def public_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query_complete": self.query_complete,
            "query_status": self.query_status,
            "query_window_start": _utc_iso(self.query_window_start),
            "query_window_end": _utc_iso(self.query_window_end),
        }
        if self.query_error_code:
            payload["query_error_code"] = self.query_error_code
        return payload


@dataclass(frozen=True)
class AttributeKeyRow:
    key: str
    type: AttributeType
    count: int
    # Supplemental response metadata must not change the row's long-standing
    # value/equality identity, which is key + dominant type + observed count.
    types: tuple[AttributeType, ...] = field(default=(), compare=False)


@dataclass(frozen=True)
class AttributeValueRow:
    value: AttributeValue
    type: AttributeType
    count: int


@dataclass(frozen=True)
class AttributeKeyRead:
    rows: tuple[AttributeKeyRow, ...]
    metadata: AttributeReadMetadata


@dataclass(frozen=True)
class AttributeValueRead:
    rows: tuple[AttributeValueRow, ...]
    metadata: AttributeReadMetadata


@dataclass(frozen=True)
class AttributeValueCursorPageRead:
    """One verified, newest-first page from a bounded physical-span walk."""

    rows: tuple[AttributeValueRow, ...]
    metadata: AttributeReadMetadata
    has_more: bool
    next_segment_end: datetime
    next_before_identity: PhysicalSpanIdentity | None
    next_resume_identity: PhysicalSpanIdentity | None
    next_resume_member_offset: int
    seen_value_digests: tuple[str, ...]
    browse_status: AttributeValueBrowseStatus = "exhausted"
    # Signed continuation hint for the next exact physical slice.  This is an
    # execution bound, not result state: old cursors may omit it and safely
    # fall back to the legacy six-hour width.
    next_segment_start: datetime | None = None
    appended_value_digests: tuple[str, ...] = ()
    seen_value_count: int = 0


@dataclass(frozen=True)
class _JsonArrayCursorState:
    """Authenticated incremental position inside one raw JSON array value."""

    position: int
    mode: int = _JSON_ARRAY_CURSOR_MODE_BOUNDARY
    auxiliary: int = 0


@dataclass(frozen=True)
class AttributeKeyCursorPageRead:
    """One verified, newest-first page of distinct attribute keys."""

    rows: tuple[AttributeKeyRow, ...]
    metadata: AttributeReadMetadata
    has_more: bool
    browse_status: AttributeKeyBrowseStatus
    next_segment_end: datetime
    next_before_identity: PhysicalSpanIdentity | None
    next_resume_identity: PhysicalSpanIdentity | None
    next_resume_key_offset: int
    seen_key_digests: tuple[str, ...]
    # Accepted only for rolling compatibility with cursors published by pods
    # that preserved an adaptively widened segment. Generic reads compress
    # proven progress into the legacy six-hour checkpoint shape; exact lookup
    # keeps a narrower active slice when that is required for safe replay.
    next_segment_start: datetime | None = None
    appended_key_digests: tuple[str, ...] = ()
    seen_key_count: int = 0


@dataclass(frozen=True)
class AttributeDetailRead:
    """Bounded latest-state value sample for one attribute's detail panel."""

    attribute_type: AttributeType | None
    rows: tuple[AttributeValueRow, ...]
    metadata: AttributeReadMetadata


@dataclass(frozen=True)
class AttributeCardinalityRead:
    max_spans_per_trace: int
    max_traces_per_session: int
    metadata: AttributeReadMetadata


class AttributeKeyInventory(list):
    """List-compatible typed inventory with explicit bounded-read metadata."""

    def __init__(self, read: AttributeKeyRead, *, include_counts: bool = False):
        super().__init__(
            {
                "key": row.key,
                "type": row.type,
                **({"count": row.count} if include_counts else {}),
            }
            for row in read.rows
        )
        self.query_complete = read.metadata.query_complete
        self.query_status = read.metadata.query_status
        self.query_error_code = read.metadata.query_error_code
        self.query_window_start = read.metadata.query_window_start
        self.query_window_end = read.metadata.query_window_end
        self.query_count = read.metadata.query_count


def _validate_text(
    value: Any,
    *,
    label: str,
    max_bytes: int,
    allow_empty: bool,
    error_type: type[ValueError],
) -> str:
    if not isinstance(value, str):
        raise error_type(f"{label} must be text")
    if not allow_empty and not value.strip():
        raise error_type(f"{label} is required")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise error_type(f"{label} contains control characters")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise error_type(f"{label} must be valid UTF-8") from exc
    if len(encoded) > max_bytes:
        raise error_type(f"{label} is too long")
    return value


def validate_attribute_key(value: Any) -> str:
    """Validate without restricting punctuation or non-ASCII key names."""

    return _validate_text(
        value,
        label="Attribute key",
        max_bytes=ATTRIBUTE_READ_MAX_KEY_BYTES,
        allow_empty=False,
        error_type=InvalidAttributeKey,
    )


def validate_attribute_search(value: Any) -> str:
    """Validate a literal UTF-8 contains-search term."""

    return _validate_text(
        value,
        label="Attribute search",
        max_bytes=ATTRIBUTE_READ_MAX_SEARCH_BYTES,
        allow_empty=True,
        error_type=InvalidAttributeSearch,
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _utc_iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def _unix_microseconds(value: datetime) -> int:
    """Encode DateTime64(6) exactly without driver tuple-datetime truncation."""

    delta = _utc(value) - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def adaptive_attribute_windows(
    window_end: datetime,
    *,
    horizon_days: int = 365,
) -> tuple[tuple[datetime, datetime], ...]:
    """Return newest-first adjacent 7d/14d/30d/6mo/1yr bands."""

    if horizon_days < 1 or horizon_days > ATTRIBUTE_READ_HORIZON_DAYS[-1]:
        raise ValueError("horizon_days must be between 1 and 365")
    end = _utc(window_end)
    boundaries = [day for day in ATTRIBUTE_READ_HORIZON_DAYS if day < horizon_days]
    boundaries.append(horizon_days)
    windows: list[tuple[datetime, datetime]] = []
    previous = 0
    for boundary in boundaries:
        windows.append((end - timedelta(days=boundary), end - timedelta(days=previous)))
        previous = boundary
    return tuple(windows)


def _prioritize_explicit_attribute_windows(
    windows: list[tuple[datetime, datetime]],
) -> tuple[tuple[datetime, datetime], ...]:
    """Put a bounded, full-range temporal sample before remaining slices.

    Exact key/value picker operations reserve at least one candidate page for
    the JSON lane. When an explicit range contains more six-hour slices than
    the remaining typed-page budget, sample evenly from newest through oldest
    first. The untouched slices stay in deterministic newest-first order after
    that prefix, so a caller with spare query budget can continue without gaps
    or duplicate probes. Metadata remains sampled whenever the hard page/query
    ceilings stop before the full list is consumed.
    """

    probe_count = max(ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT - 1, 1)
    if len(windows) <= probe_count:
        return tuple(windows)
    if probe_count == 1:
        return tuple(windows)
    last_index = len(windows) - 1
    sampled_indices = tuple(
        (position * last_index) // (probe_count - 1) for position in range(probe_count)
    )
    sampled = set(sampled_indices)
    return tuple(
        [windows[index] for index in sampled_indices]
        + [window for index, window in enumerate(windows) if index not in sampled]
    )


def _attribute_window_bounds(
    windows: tuple[tuple[datetime, datetime], ...],
) -> tuple[datetime, datetime]:
    """Return request bounds independently of temporal probe ordering."""

    return min(start for start, _ in windows), max(end for _, end in windows)


class V2AttributeQueryExecutor:
    """Read-only native-driver executor bound explicitly to ``CLICKHOUSE_V2``."""

    def __init__(self, client: ClickHouseClient | None = None):
        if client is None:
            # Lazy to avoid a query_service -> attribute_reads import cycle.
            from tracer.services.clickhouse.v2.query_service import (
                get_v2_query_client,
            )

            client = get_v2_query_client()
        self._client = client

    @property
    def client(self) -> ClickHouseClient:
        return self._client

    def execute(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> AttributeQueryPage:
        try:
            progress_execute = getattr(
                type(self._client), "execute_read_with_progress", None
            )
            if callable(progress_execute):
                (
                    rows,
                    columns,
                    query_time_ms,
                    read_rows,
                    read_bytes,
                ) = progress_execute(
                    self._client,
                    query,
                    params,
                    timeout_ms=timeout_ms,
                    settings=settings,
                )
            else:
                rows, columns, query_time_ms = self._client.execute_read(
                    query,
                    params,
                    timeout_ms=timeout_ms,
                    settings=settings,
                )
                read_rows = None
                read_bytes = None
        except TimeoutError as exc:
            # Some native-driver wrappers surface socket/read deadlines as the
            # built-in timeout type. Normalize only at this CH25 read boundary;
            # the shared budget classifier intentionally rejects arbitrary
            # application TimeoutError instances.
            raise ReadDeadlineExceeded("Attribute ClickHouse query timed out") from exc
        names = [
            column[0] if isinstance(column, tuple) else column for column in columns
        ]
        return AttributeQueryPage(
            data=[dict(zip(names, row, strict=False)) for row in rows],
            query_time_ms=float(query_time_ms),
            read_rows=read_rows,
            read_bytes=read_bytes,
        )


_ATTRIBUTE_READ_CAPACITY = threading.BoundedSemaphore(8)


# Cursor pickers freeze their full retained-data window on page one. Starting
# every project at the Unix epoch makes a legitimately exhausted vocabulary
# walk decades of empty six-hour slices. The active MergeTree-part minimum is
# metadata-only and exact for the table's physical lower bound. It is
# deliberately global: a bound earlier than a project's first row is safe,
# while a project-local ``min(start_time)`` can exceed the finite row-read cap
# on large tenants before discovery even begins.
_GLOBAL_RETAINED_START_SQL = """
    SELECT minOrNull(min_time) AS retained_start
    FROM system.parts
    WHERE active
      AND database = currentDatabase()
      AND table = 'spans'
      AND min_time < fromUnixTimestamp64Micro(%(window_end_us)s)
"""


# The first probe follows the spans sorting key and has no LIMIT BY. Picker reads
# are samples, not chronological lists: the former newest-first global sort and
# LIMIT BY forced CH25 to process every matching row before returning the first
# candidate on large tenants. ``optimize_read_in_order`` lets this storage-order
# LIMIT stop as soon as a finite page is available. Raw duplicate versions may
# consume sample slots; the +1 sentinel then marks the response incomplete, and
# every retained physical identity is still replayed through argMax(_version)
# before use. Thus background merges may change which *sample* is returned, but
# can never turn a sampled response into a false exact response.
_CANDIDATE_SQL = """
    SELECT
        toString(project_id) AS project_id,
        toString(trace_id) AS trace_id,
        toString(id) AS id,
        start_time
    FROM spans AS attribute_source
    PREWHERE project_id IN %(project_ids)s
      AND start_time >= fromUnixTimestamp64Micro(%(segment_start_us)s)
      AND start_time < fromUnixTimestamp64Micro(%(segment_end_us)s)
    WHERE is_deleted = 0
      AND ({candidate_predicate})
    ORDER BY
        attribute_source.project_id ASC,
        attribute_source.observation_type ASC,
        attribute_source.service_name ASC,
        toStartOfHour(attribute_source.start_time) ASC,
        attribute_source.trace_id ASC,
        attribute_source.id ASC
    LIMIT %(candidate_limit)s
"""

_STRATIFIED_CANDIDATE_SQL = """
    SELECT
        toString(project_id) AS project_id,
        toString(trace_id) AS trace_id,
        toString(id) AS id,
        start_time,
        toUInt64(1) AS sample_size
    FROM spans AS attribute_source
    PREWHERE project_id IN %(project_ids)s
      AND start_time >= fromUnixTimestamp64Micro(%(segment_start_us)s)
      AND start_time < fromUnixTimestamp64Micro(%(segment_end_us)s)
    WHERE is_deleted = 0
      AND ({candidate_predicate})
    ORDER BY
        attribute_source.project_id ASC,
        attribute_source.observation_type ASC,
        attribute_source.service_name ASC,
        toStartOfHour(attribute_source.start_time) ASC,
        attribute_source.trace_id ASC,
        attribute_source.id ASC
    LIMIT %(candidate_limit)s
"""

# Targeted discovery/value reads may encounter a first storage-order sample made
# entirely of stale versions whose latest state cleared the requested key. Only
# that case restarts with this deterministic keyset query. Generic browse and
# successful targeted probes never pay the global ordering cost.
_ORDERED_CANDIDATE_SQL = """
    SELECT
        toString(project_id) AS project_id,
        toString(trace_id) AS trace_id,
        toString(id) AS id,
        start_time
    FROM spans AS attribute_source
    PREWHERE project_id IN %(project_ids)s
      AND start_time >= fromUnixTimestamp64Micro(%(segment_start_us)s)
      AND start_time < fromUnixTimestamp64Micro(%(segment_end_us)s)
    WHERE is_deleted = 0
      AND ({candidate_predicate})
    ORDER BY
        start_time DESC,
        id DESC,
        trace_id DESC,
        toString(attribute_source.project_id) DESC
    LIMIT 1 BY project_id, trace_id, id, start_time
    LIMIT %(candidate_limit)s
"""

_ORDERED_STRATIFIED_CANDIDATE_SQL = """
    SELECT
        toString(project_id) AS project_id,
        toString(trace_id) AS trace_id,
        toString(id) AS id,
        start_time,
        toUInt64(1) AS sample_size
    FROM spans AS attribute_source
    PREWHERE project_id IN %(project_ids)s
      AND start_time >= fromUnixTimestamp64Micro(%(segment_start_us)s)
      AND start_time < fromUnixTimestamp64Micro(%(segment_end_us)s)
    WHERE is_deleted = 0
      AND ({candidate_predicate})
    ORDER BY
        start_time DESC,
        id DESC,
        trace_id DESC,
        toString(attribute_source.project_id) DESC
    LIMIT 1 BY project_id, trace_id, id, start_time
    LIMIT %(candidate_limit)s
"""

# Typed value pickers carry the raw row version alongside the physical
# identity, but deliberately never project a Map value.  The version is a
# narrow certificate input: an exact latest-state replay below decides whether
# this key-bearing row is still current before any wide value subcolumn is
# hydrated.
_TYPED_VALUE_CANDIDATE_SQL = """
    SELECT
        toString(project_id) AS project_id,
        toString(trace_id) AS trace_id,
        toString(id) AS id,
        start_time,
        toUInt64(_version) AS candidate_version
    FROM spans AS attribute_source
    PREWHERE project_id IN %(project_ids)s
      AND start_time >= fromUnixTimestamp64Micro(%(segment_start_us)s)
      AND start_time < fromUnixTimestamp64Micro(%(segment_end_us)s)
    WHERE is_deleted = 0
      AND ({candidate_predicate})
    ORDER BY
        attribute_source.project_id ASC,
        attribute_source.observation_type ASC,
        attribute_source.service_name ASC,
        toStartOfHour(attribute_source.start_time) ASC,
        attribute_source.trace_id ASC,
        attribute_source.id ASC
    LIMIT %(candidate_limit)s
"""

_ORDERED_TYPED_VALUE_CANDIDATE_SQL = """
    SELECT
        toString(project_id) AS project_id,
        toString(trace_id) AS trace_id,
        toString(id) AS id,
        start_time,
        toUInt64(_version) AS candidate_version
    FROM spans AS attribute_source
    PREWHERE project_id IN %(project_ids)s
      AND start_time >= fromUnixTimestamp64Micro(%(segment_start_us)s)
      AND start_time < fromUnixTimestamp64Micro(%(segment_end_us)s)
    WHERE is_deleted = 0
      AND ({candidate_predicate})
    ORDER BY
        start_time DESC,
        id DESC,
        trace_id DESC,
        toString(attribute_source.project_id) DESC,
        _version DESC
    LIMIT 1 BY project_id, trace_id, id, start_time
    LIMIT %(candidate_limit)s
"""

# Exact searched-continuation certificate.  This query intentionally observes
# physical versions rather than trying to publish latest state.  Every current
# live value must occur in at least one physical version, so a complete raw
# distinct vocabulary is a safe superset.  Stale values and tombstones can only
# introduce an unseen false positive, which makes the selector fall back at the
# unchanged frontier.  They can never make it skip a current unseen value.
_VALUE_CURSOR_SEARCHED_RAW_SUPERSET_SQL = """
    SELECT DISTINCT
        tupleElement(raw_value, 1) AS value_type,
        tupleElement(raw_value, 2) AS value_string,
        tupleElement(raw_value, 3) AS value_number,
        tupleElement(raw_value, 4) AS value_boolean,
        tupleElement(raw_value, 5) AS value_json_raw,
        toUInt64(1) AS value_count
    FROM spans AS raw_source
    ARRAY JOIN [{raw_value_lanes}] AS raw_value
    PREWHERE {raw_scope_predicate}
      AND raw_source.start_time
          >= fromUnixTimestamp64Micro(%(segment_start_us)s)
      AND raw_source.start_time
          < fromUnixTimestamp64Micro(%(segment_end_us)s)
    WHERE ({raw_frontier_predicate})
      AND ({raw_any_candidate_predicate})
      AND tupleElement(raw_value, 6) != 0
      AND tupleElement(raw_value, 7) != 0
    LIMIT %(distinct_limit)s
"""

# The common pinned-type path has only one value lane.  Avoid wrapping that
# scalar in a one-element ARRAY JOIN: on the densest production slice this
# preserves the identical read set but cuts roughly a fifth of executor wall
# time.  Multi-type reads still use the tagged-lane query above.
_VALUE_CURSOR_SEARCHED_RAW_SINGLE_LANE_SQL = """
    SELECT DISTINCT
        '{value_type}' AS value_type,
        {value_string} AS value_string,
        {value_number} AS value_number,
        {value_boolean} AS value_boolean,
        {value_json_raw} AS value_json_raw,
        toUInt64(1) AS value_count
    FROM spans AS raw_source
    PREWHERE {raw_scope_predicate}
      AND raw_source.start_time
          >= fromUnixTimestamp64Micro(%(segment_start_us)s)
      AND raw_source.start_time
          < fromUnixTimestamp64Micro(%(segment_end_us)s)
    WHERE ({raw_frontier_predicate})
      AND ({raw_candidate_predicate})
    LIMIT %(distinct_limit)s
"""

_LATEST_TARGET_SQL = """
    SELECT
        toString(project_id) AS project_id,
        toString(id) AS id,
        tupleElement(latest_state, 1) AS start_time,
        tupleElement(latest_state, 2) AS is_deleted,
        tupleElement(latest_state, 3) AS trace_id,
        tupleElement(latest_state, 4) AS trace_session_id,
        tupleElement(latest_state, 5) AS parent_span_id,
        tupleElement(latest_state, 6) AS string_present,
        tupleElement(latest_state, 7) AS string_value,
        tupleElement(latest_state, 8) AS number_present,
        tupleElement(latest_state, 9) AS number_value,
        tupleElement(latest_state, 10) AS boolean_present,
        tupleElement(latest_state, 11) AS boolean_value,
        tupleElement(latest_state, 12) AS legacy_present,
        tupleElement(latest_state, 13) AS legacy_value_raw,
        lower(hex(SHA256(tupleElement(latest_state, 13))))
            AS legacy_value_fingerprint
    FROM
    (
        SELECT
            project_id,
            id,
            argMax(
                tuple(
                    start_time,
                    is_deleted,
                    trace_id,
                    ifNull(toString(trace_session_id), ''),
                    parent_span_id,
                    mapContains(attrs_string, %(attribute_key)s),
                    attrs_string[%(attribute_key)s],
                    mapContains(attrs_number, %(attribute_key)s),
                    attrs_number[%(attribute_key)s],
                    mapContains(attrs_bool, %(attribute_key)s),
                    attrs_bool[%(attribute_key)s],
                    JSONHas(attributes_extra, %(attribute_key)s),
                    JSONExtractRaw(attributes_extra, %(attribute_key)s)
                ),
                _version
            ) AS latest_state
        FROM spans AS attribute_source
        PREWHERE project_id IN %(project_ids)s
          AND ({candidate_predicate})
        GROUP BY project_id, trace_id, id, start_time
    )
"""

_LATEST_TYPED_TARGET_SQL = """
    SELECT
        toString(project_id) AS project_id,
        toString(trace_id) AS trace_id,
        toString(id) AS id,
        start_time,
        tupleElement(latest_state, 1) AS is_deleted,
        tupleElement(latest_state, 2) AS string_present,
        tupleElement(latest_state, 3) AS string_value,
        tupleElement(latest_state, 4) AS number_present,
        tupleElement(latest_state, 5) AS number_value,
        tupleElement(latest_state, 6) AS boolean_present,
        tupleElement(latest_state, 7) AS boolean_value
    FROM
    (
        SELECT
            project_id,
            trace_id,
            id,
            start_time,
            argMax(
                tuple(
                    is_deleted,
                    mapContains(attrs_string, %(attribute_key)s),
                    attrs_string[%(attribute_key)s],
                    mapContains(attrs_number, %(attribute_key)s),
                    attrs_number[%(attribute_key)s],
                    mapContains(attrs_bool, %(attribute_key)s),
                    attrs_bool[%(attribute_key)s]
                ),
                _version
            ) AS latest_state
        FROM spans AS attribute_source
        PREWHERE project_id IN %(project_ids)s
          AND ({candidate_predicate})
        GROUP BY project_id, trace_id, id, start_time
    )
"""

_LATEST_JSON_TARGET_SQL = """
    SELECT
        toString(project_id) AS project_id,
        toString(trace_id) AS trace_id,
        toString(id) AS id,
        start_time,
        tupleElement(latest_state, 1) AS is_deleted,
        tupleElement(latest_state, 2) AS legacy_present,
        tupleElement(latest_state, 3) AS legacy_value_raw,
        lower(hex(SHA256(tupleElement(latest_state, 3))))
            AS legacy_value_fingerprint
    FROM
    (
        SELECT
            project_id,
            trace_id,
            id,
            start_time,
            argMax(
                tuple(
                    is_deleted,
                    JSONHas(attributes_extra, %(attribute_key)s),
                    JSONExtractRaw(attributes_extra, %(attribute_key)s)
                ),
                _version
            ) AS latest_state
        FROM spans AS attribute_source
        PREWHERE project_id IN %(project_ids)s
          AND ({candidate_predicate})
        GROUP BY project_id, trace_id, id, start_time
    )
"""

# Certify candidate versions and tombstones without reading any Map subcolumn.
# The candidate query already proved that its raw row had the requested key;
# only a row whose version is still latest and live may advance to value
# hydration.  High-churn tenants therefore pay this narrow replay for stale or
# cleared identities instead of replaying their wide Map keys or values.
_LATEST_TYPED_VERSION_SQL = """
    SELECT
        toString(project_id) AS project_id,
        toString(trace_id) AS trace_id,
        toString(id) AS id,
        start_time,
        max(_version) AS latest_version,
        argMax(is_deleted, _version) AS is_deleted
    FROM
    (
        SELECT
            project_id,
            trace_id,
            id,
            start_time,
            _version,
            is_deleted
        FROM spans AS attribute_source
        PREWHERE project_id IN %(project_ids)s
          AND ({candidate_predicate})
    )
    GROUP BY project_id, trace_id, id, start_time
"""

_LATEST_BROWSE_SQL = """
    SELECT
        toString(project_id) AS project_id,
        toString(id) AS id,
        tupleElement(latest_state, 1) AS start_time,
        tupleElement(latest_state, 2) AS is_deleted,
        tupleElement(latest_state, 3) AS trace_id,
        tupleElement(latest_state, 4) AS trace_session_id,
        tupleElement(latest_state, 5) AS parent_span_id,
        tupleElement(latest_state, 6) AS string_keys,
        tupleElement(latest_state, 7) AS number_keys,
        tupleElement(latest_state, 8) AS boolean_keys,
        tupleElement(latest_state, 9) AS attributes_extra
    FROM
    (
        SELECT
            project_id,
            id,
            argMax(
                tuple(
                    start_time,
                    is_deleted,
                    trace_id,
                    ifNull(toString(trace_session_id), ''),
                    parent_span_id,
                    attrs_string.keys,
                    attrs_number.keys,
                    attrs_bool.keys,
                    attributes_extra
                ),
                _version
            ) AS latest_state
        FROM spans AS attribute_source
        PREWHERE project_id IN %(project_ids)s
          AND ({candidate_predicate})
        GROUP BY project_id, trace_id, id, start_time
    )
"""

_LATEST_TYPED_BROWSE_SQL = """
    SELECT
        toString(project_id) AS project_id,
        toString(trace_id) AS trace_id,
        toString(id) AS id,
        start_time,
        tupleElement(latest_state, 1) AS is_deleted,
        tupleElement(latest_state, 2) AS string_keys,
        tupleElement(latest_state, 3) AS number_keys,
        tupleElement(latest_state, 4) AS boolean_keys
    FROM
    (
        SELECT
            project_id,
            trace_id,
            id,
            start_time,
            argMax(
                tuple(
                    is_deleted,
                    attrs_string.keys,
                    attrs_number.keys,
                    attrs_bool.keys
                ),
                _version
            ) AS latest_state
        FROM spans AS attribute_source
        PREWHERE project_id IN %(project_ids)s
          AND ({candidate_predicate})
        GROUP BY project_id, trace_id, id, start_time
    )
"""

_LATEST_CARDINALITY_SQL = """
    SELECT
        toString(project_id) AS project_id,
        toString(id) AS id,
        tupleElement(latest_state, 1) AS start_time,
        tupleElement(latest_state, 2) AS is_deleted,
        tupleElement(latest_state, 3) AS trace_id,
        tupleElement(latest_state, 4) AS trace_session_id
    FROM
    (
        SELECT
            project_id,
            id,
            argMax(
                tuple(
                    start_time,
                    is_deleted,
                    trace_id,
                    ifNull(toString(trace_session_id), '')
                ),
                _version
            ) AS latest_state
        FROM spans AS attribute_source
        PREWHERE project_id IN %(project_ids)s
          AND ({candidate_predicate})
        GROUP BY project_id, trace_id, id, start_time
    )
"""


class AttributeReadSelector:
    """Thin typed selector shared by every production attribute picker.

    Each public operation gets one configured wall budget shared by all of its
    adaptive candidate and latest-state replay queries. Default-horizon reads
    keep the existing finite band/page caps; caller-supplied windows are split
    into adjacent six-hour probes under the same whole-operation deadline. Common
    dense typed reads stop after one candidate/replay pair and explicitly
    report a sample. Reusing a
    selector for a second public operation starts a fresh operation budget;
    authoritative statements receive the remaining operation wall. Optional
    speculative accelerators retain shorter fail-open budgets so they cannot
    consume the exact fallback's deadline.
    """

    def __init__(
        self,
        executor: V2AttributeQueryExecutor | None = None,
        *,
        now: datetime | None = None,
        wall_timeout_ms: int = ATTRIBUTE_READ_WALL_TIMEOUT_MS,
        clock: Callable[[], float] = time.monotonic,
        typed_only: bool = False,
        json_attribute_mode: JsonAttributeMode | None = None,
    ):
        self._executor = executor or V2AttributeQueryExecutor()
        self._clock = clock
        self._wall_timeout_seconds = max(int(wall_timeout_ms), 1) / 1000
        self._deadline: float | None = None
        self._window_end = _utc(now or datetime.now(UTC))
        self._query_count = 0
        self._last_query_time_ms: float | None = None
        self._last_query_read_rows: int | None = None
        self._last_query_read_bytes: int | None = None
        # ``typed_only`` remains the compatibility switch for callers that
        # must never touch the JSON overflow.  Filter pickers opt into
        # ``structured`` explicitly: bounded predicates support JSON array and
        # finite flat-object filters, while JSON-only scalars are not indexed
        # and therefore must not be advertised as filterable.  Eval mapping
        # uses ``all`` because it needs key names, not a filter operator
        # contract.
        self._typed_only = bool(typed_only)
        if json_attribute_mode is None:
            json_attribute_mode = "none" if self._typed_only else "scalars"
        if json_attribute_mode not in {
            "none",
            "scalars",
            "arrays",
            "structured",
            "all",
        }:
            raise ValueError("Unsupported JSON attribute discovery mode")
        self._json_attribute_mode: JsonAttributeMode = json_attribute_mode
        self._reads_json_overflow = json_attribute_mode != "none"

    @property
    def executor(self) -> V2AttributeQueryExecutor:
        return self._executor

    @property
    def query_count(self) -> int:
        return self._query_count

    @property
    def query_window_end(self) -> datetime:
        return self._window_end

    def retained_window_start(
        self,
        project_ids: Iterable[Any],
        *,
        window_end: datetime,
    ) -> datetime | None:
        """Return the exact global physical lower bound before a window end.

        The active-part minimum is a safe lower bound for every project's
        latest/live attributes and costs no span-row scan. It lets cursors prove
        exhaustion at the real table-retention boundary instead of emitting
        empty continuations back to 1970. ``None`` means the spans table has no
        active part before the frozen window. A read-budget failure returns
        Unix epoch, the conservative lossless bound, so this optional metadata
        optimization cannot make the authoritative cursor unavailable.
        """

        self._begin_operation()
        projects = self._project_ids(project_ids)
        if not projects:
            return None
        end = _utc(window_end)
        try:
            rows = self._execute(
                _GLOBAL_RETAINED_START_SQL,
                {
                    "window_end_us": _unix_microseconds(end),
                },
                max_result_rows=1,
                query_timeout_ms=ATTRIBUTE_READ_METADATA_TIMEOUT_MS,
            )
        except Exception as exc:
            if not is_read_budget_error(exc):
                raise
            logger.warning("attribute_retained_window_metadata_budget_exceeded")
            return ATTRIBUTE_READ_FALLBACK_RETAINED_START
        if len(rows) != 1:
            raise IncompleteLatestStateReplay(
                "Attribute retained-window query returned an invalid result"
            )
        retained_start = rows[0].get("retained_start")
        if retained_start is None:
            return None
        if not isinstance(retained_start, datetime):
            raise IncompleteLatestStateReplay(
                "Attribute retained-window query returned an invalid timestamp"
            )
        retained_start = _utc(retained_start)
        if retained_start >= end:
            raise IncompleteLatestStateReplay(
                "Attribute retained-window query returned an invalid boundary"
            )
        return retained_start

    def degraded_metadata(self, error_code: str) -> AttributeReadMetadata:
        """Build a sanitized failure envelope for a discarded read."""

        return self._metadata(
            complete=False,
            error_code=error_code,
            window_start=self._window_end
            - timedelta(days=ATTRIBUTE_READ_HORIZON_DAYS[-1]),
            window_end=self._window_end,
            query_count=self._query_count,
        )

    def _warn_partial_budget(self, operation: str) -> None:
        """Record intentional partial retention without leaking query details."""

        logger.warning(
            "attribute_read_partial_budget_exceeded",
            operation=operation,
            query_count=self._query_count,
        )

    def _begin_operation(self) -> None:
        """Start a fresh whole-operation budget at the public call boundary."""

        self._deadline = self._clock() + self._wall_timeout_seconds
        self._query_count = 0
        self._last_query_time_ms = None
        self._last_query_read_rows = None
        self._last_query_read_bytes = None

    def _execute(
        self,
        query: str,
        params: dict[str, Any],
        *,
        max_result_rows: int,
        query_settings: dict[str, Any] | None = None,
        query_timeout_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        if self._deadline is None:
            self._begin_operation()
        assert self._deadline is not None
        remaining_ms = int((self._deadline - self._clock()) * 1000)
        if remaining_ms < 25:
            raise ReadDeadlineExceeded("Attribute read deadline exceeded")
        timeout_cap_ms = (
            ATTRIBUTE_READ_QUERY_TIMEOUT_MS
            if query_timeout_ms is None
            else min(
                max(int(query_timeout_ms), 1),
                ATTRIBUTE_READ_EXACT_KEY_QUERY_TIMEOUT_MS,
            )
        )
        # Admission is part of the read, not free time before it. In
        # particular, a fail-open speculative probe must not wait for the
        # semaphore longer than its own short cap and consume the exact
        # fallback's whole operation wall.
        acquired = _ATTRIBUTE_READ_CAPACITY.acquire(
            timeout=max(min(remaining_ms, timeout_cap_ms), 0) / 1000
        )
        if not acquired:
            raise ReadDeadlineExceeded("Attribute read capacity is busy")
        try:
            remaining_ms = int((self._deadline - self._clock()) * 1000)
            if remaining_ms < 25:
                raise ReadDeadlineExceeded("Attribute read deadline exceeded")
            if self._query_count >= ATTRIBUTE_READ_MAX_QUERY_COUNT:
                raise AttributeReadQueryLimitExceeded(
                    "Attribute read query limit exceeded"
                )
            self._query_count += 1
            self._last_query_time_ms = None
            self._last_query_read_rows = None
            self._last_query_read_bytes = None
            page = self._executor.execute(
                query,
                params,
                timeout_ms=min(timeout_cap_ms, remaining_ms),
                settings={
                    **ATTRIBUTE_READ_SETTINGS,
                    **(query_settings or {}),
                    "max_result_rows": max(int(max_result_rows), 1),
                },
            )
        finally:
            _ATTRIBUTE_READ_CAPACITY.release()
        if not isinstance(page, AttributeQueryPage) or not isinstance(page.data, list):
            raise IncompleteLatestStateReplay(
                "Attribute query returned an invalid result envelope"
            )
        try:
            measured_query_time_ms = float(page.query_time_ms)
        except (TypeError, ValueError):
            measured_query_time_ms = math.inf
        if math.isfinite(measured_query_time_ms) and measured_query_time_ms >= 0:
            self._last_query_time_ms = measured_query_time_ms

        def progress_metric(value: Any) -> int | None:
            try:
                measured = int(value)
            except (TypeError, ValueError):
                return None
            return measured if measured >= 0 else None

        self._last_query_read_rows = progress_metric(page.read_rows)
        self._last_query_read_bytes = progress_metric(page.read_bytes)
        return page.data

    def _windows(
        self,
        *,
        horizon_days: int,
        window_start: datetime | None,
        window_end: datetime | None,
    ) -> tuple[tuple[datetime, datetime], ...]:
        if (window_start is None) != (window_end is None):
            raise ValueError("window_start and window_end must be provided together")
        if window_start is not None and window_end is not None:
            start = _utc(window_start)
            end = _utc(window_end)
            if start >= end:
                raise ValueError("window_start must be before window_end")
            # Explicit dashboard/eval windows can be dense even at seven days.
            # Walk adjacent newest-first six-hour slices so one picker probe
            # cannot turn the entire requested range into one physical scan.
            windows: list[tuple[datetime, datetime]] = []
            segment_end = end
            while segment_end > start:
                segment_start = max(
                    start, segment_end - ATTRIBUTE_READ_EXPLICIT_SEGMENT
                )
                windows.append((segment_start, segment_end))
                segment_end = segment_start
            return _prioritize_explicit_attribute_windows(windows)
        return adaptive_attribute_windows(
            self._window_end,
            horizon_days=horizon_days,
        )

    @staticmethod
    def _project_ids(project_ids: Iterable[Any]) -> tuple[str, ...]:
        projects: list[str] = []
        for project_id in project_ids:
            if not project_id:
                continue
            try:
                canonical = str(uuid.UUID(str(project_id)))
            except (TypeError, ValueError, AttributeError) as exc:
                raise IncompleteLatestStateReplay(
                    "Attribute read received an invalid project identity"
                ) from exc
            if canonical not in projects:
                projects.append(canonical)
            if len(projects) > ATTRIBUTE_READ_MAX_PROJECTS:
                raise IncompleteLatestStateReplay(
                    "Attribute read project scope exceeds its hard cap"
                )
        return tuple(projects)

    @staticmethod
    def _candidate_pair_predicate(
        candidate_ids: tuple[PhysicalSpanIdentity, ...],
    ) -> tuple[str, dict[str, Any]]:
        """Compile a finite, fully parameterized physical replay predicate.

        A direct-write span is identified by project, trace, id and start time;
        span ids are only trace-unique. Keeping raw ``id``/``trace_id`` IN
        predicates alongside exact partition dates and an integer-microsecond
        tuple lets ClickHouse use both bloom indexes and partition pruning,
        without allowing a tombstone from another physical span to win. Integer
        microseconds avoid clickhouse-driver truncating DateTime64 values inside
        tuple parameters. Only generated parameter names enter SQL; values
        remain driver-bound.
        """

        identities_by_project: dict[str, list[tuple[str, str, datetime]]] = defaultdict(
            list
        )
        for project_id, trace_id, candidate_id, start_time in candidate_ids:
            identities_by_project[project_id].append(
                (trace_id, candidate_id, start_time)
            )

        clauses: list[str] = []
        params: dict[str, Any] = {}
        for index, (project_id, identities) in enumerate(identities_by_project.items()):
            project_param = f"candidate_project_{index}"
            ids_param = f"candidate_ids_{index}"
            trace_ids_param = f"candidate_trace_ids_{index}"
            dates_param = f"candidate_dates_{index}"
            identities_param = f"candidate_physical_identities_{index}"
            span_ids = tuple(dict.fromkeys(item[1] for item in identities))
            trace_ids = tuple(dict.fromkeys(item[0] for item in identities))
            dates = tuple(dict.fromkeys(item[2].date() for item in identities))
            encoded_identities = tuple(
                (trace_id, span_id, _unix_microseconds(start_time))
                for trace_id, span_id, start_time in identities
            )
            clauses.append(
                f"(project_id = toUUID(%({project_param})s) "
                f"AND id IN %({ids_param})s "
                f"AND trace_id IN %({trace_ids_param})s "
                f"AND toDate(start_time) IN %({dates_param})s "
                "AND (trace_id, id, toUnixTimestamp64Micro(start_time)) "
                f"IN %({identities_param})s)"
            )
            params[project_param] = project_id
            params[ids_param] = span_ids
            params[trace_ids_param] = trace_ids
            params[dates_param] = dates
            params[identities_param] = encoded_identities
        if not clauses:
            raise IncompleteLatestStateReplay(
                "Attribute latest-state replay had no candidate identities"
            )
        return " OR ".join(clauses), params

    @staticmethod
    def _single_project_scope_sql(
        sql: str,
        project_ids: tuple[str, ...],
        params: dict[str, Any],
    ) -> str:
        """Avoid CH25 Set/index planning for the normal one-project API case."""

        if len(project_ids) != 1:
            return sql
        params["scope_project_id"] = project_ids[0]
        return sql.replace(
            "project_id IN %(project_ids)s",
            "attribute_source.project_id = toUUID(%(scope_project_id)s)",
        )

    def _candidate_ids(
        self,
        project_ids: tuple[str, ...],
        segment: tuple[datetime, datetime],
        *,
        predicate: str,
        attribute_key: str | None,
        stratified: bool = False,
        ordered: bool = False,
        before_identity: PhysicalSpanIdentity | None = None,
        candidate_limit: int,
        query_timeout_ms: int | None = None,
        candidate_query_settings: dict[str, Any] | None = None,
        include_versions: bool = False,
        predicate_params: dict[str, Any] | None = None,
    ) -> tuple[
        tuple[PhysicalSpanIdentity, ...],
        bool,
        dict[PhysicalSpanIdentity, int],
    ]:
        segment_start, segment_end = segment
        params: dict[str, Any] = {
            "project_ids": project_ids,
            "segment_start": segment_start,
            "segment_end": segment_end,
            # clickhouse-driver may bind an untyped datetime parameter at
            # second precision.  A keyset checkpoint can be only a few
            # microseconds below a segment boundary; allowing that row into
            # the current segment makes the next signed cursor fail its own
            # exact boundary invariant.  Integer DateTime64 bounds preserve
            # adjacent half-open segments without wrapping the indexed column.
            "segment_start_us": _unix_microseconds(segment_start),
            "segment_end_us": _unix_microseconds(segment_end),
            "candidate_limit": candidate_limit + 1,
        }
        if attribute_key is not None:
            params["attribute_key"] = attribute_key
        if predicate_params:
            reserved = set(params).intersection(predicate_params)
            if reserved:
                raise ValueError("attribute candidate predicate parameter collision")
            params.update(predicate_params)
        ordered = ordered or before_identity is not None
        candidate_sql = _ORDERED_CANDIDATE_SQL if ordered else _CANDIDATE_SQL
        query_settings = dict(candidate_query_settings or {})
        if include_versions:
            if stratified:
                raise ValueError("Versioned attribute candidates cannot be stratified")
            candidate_sql = (
                _ORDERED_TYPED_VALUE_CANDIDATE_SQL
                if ordered
                else _TYPED_VALUE_CANDIDATE_SQL
            )
        if stratified:
            candidate_sql = (
                _ORDERED_STRATIFIED_CANDIDATE_SQL
                if ordered
                else _STRATIFIED_CANDIDATE_SQL
            )
            # Generic inventory predicates cannot use the Map-key bloom
            # indexes.  Disabling skip-index planning avoids building useless
            # index conditions; primary-key and partition pruning remain on.
            query_settings["use_skip_indexes"] = 0
        if not ordered:
            # The first finite probe is aligned exactly with the MergeTree
            # sorting-key prefix, so CH25 can stop without a global sort.
            query_settings["optimize_read_in_order"] = 1
        if before_identity is not None:
            before_project_id, before_trace_id, before_id, before_start_time = (
                before_identity
            )
            if (
                before_project_id not in project_ids
                or not segment_start <= before_start_time < segment_end
            ):
                raise ValueError("candidate keyset must stay inside its segment")
            params.update(
                {
                    "candidate_before_start_us": _unix_microseconds(before_start_time),
                    "candidate_before_id": before_id,
                    "candidate_before_trace_id": before_trace_id,
                    "candidate_before_project_id": before_project_id,
                }
            )
            predicate = (
                f"({predicate}) AND "
                "(toUnixTimestamp64Micro(start_time) "
                "< %(candidate_before_start_us)s "
                "OR (toUnixTimestamp64Micro(start_time) "
                "= %(candidate_before_start_us)s AND "
                "(id < %(candidate_before_id)s "
                "OR (id = %(candidate_before_id)s AND "
                "(trace_id < %(candidate_before_trace_id)s "
                "OR (trace_id = %(candidate_before_trace_id)s AND "
                "toString(attribute_source.project_id) "
                "< %(candidate_before_project_id)s))))))"
            )
        candidate_sql = self._single_project_scope_sql(
            candidate_sql, project_ids, params
        )
        rows = self._execute(
            candidate_sql.format(candidate_predicate=predicate),
            params,
            max_result_rows=candidate_limit + 1,
            query_settings=query_settings,
            query_timeout_ms=query_timeout_ms,
        )
        truncated = len(rows) > candidate_limit
        identities: list[PhysicalSpanIdentity] = []
        seen: set[PhysicalSpanIdentity] = set()
        versions: dict[PhysicalSpanIdentity, int] = {}
        for row in rows[:candidate_limit]:
            candidate_project_id = str(row.get("project_id") or "")
            candidate_trace_id = str(row.get("trace_id") or "")
            candidate_id = str(row.get("id") or "")
            candidate_start_time = row.get("start_time")
            if (
                not candidate_project_id
                or not candidate_id
                or not isinstance(candidate_start_time, datetime)
            ):
                raise IncompleteLatestStateReplay(
                    "Attribute candidate query returned an invalid identity"
                )
            identity = (
                candidate_project_id,
                candidate_trace_id,
                candidate_id,
                _utc(candidate_start_time),
            )
            if identity not in seen:
                seen.add(identity)
                identities.append(identity)
            if include_versions:
                try:
                    candidate_version = int(row["candidate_version"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise IncompleteLatestStateReplay(
                        "Attribute candidate query omitted its row version"
                    ) from exc
                if candidate_version < 0:
                    raise IncompleteLatestStateReplay(
                        "Attribute candidate query returned an invalid row version"
                    )
                prior_version = versions.get(identity)
                if prior_version is None or candidate_version > prior_version:
                    versions[identity] = candidate_version
        return tuple(identities), truncated, versions

    def _seen_value_slice_groups(
        self,
        *,
        project_ids: tuple[str, ...],
        attribute_key: str,
        attribute_type: AttributeType | None,
        search: str,
        candidate_predicates: dict[str, str],
        candidate_predicate_params: dict[str, Any],
        segment: tuple[datetime, datetime],
        before_identity: PhysicalSpanIdentity | None,
        distinct_limit: int,
        query_timeout_ms: int = ATTRIBUTE_VALUE_CURSOR_DISTINCT_TIMEOUT_MS,
    ) -> list[dict[str, Any]]:
        """Return a complete finite raw-value vocabulary superset for one slice.

        This is a speculative continuation accelerator, not a sampled read.
        SQL returns a complete distinct superset of raw physical values. Every
        current live value must occur in that set; stale/tombstoned values only
        cause conservative fallback. Search is deliberately rechecked in
        Python because ClickHouse case folding, Float64 formatting, and raw
        JSON escaping do not equal the public canonical search contract. A
        result at the sentinel limit is discarded rather than interpreted as
        complete.
        """

        segment_start, segment_end = segment
        if not segment_start < segment_end:
            raise ValueError("invalid filter-value distinct segment")
        distinct_limit = int(distinct_limit)
        if not 2 <= distinct_limit <= ATTRIBUTE_VALUE_CURSOR_PROOF_MAX_RESULT_ROWS:
            raise ValueError("invalid filter-value distinct result limit")
        if not candidate_predicates:
            raise ValueError("distinct proof has no value lanes")
        params: dict[str, Any] = {
            "project_ids": project_ids,
            "attribute_key": attribute_key,
            "distinct_attribute_type": attribute_type or "",
            "distinct_attribute_search": search,
            "segment_start_us": _unix_microseconds(segment_start),
            "segment_end_us": _unix_microseconds(segment_end),
            "distinct_limit": distinct_limit,
        }
        reserved = set(params).intersection(candidate_predicate_params)
        if reserved:
            raise ValueError("distinct candidate predicate parameter collision")
        params.update(candidate_predicate_params)

        raw_frontier_predicate = "1"
        if before_identity is not None:
            before_project_id, before_trace_id, before_id, before_start_time = (
                before_identity
            )
            if (
                before_project_id not in project_ids
                or not segment_start <= before_start_time < segment_end
            ):
                raise ValueError("distinct keyset must stay inside its segment")
            params.update(
                {
                    "distinct_before_start_us": _unix_microseconds(before_start_time),
                    "distinct_before_id": before_id,
                    "distinct_before_trace_id": before_trace_id,
                    "distinct_before_project_id": before_project_id,
                }
            )
            raw_frontier_predicate = (
                "toUnixTimestamp64Micro(raw_source.start_time) "
                "< %(distinct_before_start_us)s "
                "OR (toUnixTimestamp64Micro(raw_source.start_time) "
                "= %(distinct_before_start_us)s AND "
                "(raw_source.id < %(distinct_before_id)s "
                "OR (raw_source.id = %(distinct_before_id)s AND "
                "(raw_source.trace_id < %(distinct_before_trace_id)s "
                "OR (raw_source.trace_id = %(distinct_before_trace_id)s AND "
                "toString(raw_source.project_id) "
                "< %(distinct_before_project_id)s)))))"
            )

        if len(project_ids) == 1:
            params["scope_project_id"] = project_ids[0]
            raw_scope_predicate = "raw_source.project_id = toUUID(%(scope_project_id)s)"
        else:
            raw_scope_predicate = "raw_source.project_id IN %(project_ids)s"

        lane_specs = {
            "string": (
                "attrs_string[%(attribute_key)s]",
                "toFloat64(0)",
                "toUInt8(0)",
                "''",
                "mapContains(attrs_string, %(attribute_key)s)",
            ),
            "number": (
                "''",
                "attrs_number[%(attribute_key)s]",
                "toUInt8(0)",
                "''",
                "mapContains(attrs_number, %(attribute_key)s)",
            ),
            "boolean": (
                "''",
                "toFloat64(0)",
                "attrs_bool[%(attribute_key)s]",
                "''",
                "mapContains(attrs_bool, %(attribute_key)s)",
            ),
            "json": (
                "''",
                "toFloat64(0)",
                "toUInt8(0)",
                "JSONExtractRaw(attributes_extra, %(attribute_key)s)",
                "attributes_extra NOT IN ('', '{}', 'null') "
                "AND JSONHas(attributes_extra, %(attribute_key)s)",
            ),
        }
        raw_value_lanes = []
        single_lane_parts: tuple[str, str, str, str, str, str] | None = None
        for value_type, relevance_predicate in candidate_predicates.items():
            try:
                (
                    value_string,
                    value_number,
                    value_boolean,
                    value_json_raw,
                    presence_predicate,
                ) = lane_specs[value_type]
            except KeyError as exc:
                raise ValueError("invalid distinct proof value lane") from exc
            raw_value_lanes.append(
                "tuple("
                f"'{value_type}', {value_string}, {value_number}, "
                f"{value_boolean}, {value_json_raw}, "
                f"toUInt8({presence_predicate}), "
                f"toUInt8({relevance_predicate}))"
            )
            single_lane_parts = (
                value_type,
                value_string,
                value_number,
                value_boolean,
                value_json_raw,
                relevance_predicate,
            )
        if len(raw_value_lanes) == 1:
            assert single_lane_parts is not None
            (
                value_type,
                value_string,
                value_number,
                value_boolean,
                value_json_raw,
                relevance_predicate,
            ) = single_lane_parts
            sql = _VALUE_CURSOR_SEARCHED_RAW_SINGLE_LANE_SQL.format(
                value_type=value_type,
                value_string=value_string,
                value_number=value_number,
                value_boolean=value_boolean,
                value_json_raw=value_json_raw,
                raw_scope_predicate=raw_scope_predicate,
                raw_frontier_predicate=raw_frontier_predicate,
                raw_candidate_predicate=relevance_predicate,
            )
        else:
            sql = _VALUE_CURSOR_SEARCHED_RAW_SUPERSET_SQL.format(
                raw_value_lanes=", ".join(raw_value_lanes),
                raw_scope_predicate=raw_scope_predicate,
                raw_frontier_predicate=raw_frontier_predicate,
                raw_any_candidate_predicate=" OR ".join(
                    f"({predicate})" for predicate in candidate_predicates.values()
                ),
            )
        return self._execute(
            sql,
            params,
            max_result_rows=distinct_limit,
            query_timeout_ms=query_timeout_ms,
            # Do not set ``max_rows_in_distinct`` here. ClickHouse checks that
            # restriction before this query's SQL LIMIT can stop the DISTINCT
            # transform, causing Code 191 at a safe overflow sentinel. The SQL
            # LIMIT and max_result_rows cap output; independent read, byte,
            # memory, statement, and wall limits still bound all work.
            query_settings=_ATTRIBUTE_VALUE_PROOF_MAP_SETTINGS,
        )

    def _verify_latest(
        self,
        *,
        sql: str,
        project_ids: tuple[str, ...],
        candidate_ids: tuple[PhysicalSpanIdentity, ...],
        attribute_key: str | None = None,
        query_timeout_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        if not candidate_ids:
            return []
        candidate_predicate, candidate_params = self._candidate_pair_predicate(
            candidate_ids
        )
        params: dict[str, Any] = {
            "project_ids": project_ids,
            **candidate_params,
        }
        if attribute_key is not None:
            params["attribute_key"] = attribute_key
        replay_sql = self._single_project_scope_sql(
            sql.format(candidate_predicate=candidate_predicate),
            project_ids,
            params,
        )
        rows = self._execute(
            replay_sql,
            params,
            max_result_rows=len(candidate_ids),
            query_timeout_ms=query_timeout_ms,
        )
        returned_ids = {self._physical_identity(row) for row in rows}
        if returned_ids != set(candidate_ids):
            raise IncompleteLatestStateReplay(
                "Attribute candidate latest-state replay was incomplete"
            )
        return rows

    def _verify_latest_typed_values(
        self,
        *,
        project_ids: tuple[str, ...],
        candidate_ids: tuple[PhysicalSpanIdentity, ...],
        candidate_versions: dict[PhysicalSpanIdentity, int],
        attribute_key: str,
        query_timeout_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """Hydrate only key-bearing candidate rows certified latest and live."""

        if set(candidate_versions) != set(candidate_ids):
            raise IncompleteLatestStateReplay(
                "Attribute value candidates had incomplete version certificates"
            )
        version_rows = self._verify_latest(
            sql=_LATEST_TYPED_VERSION_SQL,
            project_ids=project_ids,
            candidate_ids=candidate_ids,
            query_timeout_ms=query_timeout_ms,
        )
        version_by_identity = {
            self._physical_identity(row): row for row in version_rows
        }
        active_ids = tuple(
            identity
            for identity in candidate_ids
            if int(version_by_identity[identity].get("is_deleted") or 0) == 0
            and int(version_by_identity[identity].get("latest_version", -1))
            == candidate_versions[identity]
        )
        if not active_ids:
            return []
        hydrated_rows = self._verify_latest(
            sql=_LATEST_TYPED_TARGET_SQL,
            project_ids=project_ids,
            candidate_ids=active_ids,
            attribute_key=attribute_key,
            query_timeout_ms=query_timeout_ms,
        )
        hydrated_by_identity = {
            self._physical_identity(row): row for row in hydrated_rows
        }
        return [hydrated_by_identity[identity] for identity in active_ids]

    @staticmethod
    def _typed_target_has_key(row: dict[str, Any]) -> bool:
        """Return whether latest state stores the key in any typed Map."""

        return any(
            bool(row.get(field))
            for field in ("string_present", "number_present", "boolean_present")
        )

    def _hydrate_latest_unpinned_values(
        self,
        *,
        project_ids: tuple[str, ...],
        candidate_ids: tuple[PhysicalSpanIdentity, ...],
        attribute_key: str,
        query_timeout_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """Hydrate typed state first and JSON only for typed-absent identities."""

        if not candidate_ids:
            return []
        typed_rows = self._verify_latest(
            sql=_LATEST_TYPED_TARGET_SQL,
            project_ids=project_ids,
            candidate_ids=candidate_ids,
            attribute_key=attribute_key,
            query_timeout_ms=query_timeout_ms,
        )
        typed_by_identity = {self._physical_identity(row): row for row in typed_rows}
        json_ids = tuple(
            identity
            for identity in candidate_ids
            if int(typed_by_identity[identity].get("is_deleted") or 0) == 0
            and not self._typed_target_has_key(typed_by_identity[identity])
        )
        if not json_ids:
            return [typed_by_identity[identity] for identity in candidate_ids]
        json_rows = self._verify_latest(
            sql=_LATEST_JSON_TARGET_SQL,
            project_ids=project_ids,
            candidate_ids=json_ids,
            attribute_key=attribute_key,
            query_timeout_ms=query_timeout_ms,
        )
        for json_row in json_rows:
            identity = self._physical_identity(json_row)
            typed_row = typed_by_identity[identity]
            typed_by_identity[identity] = {
                **typed_row,
                "is_deleted": json_row.get("is_deleted"),
                "legacy_present": json_row.get("legacy_present"),
                "legacy_value_raw": json_row.get("legacy_value_raw"),
                "legacy_value_fingerprint": json_row.get("legacy_value_fingerprint"),
            }
        return [typed_by_identity[identity] for identity in candidate_ids]

    def _verify_latest_unpinned_values(
        self,
        *,
        project_ids: tuple[str, ...],
        candidate_ids: tuple[PhysicalSpanIdentity, ...],
        candidate_versions: dict[PhysicalSpanIdentity, int],
        attribute_key: str,
        query_timeout_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """Certify an unpinned mixed-type page, then hydrate narrow lanes."""

        if set(candidate_versions) != set(candidate_ids):
            raise IncompleteLatestStateReplay(
                "Attribute value candidates had incomplete version certificates"
            )
        version_rows = self._verify_latest(
            sql=_LATEST_TYPED_VERSION_SQL,
            project_ids=project_ids,
            candidate_ids=candidate_ids,
            query_timeout_ms=query_timeout_ms,
        )
        version_by_identity = {
            self._physical_identity(row): row for row in version_rows
        }
        active_ids = tuple(
            identity
            for identity in candidate_ids
            if int(version_by_identity[identity].get("is_deleted") or 0) == 0
            and int(version_by_identity[identity].get("latest_version", -1))
            == candidate_versions[identity]
        )
        return self._hydrate_latest_unpinned_values(
            project_ids=project_ids,
            candidate_ids=active_ids,
            attribute_key=attribute_key,
            query_timeout_ms=query_timeout_ms,
        )

    @staticmethod
    def _physical_identity(row: dict[str, Any]) -> PhysicalSpanIdentity:
        """Return the immutable identity used by direct-write span readers."""

        project_id = str(row.get("project_id") or "")
        trace_id = str(row.get("trace_id") or "")
        span_id = str(row.get("id") or "")
        start_time = row.get("start_time")
        if not project_id or not span_id or not isinstance(start_time, datetime):
            raise IncompleteLatestStateReplay(
                "Attribute latest-state replay omitted physical identity"
            )
        return project_id, trace_id, span_id, _utc(start_time)

    @staticmethod
    def _row_is_active_in_window(
        row: dict[str, Any],
        window_start: datetime,
        window_end: datetime,
    ) -> bool:
        start_time = row.get("start_time")
        if not isinstance(start_time, datetime):
            raise IncompleteLatestStateReplay(
                "Attribute latest-state replay omitted start_time"
            )
        return (
            int(row.get("is_deleted") or 0) == 0
            and window_start <= _utc(start_time) < window_end
        )

    @staticmethod
    def _decode_legacy_scalar(
        raw: Any, *, json_encoded: bool = True
    ) -> tuple[AttributeType, Any] | None:
        if raw in (None, ""):
            return None
        try:
            value = json.loads(raw) if json_encoded and isinstance(raw, str) else raw
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if isinstance(value, str):
            return "string", value
        if isinstance(value, bool):
            return "boolean", value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric = float(value)
            if math.isfinite(numeric):
                return "number", numeric
        return None

    @classmethod
    def _decode_json_attribute_bounded(
        cls,
        raw: Any,
        *,
        mode: JsonAttributeMode,
        json_encoded: bool = True,
    ) -> tuple[tuple[AttributeType, Any] | None, bool]:
        """Decode a compatibility sample and report whether it was complete.

        ``arrays`` is the value-picker contract. ``structured`` is the
        filter-key contract and adds finite flat JSON objects (``map``) while
        still excluding JSON-only scalars. ``all`` is reserved for eval
        mapping, where an object/null key is a valid field path even though it
        is not necessarily filterable. Array members are reduced to the exact
        finite JSON scalar vocabulary accepted by the public serializer.
        """

        if mode == "none" or raw == "" or (raw is None and json_encoded):
            return None, True
        try:
            value = json.loads(raw) if json_encoded and isinstance(raw, str) else raw
        except (TypeError, ValueError, json.JSONDecodeError):
            return None, True

        if mode in {"scalars", "all"}:
            scalar = cls._decode_legacy_scalar(value, json_encoded=False)
            if scalar is not None:
                return scalar, True
        if mode in {"arrays", "structured", "all"} and isinstance(value, list):
            members: list[JsonScalar] = []
            seen: set[tuple[str, str]] = set()
            total_string_bytes = 0
            complete = True
            for member in value:
                if member is None or member == "":
                    continue
                if isinstance(member, bool):
                    canonical = ("boolean", "true" if member else "false")
                elif isinstance(member, str):
                    member_bytes = len(member.encode("utf-8"))
                    if member_bytes > JSON_ARRAY_FILTER_MAX_STRING_UTF8_BYTES:
                        continue
                    if (
                        total_string_bytes + member_bytes
                        > JSON_ARRAY_FILTER_MAX_TOTAL_STRING_UTF8_BYTES
                    ):
                        # Compatibility reads remain intentionally finite, but
                        # the cursor proof must not treat this omission as an
                        # exact vocabulary. Cursor replay pages this boundary.
                        complete = False
                        continue
                    total_string_bytes += member_bytes
                    canonical = (
                        "string",
                        json.dumps(member, ensure_ascii=False, separators=(",", ":")),
                    )
                elif isinstance(member, int):
                    if not (-(1 << 63) <= member <= (1 << 64) - 1):
                        continue
                    canonical = ("integer", str(member))
                elif isinstance(member, float) and math.isfinite(member):
                    canonical = (
                        "number",
                        json.dumps(member, allow_nan=False, separators=(",", ":")),
                    )
                else:
                    # Nested arrays/objects are deliberately not selectable;
                    # the backend rejects them instead of relying on JSON
                    # serialization order.
                    continue
                if canonical not in seen:
                    seen.add(canonical)
                    members.append(member)
                    if len(members) > ATTRIBUTE_READ_MAX_VALUES:
                        complete = False
                        break
            return ("array", tuple(members)), complete
        if mode == "structured" and isinstance(value, dict):
            # Key discovery consumes only the type.  Do not retain or copy the
            # object here: the filter serializer independently enforces the
            # finite, flat, scalar-only public map contract on user input.
            return ("map", None), True
        if mode == "all":
            # Eval mapping only consumes the key/type, never this value.  A
            # single sentinel keeps null/object keys discoverable without
            # copying their potentially large structure into Python state.
            return ("json", None), True
        return None, True

    @classmethod
    def _decode_json_attribute(
        cls,
        raw: Any,
        *,
        mode: JsonAttributeMode,
        json_encoded: bool = True,
    ) -> tuple[AttributeType, Any] | None:
        """Decode only JSON value families the caller can faithfully use.

        Non-cursor compatibility consumers preserve their historical finite
        sample. Exact cursor callers use the incremental raw-array path below.
        """

        decoded, _complete = cls._decode_json_attribute_bounded(
            raw,
            mode=mode,
            json_encoded=json_encoded,
        )
        return decoded

    @staticmethod
    def _json_array_cursor_text(raw: Any) -> tuple[str, int] | None:
        """Return raw array text and the first member position without loading it."""

        if isinstance(raw, str):
            text = raw
        elif isinstance(raw, (list, tuple)):
            # Native ClickHouse reads return JSONExtractRaw strings. This
            # compatibility arm keeps lightweight/mock executors exact.
            text = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        else:
            return None
        position = 0
        while position < len(text) and text[position] in " \t\r\n":
            position += 1
        if position >= len(text) or text[position] != "[":
            return None
        return text, position + 1

    @staticmethod
    def _json_array_cursor_fingerprint(row: dict[str, Any], text: str) -> str:
        """Return the strong raw-value hash produced by the latest-state query."""

        supplied = str(row.get("legacy_value_fingerprint") or "").lower()
        if len(supplied) == 64 and all(
            character in "0123456789abcdef" for character in supplied
        ):
            return supplied
        # Production SQL always supplies SHA256 beside JSONExtractRaw. The
        # fallback is for compatible custom/test executors only.
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _encode_json_array_member_cursor(
        fingerprint: str,
        state: _JsonArrayCursorState,
    ) -> int:
        """Pack one lexer checkpoint into the signed cursor's integer slot."""

        if (
            len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
            or not 0 < state.position < (1 << _JSON_ARRAY_CURSOR_POSITION_BITS)
            or not 0 <= state.mode < (1 << _JSON_ARRAY_CURSOR_MODE_BITS)
            or not 0 <= state.auxiliary < (1 << _JSON_ARRAY_CURSOR_AUX_BITS)
        ):
            raise ValueError("invalid filter-value JSON member cursor")
        if state.mode == _JSON_ARRAY_CURSOR_MODE_BOUNDARY and state.auxiliary != 0:
            raise ValueError("invalid filter-value JSON member cursor")
        if state.mode == _JSON_ARRAY_CURSOR_MODE_STRING and state.auxiliary not in {
            0,
            1,
        }:
            raise ValueError("invalid filter-value JSON member cursor")
        if state.mode == _JSON_ARRAY_CURSOR_MODE_NESTED:
            depth = state.auxiliary & (_JSON_ARRAY_CURSOR_NESTED_IN_STRING - 1)
            if depth < 1:
                raise ValueError("invalid filter-value JSON member cursor")
        if state.mode == _JSON_ARRAY_CURSOR_MODE_PRIMITIVE and state.auxiliary != 0:
            raise ValueError("invalid filter-value JSON member cursor")

        header = (_JSON_ARRAY_CURSOR_PREFIX << 4) | _JSON_ARRAY_CURSOR_VERSION
        packed = header
        packed = (packed << _JSON_ARRAY_CURSOR_FINGERPRINT_BITS) | int(fingerprint, 16)
        packed = (packed << _JSON_ARRAY_CURSOR_MODE_BITS) | state.mode
        packed = (packed << _JSON_ARRAY_CURSOR_AUX_BITS) | state.auxiliary
        return (packed << _JSON_ARRAY_CURSOR_POSITION_BITS) | state.position

    @staticmethod
    def _decode_json_array_member_cursor(
        offset: int,
        *,
        fingerprint: str,
        initial_position: int,
        text_length: int,
    ) -> _JsonArrayCursorState:
        """Validate a packed checkpoint; safely restart rolling legacy state."""

        raw_offset = int(offset)
        if raw_offset < 0:
            raise ValueError("invalid filter-value JSON member cursor")
        if raw_offset <= ATTRIBUTE_READ_MAX_VALUES + 1:
            # Old cursors counted members in the bounded decoded tuple. A raw
            # character checkpoint cannot reconstruct that position without an
            # unbounded prefix replay, so restart the same latest array. The
            # authenticated exact seen set suppresses every published member.
            return _JsonArrayCursorState(initial_position)

        position_mask = (1 << _JSON_ARRAY_CURSOR_POSITION_BITS) - 1
        auxiliary_mask = (1 << _JSON_ARRAY_CURSOR_AUX_BITS) - 1
        fingerprint_mask = (1 << _JSON_ARRAY_CURSOR_FINGERPRINT_BITS) - 1
        position = raw_offset & position_mask
        packed = raw_offset >> _JSON_ARRAY_CURSOR_POSITION_BITS
        auxiliary = packed & auxiliary_mask
        packed >>= _JSON_ARRAY_CURSOR_AUX_BITS
        mode = packed & ((1 << _JSON_ARRAY_CURSOR_MODE_BITS) - 1)
        packed >>= _JSON_ARRAY_CURSOR_MODE_BITS
        encoded_fingerprint = packed & fingerprint_mask
        header = packed >> _JSON_ARRAY_CURSOR_FINGERPRINT_BITS
        expected_header = (_JSON_ARRAY_CURSOR_PREFIX << 4) | _JSON_ARRAY_CURSOR_VERSION
        if header != expected_header:
            raise ValueError("invalid filter-value JSON member cursor")

        state = _JsonArrayCursorState(position, mode, auxiliary)
        # Reuse the encoder's state-shape validation without trusting the
        # caller-provided integer merely because its header happens to match.
        AttributeReadSelector._encode_json_array_member_cursor(
            f"{encoded_fingerprint:064x}", state
        )
        if encoded_fingerprint != int(fingerprint, 16):
            # Latest state changed between pages. Restarting can repeat work but
            # can never skip a newly inserted/reordered member; exact seen-state
            # membership removes already published values.
            return _JsonArrayCursorState(initial_position)
        if not initial_position <= position < text_length:
            raise ValueError("invalid filter-value JSON member cursor")
        return state

    @classmethod
    def _decode_target_value(
        cls,
        row: dict[str, Any],
        *,
        json_attribute_mode: JsonAttributeMode = "scalars",
    ) -> tuple[AttributeType, Any] | None:
        """Apply stable typed-Map precedence, then the requested JSON tier."""

        if bool(row.get("string_present")):
            value = row.get("string_value")
            return ("string", str(value)) if value is not None else None
        if bool(row.get("number_present")):
            value = row.get("number_value")
            if value is None:
                return None
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                return None
            return ("number", numeric) if math.isfinite(numeric) else None
        if bool(row.get("boolean_present")):
            value = row.get("boolean_value")
            return ("boolean", bool(value)) if value is not None else None
        if json_attribute_mode != "none" and bool(row.get("legacy_present")):
            return cls._decode_json_attribute(
                row.get("legacy_value_raw"),
                mode=json_attribute_mode,
            )
        return None

    @classmethod
    def _decode_seen_value_slice_group(
        cls,
        row: dict[str, Any],
        *,
        json_attribute_mode: JsonAttributeMode,
    ) -> tuple[tuple[AttributeType, Any] | None, bool]:
        """Decode one tagged group from the temporal distinct proof."""

        value_type = str(row.get("value_type") or "")
        if value_type not in {"string", "number", "boolean", "json"}:
            raise IncompleteLatestStateReplay(
                "Attribute distinct proof returned an invalid value type"
            )
        try:
            value_count = int(row["value_count"])
        except (KeyError, TypeError, ValueError) as exc:
            raise IncompleteLatestStateReplay(
                "Attribute distinct proof omitted its value count"
            ) from exc
        if value_count < 1:
            raise IncompleteLatestStateReplay(
                "Attribute distinct proof returned an invalid value count"
            )
        if value_type == "json":
            raw_json = row.get("value_json_raw")
            if json_attribute_mode == "arrays" and isinstance(raw_json, str):
                position = 0
                while position < len(raw_json) and raw_json[position] in " \t\r\n":
                    position += 1
                if position >= len(raw_json) or raw_json[position] != "[":
                    # JSON scalars/objects are not selectable in array-filter
                    # mode. Avoid materializing a potentially large object just
                    # to rediscover that type fact.
                    return None, True
                if len(raw_json) - position > (
                    ATTRIBUTE_VALUE_CURSOR_JSON_MAX_SCAN_CHARS
                ):
                    # A proof may only advance when it names the complete raw
                    # vocabulary. Defer a large array to incremental latest-row
                    # replay instead of json.loads materializing it here.
                    return ("array", ()), False
            return cls._decode_json_attribute_bounded(
                raw_json,
                mode=json_attribute_mode,
            )
        return (
            cls._decode_target_value(
                {
                    "string_present": value_type == "string",
                    "string_value": row.get("value_string"),
                    "number_present": value_type == "number",
                    "number_value": row.get("value_number"),
                    "boolean_present": value_type == "boolean",
                    "boolean_value": row.get("value_boolean"),
                    "legacy_present": False,
                },
                json_attribute_mode=json_attribute_mode,
            ),
            True,
        )

    @classmethod
    def _browse_row_keys(
        cls,
        row: dict[str, Any],
        *,
        json_attribute_mode: JsonAttributeMode = "scalars",
    ) -> tuple[dict[str, AttributeType], bool]:
        """Return keys supported by the caller's explicit JSON contract.

        Typed Maps always win when the same key also appears in overflow JSON.
        Legacy scalar mode retains its historical degraded signal when it sees
        a structured value; array-filter and eval-mapping modes intentionally
        define which additional JSON families are actionable.
        """
        keys: dict[str, AttributeType] = {}
        unsupported_value_seen = False
        for attr_type, field_name in (
            ("string", "string_keys"),
            ("number", "number_keys"),
            ("boolean", "boolean_keys"),
        ):
            raw_keys = row.get(field_name) or []
            if not isinstance(raw_keys, (tuple, list)):
                raise IncompleteLatestStateReplay(
                    "Attribute latest-state replay returned invalid Map keys"
                )
            for raw_key in raw_keys:
                key = str(raw_key)
                if key and key not in keys:
                    keys[key] = attr_type

        raw_extra = (
            row.get("attributes_extra") if json_attribute_mode != "none" else None
        )
        if raw_extra not in (None, "", "{}"):
            try:
                extra = (
                    json.loads(raw_extra) if isinstance(raw_extra, str) else raw_extra
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                extra = {}
            if isinstance(extra, dict):
                for raw_key, raw_value in extra.items():
                    key = str(raw_key)
                    if not key or key in keys:
                        continue
                    decoded = cls._decode_json_attribute(
                        raw_value,
                        mode=json_attribute_mode,
                        json_encoded=False,
                    )
                    if decoded is not None:
                        keys[key] = decoded[0]
                    elif json_attribute_mode == "scalars":
                        unsupported_value_seen = True
        return keys, unsupported_value_seen

    @classmethod
    def _target_value_is_unsupported(
        cls,
        row: dict[str, Any],
        *,
        json_attribute_mode: JsonAttributeMode,
    ) -> bool:
        """Whether the selected JSON contract saw a value it cannot type."""

        if any(
            bool(row.get(field))
            for field in ("string_present", "number_present", "boolean_present")
        ):
            return False
        if not bool(row.get("legacy_present")):
            return False
        decoded = cls._decode_json_attribute(
            row.get("legacy_value_raw"),
            mode=json_attribute_mode,
        )
        # ``arrays`` intentionally excludes scalar/object overflow values from
        # the filterable-key contract.  Only legacy scalar mode retains the
        # historical degraded signal for omitted structured values.
        return decoded is None and json_attribute_mode == "scalars"

    @staticmethod
    def _metadata(
        *,
        complete: bool,
        error_code: str | None,
        sampled: bool = False,
        window_start: datetime,
        window_end: datetime,
        query_count: int,
    ) -> AttributeReadMetadata:
        query_status: QueryStatus = "complete"
        if not complete:
            query_status = (
                "sampled" if sampled and error_code == "sample_limit" else "degraded"
            )
        return AttributeReadMetadata(
            query_complete=complete,
            query_status=query_status,
            query_error_code=error_code,
            query_window_start=window_start,
            query_window_end=window_end,
            query_count=query_count,
        )

    def discover_keys(
        self,
        project_ids: Iterable[Any],
        *,
        exact_key: str | None = None,
        horizon_days: int = 365,
        max_keys: int = ATTRIBUTE_READ_MAX_KEYS,
        order_by_count_desc: bool = False,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> AttributeKeyRead:
        self._begin_operation()
        projects = self._project_ids(project_ids)
        if exact_key is not None:
            exact_key = validate_attribute_key(exact_key)
        max_keys = min(max(int(max_keys), 1), ATTRIBUTE_READ_MAX_KEYS)
        windows = self._windows(
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
        )
        overall_start, overall_end = _attribute_window_bounds(windows)
        if not projects:
            return AttributeKeyRead(
                (),
                self._metadata(
                    complete=True,
                    error_code=None,
                    window_start=overall_start,
                    window_end=overall_end,
                    query_count=self._query_count,
                ),
            )

        typed_predicate = (
            "length(attrs_string.keys) > 0 "
            "OR length(attrs_number.keys) > 0 "
            "OR length(attrs_bool.keys) > 0"
            if exact_key is None
            else (
                "(indexHint(has(mapKeys(attrs_string), %(attribute_key)s)) "
                "AND has(attrs_string.keys, %(attribute_key)s)) "
                "OR (indexHint(has(mapKeys(attrs_number), %(attribute_key)s)) "
                "AND has(attrs_number.keys, %(attribute_key)s)) "
                "OR (indexHint(has(mapKeys(attrs_bool), %(attribute_key)s)) "
                "AND has(attrs_bool.keys, %(attribute_key)s))"
            )
        )
        json_predicate = (
            "attributes_extra NOT IN ('', '{}', 'null')" if exact_key is None else "1"
        )
        lanes: list[tuple[str, str, str, JsonAttributeMode, int | None]] = [
            (
                "typed",
                typed_predicate,
                _LATEST_TYPED_TARGET_SQL
                if exact_key is not None
                else _LATEST_TYPED_BROWSE_SQL,
                "none",
                (
                    ATTRIBUTE_READ_EXACT_KEY_QUERY_TIMEOUT_MS
                    if exact_key is not None
                    else None
                ),
            )
        ]
        if self._reads_json_overflow:
            lanes.append(
                (
                    "json",
                    json_predicate,
                    _LATEST_TARGET_SQL if exact_key is not None else _LATEST_BROWSE_SQL,
                    self._json_attribute_mode,
                    ATTRIBUTE_READ_JSON_QUERY_TIMEOUT_MS,
                )
            )

        latest_keys: dict[PhysicalSpanIdentity, dict[str, AttributeType]] = {}
        truncated = False
        budget_exceeded = False
        json_budget_exceeded = False
        budget_warning_emitted = False
        covered_start = overall_end
        json_lane_available = self._reads_json_overflow
        typed_lane_halted = False

        def mark_budget_exceeded() -> None:
            nonlocal budget_exceeded, budget_warning_emitted
            budget_exceeded = True
            if not budget_warning_emitted:
                self._warn_partial_budget("discover_keys")
                budget_warning_emitted = True

        def mark_json_budget_exceeded() -> None:
            nonlocal json_budget_exceeded, budget_warning_emitted
            json_budget_exceeded = True
            if not budget_warning_emitted:
                self._warn_partial_budget("discover_keys")
                budget_warning_emitted = True

        def consume_rows(
            rows: list[dict[str, Any]],
            *,
            json_mode: JsonAttributeMode,
        ) -> bool:
            """Merge one independently verified lane; report a usable key."""

            nonlocal truncated
            usable_key_seen = False
            for row in rows:
                identity = self._physical_identity(row)
                if not self._row_is_active_in_window(row, overall_start, overall_end):
                    latest_keys.pop(identity, None)
                    continue
                if exact_key is not None:
                    decoded = self._decode_target_value(
                        row,
                        json_attribute_mode=json_mode,
                    )
                    if (
                        json_mode != "none"
                        and decoded is None
                        and self._target_value_is_unsupported(
                            row,
                            json_attribute_mode=json_mode,
                        )
                    ):
                        truncated = True
                    if decoded is None:
                        latest_keys.setdefault(identity, {})
                        continue
                    current = latest_keys.setdefault(identity, {})
                    prior_type = current.get(exact_key)
                    if (
                        prior_type is None
                        or _TYPE_PRIORITY[decoded[0]] < _TYPE_PRIORITY[prior_type]
                    ):
                        current[exact_key] = decoded[0]
                    usable_key_seen = True
                    continue

                row_keys, unsupported_value_seen = self._browse_row_keys(
                    row,
                    json_attribute_mode=json_mode,
                )
                current = latest_keys.setdefault(identity, {})
                for key, attr_type in row_keys.items():
                    prior_type = current.get(key)
                    if (
                        prior_type is None
                        or _TYPE_PRIORITY[attr_type] < _TYPE_PRIORITY[prior_type]
                    ):
                        current[key] = attr_type
                usable_key_seen = usable_key_seen or bool(row_keys)
                truncated = truncated or unsupported_value_seen
            return usable_key_seen

        # Generic browse preserves its segment-first typed/JSON sampling order.
        # Exact lookup gives indexed typed Maps every first probe and permitted
        # deterministic continuation page before the unindexed JSON overflow
        # may use the candidate-page budget that remains. This prevents a rare
        # JSON key from blocking an older Map key hidden behind stale versions.
        fallback_states: list[dict[str, Any]] = []
        exact_found = False
        candidate_pages = 0
        typed_first_probe_limit = ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT - (
            1 if exact_key is not None and self._reads_json_overflow else 0
        )
        typed_first_probe_limit_reached = False
        probe_groups = (
            tuple((segment, tuple(lanes)) for segment in windows)
            if exact_key is None
            else tuple((segment, (lanes[0],)) for segment in windows)
        )
        for segment, segment_lanes in probe_groups:
            lane_found = False
            for (
                lane_name,
                predicate,
                replay_sql,
                json_mode,
                timeout_ms,
            ) in segment_lanes:
                if lane_name == "json" and not json_lane_available:
                    continue
                if (
                    exact_key is not None
                    and lane_name == "typed"
                    and candidate_pages >= typed_first_probe_limit
                ):
                    typed_first_probe_limit_reached = True
                    truncated = True
                    break
                try:
                    candidate_ids, segment_truncated, _ = self._candidate_ids(
                        projects,
                        segment,
                        predicate=predicate,
                        attribute_key=exact_key,
                        stratified=exact_key is None,
                        candidate_limit=ATTRIBUTE_READ_CANDIDATE_LIMIT,
                        query_timeout_ms=timeout_ms,
                    )
                    candidate_pages += 1
                    rows = self._verify_latest(
                        sql=replay_sql,
                        project_ids=projects,
                        candidate_ids=candidate_ids,
                        attribute_key=exact_key,
                        query_timeout_ms=timeout_ms,
                    )
                except Exception as exc:
                    if isinstance(exc, AttributeReadQueryLimitExceeded):
                        typed_lane_halted = True
                        mark_budget_exceeded()
                        break
                    if lane_name == "json" and is_read_budget_error(exc):
                        # JSON overflow has no skip index. Its independent short
                        # lane may degrade, but must never erase verified typed
                        # Map keys such as ``final_status``.
                        json_lane_available = False
                        mark_json_budget_exceeded()
                        continue
                    if latest_keys and is_read_budget_error(exc):
                        typed_lane_halted = True
                        mark_budget_exceeded()
                        break
                    raise

                covered_start = min(covered_start, segment[0])
                lane_found = consume_rows(rows, json_mode=json_mode)
                if exact_key is None:
                    truncated = truncated or segment_truncated
                    if lane_found and segment_truncated:
                        # Discovery pickers need a useful inventory, not an
                        # accounting scan. A verified dense page is sufficient
                        # and its sentinel makes the partial coverage explicit.
                        break
                elif lane_found:
                    exact_found = True
                    truncated = truncated or segment_truncated
                    break
                elif segment_truncated and lane_name != "json":
                    # Indexed typed candidates may page past stale versions.
                    # Identity-only JSON candidates deliberately get one
                    # bounded sample per temporal segment; continuing them
                    # cannot prove absence and recreates the production scan.
                    fallback_states.append(
                        {
                            "lane_name": lane_name,
                            "predicate": predicate,
                            "replay_sql": replay_sql,
                            "json_mode": json_mode,
                            "timeout_ms": timeout_ms,
                            "segment": segment,
                            "before_identity": None,
                            "pages": 0,
                            "complete": False,
                        }
                    )

            if (
                exact_found
                or typed_lane_halted
                or typed_first_probe_limit_reached
                or (exact_key is None and lane_found and segment_truncated)
            ):
                break
            discovered_key_count = len(
                {key for keys in latest_keys.values() for key in keys}
            )
            if exact_key is None and discovered_key_count > max_keys:
                truncated = True
                break

        # Restart stale-only typed exact probes from a deterministic ordered
        # first page, then keyset-page them. The unordered/storage-order cursor
        # is deliberately never reused as an ordered cursor. JSON seeds never
        # enter this continuation phase.
        ordered_pages = 0
        while (
            exact_key is not None
            and not exact_found
            and not typed_lane_halted
            and ordered_pages < ATTRIBUTE_READ_TARGETED_CANDIDATE_PAGE_LIMIT
            and candidate_pages < ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT
            and any(not state["complete"] for state in fallback_states)
        ):
            progressed = False
            for state in fallback_states:
                if state["complete"]:
                    continue
                if ordered_pages >= ATTRIBUTE_READ_TARGETED_CANDIDATE_PAGE_LIMIT:
                    break
                if candidate_pages >= ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT:
                    truncated = True
                    break
                if state["lane_name"] == "json" and not json_lane_available:
                    state["complete"] = True
                    continue
                try:
                    candidate_ids, segment_truncated, _ = self._candidate_ids(
                        projects,
                        state["segment"],
                        predicate=state["predicate"],
                        attribute_key=exact_key,
                        ordered=True,
                        before_identity=state["before_identity"],
                        candidate_limit=ATTRIBUTE_READ_CANDIDATE_LIMIT,
                        query_timeout_ms=state["timeout_ms"],
                    )
                    candidate_pages += 1
                    rows = self._verify_latest(
                        sql=state["replay_sql"],
                        project_ids=projects,
                        candidate_ids=candidate_ids,
                        attribute_key=exact_key,
                        query_timeout_ms=state["timeout_ms"],
                    )
                except Exception as exc:
                    if isinstance(exc, AttributeReadQueryLimitExceeded):
                        typed_lane_halted = True
                        mark_budget_exceeded()
                        break
                    if state["lane_name"] == "json" and is_read_budget_error(exc):
                        json_lane_available = False
                        state["complete"] = True
                        mark_json_budget_exceeded()
                        continue
                    if latest_keys and is_read_budget_error(exc):
                        typed_lane_halted = True
                        mark_budget_exceeded()
                        break
                    raise

                progressed = True
                ordered_pages += 1
                state["pages"] += 1
                covered_start = min(covered_start, state["segment"][0])
                lane_found = consume_rows(rows, json_mode=state["json_mode"])
                if lane_found:
                    exact_found = True
                    truncated = truncated or segment_truncated
                    state["complete"] = True
                    break
                if not segment_truncated:
                    state["complete"] = True
                elif (
                    not candidate_ids
                    or state["pages"] >= ATTRIBUTE_READ_TARGETED_CANDIDATE_PAGE_LIMIT
                ):
                    truncated = True
                    state["complete"] = True
                else:
                    state["before_identity"] = candidate_ids[-1]

            if exact_found or typed_lane_halted or not progressed:
                break

        if (
            exact_key is not None
            and not exact_found
            and any(not state["complete"] for state in fallback_states)
        ):
            truncated = True

        # JSON overflow is deliberately last for exact lookups. Its raw
        # identity seed has no key index, gets no continuation, and may consume
        # only pages left after typed probes and typed keyset continuation.
        if (
            exact_key is not None
            and not exact_found
            and not typed_lane_halted
            and json_lane_available
            and len(lanes) > 1
        ):
            (
                lane_name,
                predicate,
                replay_sql,
                json_mode,
                timeout_ms,
            ) = lanes[1]
            for segment in windows:
                if candidate_pages >= ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT:
                    truncated = True
                    break
                try:
                    candidate_ids, segment_truncated, _ = self._candidate_ids(
                        projects,
                        segment,
                        predicate=predicate,
                        attribute_key=exact_key,
                        candidate_limit=ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT,
                        query_timeout_ms=timeout_ms,
                        candidate_query_settings=_JSON_VALUE_CANDIDATE_SETTINGS,
                    )
                    candidate_pages += 1
                    rows = self._verify_latest(
                        sql=replay_sql,
                        project_ids=projects,
                        candidate_ids=candidate_ids,
                        attribute_key=exact_key,
                        query_timeout_ms=timeout_ms,
                    )
                except Exception as exc:
                    if isinstance(exc, AttributeReadQueryLimitExceeded) or (
                        is_read_budget_error(exc)
                    ):
                        # A finite JSON sample cannot prove absence. Publish an
                        # explicit sample instead of converting its independent
                        # short-lane ceiling into an indexed typed-key failure.
                        truncated = True
                        if not budget_warning_emitted:
                            self._warn_partial_budget("discover_keys")
                            budget_warning_emitted = True
                        break
                    raise

                covered_start = min(covered_start, segment[0])
                lane_found = consume_rows(rows, json_mode=json_mode)
                truncated = truncated or segment_truncated
                if lane_found:
                    exact_found = True
                    break

        counts: Counter[tuple[str, AttributeType]] = Counter()
        key_totals: Counter[str] = Counter()
        for keys in latest_keys.values():
            for key, attr_type in keys.items():
                counts[(key, attr_type)] += 1
                key_totals[key] += 1

        type_counts: dict[str, list[tuple[AttributeType, int]]] = defaultdict(list)
        for (key, attr_type), count in counts.items():
            type_counts[key].append((attr_type, count))
        rows = [
            AttributeKeyRow(
                key=key,
                type=min(
                    candidates,
                    key=lambda item: (-item[1], _TYPE_PRIORITY[item[0]]),
                )[0],
                count=key_totals[key],
                types=tuple(
                    attr_type
                    for attr_type, _count in sorted(
                        candidates,
                        key=lambda item: _TYPE_PRIORITY[item[0]],
                    )
                ),
            )
            for key, candidates in type_counts.items()
        ]
        if order_by_count_desc:
            rows.sort(key=lambda row: (-row.count, row.key.casefold(), row.key))
        else:
            rows.sort(key=lambda row: (row.key.casefold(), row.key, row.type))
        if len(rows) > max_keys:
            rows = rows[:max_keys]
            truncated = True
        # A short JSON-overflow lane timing out after verified typed Map data is
        # a usable sampled response, not a failed picker. Keep the incomplete
        # coverage explicit without inviting clients to discard ``final_status``
        # and other typed results. With no usable result, retain the stronger
        # budget signal.
        usable_json_degradation = json_budget_exceeded and bool(rows)
        effective_budget_exceeded = budget_exceeded or (
            json_budget_exceeded and not rows
        )
        effective_truncated = truncated or usable_json_degradation
        return AttributeKeyRead(
            tuple(rows),
            self._metadata(
                complete=not effective_truncated and not effective_budget_exceeded,
                error_code=(
                    "read_budget_exceeded"
                    if effective_budget_exceeded
                    else "sample_limit"
                    if effective_truncated
                    else None
                ),
                sampled=(
                    effective_truncated
                    and not effective_budget_exceeded
                    and (bool(rows) or exact_key is not None)
                ),
                window_start=covered_start,
                window_end=overall_end,
                query_count=self._query_count,
            ),
        )

    def read_values(
        self,
        project_ids: Iterable[Any],
        key: str,
        *,
        search: str | None = None,
        max_values: int = ATTRIBUTE_READ_MAX_VALUES,
        horizon_days: int = 365,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
    ) -> AttributeValueRead:
        self._begin_operation()
        projects = self._project_ids(project_ids)
        key = validate_attribute_key(key)
        normalized_search = validate_attribute_search(search or "")
        max_values = min(max(int(max_values), 1), ATTRIBUTE_READ_MAX_VALUES)
        explicit_window = window_start is not None
        windows = self._windows(
            horizon_days=horizon_days,
            window_start=window_start,
            window_end=window_end,
        )
        overall_start, overall_end = _attribute_window_bounds(windows)
        if not projects:
            return AttributeValueRead(
                (),
                self._metadata(
                    complete=True,
                    error_code=None,
                    window_start=overall_start,
                    window_end=overall_end,
                    query_count=self._query_count,
                ),
            )

        typed_predicate = (
            "(indexHint(has(mapKeys(attrs_string), %(attribute_key)s)) "
            "AND has(attrs_string.keys, %(attribute_key)s)) "
            "OR (indexHint(has(mapKeys(attrs_number), %(attribute_key)s)) "
            "AND has(attrs_number.keys, %(attribute_key)s)) "
            "OR (indexHint(has(mapKeys(attrs_bool), %(attribute_key)s)) "
            "AND has(attrs_bool.keys, %(attribute_key)s))"
        )
        # JSON overflow has no usable key index. Candidate discovery must never
        # evaluate attributes_extra across the tenant: sample narrow physical
        # identities first, then inspect the requested key/search only in the
        # exact latest-state hydration and Python decoder below.
        json_predicate = "1"
        lanes: list[tuple[str, str, str, JsonAttributeMode, int | None]] = [
            (
                "typed",
                typed_predicate,
                _LATEST_TYPED_TARGET_SQL,
                "none",
                None,
            )
        ]
        if self._reads_json_overflow:
            lanes.append(
                (
                    "json",
                    json_predicate,
                    _LATEST_TARGET_SQL,
                    self._json_attribute_mode,
                    ATTRIBUTE_READ_JSON_QUERY_TIMEOUT_MS,
                )
            )

        latest_values: dict[PhysicalSpanIdentity, tuple[AttributeType, Any]] = {}
        truncated = False
        budget_exceeded = False
        json_budget_exceeded = False
        budget_warning_emitted = False
        deadline_sampled = False
        covered_start = overall_end
        json_lane_available = self._reads_json_overflow
        typed_lane_halted = False

        def mark_budget_exceeded() -> None:
            nonlocal budget_exceeded, budget_warning_emitted
            budget_exceeded = True
            if not budget_warning_emitted:
                self._warn_partial_budget("read_values")
                budget_warning_emitted = True

        def mark_json_budget_exceeded() -> None:
            nonlocal json_budget_exceeded, budget_warning_emitted
            json_budget_exceeded = True
            if not budget_warning_emitted:
                self._warn_partial_budget("read_values")
                budget_warning_emitted = True

        needle = normalized_search.casefold()

        def decoded_has_usable_value(decoded: tuple[AttributeType, Any]) -> bool:
            attr_type, value = decoded
            if attr_type == "string" and not _typed_string_is_suggestible(value):
                return False
            candidates: tuple[Any, ...]
            if attr_type == "array":
                if not isinstance(value, tuple):
                    return False
                candidates = value
            else:
                if value in (None, ""):
                    return False
                candidates = (value,)
            return any(
                not needle or needle in _value_search_text(candidate).casefold()
                for candidate in candidates
            )

        def consume_rows(
            rows: list[dict[str, Any]],
            *,
            json_mode: JsonAttributeMode,
        ) -> bool:
            """Merge a verified lane and report whether it yielded a usable value."""

            nonlocal truncated
            usable_value_seen = False
            for row in rows:
                identity = self._physical_identity(row)
                if not self._row_is_active_in_window(row, overall_start, overall_end):
                    latest_values.pop(identity, None)
                    continue
                decoded = self._decode_target_value(
                    row,
                    json_attribute_mode=json_mode,
                )
                if (
                    decoded is not None
                    and decoded[0] == "string"
                    and not (_typed_string_is_suggestible(decoded[1]))
                ):
                    # Suggestion-only policy: the typed key and exact filtering
                    # remain unchanged, but oversized picker values are omitted.
                    latest_values.pop(identity, None)
                    continue
                if (
                    json_mode != "none"
                    and decoded is None
                    and self._target_value_is_unsupported(
                        row,
                        json_attribute_mode=json_mode,
                    )
                ):
                    truncated = True
                if decoded is None or (
                    decoded[0] != "array" and decoded[1] in (None, "")
                ):
                    continue
                prior = latest_values.get(identity)
                if (
                    prior is None
                    or _TYPE_PRIORITY[decoded[0]] < _TYPE_PRIORITY[prior[0]]
                ):
                    latest_values[identity] = decoded
                usable_value_seen = usable_value_seen or decoded_has_usable_value(
                    decoded
                )
            return usable_value_seen

        # Phase one walks every typed adaptive band before touching the JSON
        # overflow lane. JSON has no key skip index and can consume the whole
        # picker deadline while an older typed value is still cheap to find.
        # Once typed replay yields a usable sample, omit JSON and report the
        # intentionally partial distribution as sampled. JSON-only keys still
        # fall through after typed absence has been checked in every band.
        fallback_states: list[dict[str, Any]] = []
        candidate_pages = 0
        deferred_json_lane = False
        typed_probe_coverage_truncated = False
        typed_first_probe_limit = ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT - (
            2 if self._reads_json_overflow else 0
        )
        usable_sample_found = False
        typed_usable_sample_found = False
        for lane_name, predicate, replay_sql, json_mode, timeout_ms in lanes:
            # JSON-enabled reads reserve one page for a deterministic typed
            # continuation and one for a bounded JSON fallback. When typed
            # coverage finishes without a stale continuation, its unused
            # reservation remains available to the normal bounded JSON walk.
            if lane_name == "json" and fallback_states:
                deferred_json_lane = True
                continue
            lane_candidate_page_limit = (
                typed_first_probe_limit
                if lane_name == "typed"
                else (
                    min(
                        candidate_pages + 1,
                        ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT,
                    )
                    if typed_probe_coverage_truncated
                    else ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT
                )
            )
            for segment_index, segment in enumerate(windows):
                if candidate_pages >= lane_candidate_page_limit:
                    truncated = True
                    if lane_name == "typed":
                        typed_probe_coverage_truncated = True
                    break
                if lane_name == "json" and not json_lane_available:
                    continue
                try:
                    (
                        candidate_ids,
                        segment_truncated,
                        candidate_versions,
                    ) = self._candidate_ids(
                        projects,
                        segment,
                        predicate=predicate,
                        attribute_key=key,
                        candidate_limit=ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT,
                        query_timeout_ms=timeout_ms,
                        candidate_query_settings=(
                            _JSON_VALUE_CANDIDATE_SETTINGS
                            if lane_name == "json"
                            else None
                        ),
                        include_versions=lane_name == "typed",
                    )
                    rows = (
                        self._verify_latest_typed_values(
                            project_ids=projects,
                            candidate_ids=candidate_ids,
                            candidate_versions=candidate_versions,
                            attribute_key=key,
                            query_timeout_ms=timeout_ms,
                        )
                        if lane_name == "typed"
                        else self._verify_latest(
                            sql=replay_sql,
                            project_ids=projects,
                            candidate_ids=candidate_ids,
                            attribute_key=key,
                            query_timeout_ms=timeout_ms,
                        )
                    )
                except Exception as exc:
                    if isinstance(exc, AttributeReadQueryLimitExceeded):
                        # The application query ceiling is a deterministic
                        # sample boundary, not a ClickHouse failure. Every row
                        # retained so far completed latest-state replay, so
                        # preserve it as an explicitly incomplete sample.
                        typed_lane_halted = True
                        truncated = True
                        break
                    if (
                        isinstance(exc, ReadDeadlineExceeded)
                        and covered_start < overall_end
                    ):
                        # This cursorless compatibility endpoint already
                        # completed latest-state replay for a bounded temporal
                        # sample. Label that sample rather than restarting the
                        # same adaptive walk as a 503 retry loop. Exhaustive
                        # consumers use the signed value cursor.
                        deadline_sampled = True
                        typed_lane_halted = True
                        truncated = True
                        break
                    if lane_name == "json" and is_read_budget_error(exc):
                        json_lane_available = False
                        mark_json_budget_exceeded()
                        continue
                    if latest_values and is_read_budget_error(exc):
                        typed_lane_halted = True
                        mark_budget_exceeded()
                        break
                    raise

                candidate_pages += 1
                covered_start = min(covered_start, segment[0])
                usable_value_seen = consume_rows(rows, json_mode=json_mode)
                if lane_name == "json" and segment_truncated:
                    # The JSON lane samples raw physical identities because no
                    # safe key index exists. Once that raw seed truncates, later
                    # keyset continuation can improve the sample but must never
                    # upgrade it into a global absence/completeness claim.
                    truncated = True
                if lane_name == "typed" and usable_value_seen:
                    typed_usable_sample_found = True
                unvisited_segments = segment_index + 1 < len(windows)
                if usable_value_seen and (
                    segment_truncated or (explicit_window and unvisited_segments)
                ):
                    # The picker has useful verified values. Stop immediately
                    # instead of scanning JSON and more temporal strata. The
                    # response remains an explicit sample whenever coverage
                    # was truncated or any requested slice was not visited.
                    truncated = True
                    usable_sample_found = True
                    break
                elif segment_truncated and lane_name != "json":
                    # Typed Map candidates have selective key indexes, so a
                    # short ordered continuation may page past stale versions
                    # to a live value. The JSON lane is an identity-only
                    # sample with no key index; continuing it repeatedly can
                    # never prove absence and only spends the operation/query
                    # budget. One finite sample + exact latest-state hydration
                    # per temporal segment is the complete JSON fallback
                    # contract, with ``sample_limit`` preserving incompleteness.
                    fallback_states.append(
                        {
                            "lane_name": lane_name,
                            "predicate": predicate,
                            "replay_sql": replay_sql,
                            "json_mode": json_mode,
                            "timeout_ms": timeout_ms,
                            "segment": segment,
                            "before_identity": None,
                            "pages": 0,
                            "complete": False,
                        }
                    )

            if typed_lane_halted or usable_sample_found:
                break
            if (
                lane_name == "typed"
                and typed_usable_sample_found
                and self._reads_json_overflow
            ):
                # Typed values are filterable and have strict precedence over
                # legacy JSON. Skipping the independent JSON population is a
                # useful sample, never a claim of global completeness.
                truncated = True
                usable_sample_found = True
                break
        if candidate_pages >= ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT and any(
            not state["complete"] for state in fallback_states
        ):
            truncated = True

        # Phase two round-robins only the stale-only truncated lanes. It restarts
        # each lane at ordered page one; a cursor is derived exclusively from a
        # preceding page with that same deterministic order. When JSON was
        # deferred behind stale typed candidates, its reserved final page must
        # remain available after this continuation phase.
        continuation_page_limit = ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT - (
            1 if deferred_json_lane else 0
        )
        while (
            not typed_lane_halted
            and not usable_sample_found
            and candidate_pages < continuation_page_limit
            and any(not state["complete"] for state in fallback_states)
        ):
            progressed = False
            for state in fallback_states:
                if state["complete"]:
                    continue
                if candidate_pages >= continuation_page_limit:
                    break
                if state["lane_name"] == "json" and not json_lane_available:
                    state["complete"] = True
                    continue
                if state["pages"] >= ATTRIBUTE_READ_VALUE_CANDIDATE_PAGE_LIMIT:
                    truncated = True
                    state["complete"] = True
                    continue
                try:
                    (
                        candidate_ids,
                        segment_truncated,
                        candidate_versions,
                    ) = self._candidate_ids(
                        projects,
                        state["segment"],
                        predicate=state["predicate"],
                        attribute_key=key,
                        ordered=True,
                        before_identity=state["before_identity"],
                        candidate_limit=ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT,
                        query_timeout_ms=state["timeout_ms"],
                        candidate_query_settings=(
                            _JSON_VALUE_CANDIDATE_SETTINGS
                            if state["lane_name"] == "json"
                            else None
                        ),
                        include_versions=state["lane_name"] == "typed",
                    )
                    rows = (
                        self._verify_latest_typed_values(
                            project_ids=projects,
                            candidate_ids=candidate_ids,
                            candidate_versions=candidate_versions,
                            attribute_key=key,
                            query_timeout_ms=state["timeout_ms"],
                        )
                        if state["lane_name"] == "typed"
                        else self._verify_latest(
                            sql=state["replay_sql"],
                            project_ids=projects,
                            candidate_ids=candidate_ids,
                            attribute_key=key,
                            query_timeout_ms=state["timeout_ms"],
                        )
                    )
                except Exception as exc:
                    if isinstance(exc, AttributeReadQueryLimitExceeded):
                        # No in-flight replay was interrupted: the next
                        # bounded page simply did not start. This is finite
                        # sample exhaustion, while real server/deadline errors
                        # below remain fail-closed as read-budget failures.
                        typed_lane_halted = True
                        truncated = True
                        break
                    if (
                        isinstance(exc, ReadDeadlineExceeded)
                        and covered_start < overall_end
                    ):
                        deadline_sampled = True
                        typed_lane_halted = True
                        truncated = True
                        break
                    if state["lane_name"] == "json" and is_read_budget_error(exc):
                        json_lane_available = False
                        state["complete"] = True
                        mark_json_budget_exceeded()
                        continue
                    if latest_values and is_read_budget_error(exc):
                        typed_lane_halted = True
                        mark_budget_exceeded()
                        break
                    raise

                progressed = True
                candidate_pages += 1
                state["pages"] += 1
                covered_start = min(covered_start, state["segment"][0])
                usable_value_seen = consume_rows(rows, json_mode=state["json_mode"])
                if usable_value_seen:
                    truncated = truncated or segment_truncated
                    state["complete"] = True
                    if state["lane_name"] == "typed" and deferred_json_lane:
                        # Typed values take precedence over legacy JSON. This is
                        # still a sample because the deferred JSON population
                        # and any remaining continuation states were not read.
                        typed_usable_sample_found = True
                        usable_sample_found = True
                        truncated = True
                elif not segment_truncated:
                    state["complete"] = True
                elif not candidate_ids:
                    truncated = True
                    state["complete"] = True
                elif state["pages"] >= ATTRIBUTE_READ_VALUE_CANDIDATE_PAGE_LIMIT:
                    truncated = True
                    state["complete"] = True
                else:
                    state["before_identity"] = candidate_ids[-1]

                if usable_sample_found:
                    break

            if typed_lane_halted or not progressed:
                break

        # A stale, truncated typed page must not starve a live legacy-JSON
        # value. Spend exactly one reserved identity-only JSON page after typed
        # continuation. One page can improve discovery, but cannot prove global
        # JSON absence, so the result always remains explicitly sampled.
        if (
            deferred_json_lane
            and not typed_lane_halted
            and not usable_sample_found
            and json_lane_available
            and candidate_pages < ATTRIBUTE_READ_VALUE_TOTAL_CANDIDATE_PAGE_LIMIT
        ):
            _, predicate, replay_sql, json_mode, timeout_ms = lanes[1]
            segment = windows[0]
            try:
                candidate_ids, _, _ = self._candidate_ids(
                    projects,
                    segment,
                    predicate=predicate,
                    attribute_key=key,
                    candidate_limit=ATTRIBUTE_READ_VALUE_CANDIDATE_LIMIT,
                    query_timeout_ms=timeout_ms,
                    candidate_query_settings=_JSON_VALUE_CANDIDATE_SETTINGS,
                    include_versions=False,
                )
                rows = self._verify_latest(
                    sql=replay_sql,
                    project_ids=projects,
                    candidate_ids=candidate_ids,
                    attribute_key=key,
                    query_timeout_ms=timeout_ms,
                )
            except Exception as exc:
                if isinstance(exc, AttributeReadQueryLimitExceeded):
                    typed_lane_halted = True
                    truncated = True
                elif (
                    isinstance(exc, ReadDeadlineExceeded)
                    and covered_start < overall_end
                ):
                    deadline_sampled = True
                    typed_lane_halted = True
                    truncated = True
                elif is_read_budget_error(exc):
                    json_lane_available = False
                    mark_json_budget_exceeded()
                else:
                    raise
            else:
                candidate_pages += 1
                covered_start = min(covered_start, segment[0])
                consume_rows(rows, json_mode=json_mode)
                truncated = True

        if any(not state["complete"] for state in fallback_states):
            truncated = True

        counts: Counter[tuple[AttributeType, str]] = Counter()
        values: dict[tuple[AttributeType, str], AttributeValue] = {}
        for attr_type, value in latest_values.values():
            if attr_type == "string" and not _typed_string_is_suggestible(value):
                continue
            candidates: tuple[AttributeValue, ...]
            if attr_type == "array":
                if not isinstance(value, tuple):
                    truncated = True
                    continue
                if len(value) > max_values:
                    truncated = True
                candidates = value
            else:
                candidates = (value,)
            # Count an array member at most once per physical span.  Repeated
            # members do not represent additional spans in the value picker.
            seen_in_span: set[tuple[AttributeType, str]] = set()
            for candidate in candidates:
                display = _value_search_text(candidate)
                if needle and needle not in display.casefold():
                    continue
                canonical = _canonical_value(attr_type, candidate)
                identity = (attr_type, canonical)
                if identity in seen_in_span:
                    continue
                seen_in_span.add(identity)
                counts[identity] += 1
                values[identity] = candidate

        ordered = sorted(
            counts,
            key=lambda item: (
                -counts[item],
                _value_search_text(values[item]).casefold(),
                _value_search_text(values[item]),
                _TYPE_PRIORITY[item[0]],
            ),
        )
        if len(ordered) > max_values:
            ordered = ordered[:max_values]
            truncated = True
        usable_json_degradation = json_budget_exceeded and bool(ordered)
        effective_budget_exceeded = budget_exceeded or (
            json_budget_exceeded and not ordered
        )
        effective_truncated = truncated or usable_json_degradation or deadline_sampled
        return AttributeValueRead(
            tuple(
                AttributeValueRow(
                    value=values[identity],
                    type=identity[0],
                    count=counts[identity],
                )
                for identity in ordered
            ),
            self._metadata(
                complete=not effective_truncated and not effective_budget_exceeded,
                error_code=(
                    "read_budget_exceeded"
                    if effective_budget_exceeded
                    else "sample_limit"
                    if effective_truncated
                    else None
                ),
                sampled=(
                    effective_truncated
                    and not effective_budget_exceeded
                    and (bool(ordered) or deadline_sampled)
                ),
                window_start=covered_start,
                window_end=overall_end,
                query_count=self._query_count,
            ),
        )

    def read_key_cursor_page(
        self,
        project_ids: Iterable[Any],
        *,
        page_size: int,
        window_start: datetime,
        window_end: datetime,
        segment_end: datetime | None = None,
        segment_start: datetime | None = None,
        before_identity: PhysicalSpanIdentity | None = None,
        resume_identity: PhysicalSpanIdentity | None = None,
        resume_key_offset: int = 0,
        seen_key_digests: Iterable[str] = (),
        seen_key_contains: Callable[[str], bool] | None = None,
        seen_key_count: int | None = None,
        exact_key: str | None = None,
        dedupe_by_type: bool = False,
        exhaustive_exact_types: bool = False,
        continue_operation: bool = False,
    ) -> AttributeKeyCursorPageRead:
        """Return a bounded newest-first page of verified unique keys.

        The public cursor freezes the caller's retained-data browse window and
        advances over physical spans, not an offset into a changing
        ``DISTINCT`` result. Each candidate is replayed through latest state
        before a key is emitted. A
        cursor also records compact digests of keys already returned, so keys
        repeated on older spans do not reappear on later pages. Exact lookup
        uses an indexed typed-Map accelerator before this same cursor performs
        a key-bound latest-state fallback.
        """

        # DEV's immutable snapshot already supplies the retained window, so a
        # fallback cursor can legitimately ask to continue an operation before
        # this selector ran the optional retained-bound metadata read. Preserve
        # a real shared budget when one exists; otherwise start the cursor's
        # public operation here instead of reaching deadline checks with None.
        if not continue_operation or self._deadline is None:
            self._begin_operation()
        projects = self._project_ids(project_ids)
        if exact_key is not None:
            exact_key = validate_attribute_key(exact_key)
        if exhaustive_exact_types and (exact_key is None or not dedupe_by_type):
            raise ValueError(
                "exhaustive exact types require an exact key and type-aware dedupe"
            )
        page_size = int(page_size)
        if not 1 <= page_size <= ATTRIBUTE_KEY_CURSOR_MAX_PAGE_SIZE:
            raise ValueError("attribute-key page_size is out of range")
        start = _utc(window_start)
        end = _utc(window_end)
        current_segment_end = _utc(segment_end or end)
        if start >= end or not start < current_segment_end <= end:
            raise ValueError("invalid attribute-key cursor window")
        if not projects:
            return AttributeKeyCursorPageRead(
                (),
                self._metadata(
                    complete=True,
                    error_code=None,
                    window_start=start,
                    window_end=end,
                    query_count=self._query_count,
                ),
                False,
                "exhausted",
                start,
                None,
                None,
                0,
                (),
            )

        def normalize_identity(
            identity: PhysicalSpanIdentity | None,
            *,
            label: str,
        ) -> PhysicalSpanIdentity | None:
            if identity is None:
                return None
            normalized = (
                str(identity[0]),
                str(identity[1]),
                str(identity[2]),
                _utc(identity[3]),
            )
            if (
                normalized[0] not in projects
                or not start <= normalized[3] < current_segment_end
            ):
                raise ValueError(f"invalid attribute-key {label} cursor")
            return normalized

        before_identity = normalize_identity(before_identity, label="physical")
        resume_identity = normalize_identity(resume_identity, label="resume")
        if before_identity is not None and resume_identity is not None:
            raise ValueError("attribute-key cursor checkpoints are mutually exclusive")
        has_physical_checkpoint = (
            before_identity is not None or resume_identity is not None
        )
        active_segment_start = (
            _utc(segment_start) if segment_start is not None else None
        )
        if active_segment_start is not None and (
            (not has_physical_checkpoint and exact_key is None)
            or not start <= active_segment_start < current_segment_end
        ):
            raise ValueError("invalid attribute-key segment cursor")
        checkpoint_identity = before_identity or resume_identity
        if checkpoint_identity is not None and active_segment_start is None:
            if exact_key is None:
                # Rolling-compatible five-field generic cursors omit the active
                # slice after checkpointing inside a fully consumed range.
                # Everything newer than the checkpoint has already been
                # certified, so resuming the whole six-hour prefix is
                # unnecessary and can turn a small picker page into a
                # multi-gigabyte global sort. Re-anchor the unchanged keyset
                # frontier to the five-minute dense slice that ends one
                # DateTime64 tick after the checkpoint. That includes lower-id
                # timestamp ties plus older rows immediately; the next adjacent
                # slice starts at the resulting lower boundary.
                current_segment_end = min(
                    current_segment_end,
                    checkpoint_identity[3] + timedelta(microseconds=1),
                )
                active_segment_start = max(
                    start,
                    current_segment_end - ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT,
                )
            else:
                # Exact-key continuations retain the rolling-compatible legacy
                # envelope; the exact lane below re-anchors it at the requested
                # key's deterministic fallback width.
                active_segment_start = max(
                    start,
                    current_segment_end - ATTRIBUTE_READ_EXPLICIT_SEGMENT,
                )
        if checkpoint_identity is not None and not (
            active_segment_start is not None
            and active_segment_start <= checkpoint_identity[3] < current_segment_end
        ):
            raise ValueError("invalid attribute-key physical cursor")
        resume_key_offset = int(resume_key_offset)
        if resume_key_offset < 0:
            raise ValueError("invalid attribute-key resume offset")
        if resume_identity is None and resume_key_offset != 0:
            raise ValueError("key offset requires a resume identity")
        initial_cursor_state = (
            current_segment_end,
            before_identity,
            resume_identity,
            resume_key_offset,
        )

        seen = tuple(dict.fromkeys(str(value) for value in seen_key_digests))
        if any(
            len(value) != ATTRIBUTE_KEY_CURSOR_DIGEST_BYTES * 2
            or any(char not in "0123456789abcdef" for char in value)
            for value in seen
        ):
            raise ValueError("invalid attribute-key seen state")
        seen_set = set(seen)
        resolved_seen_count = (
            len(seen) if seen_key_count is None else int(seen_key_count)
        )
        if resolved_seen_count < len(seen) or resolved_seen_count < 0:
            raise ValueError("invalid attribute-key seen state")

        def key_was_seen(digest: str) -> bool:
            return digest in seen_set or (
                seen_key_contains is not None and seen_key_contains(digest)
            )

        # Small continuations remain materialized for rolling compatibility;
        # large ones use exact persistent-radix membership supplied by the API
        # boundary. Neither representation imposes a browse-result ceiling.
        # An exact search is satisfied by the first verified occurrence. The
        # row remains conservatively multi-typed to downstream filters unless a
        # separate exact-coverage read certifies otherwise.
        effective_page_size = 1 if exact_key is not None else page_size
        emitted_digests: list[str] = []
        emitted_seen_digests: set[str] = set()
        emitted_order: list[str] = []
        emitted: dict[str, AttributeKeyRow] = {}

        def page_is_full() -> bool:
            return not exhaustive_exact_types and len(emitted) >= effective_page_size

        candidate_pages = 0
        is_initial_generic_page = (
            exact_key is None
            and resolved_seen_count == 0
            and not has_physical_checkpoint
            and current_segment_end == end
        )
        # Page one needs at most one new key per candidate to fill its public
        # result. Hydrating 64 wide Map/JSON rows for a ten-key response made
        # the common dense path pay over six times the required replay cost.
        # Duplicate/stale continuations retain the proven 64-row base and can
        # still grow to the existing 512-row accelerator without moving an
        # unverified cursor.
        candidate_limit = (
            min(ATTRIBUTE_KEY_CURSOR_CANDIDATE_LIMIT, effective_page_size)
            if is_initial_generic_page
            else ATTRIBUTE_KEY_CURSOR_CANDIDATE_LIMIT
        )
        cursor_before = before_identity
        empty_segment_width = (
            current_segment_end - active_segment_start
            if active_segment_start is not None
            else ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT
            if is_initial_generic_page
            else ATTRIBUTE_READ_EXPLICIT_SEGMENT
        )
        # Once ClickHouse rejects a widened empty slice, remember that ceiling
        # for the rest of this request.  Re-doubling immediately after the
        # successful narrower retry would repeatedly spend the request wall
        # budget on the same known-failing shape while walking sparse history.
        max_empty_segment_width = current_segment_end - start
        exact_probe_segment_end: datetime | None = None
        exact_fallback_started = False
        next_resume_identity: PhysicalSpanIdentity | None = None
        next_resume_key_offset = 0
        candidate_page_ceiling = (
            ATTRIBUTE_KEY_CURSOR_EXACT_MAX_CANDIDATE_PAGES
            if exact_key is not None
            else ATTRIBUTE_KEY_CURSOR_MAX_CANDIDATE_PAGES
        )

        def request_has_progress() -> bool:
            """Whether this request has an exact checkpoint safe to publish."""

            return (
                current_segment_end,
                cursor_before,
                next_resume_identity,
                next_resume_key_offset,
            ) != initial_cursor_state

        def should_publish_progress_after_budget_error() -> bool:
            """Stop only when a safe retry no longer fits the picker wall."""

            if not request_has_progress() or self._deadline is None:
                return False
            remaining_ms = int((self._deadline - self._clock()) * 1000)
            retry_reserve_ms = (
                2 * ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS
                + ATTRIBUTE_KEY_CURSOR_RETRY_GUARD_MARGIN_MS
            )
            return remaining_ms <= retry_reserve_ms

        def generic_candidate_timeout_ms(
            segment_width: timedelta,
        ) -> int | None:
            """Reserve the dense retry for a generic continuation probe.

            Page one's initial five-minute read remains authoritative.  Once a
            request or an incoming cursor has proven progress, wider probes are
            accelerators: a short failure must leave enough wall for the same
            frontier at the five-second dense floor.
            """

            if exact_key is not None:
                return None
            is_continuation_probe = (
                not is_initial_generic_page or request_has_progress()
            )
            if (
                is_continuation_probe
                and segment_width > ATTRIBUTE_KEY_CURSOR_DENSE_RETRY_MIN_SEGMENT
            ):
                return ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS
            return None

        def generic_replay_timeout_ms(
            candidate_count: int,
        ) -> int | None:
            """Bound optional continuation hydration before its exact fallback."""

            if exact_key is not None:
                return None
            is_continuation_replay = (
                not is_initial_generic_page or request_has_progress()
            )
            replay_and_fallback_fit = (
                self._query_count + 2 <= ATTRIBUTE_READ_MAX_QUERY_COUNT
            )
            if (
                is_continuation_replay
                and candidate_count > 1
                and replay_and_fallback_fit
            ):
                return ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS
            return None

        def anchor_checkpoint_window(
            checkpoint: PhysicalSpanIdentity,
            *,
            width: timedelta = ATTRIBUTE_READ_EXPLICIT_SEGMENT,
        ) -> None:
            """Resume proven progress through a bounded checkpoint slice.

            An ordered candidate page proves that every physical identity
            above ``checkpoint`` was consumed. Moving the public segment end
            to at most ``width`` above that identity therefore cannot skip an
            older row: the first legacy slice handles same-timestamp keyset
            ties, then adjacent slices continue below the checkpoint.

            This also lets a new pod recover a six-field cursor emitted during
            a rolling deploy without retrying its potentially huge segment.
            Re-anchoring is required whenever a later budget fallback shrinks
            the width again; changing only the lower bound would leave the
            keyset checkpoint outside the segment.
            """

            nonlocal active_segment_start
            nonlocal candidate_limit
            nonlocal current_segment_end
            nonlocal empty_segment_width

            current_segment_end = min(
                current_segment_end,
                checkpoint[3] + width,
            )
            active_segment_start = None
            empty_segment_width = min(width, max_empty_segment_width)
            candidate_limit = ATTRIBUTE_KEY_CURSOR_CANDIDATE_LIMIT

        def row_keys(row: dict[str, Any]) -> tuple[tuple[str, AttributeType], ...]:
            if exact_key is not None and any(
                field in row
                for field in (
                    "string_present",
                    "number_present",
                    "boolean_present",
                    "legacy_present",
                )
            ):
                decoded = self._decode_target_value(
                    row,
                    json_attribute_mode=self._json_attribute_mode,
                )
                return () if decoded is None else ((exact_key, decoded[0]),)
            keys, _unsupported = self._browse_row_keys(
                row,
                json_attribute_mode=self._json_attribute_mode,
            )
            return tuple(
                sorted(
                    (
                        item
                        for item in keys.items()
                        if exact_key is None or item[0] == exact_key
                    ),
                    key=lambda item: (item[0].casefold(), item[0], item[1]),
                )
            )

        def consume_keys(
            keys: tuple[tuple[str, AttributeType], ...],
            *,
            key_offset: int = 0,
        ) -> tuple[bool, int]:
            if key_offset > len(keys):
                raise ValueError("invalid attribute-key resume offset")
            for index in range(key_offset, len(keys)):
                key, attr_type = keys[index]
                logical_digest = attribute_key_cursor_digest(key)
                seen_digest = (
                    attribute_key_type_cursor_digest(key, attr_type)
                    if dedupe_by_type
                    else logical_digest
                )
                if key_was_seen(seen_digest):
                    continue
                prior = emitted.get(logical_digest)
                if prior is not None:
                    observed_types = set(prior.types or (prior.type,))
                    observed_types.add(attr_type)
                    ordered_types = tuple(
                        sorted(observed_types, key=lambda value: _TYPE_PRIORITY[value])
                    )
                    emitted[logical_digest] = AttributeKeyRow(
                        prior.key,
                        ordered_types[0],
                        prior.count + 1,
                        ordered_types,
                    )
                    if seen_digest not in emitted_seen_digests:
                        emitted_seen_digests.add(seen_digest)
                        emitted_digests.append(seen_digest)
                    continue
                emitted[logical_digest] = AttributeKeyRow(
                    key, attr_type, 1, (attr_type,)
                )
                emitted_order.append(logical_digest)
                emitted_seen_digests.add(seen_digest)
                emitted_digests.append(seen_digest)
                if page_is_full():
                    next_offset = index + 1
                    return next_offset >= len(keys), next_offset
            return True, len(keys)

        if resume_identity is not None:
            resume_rows = self._verify_latest(
                sql=(
                    _LATEST_TARGET_SQL if exact_key is not None else _LATEST_BROWSE_SQL
                ),
                project_ids=projects,
                candidate_ids=(resume_identity,),
                attribute_key=exact_key,
            )
            resume_row = resume_rows[0] if resume_rows else None
            if resume_row is not None and self._row_is_active_in_window(
                resume_row, start, end
            ):
                fully_consumed, next_offset = consume_keys(
                    row_keys(resume_row),
                    key_offset=resume_key_offset,
                )
                if not fully_consumed:
                    next_resume_identity = resume_identity
                    next_resume_key_offset = next_offset
                else:
                    cursor_before = resume_identity
            else:
                cursor_before = resume_identity

        browse_predicate = (
            "length(attrs_string.keys) > 0 "
            "OR length(attrs_number.keys) > 0 "
            "OR length(attrs_bool.keys) > 0"
        )
        exact_typed_predicate = (
            "(indexHint(has(mapKeys(attrs_string), %(attribute_key)s)) "
            "AND has(attrs_string.keys, %(attribute_key)s)) "
            "OR (indexHint(has(mapKeys(attrs_number), %(attribute_key)s)) "
            "AND has(attrs_number.keys, %(attribute_key)s)) "
            "OR (indexHint(has(mapKeys(attrs_bool), %(attribute_key)s)) "
            "AND has(attrs_bool.keys, %(attribute_key)s))"
        )
        # The ordered fallback owns the public physical cursor, so it must
        # cover both typed Maps and structured JSON. Bind the requested key
        # before ClickHouse sorts or hydrates candidates: the former generic
        # predicate selected every attribute-bearing span and only compared
        # ``exact_key`` after replay, which could read hundreds of MiB for one
        # rare JSON key. Keep indexHint out of this mixed predicate and disable
        # skip-index planning below; the independent typed accelerator above
        # remains the only lane that asks ClickHouse to build Map bloom-index
        # conditions.
        exact_fallback_predicate = (
            "mapContains(attrs_string, %(attribute_key)s) "
            "OR mapContains(attrs_number, %(attribute_key)s) "
            "OR mapContains(attrs_bool, %(attribute_key)s)"
        )
        if self._reads_json_overflow:
            browse_predicate = (
                f"({browse_predicate}) OR attributes_extra NOT IN ('', '{{}}', 'null')"
            )
            exact_fallback_predicate = (
                f"({exact_fallback_predicate}) "
                "OR JSONHas(attributes_extra, %(attribute_key)s)"
            )

        while (
            current_segment_end > start
            and not page_is_full()
            and next_resume_identity is None
            and candidate_pages < candidate_page_ceiling
            # Generic browsing needs a candidate/replay pair. An exact search
            # may first spend another pair on the typed Map bloom indexes; it
            # must still leave room for the ordered structured-JSON fallback.
            and self._query_count
            + (
                4
                if exact_key is not None
                and cursor_before is None
                and active_segment_start is None
                and exact_probe_segment_end is None
                else 2
            )
            <= ATTRIBUTE_READ_MAX_QUERY_COUNT
        ):
            if exact_key is not None and not exact_fallback_started:
                # Exact typed and JSON lanes start inside the same safe
                # five-minute envelope. A signed continuation may carry the
                # next geometrically grown width; retain that adaptive hint so
                # page N does not restart a sparse retained walk at five
                # minutes. A failed statement still halves at the identical
                # frontier before any progress is published.
                initial_exact_width = min(
                    (
                        ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT
                        if cursor_before is not None
                        else current_segment_end - active_segment_start
                        if active_segment_start is not None
                        else ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT
                    ),
                    ATTRIBUTE_KEY_CURSOR_EXACT_MAX_EMPTY_SEGMENT,
                    current_segment_end - start,
                )
                if active_segment_start is not None:
                    active_segment_start = max(
                        start,
                        current_segment_end - initial_exact_width,
                    )
                if cursor_before is not None:
                    initial_exact_width = min(
                        initial_exact_width,
                        current_segment_end - cursor_before[3],
                    )
                    anchor_checkpoint_window(
                        cursor_before,
                        width=initial_exact_width,
                    )
                else:
                    empty_segment_width = initial_exact_width
                exact_fallback_started = True
            current_segment_start = (
                active_segment_start
                if active_segment_start is not None
                else max(start, current_segment_end - empty_segment_width)
            )
            segment = (current_segment_start, current_segment_end)

            # Exact searches get one cheap typed-Map probe at the start of a
            # fresh request. Keep this lane independent from the ordered
            # predicate: OR-ing JSON overflow into it would prevent ClickHouse
            # from using the existing Map-key bloom indexes.  A miss, stale
            # version, or typed-lane budget failure changes no cursor state;
            # the ordinary physical walk below remains the deterministic
            # fallback for JSON-only keys and exact continuation.
            if (
                exact_key is not None
                and cursor_before is None
                and active_segment_start is None
                and exact_probe_segment_end is None
            ):
                try:
                    exact_candidate_ids, _exact_truncated, _ = self._candidate_ids(
                        projects,
                        segment,
                        predicate=exact_typed_predicate,
                        attribute_key=exact_key,
                        # This is an existence accelerator, not a published
                        # cursor. Read in MergeTree order and let the generic
                        # lane below own deterministic newest-first progress.
                        ordered=False,
                        before_identity=None,
                        candidate_limit=ATTRIBUTE_KEY_CURSOR_CANDIDATE_LIMIT,
                        query_timeout_ms=ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS,
                    )
                    candidate_pages += 1
                    exact_rows = self._verify_latest(
                        sql=_LATEST_TYPED_TARGET_SQL,
                        project_ids=projects,
                        candidate_ids=exact_candidate_ids,
                        attribute_key=exact_key,
                        query_timeout_ms=ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS,
                    )
                    exact_rows_by_identity = {
                        self._physical_identity(row): row for row in exact_rows
                    }
                    for identity in exact_candidate_ids:
                        row = exact_rows_by_identity.get(identity)
                        if row is None or not self._row_is_active_in_window(
                            row, start, end
                        ):
                            continue
                        consume_keys(row_keys(row))
                        if emitted and not exhaustive_exact_types:
                            break
                    exact_probe_segment_end = current_segment_end
                except Exception as exc:
                    if not is_read_budget_error(exc):
                        raise
                    # The key-bound ordered lane covers typed Maps too. If this
                    # accelerator times out, do not retry it after fallback
                    # backoff; the physical exact walk remains authoritative.
                    exact_probe_segment_end = current_segment_end
                if emitted and not exhaustive_exact_types:
                    break

            try:
                candidate_ids, segment_truncated, _ = self._candidate_ids(
                    projects,
                    segment,
                    predicate=(
                        exact_fallback_predicate
                        if exact_key is not None
                        else browse_predicate
                    ),
                    attribute_key=exact_key,
                    ordered=True,
                    before_identity=cursor_before,
                    candidate_limit=candidate_limit,
                    query_timeout_ms=(
                        ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS
                        if candidate_limit > ATTRIBUTE_KEY_CURSOR_CANDIDATE_LIMIT
                        or (
                            exact_key is not None
                            and cursor_before is None
                            and current_segment_end - current_segment_start
                            > ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT
                        )
                        else generic_candidate_timeout_ms(
                            current_segment_end - current_segment_start
                        )
                    ),
                    candidate_query_settings={"use_skip_indexes": 0},
                )
            except Exception as exc:
                if (
                    is_read_budget_error(exc)
                    and should_publish_progress_after_budget_error()
                ):
                    # This statement proved nothing, but an earlier complete
                    # candidate/replay batch already advanced the exact
                    # physical frontier. Publish only that proven checkpoint
                    # so an interactive page stays inside its four-second wall;
                    # the next request resumes without a gap or duplicate.
                    break
                if is_read_budget_error(exc) and exact_key is not None:
                    if candidate_limit > ATTRIBUTE_KEY_CURSOR_CANDIDATE_LIMIT:
                        # The larger duplicate-only batch is optional. Retry at
                        # the known finite base before shrinking temporal scope.
                        candidate_limit = ATTRIBUTE_KEY_CURSOR_CANDIDATE_LIMIT
                        continue
                    failed_width = current_segment_end - current_segment_start
                    exact_floor = min(
                        ATTRIBUTE_KEY_CURSOR_EXACT_MIN_SEGMENT,
                        current_segment_end - start,
                    )
                    if failed_width > exact_floor:
                        retry_width = max(exact_floor, failed_width / 2)
                        if cursor_before is not None:
                            anchor_checkpoint_window(
                                cursor_before,
                                width=retry_width,
                            )
                        else:
                            active_segment_start = None
                            empty_segment_width = retry_width
                        continue
                    # No physical progress was certified. The invariant below
                    # turns this into an honest bounded-read failure instead of
                    # falsely claiming absence or returning a looping cursor.
                    break
                if (
                    is_read_budget_error(exc)
                    and active_segment_start is not None
                    and cursor_before is not None
                    and current_segment_end - active_segment_start
                    > ATTRIBUTE_READ_EXPLICIT_SEGMENT
                ):
                    # Recover a cursor from a wider rolling-deploy slice at the
                    # ordinary six-hour frontier before applying the dense
                    # checkpoint recut below.
                    anchor_checkpoint_window(cursor_before)
                    continue
                if is_read_budget_error(exc) and candidate_limit > (
                    ATTRIBUTE_KEY_CURSOR_CANDIDATE_LIMIT
                ):
                    # An expanded duplicate-only replay is an accelerator, not
                    # a correctness requirement. Retry the same physical
                    # checkpoint at the production-qualified base size.
                    candidate_limit = ATTRIBUTE_KEY_CURSOR_CANDIDATE_LIMIT
                    continue
                if (
                    is_read_budget_error(exc)
                    and active_segment_start is not None
                    and cursor_before is not None
                    and current_segment_end - active_segment_start
                    > ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT
                ):
                    # A prior ordered page already proved this checkpoint.
                    # Re-anchor the identical keyset frontier inside the
                    # production-safe five-minute slice; no row at or below the
                    # checkpoint is skipped, and the failed statement publishes
                    # no progress.
                    anchor_checkpoint_window(
                        cursor_before,
                        width=ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT,
                    )
                    continue
                if (
                    is_read_budget_error(exc)
                    and active_segment_start is not None
                    and cursor_before is not None
                    and current_segment_end - active_segment_start
                    > ATTRIBUTE_KEY_CURSOR_DENSE_RETRY_MIN_SEGMENT
                ):
                    # Five minutes is not a universal generic-key density floor.
                    # Preserve the exact keyset checkpoint and recut only the
                    # unconsumed physical slice.  The failed statement proved
                    # nothing and therefore cannot advance the public cursor.
                    anchor_checkpoint_window(
                        cursor_before,
                        width=ATTRIBUTE_KEY_CURSOR_DENSE_RETRY_MIN_SEGMENT,
                    )
                    continue
                if (
                    is_read_budget_error(exc)
                    and active_segment_start is None
                    and empty_segment_width
                    > ATTRIBUTE_KEY_CURSOR_EMPTY_SEGMENT_SOFT_LIMIT
                ):
                    # The speculative wide empty probe proved nothing. Retry
                    # the same unconsumed range at the ordinary safe ceiling;
                    # no cursor checkpoint has moved.
                    max_empty_segment_width = min(
                        max_empty_segment_width,
                        ATTRIBUTE_KEY_CURSOR_EMPTY_SEGMENT_SOFT_LIMIT,
                    )
                    empty_segment_width = max_empty_segment_width
                    continue
                if (
                    is_read_budget_error(exc)
                    and empty_segment_width > ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT
                ):
                    max_empty_segment_width = min(
                        max_empty_segment_width,
                        ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT,
                    )
                    if cursor_before is not None:
                        anchor_checkpoint_window(
                            cursor_before,
                            width=max_empty_segment_width,
                        )
                    else:
                        empty_segment_width = max_empty_segment_width
                    continue
                if (
                    is_read_budget_error(exc)
                    and empty_segment_width
                    > ATTRIBUTE_KEY_CURSOR_DENSE_RETRY_MIN_SEGMENT
                ):
                    # An empty temporal continuation has no row checkpoint, but
                    # its segment end is still frozen. Retry the identical
                    # unconsumed frontier at the dense floor; publish movement
                    # only after that smaller statement completes.
                    max_empty_segment_width = min(
                        max_empty_segment_width,
                        ATTRIBUTE_KEY_CURSOR_DENSE_RETRY_MIN_SEGMENT,
                    )
                    if cursor_before is not None:
                        anchor_checkpoint_window(
                            cursor_before,
                            width=max_empty_segment_width,
                        )
                    else:
                        empty_segment_width = max_empty_segment_width
                    continue
                raise
            candidate_pages += 1
            try:
                replay_candidate_ids = candidate_ids
                expanded_replay_fallback_available = (
                    candidate_limit > ATTRIBUTE_KEY_CURSOR_CANDIDATE_LIMIT
                    and self._query_count + 2 <= ATTRIBUTE_READ_MAX_QUERY_COUNT
                )
                force_authoritative_replay = False
                while True:
                    try:
                        rows = self._verify_latest(
                            sql=(
                                _LATEST_TARGET_SQL
                                if exact_key is not None
                                else _LATEST_BROWSE_SQL
                            ),
                            project_ids=projects,
                            candidate_ids=replay_candidate_ids,
                            attribute_key=exact_key,
                            query_timeout_ms=(
                                None
                                if force_authoritative_replay
                                else ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS
                                if exact_key is not None
                                and len(replay_candidate_ids)
                                > ATTRIBUTE_KEY_CURSOR_CANDIDATE_LIMIT
                                else generic_replay_timeout_ms(
                                    len(replay_candidate_ids),
                                )
                            ),
                        )
                    except Exception as exc:
                        if (
                            is_read_budget_error(exc)
                            and exact_key is None
                            and len(replay_candidate_ids) > 1
                        ):
                            if force_authoritative_replay:
                                # The smaller exact replay already owned the
                                # remaining wall. A further retry cannot be
                                # guaranteed to fit and no identity from this
                                # candidate page has been certified.
                                raise
                            if self._query_count >= ATTRIBUTE_READ_MAX_QUERY_COUNT:
                                # The loop admitted one candidate/replay pair.
                                # If that authoritative replay consumed the last
                                # statement slot, do not manufacture an over-cap
                                # retry; no identity in this batch is proven.
                                raise
                            if expanded_replay_fallback_available:
                                # Preserve the historical base-size fallback for
                                # an optional 128/256/512-row accelerator. The
                                # already-complete candidate proof makes its
                                # newest 64-row prefix authoritative without
                                # repeating the ordered ClickHouse scan.
                                replay_candidate_ids = replay_candidate_ids[
                                    :ATTRIBUTE_KEY_CURSOR_CANDIDATE_LIMIT
                                ]
                                expanded_replay_fallback_available = False
                                force_authoritative_replay = True
                                continue
                            # Keep the already-complete ordered candidate page.
                            # Replaying its newest half exactly avoids paying for
                            # the same candidate sort again. The discarded suffix
                            # remains behind the last verified identity and is
                            # therefore reachable by ordinary keyset continuation.
                            replay_candidate_ids = replay_candidate_ids[
                                : max(len(replay_candidate_ids) // 2, 1)
                            ]
                            force_authoritative_replay = True
                            continue
                        raise
                    break
                if len(replay_candidate_ids) < len(candidate_ids):
                    candidate_ids = replay_candidate_ids
                    candidate_limit = len(candidate_ids)
                    segment_truncated = True
            except Exception as exc:
                if (
                    is_read_budget_error(exc)
                    and should_publish_progress_after_budget_error()
                ):
                    # Candidate identities from this failed replay are not
                    # certified. Retain only the checkpoint from a prior
                    # complete batch and let its continuation own a fresh wall.
                    break
                if is_read_budget_error(exc) and exact_key is not None:
                    if candidate_limit > 1 and len(candidate_ids) > 1:
                        # Hydrating exact latest state can be dominated by one
                        # large JSON value. Reduce only the finite replay batch;
                        # candidate/keyset progress remains unpublished until a
                        # complete replay succeeds.
                        candidate_limit = max(candidate_limit // 2, 1)
                        continue
                    failed_width = current_segment_end - current_segment_start
                    exact_floor = min(
                        ATTRIBUTE_KEY_CURSOR_EXACT_MIN_SEGMENT,
                        current_segment_end - start,
                    )
                    if failed_width > exact_floor:
                        retry_width = max(exact_floor, failed_width / 2)
                        if cursor_before is not None:
                            anchor_checkpoint_window(
                                cursor_before,
                                width=retry_width,
                            )
                        else:
                            active_segment_start = None
                            empty_segment_width = retry_width
                        continue
                    # Preserve the unchanged physical cursor; the strict
                    # progress invariant below converts this into an honest
                    # unavailable response rather than a looping continuation.
                    break
                if (
                    is_read_budget_error(exc)
                    and active_segment_start is not None
                    and cursor_before is not None
                    and current_segment_end - active_segment_start
                    > ATTRIBUTE_READ_EXPLICIT_SEGMENT
                ):
                    # The expanded batch was not verified, so discard it and
                    # retry from the last already-proven checkpoint only.
                    anchor_checkpoint_window(cursor_before)
                    continue
                if is_read_budget_error(exc) and candidate_limit > (
                    ATTRIBUTE_KEY_CURSOR_CANDIDATE_LIMIT
                ):
                    # Latest-state replay did not certify the expanded batch,
                    # so retry from the unchanged physical checkpoint. Never
                    # publish progress from an incomplete accelerator.
                    candidate_limit = ATTRIBUTE_KEY_CURSOR_CANDIDATE_LIMIT
                    continue
                raise
            rows_by_identity = {self._physical_identity(row): row for row in rows}
            fully_processed_identity = cursor_before
            for identity in candidate_ids:
                row = rows_by_identity.get(identity)
                if row is None or not self._row_is_active_in_window(row, start, end):
                    fully_processed_identity = identity
                    continue
                fully_consumed, next_offset = consume_keys(row_keys(row))
                if not fully_consumed:
                    next_resume_identity = identity
                    next_resume_key_offset = next_offset
                    break
                fully_processed_identity = identity
                if page_is_full():
                    break

            cursor_before = fully_processed_identity
            if next_resume_identity is not None or page_is_full():
                active_segment_start = current_segment_start
                break
            if segment_truncated and candidate_ids:
                cursor_before = candidate_ids[-1]
                if (
                    current_segment_start
                    < current_segment_end - ATTRIBUTE_READ_EXPLICIT_SEGMENT
                ):
                    # The ordered page proves all identities above this point
                    # were consumed. Continue through an ordinary keyset slice
                    # instead of issuing another statement over the wide range.
                    anchor_checkpoint_window(
                        cursor_before,
                        width=(
                            ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT
                            if exact_key is not None
                            else ATTRIBUTE_READ_EXPLICIT_SEGMENT
                        ),
                    )
                    continue
                active_segment_start = current_segment_start
                if not emitted:
                    if exact_key is None:
                        candidate_limit = min(
                            candidate_limit * 2,
                            ATTRIBUTE_KEY_CURSOR_MAX_CANDIDATE_LIMIT,
                        )
                else:
                    candidate_limit = ATTRIBUTE_KEY_CURSOR_CANDIDATE_LIMIT
                continue
            current_segment_end = current_segment_start
            cursor_before = None
            active_segment_start = None
            candidate_limit = ATTRIBUTE_KEY_CURSOR_CANDIDATE_LIMIT
            if candidate_ids and exact_key is None:
                empty_segment_width = min(
                    ATTRIBUTE_READ_EXPLICIT_SEGMENT,
                    max_empty_segment_width,
                )
            else:
                # A fully replayed exact slice with only stale/deleted or
                # unsupported candidates is just as strong an absence proof as
                # an empty candidate page. Widen the next adjacent slice; any
                # density cliff still falls through the exact halving path.
                empty_segment_width = min(
                    empty_segment_width * 2,
                    (
                        min(
                            max_empty_segment_width,
                            ATTRIBUTE_KEY_CURSOR_EXACT_MAX_EMPTY_SEGMENT,
                        )
                        if exact_key is not None
                        else max_empty_segment_width
                    ),
                )

        next_checkpoint = next_resume_identity or cursor_before
        if (
            next_checkpoint is not None
            and active_segment_start is not None
            and current_segment_end - active_segment_start
            > ATTRIBUTE_READ_EXPLICIT_SEGMENT
        ):
            # Page-size and request-budget exits can occur directly after a
            # successful widened candidate/replay pair. Publish that proven
            # point in the rolling-compatible five-field cursor shape.
            anchor_checkpoint_window(
                next_checkpoint,
                width=(
                    ATTRIBUTE_KEY_CURSOR_MIN_SEGMENT
                    if exact_key is not None
                    else ATTRIBUTE_READ_EXPLICIT_SEGMENT
                ),
            )

        # A positive exact search is terminal even when older physical rows
        # remain: one verified latest-state occurrence proves the selectable
        # key exists, and consumers deliberately do not treat its observed type
        # set as complete tenant-wide coverage.
        exhausted = (
            (exact_key is not None and bool(emitted) and not exhaustive_exact_types)
            or current_segment_end <= start
            and next_resume_identity is None
        )
        seen_after = (
            (*seen, *emitted_digests)
            if resolved_seen_count == len(seen)
            else tuple(emitted_digests)
        )
        browse_status: AttributeKeyBrowseStatus = (
            "exhausted" if exhausted else "continuation"
        )
        has_more = browse_status == "continuation"
        next_segment_start = (
            active_segment_start
            if has_more and next_checkpoint is not None
            else max(start, current_segment_end - empty_segment_width)
            if has_more and exact_key is not None
            else None
        )
        if next_segment_start is not None and exact_key is None:
            legacy_segment_start = max(
                start,
                current_segment_end - ATTRIBUTE_READ_EXPLICIT_SEGMENT,
            )
            if next_segment_start >= legacy_segment_start:
                # A six-hour legacy slice contains this checkpoint even when
                # the in-request budget fallback narrowed its active query to
                # five minutes. Publish the five-field rolling-deploy format;
                # re-reading the larger prefix is safe under before_identity.
                next_segment_start = None
        returned_before_identity = (
            None if next_resume_identity is not None else cursor_before
        )
        if exact_key is not None and has_more:
            next_cursor_state = (
                current_segment_end,
                returned_before_identity,
                next_resume_identity,
                next_resume_key_offset,
            )
            if next_cursor_state == initial_cursor_state:
                # Never emit a successful cursor that asks the caller to repeat
                # the identical unproven physical read forever. The API boundary
                # sanitizes this bounded read failure; no ClickHouse diagnostic
                # or false "missing key" result reaches the user.
                raise ReadDeadlineExceeded(
                    "Exact attribute-key cursor made no physical progress"
                )
        # A successful cursor page is a complete request, not a sampled
        # aggregate. Endpoint-specific browse_status communicates whether the
        # retained-data walk can continue or exhausted its frozen window. The
        # persistent exact de-duplication set keeps every continuation safe.
        metadata = self._metadata(
            complete=True,
            error_code=None,
            window_start=start,
            window_end=end,
            query_count=self._query_count,
        )
        return AttributeKeyCursorPageRead(
            tuple(emitted[digest] for digest in emitted_order),
            metadata,
            has_more,
            browse_status,
            current_segment_end,
            returned_before_identity,
            next_resume_identity,
            next_resume_key_offset,
            seen_after,
            next_segment_start,
            tuple(emitted_digests),
            resolved_seen_count + len(emitted_digests),
        )

    def read_value_cursor_page(
        self,
        project_ids: Iterable[Any],
        key: str,
        *,
        page_size: int,
        window_start: datetime,
        window_end: datetime,
        segment_end: datetime | None = None,
        segment_start: datetime | None = None,
        before_identity: PhysicalSpanIdentity | None = None,
        resume_identity: PhysicalSpanIdentity | None = None,
        resume_member_offset: int = 0,
        seen_value_digests: Iterable[str] = (),
        seen_value_contains: Callable[[str], bool] | None = None,
        seen_value_count: int | None = None,
        search: str | None = None,
        attribute_type: AttributeType | None = None,
        continue_operation: bool = False,
    ) -> AttributeValueCursorPageRead:
        """Return a bounded newest-first page of verified unique values.

        Cursor mode deliberately walks narrow physical-span batches instead of
        running an unbounded ``DISTINCT`` across a year of Map/JSON data.  Each
        selected identity is replayed through ``argMax(_version)`` so only its
        current latest state can publish a value. A signed API cursor carries
        the next physical key and digests of values already emitted, so later
        pages neither repeat options nor trust client state. Each page resolves
        current state independently because ClickHouse 25.3 cannot preserve a
        historical ReplacingMergeTree snapshot after background merges. The
        server tracks an exact persistent de-duplication set. Its signed
        reference remains constant-size while the server-side radix grows with
        the values already published, so no page repeats a prior value and no
        retained-value ceiling is exposed to callers.

        Each returned page is exact for its ordered continuation prefix. A
        finite per-request scan budget yields another continuation rather than
        truncating the vocabulary or publishing sampled/incomplete metadata.
        ``has_more`` therefore means that older retained values remain
        reachable through the monotonic cursor; it is not an estimate or a
        claim that the whole frozen window was exhausted in this request. If a
        bounded request cannot prove any forward progress, it fails closed and
        the API returns a generic retriable response instead of a repeated or
        partial cursor.
        """

        if not continue_operation or self._deadline is None:
            self._begin_operation()
        projects = self._project_ids(project_ids)
        key = validate_attribute_key(key)
        normalized_search = validate_attribute_search(search or "")
        if attribute_type is not None and attribute_type not in _TYPE_PRIORITY:
            raise ValueError("invalid filter-value attribute type")
        page_size = int(page_size)
        if not 1 <= page_size <= ATTRIBUTE_VALUE_CURSOR_MAX_PAGE_SIZE:
            raise ValueError("filter-value page_size is out of range")
        start = _utc(window_start)
        end = _utc(window_end)
        current_segment_end = _utc(segment_end or end)
        if start >= end or not start < current_segment_end <= end:
            raise ValueError("invalid filter-value cursor window")
        if not projects:
            return AttributeValueCursorPageRead(
                (),
                self._metadata(
                    complete=True,
                    error_code=None,
                    window_start=start,
                    window_end=end,
                    query_count=self._query_count,
                ),
                False,
                start,
                None,
                None,
                0,
                (),
                "exhausted",
            )
        if before_identity is not None:
            before_identity = (
                str(before_identity[0]),
                str(before_identity[1]),
                str(before_identity[2]),
                _utc(before_identity[3]),
            )
            if (
                before_identity[0] not in projects
                or not start <= before_identity[3] < current_segment_end
            ):
                raise ValueError("invalid filter-value physical cursor")
        if resume_identity is not None:
            resume_identity = (
                str(resume_identity[0]),
                str(resume_identity[1]),
                str(resume_identity[2]),
                _utc(resume_identity[3]),
            )
            if (
                resume_identity[0] not in projects
                or not start <= resume_identity[3] < current_segment_end
                or int(resume_member_offset) < 0
            ):
                raise ValueError("invalid filter-value member cursor")
        elif int(resume_member_offset) != 0:
            raise ValueError("member offset requires a resume identity")

        seen = tuple(dict.fromkeys(str(value) for value in seen_value_digests))
        if any(
            len(value) != 32 or any(char not in "0123456789abcdef" for char in value)
            for value in seen
        ):
            raise ValueError("invalid filter-value seen-value state")
        seen_set = set(seen)
        resolved_seen_count = (
            len(seen) if seen_value_count is None else int(seen_value_count)
        )
        if resolved_seen_count < len(seen) or resolved_seen_count < 0:
            raise ValueError("invalid filter-value seen-value state")

        active_segment_start = (
            _utc(segment_start) if segment_start is not None else None
        )
        if active_segment_start is not None and (
            not start <= active_segment_start < current_segment_end
            or current_segment_end - active_segment_start
            > ATTRIBUTE_VALUE_CURSOR_MAX_EMPTY_SEGMENT
        ):
            raise ValueError("invalid filter-value segment cursor")

        # Rolling compatibility for cursors issued before candidate segment
        # bounds preserved DateTime64(6) precision, and for callers that pass a
        # bare keyset checkpoint without its segment bounds. Keep the proven
        # keyset and move only the segment end backward so the checkpoint stays
        # inside the exact first slice selected for this request. The keyset
        # predicate still reaches lower identities at the same timestamp, and
        # the following adjacent segment starts below it, so recovery neither
        # repeats nor skips a physical row.
        incoming_checkpoint = resume_identity or before_identity
        fresh_value_cursor = incoming_checkpoint is None and resolved_seen_count == 0
        incoming_segment_width = (
            current_segment_end - active_segment_start
            if active_segment_start is not None
            else ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT
            if normalized_search
            else ATTRIBUTE_VALUE_CURSOR_INITIAL_SEGMENT
            if fresh_value_cursor
            else ATTRIBUTE_READ_EXPLICIT_SEGMENT
        )
        if (
            incoming_checkpoint is not None
            and incoming_checkpoint[3] < current_segment_end - incoming_segment_width
        ):
            current_segment_end = min(
                current_segment_end,
                incoming_checkpoint[3] + incoming_segment_width,
            )
        if (
            incoming_checkpoint is not None
            and active_segment_start is not None
            and not (
                active_segment_start <= incoming_checkpoint[3] < current_segment_end
            )
        ):
            raise ValueError("invalid filter-value physical cursor")

        def value_was_seen(digest: str) -> bool:
            return digest in seen_set or (
                seen_value_contains is not None and seen_value_contains(digest)
            )

        # The API boundary supplies lazy exact membership once the legacy
        # materialized prefix grows large. This keeps each request bounded
        # without terminating high-cardinality value browsing.
        effective_page_size = page_size
        emitted_digests: list[str] = []
        emitted: dict[str, AttributeValueRow] = {}
        needle = normalized_search.casefold()
        typed_cursor_read = attribute_type in {"string", "number", "boolean"}
        unpinned_cursor_read = attribute_type is None
        versioned_cursor_read = typed_cursor_read or unpinned_cursor_read
        candidate_page_query_cost = (
            4 if unpinned_cursor_read else 3 if typed_cursor_read else 2
        )
        candidate_pages = 0
        # A typed value can repeat across hundreds of adjacent spans (voice
        # ``call_id`` is the production example).  The ordered candidate read
        # already scans the same frozen segment before its LIMIT can stop, so
        # acquiring a larger finite identity prefix costs roughly the same
        # source read as 64 identities while yielding a useful value page.
        # Keep tiny/untyped/search pages on the conservative seed and
        # retain the exact replay plus configured request wall.
        dense_typed_oversample = bool(
            key == "call_id"
            and typed_cursor_read
            and not needle
            and effective_page_size >= 10
        )
        candidate_limit = (
            ATTRIBUTE_VALUE_CURSOR_DENSE_CANDIDATE_LIMIT
            if dense_typed_oversample
            else ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
        )
        cursor_before = before_identity
        # A brand-new value request starts at the production-qualified
        # five-second floor, then grows only after a complete adjacent-slice
        # proof. Signed continuations retain their next adaptive width so page N
        # does not restart at the floor. A legacy cursor carrying a physical
        # checkpoint retains the six-hour compatibility width; an empty legacy
        # frontier safely adopts the lossless five-second seed. Wider statements
        # remain speculative and fall back at the identical unconsumed frontier
        # on any read-budget error.
        empty_segment_width = (
            current_segment_end - active_segment_start
            if active_segment_start is not None
            else ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT
            if normalized_search
            else ATTRIBUTE_VALUE_CURSOR_INITIAL_SEGMENT
            if fresh_value_cursor
            else ATTRIBUTE_READ_EXPLICIT_SEGMENT
        )
        max_empty_segment_width = ATTRIBUTE_VALUE_CURSOR_MAX_EMPTY_SEGMENT
        last_successful_segment_width = empty_segment_width
        checkpoint_from_widened_segment = False
        next_resume_identity: PhysicalSpanIdentity | None = None
        next_resume_member_offset = 0
        initial_cursor_state = (
            current_segment_end,
            before_identity,
            resume_identity,
            int(resume_member_offset),
        )
        initial_segment_start = max(
            start,
            current_segment_end - empty_segment_width,
        )

        def frontier_state() -> tuple[Any, ...]:
            return (
                current_segment_end,
                cursor_before,
                next_resume_identity,
                int(next_resume_member_offset),
            )

        def request_has_progress() -> bool:
            return frontier_state() != initial_cursor_state

        def anchor_checkpoint_window(
            checkpoint: PhysicalSpanIdentity,
            *,
            width: timedelta,
        ) -> None:
            """Keep a proven keyset checkpoint inside a narrower exact slice."""

            nonlocal candidate_limit
            nonlocal checkpoint_from_widened_segment
            nonlocal current_segment_end
            nonlocal empty_segment_width
            nonlocal last_successful_segment_width

            current_segment_end = min(
                current_segment_end,
                checkpoint[3] + width,
            )
            empty_segment_width = width
            last_successful_segment_width = width
            candidate_limit = ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
            checkpoint_from_widened_segment = False

        def narrow_physical_fallback(*, width: timedelta) -> None:
            """Keep a bounded fallback and its keyset on the same frontier."""

            nonlocal empty_segment_width
            nonlocal max_empty_segment_width

            fallback_width = min(width, current_segment_end - start)
            max_empty_segment_width = min(
                max_empty_segment_width,
                fallback_width,
            )
            if cursor_before is not None:
                anchor_checkpoint_window(
                    cursor_before,
                    width=fallback_width,
                )
            else:
                empty_segment_width = fallback_width

        def value_candidate_predicate() -> tuple[
            str,
            dict[str, Any],
            dict[str, str],
        ]:
            """Push the requested key/search into the finite identity seed.

            The predicate may admit stale physical versions, but it can never
            publish them: every identity is still replayed through argMax below.
            False positives are harmless and are rechecked in Python; avoiding
            the former ``predicate=1`` is what makes a rare typed value practical
            on a high-volume project.
            """

            clauses: list[str] = []
            lane_predicates: dict[str, str] = {}
            requested_types: set[AttributeType] = (
                {attribute_type}
                if attribute_type is not None
                else {"string", "number", "boolean"}
            )
            if attribute_type is None and self._reads_json_overflow:
                requested_types.update({"array", "map", "json"})
            for attr_type, column in (
                ("string", "attrs_string"),
                ("number", "attrs_number"),
                ("boolean", "attrs_bool"),
            ):
                if attr_type not in requested_types:
                    continue
                clause = (
                    f"indexHint(has(mapKeys({column}), %(attribute_key)s)) "
                    f"AND mapContains({column}, %(attribute_key)s)"
                )
                if needle and attr_type == "string" and normalized_search.isascii():
                    # CH case folding is equivalent to Python for ASCII text,
                    # but not for values such as ``Straße``.  The second arm
                    # conservatively retains every non-ASCII value for the
                    # canonical Python search below.
                    clause = (
                        f"({clause}) AND ("
                        "positionCaseInsensitiveUTF8("
                        "attrs_string[%(attribute_key)s], "
                        "%(attribute_search)s) > 0 OR "
                        "length(attrs_string[%(attribute_key)s]) != "
                        "lengthUTF8(attrs_string[%(attribute_key)s]))"
                    )
                elif needle and attr_type == "boolean" and normalized_search.isascii():
                    clause = (
                        f"({clause}) AND positionCaseInsensitiveUTF8("
                        "if(attrs_bool[%(attribute_key)s], 'true', 'false'), "
                        "%(attribute_search)s) > 0"
                    )
                # Number formatting in ClickHouse differs from Python, and
                # JSON strings/arrays may be escaped.  Their key-bearing lanes
                # stay conservative and are filtered only after exact replay.
                clauses.append(f"({clause})")
                lane_predicates[attr_type] = clause
            if requested_types.intersection({"array", "map", "json"}):
                clause = (
                    "attributes_extra NOT IN ('', '{}', 'null') "
                    "AND JSONHas(attributes_extra, %(attribute_key)s)"
                )
                clauses.append(f"({clause})")
                lane_predicates["json"] = clause
            if not clauses:
                raise ValueError("invalid filter-value attribute type")
            return (
                " OR ".join(clauses),
                {"attribute_search": normalized_search} if needle else {},
                lane_predicates,
            )

        (
            candidate_predicate,
            candidate_predicate_params,
            proof_candidate_predicates,
        ) = value_candidate_predicate()
        proof_candidate_predicate_params = candidate_predicate_params

        def candidates_for(decoded: tuple[AttributeType, Any]) -> tuple[Any, ...]:
            attr_type, value = decoded
            if attr_type == "array":
                return value if isinstance(value, tuple) else ()
            if attr_type == "string" and not _typed_string_is_suggestible(value):
                return ()
            return () if value in (None, "") else (value,)

        def consume_decoded(
            decoded: tuple[AttributeType, Any],
            *,
            member_offset: int = 0,
        ) -> tuple[bool, int]:
            """Consume one row; return (fully_consumed, next_member_offset)."""

            attr_type = decoded[0]
            if attribute_type is not None and attr_type != attribute_type:
                return True, len(candidates_for(decoded))
            candidates = candidates_for(decoded)
            if member_offset > len(candidates):
                raise ValueError("invalid filter-value member offset")
            for index in range(member_offset, len(candidates)):
                value = candidates[index]
                display = _value_search_text(value)
                if needle and needle not in display.casefold():
                    continue
                digest = attribute_value_cursor_digest(attr_type, value)
                if value_was_seen(digest):
                    continue
                prior = emitted.get(digest)
                if prior is not None:
                    emitted[digest] = AttributeValueRow(
                        prior.value, prior.type, prior.count + 1
                    )
                    continue
                emitted[digest] = AttributeValueRow(value, attr_type, 1)
                emitted_digests.append(digest)
                if len(emitted) >= effective_page_size:
                    next_offset = index + 1
                    return next_offset >= len(candidates), next_offset
            return True, len(candidates)

        def consume_json_array(
            row: dict[str, Any],
            text: str,
            initial_position: int,
            *,
            member_offset: int,
        ) -> tuple[bool, int]:
            """Consume a bounded raw-array slice without capping its vocabulary."""

            fingerprint = self._json_array_cursor_fingerprint(row, text)
            state = self._decode_json_array_member_cursor(
                member_offset,
                fingerprint=fingerprint,
                initial_position=initial_position,
                text_length=len(text),
            )
            scan_chars = 0
            scanned_members = 0
            total_string_bytes = 0
            row_member_digests: set[str] = set()

            def continuation(next_state: _JsonArrayCursorState) -> tuple[bool, int]:
                return False, self._encode_json_array_member_cursor(
                    fingerprint,
                    next_state,
                )

            def add_member(value: JsonScalar) -> bool:
                """Publish one locally unique member; return whether page filled."""

                digest = attribute_value_cursor_digest("array", value)
                if digest in row_member_digests:
                    return False
                row_member_digests.add(digest)
                if needle and needle not in _value_search_text(value).casefold():
                    return False
                if value_was_seen(digest):
                    return False
                prior = emitted.get(digest)
                if prior is not None:
                    emitted[digest] = AttributeValueRow(
                        prior.value,
                        prior.type,
                        prior.count + 1,
                    )
                    return False
                emitted[digest] = AttributeValueRow(value, "array", 1)
                emitted_digests.append(digest)
                return len(emitted) >= effective_page_size

            def decoded_scalar(token: str) -> JsonScalar | None:
                try:
                    member = json.loads(token)
                except (TypeError, ValueError, json.JSONDecodeError):
                    raise ValueError("invalid filter-value JSON array") from None
                if member is None or member == "":
                    return None
                if isinstance(member, bool):
                    return member
                if isinstance(member, str):
                    return member
                if isinstance(member, int):
                    return member if -(1 << 63) <= member <= (1 << 64) - 1 else None
                if isinstance(member, float) and math.isfinite(member):
                    return member
                return None

            def advance_after_member(
                position: int,
            ) -> tuple[_JsonArrayCursorState | None, bool]:
                """Reach the next member boundary or exact array exhaustion."""

                nonlocal scan_chars
                while position < len(text) and text[position] in " \t\r\n":
                    if scan_chars >= ATTRIBUTE_VALUE_CURSOR_JSON_MAX_SCAN_CHARS:
                        return (
                            _JsonArrayCursorState(
                                position,
                                _JSON_ARRAY_CURSOR_MODE_PRIMITIVE,
                            ),
                            False,
                        )
                    position += 1
                    scan_chars += 1
                if position >= len(text):
                    raise ValueError("invalid filter-value JSON array")
                delimiter = text[position]
                scan_chars += 1
                if delimiter == ",":
                    return _JsonArrayCursorState(position + 1), False
                if delimiter == "]":
                    return None, True
                raise ValueError("invalid filter-value JSON array")

            while True:
                if state.mode == _JSON_ARRAY_CURSOR_MODE_BOUNDARY:
                    position = state.position
                    while position < len(text) and text[position] in " \t\r\n":
                        if scan_chars >= ATTRIBUTE_VALUE_CURSOR_JSON_MAX_SCAN_CHARS:
                            return continuation(_JsonArrayCursorState(position))
                        position += 1
                        scan_chars += 1
                    if position >= len(text):
                        raise ValueError("invalid filter-value JSON array")
                    if text[position] == "]":
                        return True, 0
                    if (
                        scanned_members
                        >= ATTRIBUTE_VALUE_CURSOR_JSON_MAX_SCANNED_MEMBERS
                    ):
                        return continuation(_JsonArrayCursorState(position))
                    member_start = position
                    leading = text[position]

                    if leading == '"':
                        position += 1
                        scan_chars += 1
                        escaped = False
                        closed = False
                        while position < len(text):
                            if scan_chars >= ATTRIBUTE_VALUE_CURSOR_JSON_MAX_SCAN_CHARS:
                                # A potentially selectable token must restart
                                # intact. This can occur only after prior proven
                                # progress because a fresh request has >32 KiB.
                                return continuation(_JsonArrayCursorState(member_start))
                            character = text[position]
                            position += 1
                            scan_chars += 1
                            if escaped:
                                escaped = False
                            elif character == "\\":
                                escaped = True
                            elif character == '"':
                                closed = True
                                break
                            if (
                                position - member_start
                                >= ATTRIBUTE_VALUE_CURSOR_JSON_MAX_SELECTABLE_TOKEN_CHARS
                            ):
                                # This token cannot decode below the public
                                # 4-KiB per-string contract. Continue skipping
                                # it incrementally, retaining escape state.
                                return continuation(
                                    _JsonArrayCursorState(
                                        position,
                                        _JSON_ARRAY_CURSOR_MODE_STRING,
                                        int(escaped),
                                    )
                                )
                        if not closed:
                            raise ValueError("invalid filter-value JSON array")
                        member = decoded_scalar(text[member_start:position])
                        scanned_members += 1
                        page_filled = False
                        if isinstance(member, str):
                            member_bytes = len(member.encode("utf-8"))
                            if member_bytes <= JSON_ARRAY_FILTER_MAX_STRING_UTF8_BYTES:
                                if (
                                    total_string_bytes + member_bytes
                                    > JSON_ARRAY_FILTER_MAX_TOTAL_STRING_UTF8_BYTES
                                ):
                                    # Do not consume the member. A fresh request
                                    # gets a fresh byte allowance and publishes
                                    # it from this exact character boundary.
                                    return continuation(
                                        _JsonArrayCursorState(member_start)
                                    )
                                total_string_bytes += member_bytes
                                page_filled = add_member(member)
                            # Oversized strings are intentionally unfilterable,
                            # but consuming them does not hide later members.
                        elif member is not None:
                            page_filled = add_member(member)
                        state, exhausted = advance_after_member(position)
                        if exhausted:
                            return True, 0
                        assert state is not None
                        if page_filled:
                            return continuation(state)
                        continue

                    if leading in "[{":
                        scan_chars += 1
                        scanned_members += 1
                        state = _JsonArrayCursorState(
                            position + 1,
                            _JSON_ARRAY_CURSOR_MODE_NESTED,
                            1,
                        )
                        continue

                    token_end = position
                    while token_end < len(text) and text[token_end] not in " \t\r\n,]":
                        if (
                            token_end - member_start >= 128
                            or scan_chars >= ATTRIBUTE_VALUE_CURSOR_JSON_MAX_SCAN_CHARS
                        ):
                            # Every supported JSON number/literal fits in this
                            # prefix; a longer primitive is unfilterable.
                            return continuation(
                                _JsonArrayCursorState(
                                    token_end,
                                    _JSON_ARRAY_CURSOR_MODE_PRIMITIVE,
                                )
                            )
                        token_end += 1
                        scan_chars += 1
                    if token_end == member_start:
                        raise ValueError("invalid filter-value JSON array")
                    member = decoded_scalar(text[member_start:token_end])
                    scanned_members += 1
                    page_filled = member is not None and add_member(member)
                    state, exhausted = advance_after_member(token_end)
                    if exhausted:
                        return True, 0
                    assert state is not None
                    if page_filled:
                        return continuation(state)
                    continue

                if state.mode == _JSON_ARRAY_CURSOR_MODE_STRING:
                    position = state.position
                    escaped = bool(state.auxiliary)
                    while position < len(text):
                        if scan_chars >= ATTRIBUTE_VALUE_CURSOR_JSON_MAX_SCAN_CHARS:
                            return continuation(
                                _JsonArrayCursorState(
                                    position,
                                    _JSON_ARRAY_CURSOR_MODE_STRING,
                                    int(escaped),
                                )
                            )
                        character = text[position]
                        position += 1
                        scan_chars += 1
                        if escaped:
                            escaped = False
                        elif character == "\\":
                            escaped = True
                        elif character == '"':
                            scanned_members += 1
                            state, exhausted = advance_after_member(position)
                            if exhausted:
                                return True, 0
                            assert state is not None
                            break
                    else:
                        raise ValueError("invalid filter-value JSON array")
                    continue

                if state.mode == _JSON_ARRAY_CURSOR_MODE_NESTED:
                    position = state.position
                    depth = state.auxiliary & (_JSON_ARRAY_CURSOR_NESTED_IN_STRING - 1)
                    in_string = bool(
                        state.auxiliary & _JSON_ARRAY_CURSOR_NESTED_IN_STRING
                    )
                    escaped = bool(state.auxiliary & _JSON_ARRAY_CURSOR_NESTED_ESCAPE)
                    while position < len(text):
                        if scan_chars >= ATTRIBUTE_VALUE_CURSOR_JSON_MAX_SCAN_CHARS:
                            auxiliary = depth
                            if in_string:
                                auxiliary |= _JSON_ARRAY_CURSOR_NESTED_IN_STRING
                            if escaped:
                                auxiliary |= _JSON_ARRAY_CURSOR_NESTED_ESCAPE
                            return continuation(
                                _JsonArrayCursorState(
                                    position,
                                    _JSON_ARRAY_CURSOR_MODE_NESTED,
                                    auxiliary,
                                )
                            )
                        character = text[position]
                        position += 1
                        scan_chars += 1
                        if in_string:
                            if escaped:
                                escaped = False
                            elif character == "\\":
                                escaped = True
                            elif character == '"':
                                in_string = False
                            continue
                        if character == '"':
                            in_string = True
                        elif character in "[{":
                            depth += 1
                            if depth >= _JSON_ARRAY_CURSOR_NESTED_IN_STRING:
                                raise ValueError("invalid filter-value JSON array")
                        elif character in "]}":
                            depth -= 1
                            if depth < 0:
                                raise ValueError("invalid filter-value JSON array")
                            if depth == 0:
                                state, exhausted = advance_after_member(position)
                                if exhausted:
                                    return True, 0
                                assert state is not None
                                break
                    else:
                        raise ValueError("invalid filter-value JSON array")
                    continue

                # Oversized primitive or post-member whitespace. Both are
                # already intentionally consumed; walk only to the delimiter.
                position = state.position
                while position < len(text):
                    if scan_chars >= ATTRIBUTE_VALUE_CURSOR_JSON_MAX_SCAN_CHARS:
                        return continuation(
                            _JsonArrayCursorState(
                                position,
                                _JSON_ARRAY_CURSOR_MODE_PRIMITIVE,
                            )
                        )
                    character = text[position]
                    position += 1
                    scan_chars += 1
                    if character == ",":
                        state = _JsonArrayCursorState(position)
                        break
                    if character == "]":
                        return True, 0
                else:
                    raise ValueError("invalid filter-value JSON array")

        def consume_cursor_row(
            row: dict[str, Any],
            *,
            member_offset: int = 0,
        ) -> tuple[bool, int]:
            """Consume one latest row, streaming actionable JSON arrays."""

            typed_value_present = any(
                bool(row.get(field))
                for field in (
                    "string_present",
                    "number_present",
                    "boolean_present",
                )
            )
            if (
                not typed_value_present
                and bool(row.get("legacy_present"))
                and self._json_attribute_mode in {"arrays", "structured", "all"}
            ):
                array_source = self._json_array_cursor_text(row.get("legacy_value_raw"))
                if array_source is not None:
                    if attribute_type is not None and attribute_type != "array":
                        return True, 0
                    text, initial_position = array_source
                    return consume_json_array(
                        row,
                        text,
                        initial_position,
                        member_offset=member_offset,
                    )
            decoded = self._decode_target_value(
                row,
                json_attribute_mode=self._json_attribute_mode,
            )
            if decoded is None:
                return True, 0
            # Latest state may change from an array to a scalar between cursor
            # pages. Re-evaluate the scalar from its beginning; the exact seen
            # set prevents repeats and no replacement value can be skipped.
            return consume_decoded(decoded)

        if resume_identity is not None:
            resume_rows = (
                self._hydrate_latest_unpinned_values(
                    project_ids=projects,
                    candidate_ids=(resume_identity,),
                    attribute_key=key,
                )
                if unpinned_cursor_read
                else self._verify_latest(
                    # A typed cursor cannot resume inside a scalar value, but an
                    # older signed cursor can still carry a defensive checkpoint.
                    # Keep that replay exact without pulling the legacy JSON
                    # column for a request pinned to a typed Map family.
                    sql=(
                        _LATEST_TYPED_TARGET_SQL
                        if typed_cursor_read
                        else _LATEST_TARGET_SQL
                    ),
                    project_ids=projects,
                    candidate_ids=(resume_identity,),
                    attribute_key=key,
                )
            )
            resume_row = resume_rows[0] if resume_rows else None
            if resume_row is not None and self._row_is_active_in_window(
                resume_row, start, end
            ):
                fully_consumed, next_offset = consume_cursor_row(
                    resume_row,
                    member_offset=int(resume_member_offset),
                )
                if not fully_consumed:
                    next_resume_identity = resume_identity
                    next_resume_member_offset = next_offset
                else:
                    cursor_before = resume_identity
            else:
                cursor_before = resume_identity

        # A search or continuation can cheaply prove that a whole adjacent
        # temporal slice contains no relevant *new* logical value. The query
        # returns a complete raw-value superset and never publishes a value: an
        # unseen match, result sentinel, oversized array, or bounded-read
        # failure falls back to the ordinary latest-state cursor at the
        # identical unconsumed frontier. A searched first page can therefore
        # skip a slice only when the raw superset contains no matching value;
        # later pages may additionally skip matches already named by their
        # server-held digests.
        skip_physical_walk = False
        distinct_proof_supported = unpinned_cursor_read or typed_cursor_read
        distinct_proof_count = 0
        if (
            (resolved_seen_count or needle)
            and distinct_proof_supported
            and next_resume_identity is None
            and len(emitted) < effective_page_size
        ):
            distinct_width = ATTRIBUTE_VALUE_CURSOR_DISTINCT_INITIAL_SEGMENT
            if active_segment_start is not None:
                # ``next_segment_start`` carries the last fully certified
                # width without expanding the signed cursor schema.  Reuse it
                # on the next request so a dense steady-state walk does not
                # restart at 5s forever; an over-dense frontier still halves
                # this width before any public progress is published.
                distinct_width = min(
                    max(
                        current_segment_end - active_segment_start,
                        ATTRIBUTE_VALUE_CURSOR_DISTINCT_MIN_SEGMENT,
                    ),
                    ATTRIBUTE_VALUE_CURSOR_DISTINCT_MAX_SEGMENT,
                )
            distinct_advanced = False
            failed_distinct_ceiling: timedelta | None = None
            while current_segment_end > start:
                if distinct_proof_count >= (
                    ATTRIBUTE_VALUE_CURSOR_MAX_SEARCH_PROOFS
                    if needle
                    else ATTRIBUTE_VALUE_CURSOR_MAX_UNSEARCHED_CONTINUATION_PROOFS
                ):
                    if failed_distinct_ceiling is not None and not distinct_advanced:
                        narrow_physical_fallback(
                            width=ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
                        )
                    skip_physical_walk = distinct_advanced
                    break
                assert self._deadline is not None
                remaining_ms = int((self._deadline - self._clock()) * 1000)
                # Before the first proof, preserve the complete physical
                # fallback budget.  Once a complete slice has advanced the
                # public frontier, the exact response can end at that
                # checkpoint; reserving an unrelated four-statement fallback
                # would prevent adaptive proof growth inside the same request.
                proof_query_reserve = (
                    0
                    if distinct_advanced
                    else ATTRIBUTE_VALUE_CURSOR_DISTINCT_QUERY_RESERVE
                )
                proof_wall_reserve_ms = (
                    0
                    if distinct_advanced
                    else ATTRIBUTE_VALUE_CURSOR_DISTINCT_WALL_RESERVE_MS
                )
                planned_proof_timeout_ms = ATTRIBUTE_VALUE_CURSOR_DISTINCT_TIMEOUT_MS
                if (
                    self._query_count + 1 + proof_query_reserve
                    > ATTRIBUTE_READ_MAX_QUERY_COUNT
                    or remaining_ms
                    <= proof_wall_reserve_ms
                    + planned_proof_timeout_ms
                    + ATTRIBUTE_VALUE_CURSOR_DISTINCT_GUARD_MARGIN_MS
                ):
                    # When at least one complete slice was certified, returning
                    # its advanced continuation is more useful than spending
                    # the fallback reserve re-reading known duplicates.  With
                    # no proof yet, retain the original physical path.
                    if failed_distinct_ceiling is not None and not distinct_advanced:
                        narrow_physical_fallback(
                            width=distinct_width,
                        )
                    skip_physical_walk = distinct_advanced
                    break

                proof_end = current_segment_end
                if cursor_before is not None:
                    # DateTime64(6) identities at the checkpoint timestamp but
                    # lower in `(id, trace_id, project_id)` remain unconsumed.
                    # Include that tail while excluding the checkpoint itself.
                    proof_end = min(
                        proof_end,
                        cursor_before[3] + timedelta(microseconds=1),
                    )
                proof_start = max(start, proof_end - distinct_width)
                if proof_start >= proof_end:
                    break
                # The proof can advance only when every relevant raw value is
                # already known.  Include the exact persisted digest prefix,
                # this page's maximum resumable emissions, and one overflow
                # sentinel; a vocabulary larger than that remains an ordinary
                # ordered fallback at the unchanged physical frontier.
                distinct_limit = min(
                    resolved_seen_count + effective_page_size + 1,
                    ATTRIBUTE_VALUE_CURSOR_PROOF_MAX_RESULT_ROWS,
                )
                proof_timeout_ms = planned_proof_timeout_ms
                distinct_proof_count += 1
                try:
                    distinct_rows = self._seen_value_slice_groups(
                        project_ids=projects,
                        attribute_key=key,
                        attribute_type=attribute_type,
                        search=normalized_search,
                        candidate_predicates=proof_candidate_predicates,
                        candidate_predicate_params=(proof_candidate_predicate_params),
                        segment=(proof_start, proof_end),
                        before_identity=cursor_before,
                        distinct_limit=distinct_limit,
                        query_timeout_ms=proof_timeout_ms,
                    )
                    proof_query_time_ms = self._last_query_time_ms
                    proof_read_bytes = self._last_query_read_bytes
                except Exception as exc:
                    if not is_read_budget_error(exc):
                        raise
                    failed_distinct_ceiling = (
                        distinct_width
                        if failed_distinct_ceiling is None
                        else min(failed_distinct_ceiling, distinct_width)
                    )
                    if distinct_width > ATTRIBUTE_VALUE_CURSOR_DISTINCT_MIN_SEGMENT:
                        distinct_width = max(
                            ATTRIBUTE_VALUE_CURSOR_DISTINCT_MIN_SEGMENT,
                            distinct_width / 2,
                        )
                        continue
                    # The minimum slice proved nothing.  The physical cursor is
                    # still exactly where it was before the failed statement.
                    # If earlier slices did advance, return that proven
                    # continuation instead of spending their remaining wall
                    # reserve on an unrelated physical fallback.  The next
                    # request retries this untouched frontier with a fresh
                    # operation deadline.
                    if not distinct_advanced:
                        # A signed continuation may remember a much wider
                        # successful slice from a previous request.  Once the
                        # exact proof at this frontier has shown that even the
                        # minimum slice is dense, do not waste the remaining
                        # wall budget probing that stale wide width first.
                        narrow_physical_fallback(
                            width=ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
                        )
                    skip_physical_walk = distinct_advanced
                    break

                proof_is_complete = len(distinct_rows) < distinct_limit
                if proof_is_complete:
                    for distinct_row in distinct_rows:
                        decoded, decoded_complete = self._decode_seen_value_slice_group(
                            distinct_row,
                            json_attribute_mode=self._json_attribute_mode,
                        )
                        if not decoded_complete:
                            proof_is_complete = False
                            break
                        if decoded is None:
                            continue
                        attr_type, raw_value = decoded
                        if attribute_type is not None and attr_type != attribute_type:
                            continue
                        relevant_candidates = (
                            candidate
                            for candidate in candidates_for(decoded)
                            if not needle
                            or needle in _value_search_text(candidate).casefold()
                        )
                        if any(
                            (
                                attribute_value_cursor_digest(attr_type, candidate)
                                not in emitted
                                and not value_was_seen(
                                    attribute_value_cursor_digest(attr_type, candidate)
                                )
                            )
                            for candidate in relevant_candidates
                        ):
                            proof_is_complete = False
                            break
                if not proof_is_complete:
                    # The speculative query changed no public state. Preserve
                    # the exact frontier for ordinary ordered discovery.  A
                    # prior successful proof is still useful progress, so
                    # publish that continuation and let the next request start
                    # the physical fallback with its full wall budget.
                    skip_physical_walk = distinct_advanced
                    break

                current_segment_end = proof_start
                cursor_before = None
                checkpoint_from_widened_segment = False
                candidate_limit = ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
                proven_width = proof_end - proof_start
                empty_segment_width = proven_width
                last_successful_segment_width = proven_width
                distinct_advanced = True
                if not distinct_rows:
                    # A searched proof with no relevant raw value is an exact
                    # empty slice. Keep walking adjacent safe slices; handing
                    # this frontier to the geometrically widened physical path
                    # would reintroduce the known Coletia byte failures.
                    empty_segment_width = proven_width
                    last_successful_segment_width = proven_width
                # A completed proof advances this exact half-open slice before
                # deciding how to read the next adjacent one. Native progress
                # is the only proactive signal of byte pressure: preserve
                # four-times headroom for unknown adjacent density, shrinking
                # a width already at one quarter of a cap and freezing
                # intermediate widths. Cheap, low-volume slices still grow
                # geometrically.
                failed_distinct_ceiling = None
                resource_utilizations = []
                for consumed, cap in (
                    (
                        proof_read_bytes,
                        _ATTRIBUTE_VALUE_PROOF_MAP_SETTINGS["max_bytes_to_read"],
                    ),
                ):
                    if consumed is not None and int(cap) > 0:
                        resource_utilizations.append(consumed / int(cap))
                peak_resource_utilization = (
                    max(resource_utilizations) if resource_utilizations else None
                )
                resource_requires_shrink = (
                    peak_resource_utilization is not None
                    and peak_resource_utilization
                    >= ATTRIBUTE_VALUE_CURSOR_DISTINCT_RESOURCE_TARGET_FRACTION
                )
                resource_allows_growth = (
                    peak_resource_utilization is None
                    or peak_resource_utilization * 2
                    < ATTRIBUTE_VALUE_CURSOR_DISTINCT_RESOURCE_TARGET_FRACTION
                )
                if resource_requires_shrink:
                    assert peak_resource_utilization is not None
                    distinct_width = proven_width
                    while (
                        distinct_width > ATTRIBUTE_VALUE_CURSOR_DISTINCT_MIN_SEGMENT
                        and peak_resource_utilization * (distinct_width / proven_width)
                        >= ATTRIBUTE_VALUE_CURSOR_DISTINCT_RESOURCE_TARGET_FRACTION
                    ):
                        distinct_width = max(
                            ATTRIBUTE_VALUE_CURSOR_DISTINCT_MIN_SEGMENT,
                            distinct_width / 2,
                        )
                    # The signed continuation field is a safe next-width hint,
                    # not a coverage claim. Carry the narrower width if the
                    # request wall ends before it can be exercised.
                    empty_segment_width = distinct_width
                elif (
                    proof_query_time_ms is not None
                    and proof_query_time_ms
                    <= ATTRIBUTE_VALUE_CURSOR_DISTINCT_GROWTH_QUERY_TIME_MS
                    and resource_allows_growth
                ):
                    distinct_width = min(
                        proven_width * 2,
                        ATTRIBUTE_VALUE_CURSOR_DISTINCT_MAX_SEGMENT,
                    )
                else:
                    distinct_width = proven_width

        while (
            current_segment_end > start
            and len(emitted) < effective_page_size
            and next_resume_identity is None
            and not skip_physical_walk
            and candidate_pages
            < (
                ATTRIBUTE_VALUE_CURSOR_MAX_CANDIDATE_PAGES
                if emitted
                else ATTRIBUTE_VALUE_CURSOR_DUPLICATE_ONLY_MAX_CANDIDATE_PAGES
            )
            # A pinned JSON page uses a candidate query plus its exact replay;
            # a pinned scalar adds a version certificate. An unpinned page can
            # additionally need the isolated JSON lane after typed hydration,
            # so reserve four queries in that worst case. This prevents the
            # hard ceiling from interrupting an exact page mid-hydration.
            and self._query_count + candidate_page_query_cost
            <= ATTRIBUTE_READ_MAX_QUERY_COUNT
        ):
            assert self._deadline is not None
            remaining_ms = int((self._deadline - self._clock()) * 1000)
            physical_batch_reserve_ms = (
                candidate_page_query_cost
                * ATTRIBUTE_VALUE_CURSOR_SPECULATIVE_TIMEOUT_MS
                + ATTRIBUTE_VALUE_CURSOR_DISTINCT_GUARD_MARGIN_MS
            )
            if request_has_progress() and remaining_ms <= physical_batch_reserve_ms:
                # Do not start a multi-statement candidate/replay batch that can
                # only fail after a prior exact checkpoint. Return that proven
                # continuation and let the next request own a fresh wall budget.
                break
            segment_start = max(start, current_segment_end - empty_segment_width)
            segment = (segment_start, current_segment_end)
            candidate_ids: tuple[PhysicalSpanIdentity, ...] = ()
            candidate_versions: dict[PhysicalSpanIdentity, int] = {}
            segment_truncated = False
            widened_probe = (
                segment_start < current_segment_end - ATTRIBUTE_READ_EXPLICIT_SEGMENT
            )
            adaptive_search_probe = (
                bool(needle)
                and current_segment_end - segment_start
                > ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT
            )
            adaptive_first_page_probe = (
                not needle
                and fresh_value_cursor
                and active_segment_start is None
                and current_segment_end - segment_start
                > ATTRIBUTE_VALUE_CURSOR_INITIAL_SEGMENT
            )
            try:
                (
                    candidate_ids,
                    segment_truncated,
                    candidate_versions,
                ) = self._candidate_ids(
                    projects,
                    segment,
                    predicate=candidate_predicate,
                    attribute_key=key,
                    ordered=True,
                    before_identity=cursor_before,
                    candidate_limit=candidate_limit,
                    predicate_params=candidate_predicate_params,
                    candidate_query_settings=_ATTRIBUTE_VALUE_CANDIDATE_MAP_SETTINGS,
                    query_timeout_ms=(
                        ATTRIBUTE_VALUE_CURSOR_DISTINCT_TIMEOUT_MS
                        if current_segment_end - segment_start
                        <= ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT
                        else ATTRIBUTE_VALUE_CURSOR_SPECULATIVE_TIMEOUT_MS
                        if adaptive_search_probe
                        or adaptive_first_page_probe
                        or widened_probe
                        or (
                            unpinned_cursor_read
                            and cursor_before is None
                            and not needle
                        )
                        or (
                            candidate_limit > ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
                            and not dense_typed_oversample
                        )
                        else None
                    ),
                    include_versions=versioned_cursor_read,
                )
            except Exception as exc:
                if (
                    is_read_budget_error(exc)
                    and adaptive_search_probe
                    and cursor_before is None
                ):
                    # A failed speculative width proves nothing. Retry the same
                    # segment end at half the width; successful earlier slices
                    # remain committed locally, but no byte of this failed
                    # interval is skipped. After the narrower slice succeeds,
                    # geometric growth may resume at its next older frontier.
                    failed_width = current_segment_end - segment_start
                    empty_segment_width = max(
                        ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
                        failed_width / 2,
                    )
                    candidate_limit = ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
                    checkpoint_from_widened_segment = False
                    continue
                if is_read_budget_error(exc) and request_has_progress():
                    # This statement certified nothing, but an earlier complete
                    # batch already moved the request-local physical frontier.
                    # Publish only that earlier checkpoint.
                    empty_segment_width = last_successful_segment_width
                    break
                if (
                    is_read_budget_error(exc)
                    and dense_typed_oversample
                    and candidate_limit > ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
                ):
                    # The large first prefix is an accelerator, not a
                    # correctness dependency. Retry the identical unconsumed
                    # frontier with the proven 64-identity batch when a hotter
                    # tenant cannot acquire/replay 960 identities in-budget.
                    dense_typed_oversample = False
                    candidate_limit = ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
                if is_read_budget_error(exc) and candidate_limit > (
                    ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
                ):
                    # Expanded duplicate replay is optional.  The failed seed
                    # verified no additional identity, so publish the last
                    # fully verified checkpoint as a safe continuation instead
                    # of failing the picker or moving through unverified rows.
                    candidate_limit = ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
                    break
                if (
                    is_read_budget_error(exc)
                    and cursor_before is None
                    and widened_probe
                ):
                    # The failed wide probe proved nothing. Retry from the
                    # identical segment end at the ordinary six-hour width and
                    # do not widen again during this request.
                    max_empty_segment_width = ATTRIBUTE_READ_EXPLICIT_SEGMENT
                    empty_segment_width = ATTRIBUTE_READ_EXPLICIT_SEGMENT
                    continue
                if is_read_budget_error(exc):
                    failed_width = current_segment_end - segment_start
                    retry_width = min(
                        ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
                        current_segment_end - start,
                    )
                    if failed_width > retry_width:
                        # The failed read did not advance any public cursor state.
                        # Retry the same frontier at the five-second exact floor;
                        # a keyset checkpoint must be re-anchored so timestamp ties
                        # below it stay inside the half-open segment.
                        if cursor_before is not None:
                            anchor_checkpoint_window(
                                cursor_before,
                                width=retry_width,
                            )
                        else:
                            empty_segment_width = retry_width
                            candidate_limit = ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
                            checkpoint_from_widened_segment = False
                        continue
                raise

            if candidate_ids:
                try:
                    replay_timeout_ms = (
                        ATTRIBUTE_VALUE_CURSOR_SPECULATIVE_TIMEOUT_MS
                        if (
                            candidate_limit > ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
                            and not dense_typed_oversample
                        )
                        else None
                    )
                    rows = (
                        self._verify_latest_unpinned_values(
                            project_ids=projects,
                            candidate_ids=candidate_ids,
                            candidate_versions=candidate_versions,
                            attribute_key=key,
                            query_timeout_ms=replay_timeout_ms,
                        )
                        if unpinned_cursor_read
                        else self._verify_latest_typed_values(
                            project_ids=projects,
                            candidate_ids=candidate_ids,
                            candidate_versions=candidate_versions,
                            attribute_key=key,
                            query_timeout_ms=replay_timeout_ms,
                        )
                        if typed_cursor_read
                        else self._verify_latest(
                            sql=_LATEST_TARGET_SQL,
                            project_ids=projects,
                            candidate_ids=candidate_ids,
                            attribute_key=key,
                            query_timeout_ms=replay_timeout_ms,
                        )
                    )
                except Exception as exc:
                    if is_read_budget_error(exc) and request_has_progress():
                        # Candidate identities from this failed replay are not
                        # certified. Retain only the checkpoint from a prior
                        # complete batch in this request.
                        empty_segment_width = last_successful_segment_width
                        break
                    if (
                        is_read_budget_error(exc)
                        and dense_typed_oversample
                        and candidate_limit > ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
                    ):
                        # Discard the unverified large prefix and retry the
                        # same cursor with the conservative batch.
                        dense_typed_oversample = False
                        candidate_limit = ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
                    if is_read_budget_error(exc) and candidate_limit > (
                        ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
                    ):
                        # The expanded identities were not verified, so they
                        # cannot advance the public cursor.  Return the prior
                        # proven checkpoint as an exact continuation.
                        candidate_limit = ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
                        break
                    if is_read_budget_error(exc):
                        failed_width = current_segment_end - segment_start
                        retry_width = min(
                            ATTRIBUTE_VALUE_CURSOR_MIN_SEGMENT,
                            current_segment_end - start,
                        )
                        if failed_width > retry_width:
                            # The candidate prefix was not certified, so narrow
                            # the same temporal frontier before retrying it.
                            if cursor_before is not None:
                                anchor_checkpoint_window(
                                    cursor_before,
                                    width=retry_width,
                                )
                            else:
                                empty_segment_width = retry_width
                                candidate_limit = ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
                                checkpoint_from_widened_segment = False
                            continue
                    if is_read_budget_error(exc) and candidate_limit > 1:
                        # Replay cost depends on the finite identity batch more
                        # directly than temporal width. Discard the unverified
                        # candidates and retry the identical frontier with a
                        # smaller prefix; no cursor state has moved yet.
                        candidate_limit = max(candidate_limit // 2, 1)
                        continue
                    raise
                candidate_pages += 1
            else:
                rows = ()

            # Candidate discovery plus any required latest-state replay has
            # completed for this whole half-open slice.  Remember only this
            # proven width; a later wider failure may publish it as the signed
            # continuation hint without claiming progress for the failed read.
            last_successful_segment_width = current_segment_end - segment_start

            rows_by_identity = {self._physical_identity(row): row for row in rows}
            fully_processed_identity = cursor_before
            for identity in candidate_ids:
                row = rows_by_identity.get(identity)
                if row is None or not self._row_is_active_in_window(row, start, end):
                    fully_processed_identity = identity
                    continue
                fully_consumed, next_offset = consume_cursor_row(row)
                if not fully_consumed:
                    next_resume_identity = identity
                    next_resume_member_offset = next_offset
                    break
                fully_processed_identity = identity
                if len(emitted) >= effective_page_size:
                    break

            cursor_before = fully_processed_identity
            if next_resume_identity is not None:
                checkpoint_from_widened_segment = widened_probe
                break
            if len(emitted) >= effective_page_size:
                checkpoint_from_widened_segment = widened_probe
                break
            if segment_truncated and candidate_ids:
                cursor_before = candidate_ids[-1]
                if widened_probe:
                    # The ordered page is a proof that every matching identity
                    # above this checkpoint was consumed. Move immediately to
                    # a normal-width keyset segment so a second wide statement
                    # is never required to preserve progress.
                    current_segment_end = min(
                        current_segment_end,
                        cursor_before[3] + ATTRIBUTE_READ_EXPLICIT_SEGMENT,
                    )
                    empty_segment_width = ATTRIBUTE_READ_EXPLICIT_SEGMENT
                    checkpoint_from_widened_segment = False
                else:
                    checkpoint_from_widened_segment = False
                if emitted:
                    candidate_limit = ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
                else:
                    candidate_limit = min(
                        candidate_limit * 2,
                        ATTRIBUTE_VALUE_CURSOR_MAX_CANDIDATE_LIMIT,
                    )
            else:
                current_segment_end = segment_start
                cursor_before = None
                checkpoint_from_widened_segment = False
                candidate_limit = ATTRIBUTE_VALUE_CURSOR_CANDIDATE_LIMIT
                empty_segment_width = min(
                    empty_segment_width * 2,
                    max_empty_segment_width,
                )

        next_checkpoint = next_resume_identity or cursor_before
        if checkpoint_from_widened_segment and next_checkpoint is not None:
            # The ordered widened probe proves every matching identity above
            # this checkpoint was already consumed. Compress the public
            # segment end to a normal six-hour window around the checkpoint so
            # the existing five-field cursor can resume without carrying a new
            # schema field or skipping older identities.
            current_segment_end = min(
                current_segment_end,
                next_checkpoint[3] + ATTRIBUTE_READ_EXPLICIT_SEGMENT,
            )

        exhausted = current_segment_end <= start and next_resume_identity is None
        seen_after = (
            (*seen, *emitted_digests)
            if resolved_seen_count == len(seen)
            else tuple(emitted_digests)
        )
        browse_status: AttributeValueBrowseStatus = (
            "exhausted" if exhausted else "continuation"
        )
        has_more = browse_status == "continuation"
        # A list page is exact even while a later continuation exists.  This is
        # not an aggregate coverage claim: every emitted option was verified
        # against latest state and the cursor can exhaust the remaining window.
        metadata = self._metadata(
            complete=True,
            error_code=None,
            window_start=start,
            window_end=end,
            query_count=self._query_count,
        )
        next_segment_start = (
            max(start, current_segment_end - empty_segment_width) if has_more else None
        )
        if (
            has_more
            and frontier_state() == initial_cursor_state
            and next_segment_start == initial_segment_start
        ):
            # A narrower next slice changes no coverage state but is still a
            # finite retry strategy: the signed cursor retries the identical
            # frontier with less work and can narrow only to the five-second
            # floor. Reject only a truly repeated frontier *and* width, which
            # would otherwise leave Load more in an endless loop.
            raise ReadDeadlineExceeded(
                "Exact filter-value cursor made no physical progress"
            )
        return AttributeValueCursorPageRead(
            tuple(emitted[digest] for digest in emitted_digests),
            metadata,
            has_more,
            current_segment_end,
            cursor_before,
            next_resume_identity,
            next_resume_member_offset,
            seen_after,
            browse_status,
            next_segment_start,
            tuple(emitted_digests),
            resolved_seen_count + len(emitted_digests),
        )

    def read_detail(
        self,
        project_ids: Iterable[Any],
        key: str,
        *,
        horizon_days: int = 365,
    ) -> AttributeDetailRead:
        """Read a bounded, latest-state distribution for one typed attribute.

        A key can exist in more than one typed Map/JSON family across spans.
        Preserve the detail endpoint's historical dominant-type contract while
        deriving it only from active latest rows. Stable typed-Map-before-array
        priority resolves equal occurrence counts.
        """

        value_read = self.read_values(
            project_ids,
            key,
            max_values=ATTRIBUTE_READ_MAX_VALUES,
            horizon_days=horizon_days,
        )
        type_totals: Counter[AttributeType] = Counter()
        for row in value_read.rows:
            type_totals[row.type] += row.count
        if not type_totals:
            return AttributeDetailRead(None, (), value_read.metadata)
        attribute_type = min(
            type_totals,
            key=lambda item: (-type_totals[item], _TYPE_PRIORITY[item]),
        )
        return AttributeDetailRead(
            attribute_type,
            tuple(row for row in value_read.rows if row.type == attribute_type),
            value_read.metadata,
        )

    def sample_cardinality(
        self,
        project_ids: Iterable[Any],
        *,
        horizon_days: int = 30,
        ensure_session_sample: bool = True,
    ) -> AttributeCardinalityRead:
        """Sample nested picker dimensions from CH only under one operation budget.

        A generic storage-order sample is sufficient for trace span slots, but
        it is not evidence that a dense project has no session-bearing spans.
        When session dimensions are required, an empty generic session sample
        therefore gets one independently bounded lane whose raw candidates are
        restricted by the indexed ``trace_session_id`` column.  Every candidate
        still goes through the same latest-state replay before it can affect the
        public cardinality.
        """

        self._begin_operation()
        projects = self._project_ids(project_ids)
        windows = self._windows(
            horizon_days=horizon_days,
            window_start=None,
            window_end=None,
        )
        overall_start, overall_end = _attribute_window_bounds(windows)
        if not projects:
            return AttributeCardinalityRead(
                0,
                0,
                self._metadata(
                    complete=True,
                    error_code=None,
                    window_start=overall_start,
                    window_end=overall_end,
                    query_count=self._query_count,
                ),
            )

        latest_rows: dict[PhysicalSpanIdentity, dict[str, Any]] = {}
        truncated = False
        budget_exceeded = False
        covered_start = overall_end
        for segment in windows:
            try:
                candidate_ids, segment_truncated, _ = self._candidate_ids(
                    projects,
                    segment,
                    predicate="1",
                    attribute_key=None,
                    stratified=True,
                    candidate_limit=ATTRIBUTE_READ_CANDIDATE_LIMIT,
                )
                rows = self._verify_latest(
                    sql=_LATEST_CARDINALITY_SQL,
                    project_ids=projects,
                    candidate_ids=candidate_ids,
                )
            except Exception as exc:
                if not latest_rows or not is_read_budget_error(exc):
                    raise
                budget_exceeded = True
                self._warn_partial_budget("sample_cardinality")
                break
            covered_start = segment[0]
            truncated = truncated or segment_truncated
            for row in rows:
                identity = self._physical_identity(row)
                if self._row_is_active_in_window(row, overall_start, overall_end):
                    latest_rows[identity] = row
                else:
                    latest_rows.pop(identity, None)

            if segment_truncated:
                break

        def row_has_session(row: dict[str, Any]) -> bool:
            session_id = str(row.get("trace_session_id") or "")
            return bool(session_id and session_id != _NIL_UUID)

        # A truncated generic sample with no session-bearing rows used to make
        # the session eval picker return a false 503.  Probe the session index
        # directly instead.  Empty *complete* segments continue into the older
        # adjacent bands.  A truncated page whose latest-state replay contains
        # only tombstoned/session-cleared rows restarts in deterministic order
        # and keyset-pages inside that same segment; the unordered storage-order
        # page is deliberately never reused as an ordered cursor.  The global
        # targeted-page/query/deadline ceilings keep this lane finite, and the
        # first verified live session sample is sufficient because cardinality
        # is intentionally a labelled finite sample.
        if ensure_session_sample and not any(
            row_has_session(row) for row in latest_rows.values()
        ):
            targeted_candidate_pages = 0
            targeted_lane_halted = False
            for segment in windows:
                if (
                    targeted_candidate_pages
                    >= ATTRIBUTE_READ_TARGETED_CANDIDATE_PAGE_LIMIT
                ):
                    truncated = True
                    break
                try:
                    candidate_ids, segment_truncated, _ = self._candidate_ids(
                        projects,
                        segment,
                        predicate=_SESSION_CARDINALITY_CANDIDATE_PREDICATE,
                        attribute_key=None,
                        candidate_limit=ATTRIBUTE_READ_CANDIDATE_LIMIT,
                    )
                    rows = self._verify_latest(
                        sql=_LATEST_CARDINALITY_SQL,
                        project_ids=projects,
                        candidate_ids=candidate_ids,
                    )
                    targeted_candidate_pages += 1
                except Exception as exc:
                    if not latest_rows or not is_read_budget_error(exc):
                        raise
                    budget_exceeded = True
                    targeted_lane_halted = True
                    self._warn_partial_budget("sample_cardinality_session")
                    break

                covered_start = min(covered_start, segment[0])
                truncated = truncated or segment_truncated
                verified_session_found = False
                for row in rows:
                    identity = self._physical_identity(row)
                    if self._row_is_active_in_window(row, overall_start, overall_end):
                        latest_rows[identity] = row
                        verified_session_found = (
                            verified_session_found or row_has_session(row)
                        )
                    else:
                        latest_rows.pop(identity, None)

                if verified_session_found:
                    break
                if not segment_truncated:
                    continue

                # The first candidate page follows storage order, so it cannot
                # supply a cursor for the globally ordered query.  Restart at
                # ordered page one, then derive every subsequent cursor only
                # from the preceding page in that same order.
                before_identity: PhysicalSpanIdentity | None = None
                segment_complete = False
                while (
                    targeted_candidate_pages
                    < ATTRIBUTE_READ_TARGETED_CANDIDATE_PAGE_LIMIT
                ):
                    try:
                        (
                            candidate_ids,
                            continuation_truncated,
                            _,
                        ) = self._candidate_ids(
                            projects,
                            segment,
                            predicate=_SESSION_CARDINALITY_CANDIDATE_PREDICATE,
                            attribute_key=None,
                            ordered=True,
                            before_identity=before_identity,
                            candidate_limit=ATTRIBUTE_READ_CANDIDATE_LIMIT,
                        )
                        rows = self._verify_latest(
                            sql=_LATEST_CARDINALITY_SQL,
                            project_ids=projects,
                            candidate_ids=candidate_ids,
                        )
                        targeted_candidate_pages += 1
                    except Exception as exc:
                        if not latest_rows or not is_read_budget_error(exc):
                            raise
                        budget_exceeded = True
                        targeted_lane_halted = True
                        self._warn_partial_budget("sample_cardinality_session")
                        break

                    verified_session_found = False
                    for row in rows:
                        identity = self._physical_identity(row)
                        if self._row_is_active_in_window(
                            row, overall_start, overall_end
                        ):
                            latest_rows[identity] = row
                            verified_session_found = (
                                verified_session_found or row_has_session(row)
                            )
                        else:
                            latest_rows.pop(identity, None)

                    if verified_session_found:
                        break
                    if not continuation_truncated or not candidate_ids:
                        segment_complete = True
                        break
                    before_identity = candidate_ids[-1]

                if verified_session_found or targeted_lane_halted:
                    break
                if not segment_complete:
                    # The segment still has raw session-bearing candidates,
                    # but the finite targeted-page ceiling was exhausted.
                    truncated = True
                    break

        spans_by_trace: Counter[tuple[str, str]] = Counter()
        traces_by_session: dict[tuple[str, str], set[str]] = defaultdict(set)
        for row in latest_rows.values():
            project_id = str(row.get("project_id") or "")
            trace_id = str(row.get("trace_id") or "")
            session_id = str(row.get("trace_session_id") or "")
            if not trace_id:
                continue
            spans_by_trace[(project_id, trace_id)] += 1
            if session_id and session_id != _NIL_UUID:
                traces_by_session[(project_id, session_id)].add(trace_id)
        return AttributeCardinalityRead(
            max(spans_by_trace.values(), default=0),
            max(
                (len(trace_ids) for trace_ids in traces_by_session.values()), default=0
            ),
            self._metadata(
                complete=not truncated and not budget_exceeded,
                error_code=(
                    "read_budget_exceeded"
                    if budget_exceeded
                    else "sample_limit"
                    if truncated
                    else None
                ),
                sampled=truncated and not budget_exceeded,
                window_start=covered_start,
                window_end=overall_end,
                query_count=self._query_count,
            ),
        )


def _canonical_value(attr_type: AttributeType, value: Any) -> str:
    if attr_type == "boolean":
        return "true" if bool(value) else "false"
    if attr_type == "number":
        return json.dumps(float(value), allow_nan=False, separators=(",", ":"))
    if attr_type == "array":
        # Array picker rows are individual JSON scalar members.  Preserve
        # their JSON type so ``1``, ``1.0``, ``true`` and ``"1"`` never merge.
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        )
    return json.dumps(str(value), ensure_ascii=False, separators=(",", ":"))


def attribute_value_cursor_digest(attr_type: AttributeType, value: Any) -> str:
    """Return a compact opaque identity for signed cursor de-duplication."""

    canonical = f"{attr_type}\0{_canonical_value(attr_type, value)}".encode()
    return hashlib.blake2s(canonical, digest_size=16).hexdigest()


def attribute_key_cursor_digest(key: str) -> str:
    """Return the opaque identity used to de-duplicate key browse pages."""

    # Preserve a full 128-bit identity. The bounded suggestion count and
    # mutually-exclusive physical checkpoint—not weaker hashing—keep the
    # signed continuation below the request-line ceiling.
    return hashlib.blake2s(
        str(key).encode("utf-8"),
        digest_size=ATTRIBUTE_KEY_CURSOR_DIGEST_BYTES,
    ).hexdigest()


def attribute_key_type_cursor_digest(key: str, attr_type: AttributeType) -> str:
    """Return a workspace cursor identity for one key storage family.

    Single-project cursors deliberately keep using ``attribute_key_cursor_digest``
    for byte-compatible key de-duplication. A workspace walk spans independent
    project batches, so its seen set must allow a later project to contribute a
    newly observed type for a key that an earlier project already published.
    """

    canonical = f"workspace-key-type\0{attr_type}\0{key}".encode()
    return hashlib.blake2s(
        canonical,
        digest_size=ATTRIBUTE_KEY_CURSOR_DIGEST_BYTES,
    ).hexdigest()


def _value_search_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _typed_string_is_suggestible(value: Any) -> bool:
    """Keep typed strings filterable while bounding picker payloads only."""

    if not isinstance(value, str) or not value:
        return False
    try:
        return len(value.encode("utf-8")) <= TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES
    except UnicodeEncodeError:
        return False


def merge_read_metadata(
    *metadata: AttributeReadMetadata,
) -> AttributeReadMetadata:
    """Merge multiple selector phases without hiding a degraded phase."""

    if not metadata:
        raise ValueError("At least one metadata value is required")
    complete = all(item.query_complete for item in metadata)
    degraded_metadata = next(
        (item for item in metadata if item.query_status == "degraded"),
        None,
    )
    has_sampled_metadata = any(item.query_status == "sampled" for item in metadata)
    error_code = (
        degraded_metadata.query_error_code
        if degraded_metadata is not None
        else next(
            (item.query_error_code for item in metadata if item.query_error_code),
            None,
        )
    )
    query_status: QueryStatus = "complete"
    if not complete:
        query_status = (
            "sampled"
            if degraded_metadata is None and has_sampled_metadata
            else "degraded"
        )
    return AttributeReadMetadata(
        query_complete=complete,
        query_status=query_status,
        query_error_code=error_code,
        query_window_start=min(item.query_window_start for item in metadata),
        query_window_end=max(item.query_window_end for item in metadata),
        query_count=sum(item.query_count for item in metadata),
    )


__all__ = [
    "ATTRIBUTE_KEY_CURSOR_MAX_CANDIDATE_LIMIT",
    "ATTRIBUTE_KEY_CURSOR_MAX_CANDIDATE_PAGES",
    "ATTRIBUTE_KEY_CURSOR_MAX_PAGE_SIZE",
    "ATTRIBUTE_KEY_CURSOR_SPECULATIVE_TIMEOUT_MS",
    "ATTRIBUTE_READ_HORIZON_DAYS",
    "ATTRIBUTE_READ_MAX_PROJECTS",
    "ATTRIBUTE_PROPERTY_PICKER_WALL_TIMEOUT_MS",
    "ATTRIBUTE_READ_SETTINGS",
    "AttributeCardinalityRead",
    "AttributeKeyCursorPageRead",
    "AttributeKeyInventory",
    "AttributeKeyRead",
    "AttributeKeyRow",
    "AttributeQueryPage",
    "AttributeReadMetadata",
    "AttributeReadSelector",
    "AttributeValueRead",
    "AttributeValueCursorPageRead",
    "AttributeValueRow",
    "IncompleteLatestStateReplay",
    "InvalidAttributeKey",
    "InvalidAttributeSearch",
    "V2AttributeQueryExecutor",
    "adaptive_attribute_windows",
    "attribute_key_cursor_digest",
    "attribute_key_type_cursor_digest",
    "attribute_value_cursor_digest",
    "merge_read_metadata",
    "validate_attribute_key",
    "validate_attribute_search",
]
