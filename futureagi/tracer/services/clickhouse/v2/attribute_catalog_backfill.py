"""Bounded, resumable CH25 span-attribute catalog backfill.

This module intentionally owns no deployment or activation behavior.  It reads
one explicitly-scoped project's historical ``spans`` rows with SELECTs and may
write only the three catalog tables named in ``CATALOG_BACKFILL_WRITE_TABLES``.
The management-command wrapper supplies the ClickHouse connection and signal
handling; the runner stays deterministic and unit-testable.

Safety properties:

* UTC, half-open, hour-aligned windows and one UUID project are mandatory.
* Every source page is frozen by a per-window ``_version`` fence and advances
  on the complete ReplacingMergeTree sorting-key identity. ``FINAL`` is never
  used; versions and tombstones are collapsed explicitly with ``argMax``.
* Source page rows, typed-map entries/bytes, result bytes, memory, threads,
  windows, total runtime, and every ClickHouse call have hard ceilings.
* A page is acknowledged only after synchronous key rows, synchronous value
  rows, and then its checkpoint have succeeded. Replaying an unacknowledged
  page is safe because the catalog has no occurrence counters.
* Any bounded omission is recorded as a fixed gap reason. Unexpected input or
  I/O errors abort instead of silently qualifying incomplete history.
* Dry-run executes the same bounded SELECT/build path but performs zero writes.
"""

from __future__ import annotations

import json
import math
import queue
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Protocol

from tracer.services.clickhouse.v2.attribute_catalog_builder import (
    GAP_INVALID_ATTRIBUTE_KEY,
    GAP_INVALID_BOOLEAN,
    GAP_INVALID_SCALAR,
    GAP_MAX_ARRAY_MEMBERS,
    GAP_MAX_ENCODED_BYTES,
    GAP_MAX_KEYS,
    AttributeType,
    CatalogBuildLimits,
    CatalogKeyRow,
    CatalogScope,
    CatalogValueRow,
    build_catalog_rows,
)
from tracer.utils.attribute_suggestion_contract import (
    JSON_ARRAY_STRING_SUGGESTION_MAX_UTF8_BYTES,
    TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES,
)

CATALOG_BACKFILL_ACK = "FI_PROPERTY_CATALOG_DEV_BACKFILL"
CATALOG_BACKFILL_ENVIRONMENT = "development"
CATALOG_BACKFILL_CLOUD_DEPLOYMENT = "DEV"
CATALOG_DATABASE_PREFIX = "property_catalog_dev_"
CATALOG_PROJECTION_VERSION = 2

SOURCE_TABLE = "spans"
KEY_TABLE = "span_attribute_key_catalog"
VALUE_TABLE = "span_attribute_value_catalog"
CHECKPOINT_TABLE = "span_attribute_catalog_checkpoints"
CATALOG_BACKFILL_WRITE_TABLES = frozenset((KEY_TABLE, VALUE_TABLE, CHECKPOINT_TABLE))

MAX_CLICKHOUSE_CALL_SECONDS = 10.0
CLICKHOUSE_SERVER_MAX_EXECUTION_SECONDS = 8
CLICKHOUSE_MAX_THREADS = 1
CLICKHOUSE_MAX_MEMORY_BYTES = 768 * 1024 * 1024
CLICKHOUSE_MAX_BYTES_TO_READ = 512 * 1024 * 1024
CLICKHOUSE_MAX_ROWS_TO_READ = 1_000_000
CLICKHOUSE_MAX_RANGE_ROWS_TO_READ = 10_000_000
CLICKHOUSE_MAX_RESULT_BYTES = 128 * 1024 * 1024

DEFAULT_PAGE_ROWS = 8
MAX_PAGE_ROWS = 256
DEFAULT_MAX_WINDOWS = 24
MAX_WINDOWS = 366 * 24
DEFAULT_MAX_RUNTIME_SECONDS = 55 * 60
MAX_RUNTIME_SECONDS = 115 * 60
DEFAULT_SOURCE_ATTRIBUTE_ENTRIES = 1_024
MAX_SOURCE_ATTRIBUTE_ENTRIES = 2_048
DEFAULT_SOURCE_ATTRIBUTE_BYTES = 4 * 1024 * 1024
MAX_SOURCE_ATTRIBUTE_BYTES = 8 * 1024 * 1024
MAX_WORKER_ID_BYTES = 128
MAX_ERROR_BYTES = 2_048
MAX_DATABASE_NAME_BYTES = 128

# Keep historical construction byte-identical to the collector defaults.
CATALOG_BUILD_LIMITS = CatalogBuildLimits(
    max_keys=1_024,
    max_array_members=8_192,
    max_encoded_bytes=8 * 1024 * 1024,
)

# Typed-map strings remain filterable at every size, but both picker paths
# deliberately suggest only values through 16 KiB and retain larger keys as
# key-only. JSON array strings separately mirror the public picker's exact
# 4 KiB UTF-8 cap; larger array strings are unselectable and are omitted.
PROJECTED_TYPED_STRING_VALUE_BYTES = TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES
PROJECTED_ARRAY_STRING_VALUE_BYTES = JSON_ARRAY_STRING_SUGGESTION_MAX_UTF8_BYTES
PROJECTED_VALUE_BUDGET_BYTES = 1 * 1024 * 1024

GAP_SOURCE_ATTRIBUTE_ENTRIES = "source_attribute_entries"
GAP_SOURCE_ATTRIBUTE_BYTES = "source_attribute_bytes"
GAP_SELECTABLE_VALUE_PROJECTION = "selectable_value_projection"
GAP_INVALID_SOURCE_MAPS = "invalid_source_maps"
GAP_INVALID_ATTRIBUTES_EXTRA = "invalid_attributes_extra"
GAP_SYSTEM_VALUE_PROJECTION = "system_value_projection"
_SOURCE_GAP_ORDER = (
    GAP_SOURCE_ATTRIBUTE_ENTRIES,
    GAP_SOURCE_ATTRIBUTE_BYTES,
    GAP_SELECTABLE_VALUE_PROJECTION,
    GAP_INVALID_SOURCE_MAPS,
    GAP_INVALID_ATTRIBUTES_EXTRA,
    GAP_SYSTEM_VALUE_PROJECTION,
)
_BUILDER_GAP_ORDER = (
    GAP_MAX_KEYS,
    GAP_MAX_ARRAY_MEMBERS,
    GAP_MAX_ENCODED_BYTES,
    GAP_INVALID_ATTRIBUTE_KEY,
    GAP_INVALID_SCALAR,
    GAP_INVALID_BOOLEAN,
)

_IDENTIFIER_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")
_CATALOG_DATABASE_RE = re.compile(
    rf"\A{re.escape(CATALOG_DATABASE_PREFIX)}[a-z0-9][a-z0-9_]*\Z"
)
_TERMINAL_STATUSES = frozenset(("complete", "gap"))
_RESUMABLE_STATUSES = frozenset(("pending", "running", "failed"))
_ALL_STATUSES = _TERMINAL_STATUSES | _RESUMABLE_STATUSES
_ZERO_CURSOR = ("", "", "", "")
_PAGE_MAX_CLICKHOUSE_CALLS = 5  # identity + payload SELECT, key/value, checkpoint
_PAGE_START_BUDGET_SECONDS = (
    _PAGE_MAX_CLICKHOUSE_CALLS * MAX_CLICKHOUSE_CALL_SECONDS
) + 1.0


class CatalogBackfillError(RuntimeError):
    """Fail-closed operator-facing error."""


class CatalogBackfillCallDeadlineExceeded(CatalogBackfillError):
    """A ClickHouse operation crossed the absolute ten-second ceiling."""


class StopRequested(Protocol):
    def __call__(self) -> bool: ...


class CatalogBackfillIO(Protocol):
    """Minimal, fake-friendly ClickHouse boundary used by the pure runner."""

    def select(
        self,
        sql: str,
        params: Mapping[str, Any],
        *,
        role: Literal["source", "catalog"],
        settings: Mapping[str, Any],
    ) -> list[dict[str, Any]]: ...

    def insert(
        self,
        table: str,
        rows: Sequence[Sequence[Any]],
        columns: Sequence[str],
        *,
        settings: Mapping[str, Any],
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class CatalogBackfillConfig:
    environment: str
    cloud_deployment: str
    dev_identity: str
    acknowledgement: str
    project_id: str
    since: datetime
    until: datetime
    catalog_epoch: int
    source_database: str
    target_database: str
    page_rows: int = DEFAULT_PAGE_ROWS
    max_windows: int = DEFAULT_MAX_WINDOWS
    max_runtime_seconds: int = DEFAULT_MAX_RUNTIME_SECONDS
    max_source_attribute_entries: int = DEFAULT_SOURCE_ATTRIBUTE_ENTRIES
    max_source_attribute_bytes: int = DEFAULT_SOURCE_ATTRIBUTE_BYTES
    dry_run: bool = False
    worker_id: str = "catalog-backfill"

    def validated(self) -> CatalogBackfillConfig:
        if self.environment != CATALOG_BACKFILL_ENVIRONMENT:
            raise CatalogBackfillError("catalog backfill is development-only")
        if self.cloud_deployment != CATALOG_BACKFILL_CLOUD_DEPLOYMENT:
            raise CatalogBackfillError("catalog backfill requires CLOUD_DEPLOYMENT=DEV")
        if (
            not isinstance(self.dev_identity, str)
            or re.fullmatch(r"dev:[a-z0-9][a-z0-9._:/-]{2,127}", self.dev_identity)
            is None
            or "prod" in self.dev_identity
            or "live" in self.dev_identity
        ):
            raise CatalogBackfillError(
                "catalog backfill requires a pinned dev:<identity> endpoint identity"
            )
        if self.acknowledgement != CATALOG_BACKFILL_ACK:
            raise CatalogBackfillError(
                "explicit catalog backfill acknowledgement missing"
            )

        try:
            canonical_project_id = str(uuid.UUID(self.project_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise CatalogBackfillError("project_id must be one canonical UUID") from exc
        if canonical_project_id != self.project_id.lower():
            raise CatalogBackfillError("project_id must use canonical UUID form")

        since = _require_utc_hour(self.since, "since")
        until = _require_utc_hour(self.until, "until")
        if since >= until:
            raise CatalogBackfillError("since must be before until")
        window_count = int((until - since).total_seconds() // 3_600)
        if window_count > MAX_WINDOWS:
            raise CatalogBackfillError(
                "backfill range exceeds the fixed 12-month ceiling"
            )
        if not 1 <= self.catalog_epoch <= 65_535:
            raise CatalogBackfillError("catalog_epoch must be in UInt16 range 1..65535")

        _validate_database(self.source_database, "source_database")
        _validate_database(self.target_database, "target_database")
        if self.source_database == self.target_database:
            raise CatalogBackfillError(
                "source_database and target_database must be distinct"
            )
        if _CATALOG_DATABASE_RE.fullmatch(self.target_database) is None:
            raise CatalogBackfillError(
                f"target_database must start {CATALOG_DATABASE_PREFIX!r}"
            )
        _bounded_positive(self.page_rows, MAX_PAGE_ROWS, "page_rows")
        _bounded_positive(self.max_windows, MAX_WINDOWS, "max_windows")
        if window_count > self.max_windows:
            raise CatalogBackfillError(
                f"range has {window_count} hourly windows; max_windows is {self.max_windows}"
            )
        _bounded_positive(
            self.max_runtime_seconds,
            MAX_RUNTIME_SECONDS,
            "max_runtime_seconds",
        )
        _bounded_positive(
            self.max_source_attribute_entries,
            MAX_SOURCE_ATTRIBUTE_ENTRIES,
            "max_source_attribute_entries",
        )
        _bounded_positive(
            self.max_source_attribute_bytes,
            MAX_SOURCE_ATTRIBUTE_BYTES,
            "max_source_attribute_bytes",
        )
        if not self.worker_id:
            raise CatalogBackfillError("worker_id must not be empty")
        if len(self.worker_id.encode("utf-8")) > MAX_WORKER_ID_BYTES:
            raise CatalogBackfillError("worker_id exceeds its fixed byte ceiling")
        return self


@dataclass(frozen=True, slots=True, order=True)
class SourceCursor:
    observation_type: str = ""
    service_name: str = ""
    trace_id: str = ""
    span_id: str = ""

    def as_tuple(self) -> tuple[str, str, str, str]:
        return self.observation_type, self.service_name, self.trace_id, self.span_id


@dataclass(frozen=True, slots=True)
class HourWindow:
    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class WindowCheckpoint:
    window: HourWindow
    source_version_fence: int
    cursor: SourceCursor
    status: Literal["pending", "running", "complete", "gap", "failed"]
    source_rows: int
    processed_rows: int
    key_rows: int
    value_rows: int
    gap_count: int
    gap_reasons: tuple[str, ...]
    started_at: datetime
    state_version: int
    projection_version: int
    state_variants: int = 1


@dataclass(frozen=True, slots=True)
class SourceSpan:
    cursor: SourceCursor
    seen_at: datetime
    attrs_string: Mapping[str, str]
    attrs_number: Mapping[str, int | float | Decimal]
    attrs_bool: Mapping[str, int]
    attributes_extra: Mapping[str, Any]
    system_attributes: Mapping[str, str] = field(default_factory=dict)
    key_only_attributes: frozenset[tuple[str, AttributeType]] = frozenset()
    gap_reasons: tuple[str, ...] = ()


@dataclass(slots=True)
class MutableWindowProgress:
    window: HourWindow
    source_version_fence: int
    cursor: SourceCursor = field(default_factory=SourceCursor)
    source_rows: int = 0
    processed_rows: int = 0
    key_rows: int = 0
    value_rows: int = 0
    gap_count: int = 0
    gap_reasons: set[str] = field(default_factory=set)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class CatalogBackfillSummary:
    project_id: str
    catalog_epoch: int
    windows_total: int
    windows_skipped: int
    windows_completed: int
    windows_gap: int
    windows_pending: int
    source_rows: int
    key_rows: int
    value_rows: int
    gap_rows: int
    gap_reasons: tuple[str, ...]
    dry_run: bool
    stopped: bool
    elapsed_seconds: float


_SOURCE_OCCUPIED_HOURS_SQL_TEMPLATE = """
WITH toUInt64(toUnixTimestamp64Nano(now64(9, 'UTC'))) AS source_version_fence
SELECT
    source_version_fence,
    countIf(_version > source_version_fence) AS future_version_rows,
    arraySort(
        groupUniqArrayIf(8785)(
            toStartOfHour(start_time),
            _version <= source_version_fence
        )
    ) AS occupied_hours
FROM {source_table}
PREWHERE project_id = toUUID(%(catalog_project_id)s)
  AND start_time >= %(catalog_since)s
  AND start_time < %(catalog_until)s
"""


_SOURCE_IDENTITY_PAGE_SQL_TEMPLATE = """
SELECT
    toString(observation_type) AS observation_type,
    toString(service_name) AS service_name,
    trace_id,
    id AS span_id
FROM {source_table}
PREWHERE project_id = toUUID(%(catalog_project_id)s)
  AND start_time >= %(catalog_window_start)s
  AND start_time < %(catalog_window_end)s
  AND _version <= %(catalog_source_version_fence)s
WHERE tuple(
    toString(observation_type),
    toString(service_name),
    trace_id,
    id
) > tuple(
    %(catalog_after_observation_type)s,
    %(catalog_after_service_name)s,
    %(catalog_after_trace_id)s,
    %(catalog_after_span_id)s
)
GROUP BY
    observation_type,
    service_name,
    toStartOfHour(start_time),
    trace_id,
    id
HAVING argMax(is_deleted, _version) = 0
ORDER BY
    observation_type ASC,
    service_name ASC,
    trace_id ASC,
    span_id ASC
LIMIT %(catalog_source_limit)s
"""


_SOURCE_PAYLOAD_SQL_TEMPLATE = """
WITH projected_rows AS
(
    SELECT
        toString(sp.observation_type) AS observation_type_text,
        toString(sp.service_name) AS service_name_text,
        sp.trace_id AS trace_id,
        sp.id AS span_id,
        sp.start_time AS seen_at,
        ifNull(toString(sp.model), '') AS system_model_raw,
        (
            system_model_raw IN ('', '00000000-0000-0000-0000-000000000000')
            OR length(system_model_raw)
                <= %(catalog_projected_typed_string_value_bytes)s
        )
            AS system_model_complete,
        if(
            system_model_complete
            AND system_model_raw NOT IN (
                '', '00000000-0000-0000-0000-000000000000'
            ),
            system_model_raw,
            ''
        ) AS system_model,
        sp._version AS source_version,
        sp.is_deleted AS is_deleted,
        arraySum(
            value -> if(
                notEmpty(value)
                AND length(value)
                    <= %(catalog_projected_typed_string_value_bytes)s,
                length(value),
                0
            ),
            mapValues(sp.attrs_string)
        ) AS projected_string_value_bytes,
        projected_string_value_bytes
            <= %(catalog_projected_value_budget_bytes)s
            AS projected_string_values_complete,
        arrayMap(
            (key, value) -> tuple(
                key,
                toUInt8(
                    empty(value)
                    OR length(value)
                        > %(catalog_projected_typed_string_value_bytes)s
                    OR NOT projected_string_values_complete
                ),
                if(
                    notEmpty(value)
                    AND length(value)
                        <= %(catalog_projected_typed_string_value_bytes)s
                    AND projected_string_values_complete,
                    value,
                    ''
                )
            ),
            mapKeys(sp.attrs_string),
            mapValues(sp.attrs_string)
        ) AS projected_attrs_string,
        sp.attrs_number AS projected_attrs_number,
        sp.attrs_bool AS projected_attrs_bool,
        isValidJSON(sp.attributes_extra)
            AND toString(JSONType(sp.attributes_extra)) = 'Object'
            AS attributes_extra_valid,
        if(
            attributes_extra_valid,
            JSONExtractKeysAndValuesRaw(sp.attributes_extra),
            CAST([], 'Array(Tuple(String, String))')
        ) AS extra_key_values,
        arrayMap(
            item -> tuple(
                tupleElement(item, 1),
                toString(JSONType(tupleElement(item, 2))),
                arrayFilter(
                    member ->
                        (
                            toString(JSONType(member)) = 'String'
                            AND notEmpty(JSONExtractString(member))
                            AND length(JSONExtractString(member))
                                <= %(catalog_projected_array_string_value_bytes)s
                        )
                        OR toString(JSONType(member)) IN ('Int64', 'UInt64', 'Bool')
                        OR (
                            toString(JSONType(member)) IN ('Float64', 'Double')
                            AND isFinite(JSONExtractFloat(member))
                        ),
                    if(
                        toString(JSONType(tupleElement(item, 2))) = 'Array',
                        JSONExtractArrayRaw(tupleElement(item, 2)),
                        CAST([], 'Array(String)')
                    )
                )
            ),
            extra_key_values
        ) AS projected_extra_candidates,
        arraySum(
            item -> if(
                tupleElement(item, 2) = 'Array',
                length(tupleElement(item, 3)),
                0
            ),
            projected_extra_candidates
        ) AS projected_array_members,
        arraySum(
            item -> if(
                tupleElement(item, 2) = 'Array',
                arraySum(
                    member -> length(member),
                    tupleElement(item, 3)
                ),
                0
            ),
            projected_extra_candidates
        ) AS projected_array_value_bytes,
        projected_array_members <= %(catalog_projected_array_members)s
            AND projected_array_value_bytes
                <= %(catalog_projected_value_budget_bytes)s
            AS projected_array_values_fit,
        arrayMap(
            item -> tuple(
                tupleElement(item, 1),
                tupleElement(item, 2),
                toUInt8(
                    tupleElement(item, 2) != 'Array'
                    OR NOT projected_array_values_fit
                ),
                if(
                    tupleElement(item, 2) = 'Array'
                    AND projected_array_values_fit,
                    tupleElement(item, 3),
                    CAST([], 'Array(String)')
                )
            ),
            projected_extra_candidates
        ) AS projected_attributes_extra,
        projected_string_values_complete AND projected_array_values_fit
            AS selectable_projection_complete
    FROM {source_table} AS sp
    PREWHERE sp.project_id = toUUID(%(catalog_project_id)s)
      AND sp.start_time >= %(catalog_window_start)s
      AND sp.start_time < %(catalog_window_end)s
      AND sp._version <= %(catalog_source_version_fence)s
      AND tuple(
        toString(sp.observation_type),
        toString(sp.service_name),
        sp.trace_id,
        sp.id
    ) IN %(catalog_source_identities)s
), latest_rows AS
(
    SELECT
        observation_type_text,
        service_name_text,
        trace_id,
        span_id,
        argMax(
            tuple(
                seen_at,
                projected_attrs_string,
                projected_attrs_number,
                projected_attrs_bool,
                projected_attributes_extra,
                attributes_extra_valid,
                selectable_projection_complete,
                system_model,
                system_model_complete
            ),
            source_version
        ) AS latest_state
    FROM projected_rows
    GROUP BY
        observation_type_text,
        service_name_text,
        trace_id,
        span_id
    HAVING argMax(is_deleted, source_version) = 0
    ORDER BY
        observation_type_text ASC,
        service_name_text ASC,
        trace_id ASC,
        span_id ASC
), measured_rows AS
(
    SELECT
        observation_type_text AS observation_type,
        service_name_text AS service_name,
        trace_id,
        span_id,
        tupleElement(latest_state, 1) AS seen_at,
        tupleElement(latest_state, 2) AS attrs_string_projection,
        tupleElement(latest_state, 3) AS attrs_number,
        tupleElement(latest_state, 4) AS attrs_bool,
        tupleElement(latest_state, 5) AS attributes_extra_projection,
        tupleElement(latest_state, 6) AS attributes_extra_valid,
        tupleElement(latest_state, 7) AS selectable_projection_complete,
        tupleElement(latest_state, 8) AS system_model,
        tupleElement(latest_state, 9) AS system_model_complete,
        length(attrs_string_projection)
          + length(mapKeys(attrs_number))
          + length(mapKeys(attrs_bool))
          + length(attributes_extra_projection) AS source_attribute_entries,
        length(
            toJSONString(
                tuple(
                    attrs_string_projection,
                    attrs_number,
                    attrs_bool,
                    attributes_extra_projection
                )
            )
        ) AS source_attribute_bytes
    FROM latest_rows
)
SELECT
    observation_type,
    service_name,
    trace_id,
    span_id,
    seen_at,
    source_attribute_entries,
    source_attribute_bytes,
    attrs_string_projection,
    attrs_number,
    attrs_bool,
    attributes_extra_projection,
    attributes_extra_valid,
    selectable_projection_complete,
    system_model,
    system_model_complete
FROM measured_rows
ORDER BY
    observation_type ASC,
    service_name ASC,
    trace_id ASC,
    span_id ASC
"""


_CHECKPOINT_READ_SQL_TEMPLATE = """
WITH checkpoint_rows AS
(
    SELECT
        *,
        max(_version) OVER
        (
            PARTITION BY project_id, catalog_epoch, window_start, window_end
        ) AS latest_version
    FROM {checkpoint_table}
    PREWHERE project_id = toUUID(%(catalog_project_id)s)
      AND catalog_epoch = %(catalog_epoch)s
    WHERE window_start < %(catalog_until)s
      AND window_end > %(catalog_since)s
), latest_checkpoints AS
(
    SELECT
        window_start,
        window_end,
        argMax(
            tuple(
                source_version_fence,
                cursor_observation_type,
                cursor_service_name,
                cursor_trace_id,
                cursor_span_id,
                status,
                source_rows,
                processed_rows,
                key_rows,
                value_rows,
                gap_count,
                gap_reasons,
                started_at,
                projection_version
            ),
            _version
        ) AS state,
        max(_version) AS state_version,
        uniqExactIf(
            tuple(
                source_version_fence,
                cursor_observation_type,
                cursor_service_name,
                cursor_trace_id,
                cursor_span_id,
                status,
                source_rows,
                processed_rows,
                key_rows,
                value_rows,
                gap_count,
                gap_reasons,
                started_at,
                projection_version
            ),
            _version = latest_version
        ) AS state_variants
    FROM checkpoint_rows
    GROUP BY window_start, window_end
)
SELECT
    window_start,
    window_end,
    tupleElement(state, 1) AS source_version_fence,
    tupleElement(state, 2) AS cursor_observation_type,
    tupleElement(state, 3) AS cursor_service_name,
    tupleElement(state, 4) AS cursor_trace_id,
    tupleElement(state, 5) AS cursor_span_id,
    toString(tupleElement(state, 6)) AS status,
    tupleElement(state, 7) AS source_rows,
    tupleElement(state, 8) AS processed_rows,
    tupleElement(state, 9) AS key_rows,
    tupleElement(state, 10) AS value_rows,
    tupleElement(state, 11) AS gap_count,
    tupleElement(state, 12) AS gap_reasons,
    tupleElement(state, 13) AS started_at,
    tupleElement(state, 14) AS projection_version,
    state_version,
    state_variants
FROM latest_checkpoints
ORDER BY window_start ASC, window_end ASC
LIMIT %(catalog_checkpoint_limit)s
"""


KEY_INSERT_COLUMNS = (
    "project_id",
    "attribute_key",
    "key_folded",
    "attribute_type",
    "first_seen",
    "last_seen",
    "catalog_epoch",
    "source_kind",
)
VALUE_INSERT_COLUMNS = (
    "project_id",
    "attribute_key",
    "attribute_type",
    "value_fingerprint",
    "value_json",
    "value_search_text",
    "first_seen",
    "last_seen",
    "catalog_epoch",
    "source_kind",
)
CHECKPOINT_INSERT_COLUMNS = (
    "project_id",
    "catalog_epoch",
    "projection_version",
    "window_start",
    "window_end",
    "source_version_fence",
    "cursor_observation_type",
    "cursor_service_name",
    "cursor_trace_id",
    "cursor_span_id",
    "status",
    "source_rows",
    "processed_rows",
    "key_rows",
    "value_rows",
    "gap_count",
    "gap_reasons",
    "run_id",
    "worker_id",
    "error",
    "started_at",
    "updated_at",
    "finished_at",
    "_version",
)

_ATTRIBUTE_ENUM_TYPE = (
    "Enum8('string' = 1, 'number' = 2, 'boolean' = 3, "
    "'array' = 4, 'map' = 5, 'json' = 6)"
)
_SOURCE_KIND_ENUM_TYPE = "Enum8('custom_attribute' = 1, 'system_attribute' = 2)"
_CHECKPOINT_STATUS_ENUM_TYPE = (
    "Enum8('pending' = 1, 'running' = 2, 'complete' = 3, 'gap' = 4, 'failed' = 5)"
)
CATALOG_INSERT_COLUMNS: dict[str, tuple[str, ...]] = {
    KEY_TABLE: KEY_INSERT_COLUMNS,
    VALUE_TABLE: VALUE_INSERT_COLUMNS,
    CHECKPOINT_TABLE: CHECKPOINT_INSERT_COLUMNS,
}
CATALOG_INSERT_COLUMN_TYPES: dict[str, tuple[str, ...]] = {
    KEY_TABLE: (
        "UUID",
        "String",
        "String",
        _ATTRIBUTE_ENUM_TYPE,
        "DateTime64(6, 'UTC')",
        "DateTime64(6, 'UTC')",
        "UInt16",
        _SOURCE_KIND_ENUM_TYPE,
    ),
    VALUE_TABLE: (
        "UUID",
        "String",
        _ATTRIBUTE_ENUM_TYPE,
        "FixedString(64)",
        "String",
        "String",
        "DateTime64(6, 'UTC')",
        "DateTime64(6, 'UTC')",
        "UInt16",
        _SOURCE_KIND_ENUM_TYPE,
    ),
    CHECKPOINT_TABLE: (
        "UUID",
        "UInt16",
        "UInt16",
        "DateTime64(6, 'UTC')",
        "DateTime64(6, 'UTC')",
        "UInt64",
        "String",
        "String",
        "String",
        "String",
        _CHECKPOINT_STATUS_ENUM_TYPE,
        "UInt64",
        "UInt64",
        "UInt64",
        "UInt64",
        "UInt64",
        "Array(String)",
        "UUID",
        "String",
        "String",
        "DateTime64(6, 'UTC')",
        "DateTime64(6, 'UTC')",
        "Nullable(DateTime64(6, 'UTC'))",
        "UInt64",
    ),
}

READ_SETTINGS: dict[str, Any] = {
    # Every source/catalog read is server-enforced read-only even if an
    # operator accidentally supplies a broader ClickHouse identity.  Writes
    # use the separate WRITE_SETTINGS path and the hard table allowlist below.
    "readonly": 2,
    "max_execution_time": CLICKHOUSE_SERVER_MAX_EXECUTION_SECONDS,
    "timeout_overflow_mode": "throw",
    "max_threads": CLICKHOUSE_MAX_THREADS,
    "max_block_size": 1,
    "preferred_block_size_bytes": 1 * 1024 * 1024,
    "max_memory_usage": CLICKHOUSE_MAX_MEMORY_BYTES,
    "max_bytes_to_read": CLICKHOUSE_MAX_BYTES_TO_READ,
    "read_overflow_mode": "throw",
    "max_rows_to_read": CLICKHOUSE_MAX_ROWS_TO_READ,
    "max_result_bytes": CLICKHOUSE_MAX_RESULT_BYTES,
    "result_overflow_mode": "throw",
}
RANGE_READ_SETTINGS: dict[str, Any] = {
    **READ_SETTINGS,
    "max_rows_to_read": CLICKHOUSE_MAX_RANGE_ROWS_TO_READ,
    "max_result_rows": MAX_WINDOWS + 1,
}
WRITE_SETTINGS: dict[str, Any] = {
    "async_insert": 0,
    "wait_for_async_insert": 1,
    "max_execution_time": CLICKHOUSE_SERVER_MAX_EXECUTION_SECONDS,
    "timeout_overflow_mode": "throw",
    "max_threads": CLICKHOUSE_MAX_THREADS,
    "max_memory_usage": CLICKHOUSE_MAX_MEMORY_BYTES,
}


class TimedCatalogBackfillIO:
    """Enforce the absolute call ceiling around an injected CH client boundary."""

    def __init__(
        self,
        source_client: Any,
        catalog_client: Any,
        source_cancel_client: Any,
        catalog_cancel_client: Any,
        *,
        target_database: str,
        clock: Callable[[], float] = time.monotonic,
        max_call_seconds: float = MAX_CLICKHOUSE_CALL_SECONDS,
        query_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ) -> None:
        if max_call_seconds <= 0 or max_call_seconds > MAX_CLICKHOUSE_CALL_SECONDS:
            raise CatalogBackfillError("ClickHouse call ceiling must be in (0, 10]")
        clients = (
            source_client,
            catalog_client,
            source_cancel_client,
            catalog_cancel_client,
        )
        if len({id(client) for client in clients}) != len(clients):
            raise CatalogBackfillError(
                "source, catalog, and cancellation clients must be distinct"
            )
        _validate_database(target_database, "target_database")
        if _CATALOG_DATABASE_RE.fullmatch(target_database) is None:
            raise CatalogBackfillError(
                f"target_database must start {CATALOG_DATABASE_PREFIX!r}"
            )
        self._source_client = source_client
        self._catalog_client = catalog_client
        self._source_cancel_client = source_cancel_client
        self._catalog_cancel_client = catalog_cancel_client
        self._target_database = target_database
        self._clock = clock
        self._max_call_seconds = max_call_seconds
        self._query_id_factory = query_id_factory

    def select(
        self,
        sql: str,
        params: Mapping[str, Any],
        *,
        role: Literal["source", "catalog"],
        settings: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        if not _is_select_only(sql):
            raise CatalogBackfillError("source/checkpoint read must be one SELECT")
        if role not in ("source", "catalog"):
            raise CatalogBackfillError("ClickHouse SELECT role is invalid")
        client = self._source_client if role == "source" else self._catalog_client
        cancel_client = (
            self._source_cancel_client
            if role == "source"
            else self._catalog_cancel_client
        )
        result = self._call_with_deadline(
            client,
            cancel_client,
            "SELECT",
            lambda query_id: client.query(
                sql,
                parameters=dict(params),
                settings=dict(settings),
                transport_settings={"X-ClickHouse-Query-Id": query_id},
            ),
        )
        if hasattr(result, "named_results"):
            return list(result.named_results())
        if hasattr(result, "result_rows") and hasattr(result, "column_names"):
            return [
                dict(zip(result.column_names, row, strict=True))
                for row in result.result_rows
            ]
        raise CatalogBackfillError("ClickHouse SELECT returned an unsupported result")

    def insert(
        self,
        table: str,
        rows: Sequence[Sequence[Any]],
        columns: Sequence[str],
        *,
        settings: Mapping[str, Any],
    ) -> None:
        if not rows:
            return
        expected_prefix = f"`{self._target_database}`."
        if not table.startswith(expected_prefix):
            raise CatalogBackfillError(
                "write target must use the configured fully-qualified catalog database"
            )
        unqualified = table.removeprefix(expected_prefix).strip("`")
        if "." in unqualified or f"`{unqualified}`" != table.removeprefix(
            expected_prefix
        ):
            raise CatalogBackfillError("write target qualification is malformed")
        if unqualified not in CATALOG_BACKFILL_WRITE_TABLES:
            raise CatalogBackfillError(f"write target {unqualified!r} is not allowed")
        expected_columns = CATALOG_INSERT_COLUMNS[unqualified]
        if tuple(columns) != expected_columns:
            raise CatalogBackfillError(
                f"write columns for {unqualified!r} do not match the catalog contract"
            )
        if any(len(row) != len(expected_columns) for row in rows):
            raise CatalogBackfillError(
                f"write row for {unqualified!r} does not match the catalog contract"
            )
        self._call_with_deadline(
            self._catalog_client,
            self._catalog_cancel_client,
            "INSERT",
            lambda query_id: self._catalog_client.insert(
                table,
                list(rows),
                column_names=list(columns),
                column_type_names=list(CATALOG_INSERT_COLUMN_TYPES[unqualified]),
                settings=dict(settings),
                transport_settings={"X-ClickHouse-Query-Id": query_id},
            ),
        )

    def _call_with_deadline(
        self,
        client: Any,
        cancel_client: Any,
        operation: str,
        call: Callable[[str], Any],
    ) -> Any:
        """Cancel the exact server query within the hard wall-clock deadline.

        The operation receives 80% of the configured wall.  The remaining 20%
        is reserved for an exact query-id ``KILL QUERY`` issued through a
        separate pre-connected client.  Returning merely because the HTTP
        client was closed is not sufficient: the operation worker itself must
        observe server cancellation before the total wall expires.  The runner
        aborts on every timeout and never advances the page checkpoint.  A
        timed-out INSERT may already have committed, but replay is idempotent
        by catalog identity.
        """

        outcome: queue.SimpleQueue[tuple[bool, Any]] = queue.SimpleQueue()
        cancellation: queue.SimpleQueue[BaseException | None] = queue.SimpleQueue()
        query_id = self._new_query_id("work")

        def invoke() -> None:
            try:
                outcome.put((True, call(query_id)))
            except BaseException as exc:  # propagate driver/system exception
                outcome.put((False, exc))

        def cancel() -> None:
            try:
                cancel_client.command(
                    "KILL QUERY WHERE query_id = {query_id:String} ASYNC",
                    parameters={"query_id": query_id},
                    settings={
                        "max_execution_time": max(
                            1,
                            min(
                                CLICKHOUSE_SERVER_MAX_EXECUTION_SECONDS,
                                int(self._max_call_seconds),
                            ),
                        )
                    },
                    transport_settings={
                        "X-ClickHouse-Query-Id": self._new_query_id("cancel")
                    },
                )
                cancellation.put(None)
            except BaseException as exc:
                cancellation.put(exc)

        worker = threading.Thread(
            target=invoke,
            name=f"catalog-backfill-{operation.lower()}",
            daemon=True,
        )
        started = self._clock()
        deadline = started + self._max_call_seconds
        worker.start()
        worker.join(self._max_call_seconds * 0.8)
        if worker.is_alive():
            cancel_worker = threading.Thread(
                target=cancel,
                name="catalog-backfill-query-cancel",
                daemon=True,
            )
            cancel_worker.start()
            worker.join(max(0.0, deadline - self._clock()))
            cancel_error: BaseException | None = None
            try:
                cancel_error = cancellation.get_nowait()
            except queue.Empty:
                pass
            if worker.is_alive():
                for timed_out_client in (client, cancel_client):
                    threading.Thread(
                        target=_close_client_quietly,
                        args=(timed_out_client,),
                        name="catalog-backfill-timeout-close",
                        daemon=True,
                    ).start()
                detail = "server cancellation did not terminate the query"
                if cancel_error is not None:
                    detail = (
                        f"server cancellation failed: {type(cancel_error).__name__}"
                    )
                raise CatalogBackfillCallDeadlineExceeded(
                    f"ClickHouse {operation} exceeded {self._max_call_seconds:g}s; {detail}"
                )
            raise CatalogBackfillCallDeadlineExceeded(
                f"ClickHouse {operation} exceeded {self._max_call_seconds:g}s"
            )
        succeeded, value = outcome.get_nowait()
        if succeeded:
            return value
        raise value

    def _new_query_id(self, purpose: str) -> str:
        token = self._query_id_factory()
        query_id = f"property_catalog_backfill_{purpose}_{token}"
        if len(query_id) > 128 or re.fullmatch(r"[A-Za-z0-9_-]+", query_id) is None:
            raise CatalogBackfillError("query_id_factory returned an unsafe query id")
        return query_id


class CatalogAttributeBackfillRunner:
    def __init__(
        self,
        io: CatalogBackfillIO,
        config: CatalogBackfillConfig,
        *,
        stop_requested: StopRequested | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        version_ns: Callable[[], int] = time.time_ns,
        run_id: str | None = None,
    ) -> None:
        self.io = io
        self.config = config.validated()
        self.stop_requested = stop_requested or (lambda: False)
        self.monotonic = monotonic
        self.now = now
        self.run_id = str(uuid.UUID(run_id)) if run_id else str(uuid.uuid4())
        self._version_ns = version_ns
        self._last_checkpoint_version = 0

    def run(self) -> CatalogBackfillSummary:
        started_monotonic = self.monotonic()
        deadline = started_monotonic + self.config.max_runtime_seconds
        windows = tuple(iter_hour_windows(self.config.since, self.config.until))
        checkpoint_by_window = self._read_checkpoints(windows)

        new_windows = tuple(
            window
            for window in windows
            if (window.start, window.end) not in checkpoint_by_window
        )
        range_fence = 0
        fast_empty_windows: set[tuple[datetime, datetime]] = set()
        if new_windows:
            if not self._can_start_range_discovery(deadline):
                terminal = tuple(
                    checkpoint
                    for checkpoint in checkpoint_by_window.values()
                    if checkpoint.status in _TERMINAL_STATUSES
                )
                terminal_reasons = {
                    reason
                    for checkpoint in terminal
                    for reason in checkpoint.gap_reasons
                }
                return CatalogBackfillSummary(
                    project_id=self.config.project_id,
                    catalog_epoch=self.config.catalog_epoch,
                    windows_total=len(windows),
                    windows_skipped=len(terminal),
                    windows_completed=0,
                    windows_gap=sum(
                        checkpoint.status == "gap" for checkpoint in terminal
                    ),
                    windows_pending=len(windows) - len(terminal),
                    source_rows=sum(checkpoint.source_rows for checkpoint in terminal),
                    key_rows=sum(checkpoint.key_rows for checkpoint in terminal),
                    value_rows=sum(checkpoint.value_rows for checkpoint in terminal),
                    gap_rows=sum(checkpoint.gap_count for checkpoint in terminal),
                    gap_reasons=_ordered_gap_reasons(terminal_reasons),
                    dry_run=self.config.dry_run,
                    stopped=True,
                    elapsed_seconds=max(0.0, self.monotonic() - started_monotonic),
                )
            range_fence, occupied_hours = self._read_occupied_hours(windows)
            fast_empty_windows = {
                (window.start, window.end)
                for window in new_windows
                if window.start not in occupied_hours
            }
            if fast_empty_windows and not self.config.dry_run:
                empty_progress = [
                    MutableWindowProgress(
                        window=window,
                        source_version_fence=range_fence,
                        started_at=_as_utc(self.now(), "now"),
                    )
                    for window in new_windows
                    if (window.start, window.end) in fast_empty_windows
                ]
                self._write_checkpoint_batch(empty_progress, status="complete")

        skipped = completed = gap_windows = 0
        aggregate_source_rows = aggregate_key_rows = aggregate_value_rows = 0
        aggregate_gap_rows = 0
        aggregate_gap_reasons: set[str] = set()
        stopped = False

        for window in windows:
            window_key = (window.start, window.end)
            existing = checkpoint_by_window.get((window.start, window.end))
            if existing is not None and existing.status in _TERMINAL_STATUSES:
                skipped += 1
                aggregate_source_rows += existing.source_rows
                aggregate_key_rows += existing.key_rows
                aggregate_value_rows += existing.value_rows
                aggregate_gap_rows += existing.gap_count
                aggregate_gap_reasons.update(existing.gap_reasons)
                if existing.status == "gap":
                    gap_windows += 1
                continue
            if window_key in fast_empty_windows:
                completed += 1
                continue
            if self.stop_requested() or not self._can_start_window(deadline):
                stopped = True
                break

            progress = self._resume_or_start(
                window,
                existing,
                new_window_fence=range_fence,
            )
            window_terminal = False
            while not window_terminal:
                if self.stop_requested() or not self._can_start_page(deadline):
                    stopped = True
                    break
                source_rows, has_more = self._read_source_page(progress)
                page = source_rows[: self.config.page_rows]
                key_rows: list[CatalogKeyRow] = []
                value_rows: list[CatalogValueRow] = []
                page_gap_rows = 0
                page_gap_reasons: set[str] = set()

                for source_row in page:
                    row_reasons = set(source_row.gap_reasons)
                    if not row_reasons:
                        custom_result = build_catalog_rows(
                            scope=CatalogScope(
                                project_id=self.config.project_id,
                                seen_at=source_row.seen_at,
                                catalog_epoch=self.config.catalog_epoch,
                            ),
                            attrs_string=source_row.attrs_string,
                            attrs_number=source_row.attrs_number,
                            attrs_bool=source_row.attrs_bool,
                            attributes_extra=source_row.attributes_extra,
                            limits=CATALOG_BUILD_LIMITS,
                            key_only_attributes=source_row.key_only_attributes,
                        )
                        key_rows.extend(custom_result.key_rows)
                        value_rows.extend(custom_result.value_rows)
                        row_reasons.update(custom_result.metadata.gap_reasons)
                        if source_row.system_attributes:
                            system_result = build_catalog_rows(
                                scope=CatalogScope(
                                    project_id=self.config.project_id,
                                    seen_at=source_row.seen_at,
                                    catalog_epoch=self.config.catalog_epoch,
                                    source_kind="system_attribute",
                                ),
                                attrs_string=source_row.system_attributes,
                                attrs_number={},
                                attrs_bool={},
                                attributes_extra={},
                                limits=CATALOG_BUILD_LIMITS,
                            )
                            key_rows.extend(system_result.key_rows)
                            value_rows.extend(system_result.value_rows)
                            row_reasons.update(system_result.metadata.gap_reasons)
                    if row_reasons:
                        page_gap_rows += 1
                        page_gap_reasons.update(row_reasons)

                if page:
                    next_cursor = page[-1].cursor
                    if next_cursor.as_tuple() <= progress.cursor.as_tuple():
                        raise CatalogBackfillError("source keyset did not advance")
                else:
                    next_cursor = progress.cursor

                if not self.config.dry_run:
                    self._insert_key_rows(key_rows)
                    self._insert_value_rows(value_rows)

                progress.cursor = next_cursor
                progress.source_rows += len(page)
                progress.processed_rows += len(page)
                progress.key_rows += len(key_rows)
                progress.value_rows += len(value_rows)
                progress.gap_count += page_gap_rows
                progress.gap_reasons.update(page_gap_reasons)

                window_terminal = not has_more
                status: Literal["running", "complete", "gap"]
                if window_terminal:
                    status = "gap" if progress.gap_count else "complete"
                else:
                    status = "running"
                if not self.config.dry_run:
                    self._write_checkpoint(progress, status=status)

                if self.stop_requested() and not window_terminal:
                    stopped = True
                    break

            if window_terminal:
                completed += 1
                if progress.gap_count:
                    gap_windows += 1
                aggregate_source_rows += progress.source_rows
                aggregate_key_rows += progress.key_rows
                aggregate_value_rows += progress.value_rows
                aggregate_gap_rows += progress.gap_count
                aggregate_gap_reasons.update(progress.gap_reasons)
            if stopped:
                break

        pending = len(windows) - skipped - completed
        return CatalogBackfillSummary(
            project_id=self.config.project_id,
            catalog_epoch=self.config.catalog_epoch,
            windows_total=len(windows),
            windows_skipped=skipped,
            windows_completed=completed,
            windows_gap=gap_windows,
            windows_pending=pending,
            source_rows=aggregate_source_rows,
            key_rows=aggregate_key_rows,
            value_rows=aggregate_value_rows,
            gap_rows=aggregate_gap_rows,
            gap_reasons=_ordered_gap_reasons(aggregate_gap_reasons),
            dry_run=self.config.dry_run,
            stopped=stopped,
            elapsed_seconds=max(0.0, self.monotonic() - started_monotonic),
        )

    def _read_checkpoints(
        self, windows: Sequence[HourWindow]
    ) -> dict[tuple[datetime, datetime], WindowCheckpoint]:
        if not windows:
            return {}
        sql = _CHECKPOINT_READ_SQL_TEMPLATE.format(
            checkpoint_table=_qualified(self.config.target_database, CHECKPOINT_TABLE)
        )
        rows = self.io.select(
            sql,
            {
                "catalog_project_id": self.config.project_id,
                "catalog_epoch": self.config.catalog_epoch,
                "catalog_since": self.config.since,
                "catalog_until": self.config.until,
                "catalog_checkpoint_limit": len(windows) + 1,
            },
            role="catalog",
            settings=READ_SETTINGS,
        )
        if len(rows) > len(windows):
            raise CatalogBackfillError("checkpoint result exceeded expected windows")
        expected = {(item.start, item.end): item for item in windows}
        parsed: dict[tuple[datetime, datetime], WindowCheckpoint] = {}
        for row in rows:
            checkpoint = _parse_checkpoint_row(row)
            key = (checkpoint.window.start, checkpoint.window.end)
            if key not in expected:
                raise CatalogBackfillError(
                    "overlapping checkpoint has a different hourly window shape"
                )
            _validate_checkpoint(checkpoint)
            if key in parsed:
                raise CatalogBackfillError("duplicate latest checkpoint window")
            parsed[key] = checkpoint
            self._last_checkpoint_version = max(
                self._last_checkpoint_version, checkpoint.state_version
            )
        return parsed

    def _resume_or_start(
        self,
        window: HourWindow,
        existing: WindowCheckpoint | None,
        *,
        new_window_fence: int,
    ) -> MutableWindowProgress:
        if existing is None:
            if new_window_fence <= 0:
                raise CatalogBackfillError("new window has no frozen range fence")
            progress = MutableWindowProgress(
                window=window,
                source_version_fence=new_window_fence,
                started_at=_as_utc(self.now(), "now"),
            )
            # Persist the immutable fence before reading or inserting the first
            # page. Without this state row, a crash after catalog inserts could
            # resume under a newer fence and leave stale pre-correction values
            # mixed into the same epoch.
            if not self.config.dry_run:
                self._write_checkpoint(progress, status="running")
            return progress
        return MutableWindowProgress(
            window=window,
            source_version_fence=existing.source_version_fence,
            cursor=existing.cursor,
            source_rows=existing.source_rows,
            processed_rows=existing.processed_rows,
            key_rows=existing.key_rows,
            value_rows=existing.value_rows,
            gap_count=existing.gap_count,
            gap_reasons=set(existing.gap_reasons),
            started_at=existing.started_at,
        )

    def _read_occupied_hours(
        self, windows: Sequence[HourWindow]
    ) -> tuple[int, set[datetime]]:
        if not windows:
            return 0, set()
        sql = _SOURCE_OCCUPIED_HOURS_SQL_TEMPLATE.format(
            source_table=_qualified(self.config.source_database, SOURCE_TABLE)
        )
        rows = self.io.select(
            sql,
            {
                "catalog_project_id": self.config.project_id,
                "catalog_since": self.config.since,
                "catalog_until": self.config.until,
            },
            role="source",
            settings=RANGE_READ_SETTINGS,
        )
        if len(rows) != 1:
            raise CatalogBackfillError(
                "occupied-hour discovery must return exactly one aggregate row"
            )
        fence = _nonnegative_int(
            rows[0].get("source_version_fence"), "source range fence"
        )
        if fence <= 0:
            raise CatalogBackfillError("source range fence must be positive")
        future_version_rows = _nonnegative_int(
            rows[0].get("future_version_rows"), "future source version rows"
        )
        if future_version_rows:
            raise CatalogBackfillError(
                "source range contains versions beyond the frozen server clock fence"
            )
        raw_hours = rows[0].get("occupied_hours")
        if not isinstance(raw_hours, (list, tuple)) or len(raw_hours) > len(windows):
            raise CatalogBackfillError("occupied-hour array exceeded its ceiling")
        allowed = {window.start for window in windows}
        occupied: set[datetime] = set()
        for raw_hour in raw_hours:
            hour = _as_clickhouse_utc(raw_hour, "occupied_hour")
            if hour not in allowed or hour in occupied:
                raise CatalogBackfillError(
                    "occupied-hour discovery returned invalid or duplicate hours"
                )
            occupied.add(hour)
        return fence, occupied

    def _read_source_page(
        self, progress: MutableWindowProgress
    ) -> tuple[list[SourceSpan], bool]:
        identity_sql = _SOURCE_IDENTITY_PAGE_SQL_TEMPLATE.format(
            source_table=_qualified(self.config.source_database, SOURCE_TABLE)
        )
        cursor = progress.cursor
        identity_rows = self.io.select(
            identity_sql,
            {
                "catalog_project_id": self.config.project_id,
                "catalog_window_start": progress.window.start,
                "catalog_window_end": progress.window.end,
                "catalog_source_version_fence": progress.source_version_fence,
                "catalog_after_observation_type": cursor.observation_type,
                "catalog_after_service_name": cursor.service_name,
                "catalog_after_trace_id": cursor.trace_id,
                "catalog_after_span_id": cursor.span_id,
                "catalog_source_limit": self.config.page_rows + 1,
            },
            role="source",
            settings=READ_SETTINGS,
        )
        if len(identity_rows) > self.config.page_rows + 1:
            raise CatalogBackfillError("source identity page exceeded its row ceiling")
        identities = [_parse_source_cursor(row) for row in identity_rows]
        previous = cursor.as_tuple()
        for identity in identities:
            current = identity.as_tuple()
            if current <= previous:
                raise CatalogBackfillError(
                    "source identity page is not strictly keyset ordered"
                )
            previous = current
        has_more = len(identities) > self.config.page_rows
        page_identities = identities[: self.config.page_rows]
        if not page_identities:
            return [], False

        payload_sql = _SOURCE_PAYLOAD_SQL_TEMPLATE.format(
            source_table=_qualified(self.config.source_database, SOURCE_TABLE)
        )
        payload_rows = self.io.select(
            payload_sql,
            {
                "catalog_project_id": self.config.project_id,
                "catalog_window_start": progress.window.start,
                "catalog_window_end": progress.window.end,
                "catalog_source_version_fence": progress.source_version_fence,
                "catalog_source_identities": tuple(
                    item.as_tuple() for item in page_identities
                ),
                "catalog_projected_typed_string_value_bytes": (
                    PROJECTED_TYPED_STRING_VALUE_BYTES
                ),
                "catalog_projected_array_string_value_bytes": (
                    PROJECTED_ARRAY_STRING_VALUE_BYTES
                ),
                "catalog_projected_value_budget_bytes": (PROJECTED_VALUE_BUDGET_BYTES),
                "catalog_projected_array_members": (
                    CATALOG_BUILD_LIMITS.max_array_members
                ),
                "catalog_max_source_attribute_entries": (
                    self.config.max_source_attribute_entries
                ),
                "catalog_max_source_attribute_bytes": (
                    self.config.max_source_attribute_bytes
                ),
            },
            role="source",
            settings=READ_SETTINGS,
        )
        if len(payload_rows) != len(page_identities):
            raise CatalogBackfillError(
                "source payload hydration did not match the bounded identity page"
            )
        parsed = [_parse_source_row(row, self.config) for row in payload_rows]
        if [row.cursor for row in parsed] != page_identities:
            raise CatalogBackfillError(
                "source payload hydration changed identity order or membership"
            )
        return parsed, has_more

    def _insert_key_rows(self, rows: Sequence[CatalogKeyRow]) -> None:
        self.io.insert(
            _qualified(self.config.target_database, KEY_TABLE),
            [
                (
                    row.project_id,
                    row.attribute_key,
                    row.key_folded,
                    row.attribute_type,
                    row.first_seen,
                    row.last_seen,
                    row.catalog_epoch,
                    row.source_kind,
                )
                for row in rows
            ],
            KEY_INSERT_COLUMNS,
            settings=WRITE_SETTINGS,
        )

    def _insert_value_rows(self, rows: Sequence[CatalogValueRow]) -> None:
        self.io.insert(
            _qualified(self.config.target_database, VALUE_TABLE),
            [
                (
                    row.project_id,
                    row.attribute_key,
                    row.attribute_type,
                    row.value_fingerprint,
                    row.value_json,
                    row.value_search_text,
                    row.first_seen,
                    row.last_seen,
                    row.catalog_epoch,
                    row.source_kind,
                )
                for row in rows
            ],
            VALUE_INSERT_COLUMNS,
            settings=WRITE_SETTINGS,
        )

    def _write_checkpoint(
        self,
        progress: MutableWindowProgress,
        *,
        status: Literal["running", "complete", "gap"],
    ) -> None:
        self._write_checkpoint_batch([progress], status=status)

    def _write_checkpoint_batch(
        self,
        progresses: Sequence[MutableWindowProgress],
        *,
        status: Literal["running", "complete", "gap"],
    ) -> None:
        if not progresses:
            return
        updated_at = _as_utc(self.now(), "now")
        finished_at = updated_at if status in _TERMINAL_STATUSES else None
        rows = []
        for progress in progresses:
            rows.append(
                (
                    self.config.project_id,
                    self.config.catalog_epoch,
                    CATALOG_PROJECTION_VERSION,
                    progress.window.start,
                    progress.window.end,
                    progress.source_version_fence,
                    progress.cursor.observation_type,
                    progress.cursor.service_name,
                    progress.cursor.trace_id,
                    progress.cursor.span_id,
                    status,
                    progress.source_rows,
                    progress.processed_rows,
                    progress.key_rows,
                    progress.value_rows,
                    progress.gap_count,
                    list(_ordered_gap_reasons(progress.gap_reasons)),
                    self.run_id,
                    self.config.worker_id,
                    "",
                    progress.started_at,
                    updated_at,
                    finished_at,
                    self._next_version(),
                )
            )
        self.io.insert(
            _qualified(self.config.target_database, CHECKPOINT_TABLE),
            rows,
            CHECKPOINT_INSERT_COLUMNS,
            settings=WRITE_SETTINGS,
        )

    def _next_version(self) -> int:
        version = max(self._version_ns(), self._last_checkpoint_version + 1)
        self._last_checkpoint_version = version
        return version

    def _can_start_page(self, deadline: float) -> bool:
        return deadline - self.monotonic() >= _PAGE_START_BUDGET_SECONDS

    def _can_start_window(self, deadline: float) -> bool:
        # New window: initial fence checkpoint + one full page. A resumed
        # window needs less, but the conservative guard is simpler.
        return deadline - self.monotonic() >= (
            MAX_CLICKHOUSE_CALL_SECONDS + _PAGE_START_BUDGET_SECONDS
        )

    def _can_start_range_discovery(self, deadline: float) -> bool:
        # Discovery plus a possible batched empty-window checkpoint.
        return deadline - self.monotonic() >= ((2 * MAX_CLICKHOUSE_CALL_SECONDS) + 1.0)


def iter_hour_windows(since: datetime, until: datetime) -> Sequence[HourWindow]:
    since = _require_utc_hour(since, "since")
    until = _require_utc_hour(until, "until")
    if since >= until:
        raise CatalogBackfillError("since must be before until")
    windows: list[HourWindow] = []
    cursor = since
    while cursor < until:
        end = cursor + timedelta(hours=1)
        windows.append(HourWindow(cursor, end))
        cursor = end
    return windows


def parse_utc_hour(value: str, label: str) -> datetime:
    """Parse an explicit ISO-8601 UTC hour; naive/non-zero offsets are rejected."""

    if not isinstance(value, str) or not value:
        raise CatalogBackfillError(f"{label} must be an ISO-8601 UTC timestamp")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise CatalogBackfillError(
            f"{label} must be an ISO-8601 UTC timestamp"
        ) from exc
    return _require_utc_hour(parsed, label)


def _parse_source_row(
    row: Mapping[str, Any], config: CatalogBackfillConfig
) -> SourceSpan:
    cursor = _parse_source_cursor(row)
    seen_at = _as_clickhouse_utc(row.get("seen_at"), "seen_at")
    entries = _nonnegative_int(
        row.get("source_attribute_entries"), "source_attribute_entries"
    )
    source_bytes = _nonnegative_int(
        row.get("source_attribute_bytes"), "source_attribute_bytes"
    )
    reasons: set[str] = set()
    if entries > config.max_source_attribute_entries:
        reasons.add(GAP_SOURCE_ATTRIBUTE_ENTRIES)
    if source_bytes > config.max_source_attribute_bytes:
        reasons.add(GAP_SOURCE_ATTRIBUTE_BYTES)
    system_attributes: dict[str, str] = {}
    if "system_model_complete" in row:
        try:
            system_model_complete = _projection_flag(row.get("system_model_complete"))
        except TypeError:
            reasons.add(GAP_SYSTEM_VALUE_PROJECTION)
        else:
            system_model = row.get("system_model")
            if not isinstance(system_model, str):
                reasons.add(GAP_SYSTEM_VALUE_PROJECTION)
            elif not system_model_complete:
                reasons.add(GAP_SYSTEM_VALUE_PROJECTION)
            elif system_model:
                system_attributes["model"] = system_model

    if "attrs_string_projection" in row or "attributes_extra_projection" in row:
        (
            attrs_string,
            attrs_number,
            attrs_bool,
            extra,
            key_only_attributes,
            projection_reasons,
        ) = _parse_projected_source_attributes(row)
        reasons.update(projection_reasons)
    else:
        attrs_string = row.get("attrs_string")
        attrs_number = row.get("attrs_number")
        attrs_bool = row.get("attrs_bool")
        if not all(
            isinstance(item, Mapping)
            for item in (attrs_string, attrs_number, attrs_bool)
        ):
            reasons.add(GAP_INVALID_SOURCE_MAPS)
            attrs_string = attrs_number = attrs_bool = {}

        extra_raw = row.get("attributes_extra")
        try:
            if isinstance(extra_raw, str):
                extra = json.loads(extra_raw)
            else:
                extra = extra_raw
            if not isinstance(extra, Mapping):
                raise TypeError("attributes_extra must decode to an object")
        except (TypeError, ValueError, json.JSONDecodeError):
            reasons.add(GAP_INVALID_ATTRIBUTES_EXTRA)
            extra = {}
        key_only_attributes = frozenset()

    # Never build a row whose bounded projection itself exceeded a declared
    # source cap. Explicit key-only flags are complete only for values that the
    # authoritative picker also suppresses; selectable omissions carry the
    # durable projection gap above and therefore skip the builder as well.
    if GAP_SOURCE_ATTRIBUTE_ENTRIES in reasons or GAP_SOURCE_ATTRIBUTE_BYTES in reasons:
        attrs_string = attrs_number = attrs_bool = {}
        extra = {}
        key_only_attributes = frozenset()

    return SourceSpan(
        cursor=cursor,
        seen_at=seen_at,
        attrs_string=attrs_string,  # type: ignore[arg-type]
        attrs_number=attrs_number,  # type: ignore[arg-type]
        attrs_bool=attrs_bool,  # type: ignore[arg-type]
        attributes_extra=extra,
        system_attributes=system_attributes,
        key_only_attributes=key_only_attributes,
        gap_reasons=_ordered_gap_reasons(reasons),
    )


def _parse_projected_source_attributes(
    row: Mapping[str, Any],
) -> tuple[
    Mapping[str, str],
    Mapping[str, int | float | Decimal],
    Mapping[str, int],
    Mapping[str, Any],
    frozenset[tuple[str, AttributeType]],
    set[str],
]:
    """Decode the bounded CH projection without recreating raw JSON objects.

    Empty typed strings, non-finite typed numbers, non-selectable array members,
    nested objects, and top-level JSON scalars become key-only or are omitted
    exactly where the authoritative picker exposes no value. Any selectable
    value omitted by the projection ceiling is a durable fallback gap.
    Malformed or internally inconsistent projection tuples also fail closed.
    """

    reasons: set[str] = set()
    key_only: set[tuple[str, AttributeType]] = set()
    try:
        projection_complete = _projection_flag(
            row.get("selectable_projection_complete")
        )
    except TypeError:
        projection_complete = False
        reasons.add(GAP_INVALID_SOURCE_MAPS)
    else:
        if not projection_complete:
            reasons.add(GAP_SELECTABLE_VALUE_PROJECTION)

    attrs_string: dict[str, str] = {}
    string_value_bytes = 0
    raw_strings = row.get("attrs_string_projection")
    try:
        if not isinstance(raw_strings, (list, tuple)):
            raise TypeError("string projection must be an array")
        for item in raw_strings:
            if not isinstance(item, (list, tuple)) or len(item) != 3:
                raise TypeError("string projection tuple is invalid")
            key, key_only_raw, value = item
            if not isinstance(key, str) or not isinstance(value, str):
                raise TypeError("string projection fields are invalid")
            if key in attrs_string:
                raise ValueError("string projection contains a duplicate key")
            is_key_only = _projection_flag(key_only_raw)
            if is_key_only:
                if value:
                    raise ValueError("key-only string projection retained a value")
                key_only.add((key, "string"))
            else:
                if not value:
                    raise ValueError("empty string projection was not key-only")
                value_bytes = len(value.encode("utf-8"))
                if value_bytes > PROJECTED_TYPED_STRING_VALUE_BYTES:
                    raise ValueError(
                        "projected typed string exceeded its value ceiling"
                    )
                string_value_bytes += value_bytes
            attrs_string[key] = value
        if string_value_bytes > PROJECTED_VALUE_BUDGET_BYTES:
            raise ValueError("projected strings exceeded their global byte ceiling")
    except (TypeError, ValueError):
        reasons.add(GAP_INVALID_SOURCE_MAPS)
        attrs_string = {}
        key_only = {
            item for item in key_only if item[1] not in ("string", "number", "boolean")
        }

    raw_numbers = row.get("attrs_number")
    raw_booleans = row.get("attrs_bool")
    attrs_number: dict[str, int | float | Decimal] = {}
    attrs_bool: dict[str, int] = {}
    if not isinstance(raw_numbers, Mapping) or not isinstance(raw_booleans, Mapping):
        reasons.add(GAP_INVALID_SOURCE_MAPS)
    else:
        try:
            for key, value in raw_numbers.items():
                if not isinstance(key, str) or type(value) not in (int, float, Decimal):
                    raise TypeError("number projection fields are invalid")
                if type(value) in (float, Decimal) and not math.isfinite(float(value)):
                    attrs_number[key] = 0
                    key_only.add((key, "number"))
                else:
                    attrs_number[key] = value
            for key, value in raw_booleans.items():
                if not isinstance(key, str) or type(value) not in (bool, int):
                    raise TypeError("boolean projection fields are invalid")
                if type(value) is int and not 0 <= value <= 255:
                    raise ValueError("boolean projection escaped UInt8")
                attrs_bool[key] = int(bool(value))
        except (TypeError, ValueError, OverflowError):
            reasons.add(GAP_INVALID_SOURCE_MAPS)
            attrs_number = {}
            attrs_bool = {}
            key_only = {
                item for item in key_only if item[1] not in ("number", "boolean")
            }

    extra: dict[str, Any] = {}
    raw_extra = row.get("attributes_extra_projection")
    try:
        extra_valid = _projection_flag(row.get("attributes_extra_valid"))
        if not isinstance(raw_extra, (list, tuple)):
            raise TypeError("extra projection must be an array")
        if not extra_valid:
            if raw_extra:
                raise ValueError("invalid JSON returned a non-empty projection")
            reasons.add(GAP_INVALID_ATTRIBUTES_EXTRA)
        else:
            projected_array_members = 0
            projected_array_bytes = 0
            for item in raw_extra:
                if not isinstance(item, (list, tuple)) or len(item) != 4:
                    raise TypeError("extra projection tuple is invalid")
                key, json_type, key_only_raw, raw_members = item
                if (
                    not isinstance(key, str)
                    or not isinstance(json_type, str)
                    or not isinstance(raw_members, (list, tuple))
                    or key in extra
                ):
                    raise TypeError("extra projection fields are invalid")
                is_key_only = _projection_flag(key_only_raw)
                if json_type == "Array":
                    if is_key_only:
                        if raw_members:
                            raise ValueError(
                                "key-only array projection retained members"
                            )
                        if projection_complete:
                            raise ValueError(
                                "complete projection marked an array key-only"
                            )
                        extra[key] = []
                        key_only.add((key, "array"))
                        continue
                    decoded_members: list[str | int | float | bool] = []
                    for raw_member in raw_members:
                        if not isinstance(raw_member, str):
                            raise TypeError("projected array member must be raw JSON")
                        member_bytes = len(raw_member.encode("utf-8"))
                        member = json.loads(raw_member)
                        if type(member) not in (str, int, float, bool):
                            raise ValueError("projected array member was not a scalar")
                        if type(member) is float and not math.isfinite(member):
                            raise ValueError("projected array member was not finite")
                        if type(member) is str:
                            if not member:
                                raise ValueError(
                                    "empty projected array strings are not selectable"
                                )
                            if (
                                len(member.encode("utf-8"))
                                > PROJECTED_ARRAY_STRING_VALUE_BYTES
                            ):
                                raise ValueError(
                                    "projected array string exceeded its value ceiling"
                                )
                        if type(member) is int and not (
                            -(1 << 63) <= member <= (1 << 64) - 1
                        ):
                            raise ValueError(
                                "projected array integer escaped the public range"
                            )
                        decoded_members.append(member)
                        projected_array_members += 1
                        projected_array_bytes += member_bytes
                    extra[key] = decoded_members
                elif json_type == "Object":
                    if not is_key_only or raw_members:
                        raise ValueError(
                            "object projection was not explicitly key-only"
                        )
                    extra[key] = {}
                    key_only.add((key, "map"))
                elif json_type in {
                    "String",
                    "Int64",
                    "UInt64",
                    "Float64",
                    "Double",
                    "Bool",
                    "Null",
                }:
                    if not is_key_only or raw_members:
                        raise ValueError("JSON scalar was not explicitly key-only")
                    extra[key] = None
                    key_only.add((key, "json"))
                else:
                    raise ValueError("extra projection returned an unknown JSON type")
            if projected_array_members > CATALOG_BUILD_LIMITS.max_array_members:
                raise ValueError("projected arrays exceeded the member ceiling")
            if projected_array_bytes > PROJECTED_VALUE_BUDGET_BYTES:
                raise ValueError("projected arrays exceeded the byte ceiling")
    except (TypeError, ValueError, json.JSONDecodeError):
        reasons.add(GAP_INVALID_ATTRIBUTES_EXTRA)
        extra = {}
        key_only = {
            item for item in key_only if item[1] not in ("array", "map", "json")
        }

    return (
        attrs_string,  # type: ignore[return-value]
        attrs_number,  # type: ignore[return-value]
        attrs_bool,  # type: ignore[return-value]
        extra,
        frozenset(key_only),
        reasons,
    )


def _projection_flag(value: Any) -> bool:
    if type(value) is not int or value not in (0, 1):
        raise TypeError("projection flag must be UInt8")
    return bool(value)


def _parse_source_cursor(row: Mapping[str, Any]) -> SourceCursor:
    return SourceCursor(
        _required_string(row.get("observation_type"), "observation_type"),
        _required_string(row.get("service_name"), "service_name"),
        _required_string(row.get("trace_id"), "trace_id"),
        _required_string(row.get("span_id"), "span_id"),
    )


def _parse_checkpoint_row(row: Mapping[str, Any]) -> WindowCheckpoint:
    window = HourWindow(
        _as_clickhouse_utc(row.get("window_start"), "window_start"),
        _as_clickhouse_utc(row.get("window_end"), "window_end"),
    )
    status = _required_string(row.get("status"), "status")
    if status not in _ALL_STATUSES:
        raise CatalogBackfillError(f"unsupported checkpoint status {status!r}")
    raw_reasons = row.get("gap_reasons")
    if not isinstance(raw_reasons, (list, tuple)) or not all(
        isinstance(item, str) for item in raw_reasons
    ):
        raise CatalogBackfillError("checkpoint gap_reasons must be strings")
    return WindowCheckpoint(
        window=window,
        source_version_fence=_nonnegative_int(
            row.get("source_version_fence"), "source_version_fence"
        ),
        cursor=SourceCursor(
            _required_string(
                row.get("cursor_observation_type"), "cursor_observation_type"
            ),
            _required_string(row.get("cursor_service_name"), "cursor_service_name"),
            _required_string(row.get("cursor_trace_id"), "cursor_trace_id"),
            _required_string(row.get("cursor_span_id"), "cursor_span_id"),
        ),
        status=status,  # type: ignore[arg-type]
        source_rows=_nonnegative_int(row.get("source_rows"), "source_rows"),
        processed_rows=_nonnegative_int(row.get("processed_rows"), "processed_rows"),
        key_rows=_nonnegative_int(row.get("key_rows"), "key_rows"),
        value_rows=_nonnegative_int(row.get("value_rows"), "value_rows"),
        gap_count=_nonnegative_int(row.get("gap_count"), "gap_count"),
        gap_reasons=tuple(raw_reasons),
        started_at=_as_clickhouse_utc(row.get("started_at"), "started_at"),
        state_version=_nonnegative_int(row.get("state_version"), "state_version"),
        projection_version=_nonnegative_int(
            row.get("projection_version"),
            "projection_version",
        ),
        state_variants=_nonnegative_int(row.get("state_variants"), "state_variants"),
    )


def _validate_checkpoint(checkpoint: WindowCheckpoint) -> None:
    if checkpoint.window.end - checkpoint.window.start != timedelta(hours=1):
        raise CatalogBackfillError("checkpoint is not an exact hourly window")
    if checkpoint.source_version_fence <= 0:
        raise CatalogBackfillError("checkpoint source fence must be positive")
    if checkpoint.state_version <= 0 or checkpoint.state_variants != 1:
        raise CatalogBackfillError("checkpoint latest state is ambiguous")
    if checkpoint.projection_version != CATALOG_PROJECTION_VERSION:
        raise CatalogBackfillError(
            "checkpoint belongs to an incompatible catalog projection"
        )
    if checkpoint.source_rows != checkpoint.processed_rows:
        raise CatalogBackfillError("checkpoint source/processed counts disagree")
    if checkpoint.gap_count == 0 and checkpoint.gap_reasons:
        raise CatalogBackfillError("checkpoint has gap reasons without gap rows")
    if checkpoint.gap_count > checkpoint.processed_rows:
        raise CatalogBackfillError("checkpoint gap rows exceed processed rows")
    if checkpoint.status == "complete" and (
        checkpoint.gap_count or checkpoint.gap_reasons
    ):
        raise CatalogBackfillError("complete checkpoint declares a gap")
    if checkpoint.status == "gap" and not checkpoint.gap_count:
        raise CatalogBackfillError("gap checkpoint has no gap rows")


def _ordered_gap_reasons(reasons: set[str]) -> tuple[str, ...]:
    known = _SOURCE_GAP_ORDER + _BUILDER_GAP_ORDER
    return tuple(reason for reason in known if reason in reasons) + tuple(
        sorted(reason for reason in reasons if reason not in known)
    )


def _require_utc_hour(value: datetime, label: str) -> datetime:
    value = _as_utc(value, label, reject_non_utc=True)
    if value.minute or value.second or value.microsecond:
        raise CatalogBackfillError(f"{label} must be aligned to an exact UTC hour")
    return value


def _as_utc(value: Any, label: str, *, reject_non_utc: bool = False) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CatalogBackfillError(f"{label} must be timezone-aware")
    offset = value.utcoffset()
    if offset is None:
        raise CatalogBackfillError(f"{label} must be timezone-aware")
    if reject_non_utc and offset != timedelta(0):
        raise CatalogBackfillError(f"{label} must use UTC, not a non-zero offset")
    return value.astimezone(UTC)


def _as_clickhouse_utc(value: Any, label: str) -> datetime:
    """Normalize a value from a schema-pinned DateTime64(..., 'UTC') column.

    Some clickhouse-connect releases return those values as naive datetimes.
    Unlike operator input, their timezone is authoritative from the column
    type, so attaching UTC is safe and keeps resume keys byte-stable.
    """

    if not isinstance(value, datetime):
        raise CatalogBackfillError(f"{label} must be a ClickHouse datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _validate_database(value: str, label: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise CatalogBackfillError(f"{label} must be a simple ClickHouse identifier")
    if len(value.encode("utf-8")) > MAX_DATABASE_NAME_BYTES:
        raise CatalogBackfillError(f"{label} exceeds its fixed byte ceiling")
    if value.lower() in {"system", "information_schema"}:
        raise CatalogBackfillError(f"{label} must not be a system database")


def _qualified(database: str, table: str) -> str:
    _validate_database(database, "database")
    if table not in CATALOG_BACKFILL_WRITE_TABLES and table != SOURCE_TABLE:
        raise CatalogBackfillError(f"table {table!r} is outside the backfill contract")
    return f"`{database}`.`{table}`"


def _bounded_positive(value: int, ceiling: int, label: str) -> None:
    if type(value) is not int or not 1 <= value <= ceiling:
        raise CatalogBackfillError(f"{label} must be in 1..{ceiling}")


def _nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise CatalogBackfillError(f"{label} must be a non-negative integer")
    return value


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise CatalogBackfillError(f"{label} must be a string")
    return value


def _is_select_only(sql: str) -> bool:
    stripped = re.sub(r"/\*.*?\*/|--[^\n]*", "", sql, flags=re.DOTALL).strip()
    if not re.match(r"(?is)^(with\b.*?\bselect\b|select\b)", stripped):
        return False
    forbidden = re.compile(
        r"(?is)\b(insert|alter|create|drop|truncate|optimize|delete|update|attach|detach|rename)\b"
    )
    return forbidden.search(stripped) is None


def _close_client_quietly(client: Any) -> None:
    try:
        client.close()
    except Exception:
        pass


__all__ = [
    "CATALOG_BACKFILL_ACK",
    "CATALOG_BACKFILL_CLOUD_DEPLOYMENT",
    "CATALOG_BACKFILL_ENVIRONMENT",
    "CATALOG_DATABASE_PREFIX",
    "CATALOG_BACKFILL_WRITE_TABLES",
    "CATALOG_BUILD_LIMITS",
    "CATALOG_INSERT_COLUMNS",
    "CATALOG_INSERT_COLUMN_TYPES",
    "CHECKPOINT_INSERT_COLUMNS",
    "CHECKPOINT_TABLE",
    "CLICKHOUSE_MAX_BYTES_TO_READ",
    "CLICKHOUSE_MAX_MEMORY_BYTES",
    "CLICKHOUSE_MAX_RESULT_BYTES",
    "CLICKHOUSE_MAX_ROWS_TO_READ",
    "CLICKHOUSE_MAX_THREADS",
    "CLICKHOUSE_SERVER_MAX_EXECUTION_SECONDS",
    "CatalogAttributeBackfillRunner",
    "CatalogBackfillCallDeadlineExceeded",
    "CatalogBackfillConfig",
    "CatalogBackfillError",
    "CatalogBackfillSummary",
    "DEFAULT_MAX_RUNTIME_SECONDS",
    "DEFAULT_MAX_WINDOWS",
    "DEFAULT_PAGE_ROWS",
    "DEFAULT_SOURCE_ATTRIBUTE_BYTES",
    "DEFAULT_SOURCE_ATTRIBUTE_ENTRIES",
    "GAP_INVALID_ATTRIBUTES_EXTRA",
    "GAP_INVALID_SOURCE_MAPS",
    "GAP_SELECTABLE_VALUE_PROJECTION",
    "GAP_SOURCE_ATTRIBUTE_BYTES",
    "GAP_SOURCE_ATTRIBUTE_ENTRIES",
    "KEY_INSERT_COLUMNS",
    "KEY_TABLE",
    "MAX_CLICKHOUSE_CALL_SECONDS",
    "MAX_PAGE_ROWS",
    "MAX_RUNTIME_SECONDS",
    "MAX_SOURCE_ATTRIBUTE_BYTES",
    "MAX_SOURCE_ATTRIBUTE_ENTRIES",
    "MAX_WINDOWS",
    "PROJECTED_ARRAY_STRING_VALUE_BYTES",
    "PROJECTED_TYPED_STRING_VALUE_BYTES",
    "PROJECTED_VALUE_BUDGET_BYTES",
    "READ_SETTINGS",
    "SOURCE_TABLE",
    "SourceCursor",
    "TimedCatalogBackfillIO",
    "VALUE_INSERT_COLUMNS",
    "VALUE_TABLE",
    "WRITE_SETTINGS",
    "iter_hour_windows",
    "parse_utc_hour",
]
