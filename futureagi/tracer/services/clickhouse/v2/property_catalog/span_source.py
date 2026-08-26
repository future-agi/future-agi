"""Audited canonical-span reconciliation for catalog values/keys.

The hot Kafka producer is an acceleration path only.  This module performs the
authoritative scan of current logical ``spans``, writes the complete value
projection, and then runs one independent aggregate source audit.  The audit
generation is an immutable identity for this attempt, not an ingestion
high-water or an as-of filter.  Both paths use the same SELECT-only source
reader, one caller-owned shrinking deadline, deterministic cursors, and an
explicit empty terminal envelope.
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol

from tracer.services.clickhouse.v2.attribute_catalog_backfill import (
    _SOURCE_IDENTITY_PAGE_SQL_TEMPLATE,
    _SOURCE_PAYLOAD_SQL_TEMPLATE,
    CATALOG_BUILD_LIMITS,
    DEFAULT_SOURCE_ATTRIBUTE_BYTES,
    DEFAULT_SOURCE_ATTRIBUTE_ENTRIES,
    PROJECTED_ARRAY_STRING_VALUE_BYTES,
    PROJECTED_TYPED_STRING_VALUE_BYTES,
    PROJECTED_VALUE_BUDGET_BYTES,
    READ_SETTINGS,
    SOURCE_TABLE,
    SourceCursor,
    SourceSpan,
    _parse_source_cursor,
    _parse_source_row,
)
from tracer.services.clickhouse.v2.attribute_catalog_builder import (
    CatalogKeyRow,
    CatalogScope,
    CatalogValueRow,
    build_catalog_rows,
)

from .codec import (
    canonical_json,
    canonical_json_sha256,
    canonical_uuid,
    framed_sha256,
)
from .models import (
    EnvelopeCounts,
    EnvelopeOutcome,
    PropertyCatalogEnvelope,
    SourceAdapter,
)
from .projection import PostgresSnapshotContext
from .publisher import ClickHouseEnvelopePublisher, SharedCatalogDeadline
from .qualification import (
    CatalogCheckpoint,
    CheckpointStatus,
    StreamRequirement,
)
from .reconciler import CheckpointWrite
from .runtime_limits import RUNTIME_LIMITS
from .source_adapters import (
    PropertySourceError,
    SourceKeysetCursor,
    SpanAttributeKeyGroup,
)
from .wire import ZERO_SHA256, encode_envelope

_SOURCE_DATABASE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FORBIDDEN_SQL_RE = re.compile(
    r"\b(?:INSERT|UPDATE|DELETE|ALTER|DROP|TRUNCATE|CREATE|RENAME|OPTIMIZE|SYSTEM|KILL)\b",
    re.IGNORECASE,
)
_MAX_WINDOWS = RUNTIME_LIMITS.canonical_span_max_windows
_MAX_PROJECTS = RUNTIME_LIMITS.max_projects
# The canonical payload query is bounded by the reviewed runtime settings.
# Standard and explicitly acknowledged initial-backfill page sizes may differ,
# but both retain finite row, byte, memory, thread, and deadline ceilings.
MAX_CANONICAL_SPAN_PAGE_ROWS = RUNTIME_LIMITS.canonical_span_page_rows
DEFAULT_CANONICAL_SPAN_PAGE_ROWS = RUNTIME_LIMITS.canonical_span_default_page_rows
DEV_CANONICAL_SPAN_PAGE_ROWS = RUNTIME_LIMITS.dev_canonical_span_page_rows
DEV_INITIAL_BACKFILL_CANONICAL_SPAN_PAGE_ROWS = (
    RUNTIME_LIMITS.initial_backfill_canonical_span_page_rows
)
CANONICAL_SPAN_QUERY_TIMEOUT_MS = RUNTIME_LIMITS.canonical_span_query_timeout_ms
DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS = (
    RUNTIME_LIMITS.initial_backfill_canonical_span_query_timeout_ms
)
_MAX_CANONICAL_SPAN_QUERY_TIMEOUT_MS = max(
    CANONICAL_SPAN_QUERY_TIMEOUT_MS,
    DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS,
)

# Keep cursor addresses in the original hourly coordinate space, but scan up
# to one week at a time.  That removes the occupied-hour query floor without
# making an old v1 cursor ambiguous: v1 resumes finish their current one-hour
# unit, then switch to the v2 weekly window.  Every SELECT still retains the
# server-enforced row/byte/memory/time limits above.
CANONICAL_SPAN_SCAN_WINDOW_HOURS = RUNTIME_LIMITS.canonical_span_scan_window_hours
_MAX_OCCUPIED_HOURS = _MAX_WINDOWS + 1
_MAX_GROUPS = RUNTIME_LIMITS.canonical_span_max_groups
_MAX_GROUP_BYTES = RUNTIME_LIMITS.canonical_span_max_group_bytes
_CATALOG_GROUP_QUERY_TIMEOUT_MS = RUNTIME_LIMITS.state_store_timeout_ms
_MAX_AUDIT_RESUME_STATE_LENGTH = 512
AUTHORITATIVE_VALUE_BATCH_MAX_ROWS = RUNTIME_LIMITS.authoritative_value_batch_max_rows
AUTHORITATIVE_VALUE_BATCH_MAX_BYTES = RUNTIME_LIMITS.authoritative_value_batch_max_bytes
_EMPTY_SHA256 = canonical_json_sha256("")
SPAN_AUDIT_CUTOFF_LABEL = "clickhouse_audit_generation"

_FENCE_SQL_TEMPLATE = """
WITH toUInt64(toUnixTimestamp64Nano(now64(9, 'UTC'))) AS audit_generation
SELECT audit_generation, count() AS rows_observed_at_generation
FROM {source_table}
PREWHERE project_id IN %(catalog_project_ids)s
  AND start_time >= %(catalog_since)s
  AND start_time < %(catalog_until)s
"""

# This is deliberately a physical-row superset.  Deleted/older versions can
# keep an hour in the result and cause one unnecessary identity SELECT, but no
# logical current row can be skipped.  The extra slot covers an unaligned
# half-open range spanning portions of MAX_WINDOWS + 1 wall-clock hours.
_OCCUPIED_HOURS_SQL_TEMPLATE = f"""
SELECT
    toString(project_id) AS project_id_text,
    arraySort(
        groupUniqArray({_MAX_OCCUPIED_HOURS + 1})(toStartOfHour(start_time))
    ) AS occupied_hours
FROM {{source_table}}
PREWHERE project_id IN %(catalog_project_ids)s
  AND start_time >= %(catalog_since)s
  AND start_time < %(catalog_until)s
GROUP BY project_id
ORDER BY project_id_text ASC
LIMIT %(catalog_project_limit)s
"""

_LOGICAL_IDENTITY_PAGE_SQL_TEMPLATE = _SOURCE_IDENTITY_PAGE_SQL_TEMPLATE.replace(
    "  AND _version <= %(catalog_source_version_fence)s\n", ""
).replace("    toStartOfHour(start_time),\n", "")
_LOGICAL_PAYLOAD_SQL_TEMPLATE = _SOURCE_PAYLOAD_SQL_TEMPLATE.replace(
    "      AND sp._version <= %(catalog_source_version_fence)s\n", ""
)
_AUDIT_HASH_EXPRESSIONS = tuple(
    f"cityHash64(concat('pc{seed}:', toJSONString(tuple("
    "project_id, observation_type, service_name, trace_id, span_id, seen_at, "
    "attrs_string_projection, attrs_number, attrs_bool, "
    "attributes_extra_projection, attributes_extra_valid, "
    "selectable_projection_complete, system_model, system_model_complete"
    f")))) AS audit_h{seed}"
    for seed in range(1, 5)
)


class PropertyCatalogSpanSourceError(RuntimeError):
    """Canonical spans cannot be proved complete inside the fixed bounds."""


class AuthoritativeSpanRole(StrEnum):
    VALUES = "values"
    SOURCE_AUDIT = "source_audit"


class CanonicalSpanSourceClient(Protocol):
    source_database: str

    def query(
        self,
        sql: str,
        params: Mapping[str, Any],
        *,
        timeout_ms: int,
        settings: Mapping[str, Any],
    ) -> Sequence[Mapping[str, Any]]: ...


class RevisionPinnedCatalogClient(Protocol):
    catalog_database: str

    def query(
        self,
        sql: str,
        params: Mapping[str, Any],
        *,
        timeout_ms: int,
    ) -> Sequence[Mapping[str, Any]]: ...


class SpanCheckpointStore(Protocol):
    def append(self, value: CheckpointWrite) -> None: ...

    def load_checkpoint_write(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        catalog_epoch: int,
        catalog_revision: int,
        build_token: str,
        source_adapter: SourceAdapter,
        producer_stream_id: str,
    ) -> CheckpointWrite | None: ...


@dataclass(frozen=True, slots=True)
class FrozenSpanSource:
    project_ids: tuple[str, ...]
    since: datetime
    until: datetime
    audit_generation: int

    def __post_init__(self) -> None:
        projects = tuple(
            sorted(
                {
                    canonical_uuid(project_id, field="project_id")
                    for project_id in self.project_ids
                }
            )
        )
        if not projects or len(projects) > _MAX_PROJECTS:
            raise ValueError(f"frozen span source requires 1..{_MAX_PROJECTS} projects")
        object.__setattr__(self, "project_ids", projects)
        _require_utc(self.since, "since")
        _require_utc(self.until, "until")
        if self.since >= self.until:
            raise ValueError("span source since must precede until")
        window_count = math.ceil((self.until - self.since).total_seconds() / 3600)
        if not 1 <= window_count <= _MAX_WINDOWS:
            raise ValueError("span source window count is outside the fixed ceiling")
        _positive_uint64(self.audit_generation, "audit_generation")

    @property
    def units(self) -> tuple[tuple[str, datetime, datetime], ...]:
        units: list[tuple[str, datetime, datetime]] = []
        for project_id in self.project_ids:
            start = self.since
            while start < self.until:
                units.append(
                    (project_id, start, min(start + timedelta(hours=1), self.until))
                )
                start += timedelta(hours=1)
        return tuple(units)


@dataclass(frozen=True, slots=True)
class SpanScanCursor:
    unit_index: int
    source_cursor: SourceCursor = SourceCursor()
    window_hours: int = CANONICAL_SPAN_SCAN_WINDOW_HOURS

    def __post_init__(self) -> None:
        if type(self.unit_index) is not int or not 0 <= self.unit_index < (
            _MAX_PROJECTS * _MAX_WINDOWS
        ):
            raise ValueError("span scan unit index is outside its bound")
        if not isinstance(self.source_cursor, SourceCursor):
            raise TypeError("source_cursor must be a SourceCursor")
        if (
            type(self.window_hours) is not int
            or not 1 <= self.window_hours <= CANONICAL_SPAN_SCAN_WINDOW_HOURS
        ):
            raise ValueError("span scan window hours are outside the fixed bound")

    def encode(self) -> str:
        body = canonical_json(
            {
                "observation_type": self.source_cursor.observation_type,
                "service_name": self.source_cursor.service_name,
                "span_id": self.source_cursor.span_id,
                "trace_id": self.source_cursor.trace_id,
                "unit_index": self.unit_index,
                "window_hours": self.window_hours,
                "v": 2,
            }
        ).encode("utf-8")
        return base64.urlsafe_b64encode(body).rstrip(b"=").decode("ascii")

    @classmethod
    def decode(cls, value: str | None) -> SpanScanCursor:
        if value in (None, ""):
            return cls(0)
        if not isinstance(value, str) or len(value) > 4096:
            raise PropertyCatalogSpanSourceError("invalid canonical-span cursor")
        try:
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            decoded = json.loads(raw.decode("utf-8"))
            expected_v1 = {
                "observation_type",
                "service_name",
                "span_id",
                "trace_id",
                "unit_index",
                "v",
            }
            expected_v2 = expected_v1 | {"window_hours"}
            keys = frozenset(decoded)
            if keys not in {frozenset(expected_v1), frozenset(expected_v2)}:
                raise ValueError
            version = decoded["v"]
            if type(version) is not int:
                raise ValueError
            if (version == 1 and keys != expected_v1) or (
                version == 2 and keys != expected_v2
            ):
                raise ValueError
            if version not in (1, 2):
                raise ValueError
            return cls(
                unit_index=decoded["unit_index"],
                source_cursor=SourceCursor(
                    str(decoded["observation_type"]),
                    str(decoded["service_name"]),
                    str(decoded["trace_id"]),
                    str(decoded["span_id"]),
                ),
                # A v1 cursor was issued for one exact hourly unit.  Completing
                # that hour before widening is the only ordering-safe upgrade:
                # later hours can contain identities below the current keyset.
                window_hours=(1 if version == 1 else decoded["window_hours"]),
            )
        except (
            TypeError,
            ValueError,
            UnicodeError,
            binascii.Error,
            json.JSONDecodeError,
        ) as exc:
            raise PropertyCatalogSpanSourceError(
                "invalid canonical-span cursor"
            ) from exc


@dataclass(frozen=True, slots=True)
class SpanScanPage:
    project_id: str | None
    spans: tuple[SourceSpan, ...]
    observation_sha256s: tuple[str, ...]
    next_cursor: str | None
    terminal: bool

    def __post_init__(self) -> None:
        if len(self.spans) != len(self.observation_sha256s):
            raise ValueError("span page observation evidence does not match rows")
        if self.terminal != (self.next_cursor is None):
            raise ValueError("terminal span page cannot expose a cursor")
        if self.spans and self.project_id is None:
            raise ValueError("non-empty span page requires a project")


@dataclass(frozen=True, slots=True)
class SpanAggregateProof:
    count: int
    xor: tuple[int, int, int, int]
    total: tuple[int, int, int, int]
    state_conflict_count: int

    def __post_init__(self) -> None:
        _strict_uint(self.count, "aggregate count")
        _strict_uint(self.state_conflict_count, "state conflict count")
        if len(self.xor) != 4 or len(self.total) != 4:
            raise ValueError("aggregate proof requires four hash components")
        for index, value in enumerate((*self.xor, *self.total)):
            _strict_uint(value, f"aggregate component {index}")

    @property
    def digest(self) -> str:
        return framed_sha256(
            "futureagi.property-catalog.span-source-multiset.v2",
            self.count,
            *(f"{value:016x}" for value in self.xor),
            *(f"{value:016x}" for value in self.total),
        )


@dataclass(slots=True)
class SpanAuditAccumulator:
    count: int = 0
    xor: list[int] | None = None
    total: list[int] | None = None

    def __post_init__(self) -> None:
        self.xor = [0, 0, 0, 0] if self.xor is None else list(self.xor)
        self.total = [0, 0, 0, 0] if self.total is None else list(self.total)
        SpanAggregateProof(
            self.count,
            tuple(self.xor),  # type: ignore[arg-type]
            tuple(self.total),  # type: ignore[arg-type]
            0,
        )

    def add(self, observation_sha256: str) -> None:
        if not isinstance(observation_sha256, str) or not re.fullmatch(
            r"[0-9a-f]{64}", observation_sha256
        ):
            raise PropertyCatalogSpanSourceError("span audit identity is invalid")
        components = tuple(
            int(observation_sha256[index : index + 16], 16)
            for index in range(0, 64, 16)
        )
        self.count += 1
        if self.count >= 1 << 64:
            raise PropertyCatalogSpanSourceError("span source count exceeds UInt64")
        assert self.xor is not None and self.total is not None
        for index, value in enumerate(components):
            self.xor[index] ^= value
            self.total[index] = (self.total[index] + value) % (1 << 64)

    @property
    def proof(self) -> SpanAggregateProof:
        assert self.xor is not None and self.total is not None
        return SpanAggregateProof(
            self.count,
            tuple(self.xor),  # type: ignore[arg-type]
            tuple(self.total),  # type: ignore[arg-type]
            0,
        )

    def encode(self) -> str:
        encoded = (
            base64.urlsafe_b64encode(
                canonical_json(
                    {
                        "count": self.count,
                        "total": self.total,
                        "v": 1,
                        "xor": self.xor,
                    }
                ).encode("utf-8")
            )
            .rstrip(b"=")
            .decode("ascii")
        )
        if len(encoded) > _MAX_AUDIT_RESUME_STATE_LENGTH:
            raise PropertyCatalogSpanSourceError(
                "span audit resume state exceeded its bound"
            )
        return encoded

    @classmethod
    def decode(cls, value: str, *, expected_digest: str) -> SpanAuditAccumulator:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > _MAX_AUDIT_RESUME_STATE_LENGTH
        ):
            raise PropertyCatalogSpanSourceError("span audit resume state is invalid")
        try:
            raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
            decoded = json.loads(raw.decode("utf-8"))
            if set(decoded) != {"count", "total", "v", "xor"} or decoded["v"] != 1:
                raise ValueError
            result = cls(decoded["count"], decoded["xor"], decoded["total"])
        except (
            TypeError,
            ValueError,
            UnicodeError,
            binascii.Error,
            json.JSONDecodeError,
        ) as exc:
            raise PropertyCatalogSpanSourceError(
                "span audit resume state is invalid"
            ) from exc
        if result.proof.digest != expected_digest:
            raise PropertyCatalogSpanSourceError(
                "span audit resume digest does not match checkpoint"
            )
        return result


@dataclass(frozen=True, slots=True)
class _ParseLimits:
    max_source_attribute_entries: int = DEFAULT_SOURCE_ATTRIBUTE_ENTRIES
    max_source_attribute_bytes: int = DEFAULT_SOURCE_ATTRIBUTE_BYTES


class CanonicalSpanSourceReader:
    """SELECT-only keyset reader plus one independent aggregate source audit."""

    def __init__(
        self,
        client: CanonicalSpanSourceClient,
        *,
        source_database: str,
        catalog_database: str,
        deadline: SharedCatalogDeadline,
        timeout_ms: int = CANONICAL_SPAN_QUERY_TIMEOUT_MS,
        explicit_initial_backfill: bool = False,
        page_rows: int = DEFAULT_CANONICAL_SPAN_PAGE_ROWS,
        max_source_attribute_entries: int = DEFAULT_SOURCE_ATTRIBUTE_ENTRIES,
        max_source_attribute_bytes: int = DEFAULT_SOURCE_ATTRIBUTE_BYTES,
    ) -> None:
        if (
            not isinstance(source_database, str)
            or _SOURCE_DATABASE_RE.fullmatch(source_database) is None
        ):
            raise ValueError("source_database must be one safe ClickHouse identifier")
        if source_database == catalog_database:
            raise ValueError(
                "canonical source and isolated catalog databases must differ"
            )
        if getattr(client, "source_database", None) != source_database:
            raise ValueError("canonical-span client database identity mismatch")
        if (
            type(page_rows) is not int
            or not 1 <= page_rows <= MAX_CANONICAL_SPAN_PAGE_ROWS
        ):
            raise ValueError(
                "canonical-span page_rows must be in "
                f"[1, {MAX_CANONICAL_SPAN_PAGE_ROWS}]"
            )
        if type(explicit_initial_backfill) is not bool:
            raise ValueError("explicit_initial_backfill must be a bool")
        timeout_cap_ms = (
            DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS
            if explicit_initial_backfill
            else CANONICAL_SPAN_QUERY_TIMEOUT_MS
        )
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= timeout_cap_ms:
            raise ValueError(
                "canonical-span timeout_ms must be in "
                f"[1, {timeout_cap_ms}] for this runtime mode"
            )
        self._client = client
        self._source_database = source_database
        self._catalog_database = catalog_database
        self._deadline = deadline
        self._timeout_ms = timeout_ms
        self._page_rows = page_rows
        self._limits = _ParseLimits(
            max_source_attribute_entries=max_source_attribute_entries,
            max_source_attribute_bytes=max_source_attribute_bytes,
        )
        # One runtime reconciles one frozen revision at a time.  Keep only the
        # latest bounded occupancy snapshot so the values and definition scans
        # share it without allowing an unbounded scheduled-run cache.
        self._occupied_frozen: FrozenSpanSource | None = None
        self._occupied_hours: frozenset[tuple[str, datetime]] = frozenset()

    def freeze(
        self,
        *,
        project_ids: Sequence[str],
        since: datetime,
        until: datetime,
    ) -> FrozenSpanSource:
        candidate = FrozenSpanSource(tuple(project_ids), since, until, 1)
        sql = _FENCE_SQL_TEMPLATE.format(source_table=self._source_table())
        rows = self._query(
            sql,
            {
                "catalog_project_ids": candidate.project_ids,
                "catalog_since": candidate.since,
                "catalog_until": candidate.until,
            },
        )
        if len(rows) != 1:
            raise PropertyCatalogSpanSourceError(
                "canonical-span audit-generation query did not return one row"
            )
        generation = _strict_uint(rows[0].get("audit_generation"), "audit generation")
        if generation < 1:
            raise PropertyCatalogSpanSourceError(
                "canonical-span audit generation must be positive"
            )
        return replace(candidate, audit_generation=generation)

    def read_page(
        self, frozen: FrozenSpanSource, *, cursor: str | None = None
    ) -> SpanScanPage:
        decoded = SpanScanCursor.decode(cursor)
        units = frozen.units
        if decoded.unit_index >= len(units):
            raise PropertyCatalogSpanSourceError("canonical-span cursor exceeds source")
        occupied_hours = self._discover_occupied_hours(frozen)
        unit_index = decoded.unit_index
        source_cursor = decoded.source_cursor
        window_hours = decoded.window_hours
        while unit_index < len(units):
            project_id, window_start, _hourly_window_end = units[unit_index]
            window_end = min(
                window_start + timedelta(hours=window_hours),
                frozen.until,
            )
            window_units = max(
                1,
                math.ceil((window_end - window_start).total_seconds() / 3600),
            )
            if not _unit_may_be_occupied(
                occupied_hours,
                project_id=project_id,
                window_start=window_start,
                window_end=window_end,
            ):
                unit_index += window_units
                source_cursor = SourceCursor()
                window_hours = CANONICAL_SPAN_SCAN_WINDOW_HOURS
                continue
            identities = self._identity_page(
                project_id=project_id,
                window_start=window_start,
                window_end=window_end,
                cursor=source_cursor,
            )
            has_more = len(identities) > self._page_rows
            page_identities = identities[: self._page_rows]
            if not page_identities:
                unit_index += window_units
                source_cursor = SourceCursor()
                window_hours = CANONICAL_SPAN_SCAN_WINDOW_HOURS
                continue
            spans, observation_sha256s = self._hydrate_page(
                project_id=project_id,
                window_start=window_start,
                window_end=window_end,
                identities=page_identities,
            )
            next_unit = unit_index if has_more else unit_index + window_units
            terminal = not has_more and next_unit >= len(units)
            next_cursor = None
            if not terminal:
                next_cursor = SpanScanCursor(
                    next_unit,
                    page_identities[-1] if has_more else SourceCursor(),
                    (window_hours if has_more else CANONICAL_SPAN_SCAN_WINDOW_HOURS),
                ).encode()
            return SpanScanPage(
                project_id=project_id,
                spans=spans,
                observation_sha256s=observation_sha256s,
                next_cursor=next_cursor,
                terminal=terminal,
            )
        return SpanScanPage(None, (), (), None, True)

    def _discover_occupied_hours(
        self, frozen: FrozenSpanSource
    ) -> frozenset[tuple[str, datetime]]:
        if self._occupied_frozen == frozen:
            return self._occupied_hours
        rows = self._query(
            _OCCUPIED_HOURS_SQL_TEMPLATE.format(source_table=self._source_table()),
            {
                "catalog_project_ids": frozen.project_ids,
                "catalog_since": frozen.since,
                "catalog_until": frozen.until,
                "catalog_project_limit": len(frozen.project_ids) + 1,
            },
        )
        if len(rows) > len(frozen.project_ids):
            raise PropertyCatalogSpanSourceError(
                "occupied-hour project inventory exceeded its bound"
            )
        allowed_projects = set(frozen.project_ids)
        seen_projects: set[str] = set()
        occupied: set[tuple[str, datetime]] = set()
        for row in rows:
            project_id = row.get("project_id_text")
            if (
                not isinstance(project_id, str)
                or project_id not in allowed_projects
                or project_id in seen_projects
            ):
                raise PropertyCatalogSpanSourceError(
                    "occupied-hour project inventory is invalid"
                )
            seen_projects.add(project_id)
            raw_hours = row.get("occupied_hours")
            if (
                not isinstance(raw_hours, (list, tuple))
                or len(raw_hours) > _MAX_OCCUPIED_HOURS
            ):
                raise PropertyCatalogSpanSourceError(
                    "occupied-hour array exceeded its bound"
                )
            for raw_hour in raw_hours:
                hour = _clickhouse_utc_hour(raw_hour)
                if (
                    hour >= frozen.until
                    or hour + timedelta(hours=1) <= frozen.since
                    or (project_id, hour) in occupied
                ):
                    raise PropertyCatalogSpanSourceError(
                        "occupied-hour inventory is invalid"
                    )
                occupied.add((project_id, hour))
        result = frozenset(occupied)
        self._occupied_frozen = frozen
        self._occupied_hours = result
        return result

    def _identity_page(
        self,
        *,
        project_id: str,
        window_start: datetime,
        window_end: datetime,
        cursor: SourceCursor,
    ) -> tuple[SourceCursor, ...]:
        sql = _LOGICAL_IDENTITY_PAGE_SQL_TEMPLATE.format(
            source_table=self._source_table()
        )
        rows = self._query(
            sql,
            {
                "catalog_project_id": project_id,
                "catalog_window_start": window_start,
                "catalog_window_end": window_end,
                "catalog_after_observation_type": cursor.observation_type,
                "catalog_after_service_name": cursor.service_name,
                "catalog_after_trace_id": cursor.trace_id,
                "catalog_after_span_id": cursor.span_id,
                "catalog_source_limit": self._page_rows + 1,
            },
        )
        if len(rows) > self._page_rows + 1:
            raise PropertyCatalogSpanSourceError(
                "span identity page exceeded its limit"
            )
        identities = tuple(_parse_source_cursor(row) for row in rows)
        previous = cursor.as_tuple()
        for identity in identities:
            if identity.as_tuple() <= previous:
                raise PropertyCatalogSpanSourceError(
                    "span identity page did not strictly advance"
                )
            previous = identity.as_tuple()
        return identities

    def _hydrate_page(
        self,
        *,
        project_id: str,
        window_start: datetime,
        window_end: datetime,
        identities: tuple[SourceCursor, ...],
    ) -> tuple[tuple[SourceSpan, ...], tuple[str, ...]]:
        sql = _paged_payload_with_audit_sql(self._source_table())
        rows = self._query(
            sql,
            {
                "catalog_project_id": project_id,
                "catalog_window_start": window_start,
                "catalog_window_end": window_end,
                "catalog_source_identities": tuple(
                    item.as_tuple() for item in identities
                ),
                "catalog_projected_typed_string_value_bytes": PROJECTED_TYPED_STRING_VALUE_BYTES,
                "catalog_projected_array_string_value_bytes": PROJECTED_ARRAY_STRING_VALUE_BYTES,
                "catalog_projected_value_budget_bytes": PROJECTED_VALUE_BUDGET_BYTES,
                "catalog_projected_array_members": CATALOG_BUILD_LIMITS.max_array_members,
                "catalog_max_source_attribute_entries": self._limits.max_source_attribute_entries,
                "catalog_max_source_attribute_bytes": self._limits.max_source_attribute_bytes,
            },
        )
        if len(rows) != len(identities):
            raise PropertyCatalogSpanSourceError(
                "span payload hydration changed page membership"
            )
        parsed = tuple(_parse_source_row(row, self._limits) for row in rows)  # type: ignore[arg-type]
        if tuple(span.cursor for span in parsed) != identities:
            raise PropertyCatalogSpanSourceError(
                "span payload hydration changed identity order"
            )
        observations = tuple(_audit_observation(row) for row in rows)
        return parsed, observations

    def audit(self, frozen: FrozenSpanSource) -> SpanAggregateProof:
        """Independently audit bounded project/time shards and combine proof."""

        # A workspace-wide aggregate makes ClickHouse account every project's
        # physical reads against one max_bytes_to_read budget.  Keep the same
        # independent aggregate proof, but execute it once per disjoint project
        # and weekly time scope.  Project sharding bounds workspace fan-in;
        # time sharding also keeps one individually large project below the
        # fixed interactive query deadline.  The authoritative scan uses the
        # same half-open weekly windows, and project ID is part of every audited
        # hash, so XOR and wrapping UInt64 sums compose to exactly the same
        # workspace multiset proof.
        sql = _aggregate_audit_sql(self._source_table())
        count = 0
        xor = [0, 0, 0, 0]
        total = [0, 0, 0, 0]
        state_conflict_count = 0
        occupied_hours = (
            self._occupied_hours if self._occupied_frozen == frozen else None
        )
        for project_id in frozen.project_ids:
            window_start = frozen.since
            while window_start < frozen.until:
                window_end = min(
                    window_start + timedelta(hours=CANONICAL_SPAN_SCAN_WINDOW_HOURS),
                    frozen.until,
                )
                if occupied_hours is not None and not _unit_may_be_occupied(
                    occupied_hours,
                    project_id=project_id,
                    window_start=window_start,
                    window_end=window_end,
                ):
                    window_start = window_end
                    continue
                rows = self._query(
                    sql,
                    {
                        "catalog_project_ids": (project_id,),
                        "catalog_since": window_start,
                        "catalog_until": window_end,
                        "catalog_projected_typed_string_value_bytes": PROJECTED_TYPED_STRING_VALUE_BYTES,
                        "catalog_projected_array_string_value_bytes": PROJECTED_ARRAY_STRING_VALUE_BYTES,
                        "catalog_projected_value_budget_bytes": PROJECTED_VALUE_BUDGET_BYTES,
                        "catalog_projected_array_members": CATALOG_BUILD_LIMITS.max_array_members,
                        "catalog_max_source_attribute_entries": self._limits.max_source_attribute_entries,
                        "catalog_max_source_attribute_bytes": self._limits.max_source_attribute_bytes,
                    },
                )
                if len(rows) != 1:
                    raise PropertyCatalogSpanSourceError(
                        "canonical-span audit shard did not return one row"
                    )
                row = rows[0]
                count += _strict_uint(row.get("source_count"), "audit source_count")
                state_conflict_count += _strict_uint(
                    row.get("state_conflict_count"), "state conflict count"
                )
                _strict_uint(count, "combined audit source_count")
                _strict_uint(
                    state_conflict_count,
                    "combined state conflict count",
                )
                for index in range(1, 5):
                    xor[index - 1] ^= _strict_uint(
                        row.get(f"audit_h{index}_xor"),
                        f"audit h{index} xor",
                    )
                    total[index - 1] = (
                        total[index - 1]
                        + _strict_uint(
                            row.get(f"audit_h{index}_sum"),
                            f"audit h{index} sum",
                        )
                    ) % (1 << 64)
                window_start = window_end
        return SpanAggregateProof(
            count=count,
            xor=tuple(xor),  # type: ignore[arg-type]
            total=tuple(total),  # type: ignore[arg-type]
            state_conflict_count=state_conflict_count,
        )

    def _source_table(self) -> str:
        return f"`{self._source_database}`.`{SOURCE_TABLE}`"

    def _query(
        self, sql: str, params: Mapping[str, Any]
    ) -> tuple[Mapping[str, Any], ...]:
        if getattr(self._client, "source_database", None) != self._source_database:
            raise PropertyCatalogSpanSourceError(
                "canonical-span client identity changed during the scan"
            )
        _validate_source_select(
            sql,
            source_table=self._source_table(),
            catalog_database=self._catalog_database,
        )
        remaining = self._deadline.remaining_ms(cap_ms=self._timeout_ms)
        settings = dict(READ_SETTINGS)
        settings["readonly"] = 2
        settings["max_execution_time"] = max(1, math.ceil(remaining / 1000))
        return tuple(
            self._client.query(
                sql,
                params,
                timeout_ms=remaining,
                settings=settings,
            )
        )


class CanonicalSpanAttributeGroupPageLoader:
    """Bounded group loader over current logical rows for one audit attempt."""

    def __init__(
        self,
        reader: CanonicalSpanSourceReader,
        frozen: FrozenSpanSource,
        *,
        max_groups: int = _MAX_GROUPS,
        max_group_bytes: int = _MAX_GROUP_BYTES,
    ) -> None:
        if type(max_groups) is not int or not 1 <= max_groups <= _MAX_GROUPS:
            raise ValueError(f"span group ceiling is outside [1, {_MAX_GROUPS}]")
        if (
            type(max_group_bytes) is not int
            or not 1 <= max_group_bytes <= _MAX_GROUP_BYTES
        ):
            raise ValueError(
                f"span group byte ceiling is outside [1, {_MAX_GROUP_BYTES}]"
            )
        self._reader = reader
        self._frozen = frozen
        self._max_groups = max_groups
        self._max_group_bytes = max_group_bytes
        self._cache: (
            tuple[tuple[str, tuple[str, ...], tuple[str, ...], datetime, datetime], ...]
            | None
        ) = None

    def __call__(
        self,
        *,
        context: PostgresSnapshotContext,
        cursor: SourceKeysetCursor | None,
        limit: int,
    ) -> Sequence[SpanAttributeKeyGroup]:
        if type(limit) is not int or limit < 1:
            raise PropertySourceError("span group page limit must be positive")
        if context.catalog_epoch < 1 or context.catalog_revision < 1:
            raise PropertySourceError("span group context is invalid")
        if self._cache is None:
            self._cache = self._scan_groups(catalog_epoch=context.catalog_epoch)
        if cursor is not None and cursor.updated_at > context.snapshot_cutoff:
            raise PropertySourceError("span group cursor is beyond the revision fence")
        after = cursor.source_entity_id if cursor is not None else ""
        selected = (item for item in self._cache if item[0] > after)
        return tuple(
            SpanAttributeKeyGroup(
                attribute_key=key,
                observed_types=types,
                project_ids=projects,
                catalog_revision=context.catalog_revision,
                revision_fenced_at=context.snapshot_cutoff,
                first_seen=first_seen,
                last_seen=last_seen,
            )
            for key, types, projects, first_seen, last_seen in list(selected)[:limit]
        )

    def _scan_groups(
        self, *, catalog_epoch: int
    ) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...], datetime, datetime], ...]:
        groups: dict[str, tuple[set[str], set[str], datetime, datetime]] = {}
        cursor: str | None = None
        encoded_bytes = 0
        while True:
            page = self._reader.read_page(self._frozen, cursor=cursor)
            if page.project_id is not None:
                for span in page.spans:
                    key_rows, _, reasons = _build_span_catalog_rows(
                        project_id=page.project_id,
                        catalog_epoch=catalog_epoch,
                        span=span,
                    )
                    if reasons:
                        raise PropertySourceError(
                            "canonical-span key projection contains a bounded gap: "
                            + ",".join(reasons)
                        )
                    for row in key_rows:
                        if row.source_kind != "custom_attribute":
                            continue
                        existing = groups.get(row.attribute_key)
                        if existing is None:
                            encoded_bytes += (
                                len(row.attribute_key.encode("utf-8")) + 128
                            )
                            if (
                                len(groups) >= self._max_groups
                                or encoded_bytes > self._max_group_bytes
                            ):
                                raise PropertySourceError(
                                    "canonical-span group inventory exceeded its bound"
                                )
                            groups[row.attribute_key] = (
                                {row.attribute_type},
                                {row.project_id},
                                row.first_seen,
                                row.last_seen,
                            )
                        else:
                            types, projects, first_seen, last_seen = existing
                            types.add(row.attribute_type)
                            projects.add(row.project_id)
                            groups[row.attribute_key] = (
                                types,
                                projects,
                                min(first_seen, row.first_seen),
                                max(last_seen, row.last_seen),
                            )
            if page.terminal:
                break
            cursor = page.next_cursor
        return tuple(
            (
                key,
                tuple(sorted(types)),
                tuple(sorted(projects)),
                first_seen,
                last_seen,
            )
            for key, (types, projects, first_seen, last_seen) in sorted(groups.items())
        )


class RevisionPinnedSpanAttributeGroupPageLoader:
    """Read complete key/type/project unions from the retained build lineage.

    Authoritative value reconciliation has already projected and revision-pinned
    every selectable span attribute. Snapshot builds consume only their own
    immutable value rows. Incremental builds union the already-active snapshot
    lineage with the current revision so a touched key cannot lose an older
    project binding or observed type. The independent source audit still runs
    after definition projection and proves that the current source slice did
    not change before activation.
    """

    def __init__(
        self,
        client: RevisionPinnedCatalogClient,
        *,
        context: PostgresSnapshotContext,
        build_token: str,
        deadline: SharedCatalogDeadline,
        lineage_anchor_revision: int | None = None,
        prior_active_revision: int | None = None,
        timeout_ms: int = _CATALOG_GROUP_QUERY_TIMEOUT_MS,
        max_groups: int = _MAX_GROUPS,
        max_group_bytes: int = _MAX_GROUP_BYTES,
    ) -> None:
        database = getattr(client, "catalog_database", None)
        if (
            not isinstance(database, str)
            or _SOURCE_DATABASE_RE.fullmatch(database) is None
        ):
            raise ValueError("revision-pinned catalog database is invalid")
        if (
            type(timeout_ms) is not int
            or not 1 <= timeout_ms <= _CATALOG_GROUP_QUERY_TIMEOUT_MS
        ):
            raise ValueError(
                "catalog group query timeout is outside [1, "
                f"{_CATALOG_GROUP_QUERY_TIMEOUT_MS}]"
            )
        if type(max_groups) is not int or not 1 <= max_groups <= _MAX_GROUPS:
            raise ValueError(f"span group ceiling is outside [1, {_MAX_GROUPS}]")
        if (
            type(max_group_bytes) is not int
            or not 1 <= max_group_bytes <= _MAX_GROUP_BYTES
        ):
            raise ValueError(
                f"span group byte ceiling is outside [1, {_MAX_GROUP_BYTES}]"
            )
        self._client = client
        self._catalog_database = database
        self._context = context
        self._build_token = canonical_uuid(build_token, field="build_token")
        anchor_revision = (
            context.catalog_revision
            if lineage_anchor_revision is None
            else lineage_anchor_revision
        )
        if type(anchor_revision) is not int or not (
            1 <= anchor_revision <= context.catalog_revision
        ):
            raise ValueError("lineage anchor revision is outside the build lineage")
        if prior_active_revision is None:
            if anchor_revision != context.catalog_revision:
                raise ValueError(
                    "incremental span groups require a prior active revision"
                )
        elif type(prior_active_revision) is not int or not (
            anchor_revision <= prior_active_revision < context.catalog_revision
        ):
            raise ValueError("prior active revision is outside the build lineage")
        self._lineage_anchor_revision = anchor_revision
        self._prior_active_revision = prior_active_revision
        self._deadline = deadline
        self._timeout_ms = timeout_ms
        self._max_groups = max_groups
        self._max_group_bytes = max_group_bytes
        self._cache: (
            tuple[tuple[str, tuple[str, ...], tuple[str, ...], datetime, datetime], ...]
            | None
        ) = None

    def __call__(
        self,
        *,
        context: PostgresSnapshotContext,
        cursor: SourceKeysetCursor | None,
        limit: int,
    ) -> Sequence[SpanAttributeKeyGroup]:
        if context != self._context:
            raise PropertySourceError("span group context changed after build pinning")
        if type(limit) is not int or limit < 1:
            raise PropertySourceError("span group page limit must be positive")
        if cursor is not None and cursor.updated_at != context.snapshot_cutoff:
            raise PropertySourceError("span group cursor is outside the revision fence")
        if self._cache is None:
            self._cache = self._load_groups()
        after = cursor.source_entity_id if cursor is not None else ""
        selected = (item for item in self._cache if item[0] > after)
        revision_fenced_at = context.snapshot_cutoff.astimezone(UTC)
        return tuple(
            SpanAttributeKeyGroup(
                attribute_key=key,
                observed_types=types,
                project_ids=projects,
                catalog_revision=context.catalog_revision,
                revision_fenced_at=revision_fenced_at,
                first_seen=first_seen,
                last_seen=last_seen,
            )
            for key, types, projects, first_seen, last_seen in list(selected)[:limit]
        )

    def _load_groups(
        self,
    ) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...], datetime, datetime], ...]:
        if getattr(self._client, "catalog_database", None) != self._catalog_database:
            raise PropertySourceError(
                "revision-pinned catalog client identity changed during the read"
            )
        result_limit = self._max_groups + 1
        sql = f"""
WITH activation_versioned AS
(
    SELECT
        *,
        max(_version) OVER (
            PARTITION BY organization_id, workspace_id,
                         catalog_epoch, catalog_revision, build_token
        ) AS latest_version
    FROM `{self._catalog_database}`.`property_catalog_activations`
    PREWHERE organization_id = %(organization_id)s
      AND workspace_id = %(workspace_id)s
      AND catalog_epoch = %(catalog_epoch)s
      AND catalog_revision >= %(lineage_anchor_revision)s
      AND catalog_revision <= %(prior_active_revision)s
), activation_states AS
(
    SELECT
        versioned_rows.catalog_epoch,
        versioned_rows.catalog_revision,
        versioned_rows.build_token,
        argMax(versioned_rows.projection_version, versioned_rows._version)
            AS projection_version,
        argMax(versioned_rows.lineage_anchor_revision, versioned_rows._version)
            AS lineage_anchor_revision,
        argMax(versioned_rows.status, versioned_rows._version) AS status,
        argMax(versioned_rows.qualified_at, versioned_rows._version) AS qualified_at,
        uniqExactIf(
            tuple(
                versioned_rows.projection_version,
                versioned_rows.lineage_anchor_revision,
                versioned_rows.status,
                versioned_rows.qualified_at
            ),
            versioned_rows._version = versioned_rows.latest_version
        ) AS latest_state_variants
    FROM activation_versioned AS versioned_rows
    GROUP BY
        versioned_rows.catalog_epoch,
        versioned_rows.catalog_revision,
        versioned_rows.build_token
), active_lineage_candidates AS
(
    SELECT *
    FROM activation_states
    WHERE %(has_prior_lineage)s = 1
      AND latest_state_variants = 1
      AND projection_version = %(projection_version)s
      AND lineage_anchor_revision = %(lineage_anchor_revision)s
      AND status = 'active'
      AND qualified_at IS NOT NULL
), active_lineage AS
(
    SELECT
        catalog_epoch,
        catalog_revision,
        any(build_token) AS build_token,
        count() AS active_builds
    FROM active_lineage_candidates
    GROUP BY catalog_epoch, catalog_revision
    HAVING active_builds = 1
), retained_values AS
(
    SELECT value_rows.*
    FROM `{self._catalog_database}`.`span_attribute_value_catalog` AS value_rows
    INNER JOIN active_lineage AS lineage
        ON value_rows.catalog_epoch = lineage.catalog_epoch
       AND value_rows.catalog_revision = lineage.catalog_revision
       AND value_rows.build_token = lineage.build_token
    PREWHERE value_rows.organization_id = %(organization_id)s
      AND value_rows.workspace_id = %(workspace_id)s
      AND value_rows.project_id IN %(project_ids)s
      AND value_rows.catalog_epoch = %(catalog_epoch)s
      AND value_rows.catalog_revision >= %(lineage_anchor_revision)s
      AND value_rows.catalog_revision <= %(prior_active_revision)s

    UNION ALL

    SELECT current_rows.*
    FROM `{self._catalog_database}`.`span_attribute_value_catalog` AS current_rows
    PREWHERE current_rows.organization_id = %(organization_id)s
      AND current_rows.workspace_id = %(workspace_id)s
      AND current_rows.project_id IN %(project_ids)s
      AND current_rows.catalog_epoch = %(catalog_epoch)s
      AND current_rows.catalog_revision = %(catalog_revision)s
      AND current_rows.build_token = %(build_token)s
)
SELECT
    attribute_key,
    arraySort(groupUniqArray(toString(attribute_type))) AS observed_types,
    arraySort(groupUniqArray(toString(project_id))) AS project_ids,
    min(first_seen) AS first_seen,
    max(last_seen) AS last_seen
FROM retained_values
WHERE source_kind = 'custom_attribute'
GROUP BY attribute_key
ORDER BY attribute_key ASC
LIMIT %(catalog_group_limit)s
SETTINGS max_threads = {RUNTIME_LIMITS.canonical_span_max_threads},
         max_result_rows = {result_limit},
         max_result_bytes = {self._max_group_bytes},
         result_overflow_mode = 'throw'
"""
        remaining = self._deadline.remaining_ms(cap_ms=self._timeout_ms)
        rows = tuple(
            self._client.query(
                sql,
                {
                    "organization_id": self._context.organization_id,
                    "workspace_id": self._context.workspace_id,
                    "project_ids": self._context.project_ids,
                    "catalog_epoch": self._context.catalog_epoch,
                    "lineage_anchor_revision": self._lineage_anchor_revision,
                    "prior_active_revision": self._prior_active_revision or 0,
                    "has_prior_lineage": int(self._prior_active_revision is not None),
                    "catalog_revision": self._context.catalog_revision,
                    "build_token": self._build_token,
                    "projection_version": self._context.projection_version,
                    "catalog_group_limit": result_limit,
                },
                timeout_ms=remaining,
            )
        )
        if len(rows) > self._max_groups:
            raise PropertySourceError(
                "revision-pinned span group inventory exceeded its row bound"
            )

        groups: list[
            tuple[str, tuple[str, ...], tuple[str, ...], datetime, datetime]
        ] = []
        encoded_bytes = 0
        previous_key = ""
        expected_fields = {
            "attribute_key",
            "observed_types",
            "project_ids",
            "first_seen",
            "last_seen",
        }
        allowed_types = {"string", "number", "boolean", "array", "map", "json"}
        allowed_projects = frozenset(self._context.project_ids)
        for row in rows:
            if set(row) != expected_fields:
                raise PropertySourceError("revision-pinned span group row is malformed")
            key = row.get("attribute_key")
            raw_types = row.get("observed_types")
            raw_projects = row.get("project_ids")
            if not isinstance(key, str) or not key or key <= previous_key:
                raise PropertySourceError(
                    "revision-pinned span groups are not strictly key ordered"
                )
            if isinstance(raw_types, (str, bytes)) or not isinstance(
                raw_types, Sequence
            ):
                raise PropertySourceError("span group observed_types is malformed")
            if isinstance(raw_projects, (str, bytes)) or not isinstance(
                raw_projects, Sequence
            ):
                raise PropertySourceError("span group project_ids is malformed")
            types = tuple(sorted({str(value) for value in raw_types}))
            projects = tuple(
                sorted(
                    {
                        canonical_uuid(value, field="span group project_id")
                        for value in raw_projects
                    }
                )
            )
            if not types or not set(types) <= allowed_types:
                raise PropertySourceError("span group observed_types is unsupported")
            if not projects or not set(projects) <= allowed_projects:
                raise PropertySourceError("span group projects exceed the build scope")
            first_seen = _clickhouse_utc_datetime(
                row.get("first_seen"), "span group first_seen"
            )
            last_seen = _clickhouse_utc_datetime(
                row.get("last_seen"), "span group last_seen"
            )
            if first_seen > last_seen:
                raise PropertySourceError("span group first_seen follows last_seen")
            encoded_bytes += (
                len(key.encode("utf-8"))
                + sum(len(value.encode("utf-8")) for value in types)
                + sum(len(value.encode("utf-8")) for value in projects)
                + 128
            )
            if encoded_bytes > self._max_group_bytes:
                raise PropertySourceError(
                    "revision-pinned span group inventory exceeded its byte bound"
                )
            groups.append((key, types, projects, first_seen, last_seen))
            previous_key = key
        return tuple(groups)


@dataclass(frozen=True, slots=True)
class AuthoritativeSpanBuild:
    organization_id: str
    workspace_id: str
    catalog_epoch: int
    catalog_revision: int
    build_token: str
    projection_version: int
    emitted_at: datetime
    values_producer_stream_id: str
    audit_producer_stream_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "organization_id",
            canonical_uuid(self.organization_id, field="organization_id"),
        )
        object.__setattr__(
            self,
            "workspace_id",
            canonical_uuid(self.workspace_id, field="workspace_id"),
        )
        object.__setattr__(
            self,
            "build_token",
            canonical_uuid(self.build_token, field="build_token"),
        )
        object.__setattr__(
            self,
            "values_producer_stream_id",
            canonical_uuid(
                self.values_producer_stream_id,
                field="values_producer_stream_id",
            ),
        )
        object.__setattr__(
            self,
            "audit_producer_stream_id",
            canonical_uuid(
                self.audit_producer_stream_id,
                field="audit_producer_stream_id",
            ),
        )
        if self.values_producer_stream_id == self.audit_producer_stream_id:
            raise ValueError("authoritative and audit streams must be independent")
        _positive_uint64(self.catalog_revision, "catalog_revision")
        if type(self.catalog_epoch) is not int or not 1 <= self.catalog_epoch <= 65_535:
            raise ValueError("catalog_epoch must be a positive UInt16")
        if (
            type(self.projection_version) is not int
            or not 1 <= self.projection_version <= 65_535
        ):
            raise ValueError("projection_version must be a positive UInt16")
        _require_utc(self.emitted_at, "emitted_at")


@dataclass(frozen=True, slots=True)
class AuthoritativeSpanResult:
    values: CatalogCheckpoint
    source_audit: CatalogCheckpoint
    dry_run: bool


class AuthoritativeSpanReconciler:
    """Write VALUES, independently re-scan SOURCE_AUDIT, and compare proof."""

    def __init__(
        self,
        *,
        reader: CanonicalSpanSourceReader,
        publishers: Mapping[AuthoritativeSpanRole, ClickHouseEnvelopePublisher] | None,
        checkpoint_store: SpanCheckpointStore | None,
    ) -> None:
        self._reader = reader
        self._publishers = dict(publishers or {})
        self._checkpoint_store = checkpoint_store

    def run(
        self,
        *,
        frozen: FrozenSpanSource,
        build: AuthoritativeSpanBuild,
        dry_run: bool = False,
    ) -> AuthoritativeSpanResult:
        if not dry_run and (
            set(self._publishers) != set(AuthoritativeSpanRole)
            or self._checkpoint_store is None
        ):
            raise ValueError(
                "executing authoritative span reconciliation requires writers"
            )
        values = self._run_stream(
            frozen=frozen,
            build=build,
            role=AuthoritativeSpanRole.VALUES,
            producer_stream_id=build.values_producer_stream_id,
            dry_run=dry_run,
        )
        proof = self._reader.audit(frozen)
        if proof.state_conflict_count:
            raise PropertyCatalogSpanSourceError(
                "canonical-span audit found conflicting states at the same max version"
            )
        if values.source_count != proof.count or values.source_digest != proof.digest:
            raise PropertyCatalogSpanSourceError(
                "authoritative values and independent source audit disagree"
            )
        audit = self._run_audit_stream(
            frozen=frozen,
            build=build,
            proof=proof,
            dry_run=dry_run,
        )
        return AuthoritativeSpanResult(values, audit, dry_run)

    def _run_stream(
        self,
        *,
        frozen: FrozenSpanSource,
        build: AuthoritativeSpanBuild,
        role: AuthoritativeSpanRole,
        producer_stream_id: str,
        dry_run: bool,
    ) -> CatalogCheckpoint:
        if role is not AuthoritativeSpanRole.VALUES:
            raise PropertyCatalogSpanSourceError(
                "source audit must use the one-query aggregate path"
            )
        resume = None
        if not dry_run:
            assert self._checkpoint_store is not None
            resume = self._checkpoint_store.load_checkpoint_write(
                organization_id=build.organization_id,
                workspace_id=build.workspace_id,
                catalog_epoch=build.catalog_epoch,
                catalog_revision=build.catalog_revision,
                build_token=build.build_token,
                source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
                producer_stream_id=producer_stream_id,
            )
        if resume is not None:
            _validate_span_resume(
                resume,
                frozen=frozen,
                build=build,
                producer_stream_id=producer_stream_id,
            )
            if resume.checkpoint.terminal:
                return resume.checkpoint

        checkpoint = resume.checkpoint if resume is not None else None
        source_cursor = resume.source_cursor if resume is not None else ""
        source_count = checkpoint.source_count if checkpoint else 0
        value_count = checkpoint.value_count if checkpoint else 0
        delivery_count = checkpoint.delivery_count if checkpoint else 0
        first_sequence = checkpoint.first_sequence if checkpoint else None
        last_sequence = checkpoint.last_sequence if checkpoint else None
        accumulator = (
            SpanAuditAccumulator.decode(
                resume.watermark,
                expected_digest=checkpoint.source_digest,
            )
            if resume is not None and checkpoint is not None
            else SpanAuditAccumulator()
        )
        source_digest = accumulator.proof.digest
        emitted_digest = checkpoint.emitted_digest if checkpoint else _EMPTY_SHA256
        previous_payload = resume.previous_payload_sha256 if resume else ZERO_SHA256
        gap_reasons = set(resume.gap_reasons if resume else ())

        while True:
            page = self._reader.read_page(frozen, cursor=source_cursor)
            page_source_digest = _EMPTY_SHA256
            for observation_sha256 in page.observation_sha256s:
                page_source_digest = framed_sha256(
                    "futureagi.property-catalog.span-page-source.v1",
                    page_source_digest,
                    observation_sha256,
                )
                accumulator.add(observation_sha256)
            source_digest = accumulator.proof.digest

            value_rows: tuple[Mapping[str, Any], ...] = ()
            page_gaps: set[str] = set()
            if page.project_id is not None:
                built: list[CatalogValueRow] = []
                for span in page.spans:
                    _, rows, reasons = _build_span_catalog_rows(
                        project_id=page.project_id,
                        catalog_epoch=build.catalog_epoch,
                        span=span,
                    )
                    built.extend(rows)
                    page_gaps.update(reasons)
                if role is AuthoritativeSpanRole.VALUES:
                    value_rows = tuple(
                        _value_wire_row(build=build, row=row)
                        for row in _aggregate_value_rows(built)
                    )
            gap_reasons.update(page_gaps)

            if page.spans:
                batches = _value_batches(value_rows) if value_rows else ((),)
                for batch_index, batch in enumerate(batches):
                    sequence = (last_sequence or 0) + 1
                    envelope_gaps = tuple(sorted(page_gaps)) if batch_index == 0 else ()
                    envelope = PropertyCatalogEnvelope(
                        organization_id=build.organization_id,
                        workspace_id=build.workspace_id,
                        catalog_epoch=build.catalog_epoch,
                        catalog_revision=build.catalog_revision,
                        build_token=build.build_token,
                        projection_version=build.projection_version,
                        source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
                        producer_stream_id=producer_stream_id,
                        sequence=sequence,
                        previous_payload_sha256=previous_payload,
                        source_version=frozen.audit_generation,
                        source_fingerprint=source_digest,
                        source_batch_digest=framed_sha256(
                            "futureagi.property-catalog.span-source-batch.v1",
                            role,
                            page_source_digest,
                            batch_index,
                            *(str(row["value_fingerprint"]) for row in batch),
                        ),
                        outcome=(
                            EnvelopeOutcome.GAP
                            if envelope_gaps
                            else EnvelopeOutcome.COMMITTED
                        ),
                        counts=EnvelopeCounts(
                            source_count=len(page.spans) if batch_index == 0 else 0,
                            definition_count=0,
                            value_count=len(batch),
                            tombstone_count=0,
                            gap_count=len(envelope_gaps),
                        ),
                        definitions=(),
                        gap_reasons=envelope_gaps,
                        terminal=False,
                    )
                    payload = self._publish(
                        role=role,
                        envelope=envelope,
                        value_rows=batch,
                        dry_run=dry_run,
                    )
                    previous_payload = payload
                    emitted_digest = framed_sha256(
                        "futureagi.property-catalog.emitted-stream.v1",
                        emitted_digest,
                        payload,
                    )
                    first_sequence = first_sequence or sequence
                    last_sequence = sequence
                    delivery_count += 1
                    value_count += len(batch)
                source_count += len(page.spans)

            if page.terminal:
                sequence = (last_sequence or 0) + 1
                terminal = PropertyCatalogEnvelope(
                    organization_id=build.organization_id,
                    workspace_id=build.workspace_id,
                    catalog_epoch=build.catalog_epoch,
                    catalog_revision=build.catalog_revision,
                    build_token=build.build_token,
                    projection_version=build.projection_version,
                    source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
                    producer_stream_id=producer_stream_id,
                    sequence=sequence,
                    previous_payload_sha256=previous_payload,
                    source_version=frozen.audit_generation,
                    source_fingerprint=source_digest,
                    source_batch_digest=framed_sha256(
                        "futureagi.property-catalog.empty-terminal.v1",
                        role,
                        source_digest,
                        sequence,
                    ),
                    outcome=EnvelopeOutcome.COMMITTED,
                    counts=EnvelopeCounts(0, 0, 0, 0, 0),
                    definitions=(),
                    gap_reasons=(),
                    terminal=True,
                )
                terminal_payload = self._publish(
                    role=role,
                    envelope=terminal,
                    value_rows=(),
                    dry_run=dry_run,
                )
                emitted_digest = framed_sha256(
                    "futureagi.property-catalog.emitted-stream.v1",
                    emitted_digest,
                    terminal_payload,
                )
                first_sequence = first_sequence or sequence
                last_sequence = sequence
                delivery_count += 1
                final = _span_checkpoint(
                    frozen=frozen,
                    build=build,
                    producer_stream_id=producer_stream_id,
                    status=(
                        CheckpointStatus.GAP
                        if gap_reasons
                        else CheckpointStatus.COMPLETE
                    ),
                    terminal=True,
                    source_count=source_count,
                    value_count=value_count,
                    delivery_count=delivery_count,
                    first_sequence=first_sequence,
                    last_sequence=last_sequence,
                    terminal_payload_sha256=terminal_payload,
                    source_digest=source_digest,
                    emitted_digest=emitted_digest,
                    gap_count=len(gap_reasons),
                )
                if not dry_run:
                    assert self._checkpoint_store is not None
                    self._checkpoint_store.append(
                        CheckpointWrite(
                            checkpoint=final,
                            source_cursor="",
                            watermark=str(frozen.audit_generation),
                            source_version_fence=frozen.audit_generation,
                            source_fingerprint=source_digest,
                            previous_payload_sha256=terminal_payload,
                            processed_rows=source_count,
                            gap_reasons=tuple(sorted(gap_reasons)),
                        )
                    )
                return final

            source_cursor = page.next_cursor or ""
            running = _span_checkpoint(
                frozen=frozen,
                build=build,
                producer_stream_id=producer_stream_id,
                status=CheckpointStatus.RUNNING,
                terminal=False,
                source_count=source_count,
                value_count=value_count,
                delivery_count=delivery_count,
                first_sequence=first_sequence,
                last_sequence=last_sequence,
                terminal_payload_sha256=_EMPTY_SHA256,
                source_digest=source_digest,
                emitted_digest=emitted_digest,
                gap_count=len(gap_reasons),
            )
            if not dry_run:
                assert self._checkpoint_store is not None
                self._checkpoint_store.append(
                    CheckpointWrite(
                        checkpoint=running,
                        source_cursor=source_cursor,
                        watermark=accumulator.encode(),
                        source_version_fence=frozen.audit_generation,
                        source_fingerprint=source_digest,
                        previous_payload_sha256=previous_payload,
                        processed_rows=source_count,
                        gap_reasons=tuple(sorted(gap_reasons)),
                    )
                )

    def _run_audit_stream(
        self,
        *,
        frozen: FrozenSpanSource,
        build: AuthoritativeSpanBuild,
        proof: SpanAggregateProof,
        dry_run: bool,
    ) -> CatalogCheckpoint:
        producer_stream_id = build.audit_producer_stream_id
        existing = None
        if not dry_run:
            assert self._checkpoint_store is not None
            existing = self._checkpoint_store.load_checkpoint_write(
                organization_id=build.organization_id,
                workspace_id=build.workspace_id,
                catalog_epoch=build.catalog_epoch,
                catalog_revision=build.catalog_revision,
                build_token=build.build_token,
                source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
                producer_stream_id=producer_stream_id,
            )
        if existing is not None:
            _validate_span_resume(
                existing,
                frozen=frozen,
                build=build,
                producer_stream_id=producer_stream_id,
            )
            if (
                existing.checkpoint.terminal
                and existing.checkpoint.source_count == proof.count
                and existing.checkpoint.source_digest == proof.digest
            ):
                return existing.checkpoint
            if existing.checkpoint.terminal:
                raise PropertyCatalogSpanSourceError(
                    "source audit changed after its terminal checkpoint"
                )

        evidence = PropertyCatalogEnvelope(
            organization_id=build.organization_id,
            workspace_id=build.workspace_id,
            catalog_epoch=build.catalog_epoch,
            catalog_revision=build.catalog_revision,
            build_token=build.build_token,
            projection_version=build.projection_version,
            source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
            producer_stream_id=producer_stream_id,
            sequence=1,
            previous_payload_sha256=ZERO_SHA256,
            source_version=frozen.audit_generation,
            source_fingerprint=proof.digest,
            source_batch_digest=framed_sha256(
                "futureagi.property-catalog.span-source-audit.v2",
                frozen.audit_generation,
                proof.digest,
            ),
            outcome=EnvelopeOutcome.COMMITTED,
            counts=EnvelopeCounts(proof.count, 0, 0, 0, 0),
            definitions=(),
            gap_reasons=(),
            terminal=False,
        )
        evidence_payload = self._publish(
            role=AuthoritativeSpanRole.SOURCE_AUDIT,
            envelope=evidence,
            value_rows=(),
            dry_run=dry_run,
        )
        terminal = PropertyCatalogEnvelope(
            organization_id=build.organization_id,
            workspace_id=build.workspace_id,
            catalog_epoch=build.catalog_epoch,
            catalog_revision=build.catalog_revision,
            build_token=build.build_token,
            projection_version=build.projection_version,
            source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
            producer_stream_id=producer_stream_id,
            sequence=2,
            previous_payload_sha256=evidence_payload,
            source_version=frozen.audit_generation,
            source_fingerprint=proof.digest,
            source_batch_digest=framed_sha256(
                "futureagi.property-catalog.empty-terminal.v1",
                AuthoritativeSpanRole.SOURCE_AUDIT,
                proof.digest,
                2,
            ),
            outcome=EnvelopeOutcome.COMMITTED,
            counts=EnvelopeCounts(0, 0, 0, 0, 0),
            definitions=(),
            gap_reasons=(),
            terminal=True,
        )
        terminal_payload = self._publish(
            role=AuthoritativeSpanRole.SOURCE_AUDIT,
            envelope=terminal,
            value_rows=(),
            dry_run=dry_run,
        )
        emitted_digest = framed_sha256(
            "futureagi.property-catalog.emitted-stream.v1",
            framed_sha256(
                "futureagi.property-catalog.emitted-stream.v1",
                _EMPTY_SHA256,
                evidence_payload,
            ),
            terminal_payload,
        )
        checkpoint = _span_checkpoint(
            frozen=frozen,
            build=build,
            producer_stream_id=producer_stream_id,
            status=CheckpointStatus.COMPLETE,
            terminal=True,
            source_count=proof.count,
            value_count=0,
            delivery_count=2,
            first_sequence=1,
            last_sequence=2,
            terminal_payload_sha256=terminal_payload,
            source_digest=proof.digest,
            emitted_digest=emitted_digest,
            gap_count=0,
        )
        if not dry_run:
            assert self._checkpoint_store is not None
            self._checkpoint_store.append(
                CheckpointWrite(
                    checkpoint=checkpoint,
                    source_cursor="",
                    watermark=str(frozen.audit_generation),
                    source_version_fence=frozen.audit_generation,
                    source_fingerprint=proof.digest,
                    previous_payload_sha256=terminal_payload,
                    processed_rows=proof.count,
                    gap_reasons=(),
                )
            )
        return checkpoint

    def _publish(
        self,
        *,
        role: AuthoritativeSpanRole,
        envelope: PropertyCatalogEnvelope,
        value_rows: Sequence[Mapping[str, Any]],
        dry_run: bool,
    ) -> str:
        if dry_run:
            return encode_envelope(envelope, value_rows=value_rows).payload_sha256
        publisher = self._publishers.get(role)
        if publisher is None:
            raise PropertyCatalogSpanSourceError(
                "authoritative span role has no exact stream publisher"
            )
        return publisher.publish(envelope, value_rows=value_rows)


def stream_requirement(checkpoint: CatalogCheckpoint) -> StreamRequirement:
    """Freeze exact expected evidence without weakening terminal requirements."""

    return StreamRequirement(
        source_adapter=checkpoint.source_adapter,
        producer_stream_id=checkpoint.producer_stream_id,
        source_version_fence=checkpoint.source_version_fence,
        expected_source_count=checkpoint.source_count,
        expected_definition_count=checkpoint.definition_count,
        expected_value_count=checkpoint.value_count,
        expected_tombstone_count=checkpoint.tombstone_count,
        expected_source_digest=checkpoint.source_digest,
        expected_emitted_digest=checkpoint.emitted_digest,
        expected_first_sequence=checkpoint.first_sequence,
        expected_last_sequence=checkpoint.last_sequence,
        expected_terminal_payload_sha256=checkpoint.terminal_payload_sha256,
    )


def _span_checkpoint(
    *,
    frozen: FrozenSpanSource,
    build: AuthoritativeSpanBuild,
    producer_stream_id: str,
    status: CheckpointStatus,
    terminal: bool,
    source_count: int,
    value_count: int,
    delivery_count: int,
    first_sequence: int | None,
    last_sequence: int | None,
    terminal_payload_sha256: str,
    source_digest: str,
    emitted_digest: str,
    gap_count: int,
) -> CatalogCheckpoint:
    return CatalogCheckpoint(
        organization_id=build.organization_id,
        workspace_id=build.workspace_id,
        catalog_epoch=build.catalog_epoch,
        catalog_revision=build.catalog_revision,
        build_token=build.build_token,
        projection_version=build.projection_version,
        source_adapter=SourceAdapter.SPAN_ATTRIBUTE,
        producer_stream_id=producer_stream_id,
        source_version_fence=frozen.audit_generation,
        status=status,
        terminal=terminal,
        source_count=source_count,
        definition_count=0,
        value_count=value_count,
        tombstone_count=0,
        gap_count=gap_count,
        poison_count=0,
        conflict_count=0,
        first_sequence=first_sequence,
        last_sequence=last_sequence,
        last_issued_sequence=last_sequence or 0,
        fenced_sequence=(last_sequence or 0) if terminal else 0,
        terminal_payload_sha256=terminal_payload_sha256,
        delivery_count=delivery_count,
        source_digest=source_digest,
        emitted_digest=emitted_digest,
    )


def _validate_span_resume(
    resume: CheckpointWrite,
    *,
    frozen: FrozenSpanSource,
    build: AuthoritativeSpanBuild,
    producer_stream_id: str,
) -> None:
    checkpoint = resume.checkpoint
    if (
        checkpoint.organization_id != build.organization_id
        or checkpoint.workspace_id != build.workspace_id
        or checkpoint.catalog_epoch != build.catalog_epoch
        or checkpoint.catalog_revision != build.catalog_revision
        or checkpoint.build_token != build.build_token
        or checkpoint.projection_version != build.projection_version
        or checkpoint.source_adapter is not SourceAdapter.SPAN_ATTRIBUTE
        or checkpoint.producer_stream_id != producer_stream_id
        or checkpoint.source_version_fence != frozen.audit_generation
        or resume.source_version_fence != frozen.audit_generation
        or resume.source_fingerprint != checkpoint.source_digest
        or (
            not checkpoint.terminal
            and checkpoint.status is not CheckpointStatus.RUNNING
        )
    ):
        raise PropertyCatalogSpanSourceError("canonical-span resume scope mismatch")
    if checkpoint.terminal:
        if resume.watermark != str(frozen.audit_generation):
            raise PropertyCatalogSpanSourceError(
                "canonical-span terminal watermark mismatch"
            )
        return
    SpanScanCursor.decode(resume.source_cursor)
    SpanAuditAccumulator.decode(
        resume.watermark,
        expected_digest=checkpoint.source_digest,
    )


def _build_span_catalog_rows(
    *, project_id: str, catalog_epoch: int, span: SourceSpan
) -> tuple[tuple[CatalogKeyRow, ...], tuple[CatalogValueRow, ...], tuple[str, ...]]:
    reasons = set(span.gap_reasons)
    key_rows: list[CatalogKeyRow] = []
    value_rows: list[CatalogValueRow] = []
    if not reasons:
        custom = build_catalog_rows(
            scope=CatalogScope(project_id, span.seen_at, catalog_epoch),
            attrs_string=span.attrs_string,
            attrs_number=span.attrs_number,
            attrs_bool=span.attrs_bool,
            attributes_extra=span.attributes_extra,
            limits=CATALOG_BUILD_LIMITS,
            key_only_attributes=span.key_only_attributes,
        )
        key_rows.extend(custom.key_rows)
        value_rows.extend(custom.value_rows)
        reasons.update(custom.metadata.gap_reasons)
        if span.system_attributes:
            system = build_catalog_rows(
                scope=CatalogScope(
                    project_id,
                    span.seen_at,
                    catalog_epoch,
                    source_kind="system_attribute",
                ),
                attrs_string=span.system_attributes,
                attrs_number={},
                attrs_bool={},
                attributes_extra={},
                limits=CATALOG_BUILD_LIMITS,
            )
            key_rows.extend(system.key_rows)
            value_rows.extend(system.value_rows)
            reasons.update(system.metadata.gap_reasons)
    return tuple(key_rows), tuple(value_rows), tuple(sorted(reasons))


def _aggregate_value_rows(
    rows: Sequence[CatalogValueRow],
) -> tuple[CatalogValueRow, ...]:
    grouped: dict[tuple[str, str, str, str, str], CatalogValueRow] = {}
    for row in rows:
        key = (
            row.project_id,
            row.source_kind,
            row.attribute_key,
            row.attribute_type,
            row.value_fingerprint,
        )
        existing = grouped.get(key)
        grouped[key] = (
            row
            if existing is None
            else replace(
                existing,
                first_seen=min(existing.first_seen, row.first_seen),
                last_seen=max(existing.last_seen, row.last_seen),
            )
        )
    return tuple(grouped[key] for key in sorted(grouped))


def _value_wire_row(
    *, build: AuthoritativeSpanBuild, row: CatalogValueRow
) -> Mapping[str, Any]:
    return {
        "organization_id": build.organization_id,
        "workspace_id": build.workspace_id,
        "project_id": row.project_id,
        "catalog_epoch": build.catalog_epoch,
        "catalog_revision": build.catalog_revision,
        "build_token": build.build_token,
        "source_kind": row.source_kind,
        "attribute_key": row.attribute_key,
        "attribute_type": row.attribute_type,
        "value_fingerprint": row.value_fingerprint,
        "value_json": row.value_json,
        "value_search_text_folded": row.value_search_text.casefold(),
        "first_seen": _clickhouse_time(row.first_seen),
        "last_seen": _clickhouse_time(row.last_seen),
    }


def _value_batches(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[tuple[Mapping[str, Any], ...], ...]:
    batches: list[tuple[Mapping[str, Any], ...]] = []
    current: list[Mapping[str, Any]] = []
    current_bytes = 0
    for row in rows:
        encoded_bytes = (
            len(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            + 1
        )
        if encoded_bytes > AUTHORITATIVE_VALUE_BATCH_MAX_BYTES:
            raise PropertyCatalogSpanSourceError(
                "one value row exceeds envelope budget"
            )
        if current and (
            len(current) >= AUTHORITATIVE_VALUE_BATCH_MAX_ROWS
            or current_bytes + encoded_bytes > AUTHORITATIVE_VALUE_BATCH_MAX_BYTES
        ):
            batches.append(tuple(current))
            current = []
            current_bytes = 0
        current.append(row)
        current_bytes += encoded_bytes
    if current:
        batches.append(tuple(current))
    return tuple(batches)


def _logical_payload_template() -> str:
    sql = _LOGICAL_PAYLOAD_SQL_TEMPLATE
    replacements = (
        (
            "    SELECT\n        toString(sp.observation_type)",
            "    SELECT\n        toString(sp.project_id) AS project_id_text,\n"
            "        toString(sp.observation_type)",
        ),
        (
            "    SELECT\n        observation_type_text,",
            "    SELECT\n        project_id_text,\n        observation_type_text,",
        ),
        (
            "    GROUP BY\n        observation_type_text,",
            "    GROUP BY\n        project_id_text,\n        observation_type_text,",
        ),
        (
            "    SELECT\n        observation_type_text AS observation_type,",
            "    SELECT\n        project_id_text AS project_id,\n"
            "        observation_type_text AS observation_type,",
        ),
        (
            "SELECT\n    observation_type,",
            "SELECT\n    project_id,\n    observation_type,",
        ),
    )
    for before, after in replacements:
        if before not in sql:
            raise PropertyCatalogSpanSourceError(
                "canonical-span logical payload SQL template drifted"
            )
        sql = sql.replace(before, after, 1)
    return sql


def _paged_payload_with_audit_sql(source_table: str) -> str:
    inner = _logical_payload_template().format(source_table=source_table)
    hashes = ",\n    ".join(_AUDIT_HASH_EXPRESSIONS)
    return (
        "SELECT logical_rows.*,\n    "
        + hashes
        + "\nFROM (\n"
        + inner
        + "\n) AS logical_rows\n"
        "ORDER BY observation_type, service_name, trace_id, span_id"
    )


def _aggregate_audit_sql(source_table: str) -> str:
    payload = _logical_payload_template()
    payload = (
        payload.replace(
            "sp.project_id = toUUID(%(catalog_project_id)s)",
            "sp.project_id IN %(catalog_project_ids)s",
        )
        .replace("%(catalog_window_start)s", "%(catalog_since)s")
        .replace("%(catalog_window_end)s", "%(catalog_until)s")
    )
    identity_filter = """      AND tuple(
        toString(sp.observation_type),
        toString(sp.service_name),
        sp.trace_id,
        sp.id
    ) IN %(catalog_source_identities)s
"""
    if identity_filter not in payload:
        raise PropertyCatalogSpanSourceError(
            "canonical-span aggregate payload SQL template drifted"
        )
    payload = payload.replace(identity_filter, "", 1).format(source_table=source_table)
    hashes = ",\n        ".join(_AUDIT_HASH_EXPRESSIONS)
    summaries = ",\n    ".join(
        component
        for index in range(1, 5)
        for component in (
            f"groupBitXor(audit_h{index}) AS audit_h{index}_xor",
            f"sumWithOverflow(audit_h{index}) AS audit_h{index}_sum",
        )
    )
    return f"""
WITH logical_rows AS
(
{payload}
), audited_rows AS
(
    SELECT
        logical_rows.*,
        {hashes}
    FROM logical_rows
), max_versions AS
(
    SELECT
        project_id,
        toString(observation_type) AS observation_type_text,
        toString(service_name) AS service_name_text,
        trace_id,
        id AS span_id,
        max(_version) AS max_version
    FROM {source_table}
    PREWHERE project_id IN %(catalog_project_ids)s
      AND start_time >= %(catalog_since)s
      AND start_time < %(catalog_until)s
    GROUP BY
        project_id,
        observation_type_text,
        service_name_text,
        trace_id,
        span_id
), latest_state_variants AS
(
    SELECT
        countIf(state_variants != 1) AS state_conflict_count
    FROM
    (
        SELECT
            uniqExact(tuple(
                sp.is_deleted,
                sp.start_time,
                sp.attrs_string,
                sp.attrs_number,
                sp.attrs_bool,
                sp.attributes_extra,
                sp.model
            )) AS state_variants
        FROM {source_table} AS sp
        INNER JOIN max_versions AS mv
            ON sp.project_id = mv.project_id
           AND toString(sp.observation_type) = mv.observation_type_text
           AND toString(sp.service_name) = mv.service_name_text
           AND sp.trace_id = mv.trace_id
           AND sp.id = mv.span_id
           AND sp._version = mv.max_version
        PREWHERE sp.project_id IN %(catalog_project_ids)s
          AND sp.start_time >= %(catalog_since)s
          AND sp.start_time < %(catalog_until)s
        GROUP BY
            sp.project_id,
            observation_type_text,
            service_name_text,
            sp.trace_id,
            sp.id
    )
)
SELECT
    count() AS source_count,
    {summaries},
    (SELECT state_conflict_count FROM latest_state_variants)
        AS state_conflict_count
FROM audited_rows
"""


def _audit_observation(row: Mapping[str, Any]) -> str:
    return "".join(
        f"{_strict_uint(row.get(f'audit_h{index}'), f'audit h{index}'):016x}"
        for index in range(1, 5)
    )


def _validate_source_select(
    sql: str, *, source_table: str, catalog_database: str
) -> None:
    stripped = sql.strip()
    if (
        not stripped
        or ";" in stripped
        or not re.match(r"^(?:SELECT|WITH)\b", stripped, re.IGNORECASE)
        or _FORBIDDEN_SQL_RE.search(stripped) is not None
        or source_table not in stripped
        or f"`{catalog_database}`" in stripped
    ):
        raise PropertyCatalogSpanSourceError(
            "canonical-span source boundary accepts only its pinned SELECTs"
        )


def _clickhouse_time(value: datetime) -> str:
    _require_utc(value, "catalog value timestamp")
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")


def _clickhouse_utc_hour(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise PropertyCatalogSpanSourceError(
            "occupied-hour value must be a ClickHouse datetime"
        )
    hour = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    if hour.minute or hour.second or hour.microsecond:
        raise PropertyCatalogSpanSourceError(
            "occupied-hour value is not aligned to an hour"
        )
    return hour


def _clickhouse_utc_datetime(value: Any, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise PropertySourceError(f"{field} must be a ClickHouse datetime")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _unit_may_be_occupied(
    occupied_hours: frozenset[tuple[str, datetime]],
    *,
    project_id: str,
    window_start: datetime,
    window_end: datetime,
) -> bool:
    # Frozen scan units may start at a non-hour boundary.  Test both wall-clock
    # hours they can overlap so the physical occupancy hint never introduces a
    # false negative; a false positive merely costs one ordinary identity read.
    hour = window_start.replace(minute=0, second=0, microsecond=0)
    while hour < window_end:
        if (project_id, hour) in occupied_hours:
            return True
        hour += timedelta(hours=1)
    return False


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be timezone-aware UTC")


def _strict_uint(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value < (1 << 64):
        raise PropertyCatalogSpanSourceError(f"{field} is not a UInt64")
    return value


def _positive_uint64(value: Any, field: str) -> None:
    if type(value) is not int or not 1 <= value < (1 << 64):
        raise ValueError(f"{field} must be a positive UInt64")


__all__ = [
    "AUTHORITATIVE_VALUE_BATCH_MAX_BYTES",
    "AUTHORITATIVE_VALUE_BATCH_MAX_ROWS",
    "AuthoritativeSpanBuild",
    "AuthoritativeSpanReconciler",
    "AuthoritativeSpanResult",
    "AuthoritativeSpanRole",
    "CanonicalSpanAttributeGroupPageLoader",
    "CanonicalSpanSourceClient",
    "CanonicalSpanSourceReader",
    "CANONICAL_SPAN_QUERY_TIMEOUT_MS",
    "CANONICAL_SPAN_SCAN_WINDOW_HOURS",
    "DEV_CANONICAL_SPAN_PAGE_ROWS",
    "DEV_INITIAL_BACKFILL_CANONICAL_SPAN_PAGE_ROWS",
    "DEV_INITIAL_BACKFILL_CANONICAL_SPAN_QUERY_TIMEOUT_MS",
    "FrozenSpanSource",
    "PropertyCatalogSpanSourceError",
    "RevisionPinnedCatalogClient",
    "RevisionPinnedSpanAttributeGroupPageLoader",
    "MAX_CANONICAL_SPAN_PAGE_ROWS",
    "SPAN_AUDIT_CUTOFF_LABEL",
    "SpanScanCursor",
    "SpanScanPage",
    "stream_requirement",
]
