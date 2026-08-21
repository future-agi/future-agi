"""Fail-closed reader for immutable span-attribute catalog epochs.

Only the additive catalog tables are read. Admission requires an active row
whose handoff interval exactly equals the request window, complete gap-free
hourly checkpoints for that entire interval, and unambiguous terminal
source-stream evidence for every authorized project. Exact-window admission is
essential because the current tables store epoch-global first/last bounds, not
occurrence buckets. An ``open`` stream keeps an actively-written epoch out of
reads; ``frozen`` and ``complete`` streams make keyset continuation safe.

ClickHouse's lower-casing is deliberately *not* treated as authoritative for
search.  SQL returns an indexed ASCII superset plus every non-ASCII row and the
reader applies Python ``casefold`` before publishing a page.  It scans until it
has a complete page and a proven continuation (or proves exhaustion) inside one
two-second wall, so a timeout can never leak an uncontinuable partial page.

All pagination state is internal and must be wrapped by the existing signed
cursor boundary at an HTTP surface.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from time import monotonic
from typing import Any, Literal, Protocol, TypeAlias, cast

from tracer.services.clickhouse.v2.attribute_catalog_codec import (
    encode_catalog_scalar,
)
from tracer.utils.attribute_suggestion_contract import (
    TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES,
)

CATALOG_MAX_PROJECTS = 64
CATALOG_MAX_PAGE_SIZE = 50
CATALOG_MAX_ATTRIBUTE_KEY_BYTES = 512
CATALOG_MAX_SEARCH_BYTES = 512
CATALOG_MAX_VALUE_SEARCH_TEXT_BYTES = TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES
# A 16-KiB UTF-8 string can expand when control characters are emitted through
# the canonical JSON codec.  Keep one row bounded at 128 KiB and one maximum
# 50-value page below the separate eight-MiB result envelope.
CATALOG_MAX_VALUE_JSON_BYTES = 128 * 1024
CATALOG_QUERY_TIMEOUT_MS = 2_000
CATALOG_MAX_DATABASE_NAME_BYTES = 128
CATALOG_SEARCH_SCAN_ROWS = 512

CATALOG_READ_SETTINGS: dict[str, Any] = {
    "max_threads": 2,
    "max_bytes_to_read": 512 * 1024 * 1024,
    "read_overflow_mode": "throw",
    "max_memory_usage": 512 * 1024 * 1024,
    "max_result_bytes": 8 * 1024 * 1024,
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}

AttributeType = Literal["string", "number", "boolean", "array", "map", "json"]
CatalogSourceKind = Literal["custom_attribute", "system_attribute"]
CatalogScalar: TypeAlias = str | int | Decimal | bool


class CatalogCheckpointStatus(StrEnum):
    """Checkpoint states shared with schema 025 and its future writers."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    GAP = "gap"
    FAILED = "failed"


class CatalogActivationStatus(StrEnum):
    """Activation states shared with schema 025 and its future writers."""

    SHADOW = "shadow"
    ACTIVE = "active"
    DISABLED = "disabled"


_ATTRIBUTE_TYPES = frozenset(("string", "number", "boolean", "array", "map", "json"))
_SCALAR_ATTRIBUTE_TYPES = frozenset(("string", "number", "boolean", "array"))
_ATTRIBUTE_TYPE_RANK = {
    "string": 1,
    "number": 2,
    "boolean": 3,
    "array": 4,
    "map": 5,
    "json": 6,
}
_ALL_ATTRIBUTE_TYPES = tuple(
    sorted(_ATTRIBUTE_TYPES, key=_ATTRIBUTE_TYPE_RANK.__getitem__)
)
_KEY_SOURCE = "span_attribute_catalog.keys.v1"
_VALUE_SOURCE = "span_attribute_catalog.values.v1"
_QUALIFICATION_SOURCE = "span_attribute_catalog.qualification.v1"
_CATALOG_TABLES = (
    "span_attribute_catalog_activations",
    "span_attribute_catalog_source_streams",
    "span_attribute_catalog_checkpoints",
    "span_attribute_key_catalog",
    "span_attribute_value_catalog",
)
_DATABASE_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")


class _Result(Protocol):
    data: list[dict[str, Any]]


class CatalogQueryExecutor(Protocol):
    def execute(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> _Result: ...


@dataclass(frozen=True, slots=True)
class CatalogUnavailable:
    """Sanitized signal that the caller must use its authoritative fallback."""

    reason: str
    source: str = _QUALIFICATION_SOURCE


@dataclass(frozen=True, slots=True)
class CatalogQualification:
    source: str
    catalog_epoch: int
    project_scope_fingerprint: str
    window_start: datetime
    window_end: datetime
    qualification_fingerprint: str
    source_fence_fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class CatalogKeyCandidate:
    attribute_key: str
    attribute_type: AttributeType
    first_seen: datetime
    last_seen: datetime


@dataclass(frozen=True, slots=True)
class CatalogValueCandidate:
    attribute_key: str
    attribute_type: AttributeType
    scalar_kind: Literal["string", "number", "boolean"]
    value: CatalogScalar
    value_json: str
    value_search_text: str
    value_fingerprint: str
    first_seen: datetime
    last_seen: datetime


@dataclass(frozen=True, slots=True)
class CatalogKeyCheckpoint:
    source: str
    catalog_epoch: int
    project_scope_fingerprint: str
    window_start: datetime
    window_end: datetime
    attribute_types: tuple[AttributeType, ...]
    normalized_search: str
    query_fingerprint: str
    qualification_fingerprint: str
    key_folded: str
    attribute_key: str
    attribute_type_rank: int


@dataclass(frozen=True, slots=True)
class CatalogValueCheckpoint:
    source: str
    catalog_epoch: int
    project_scope_fingerprint: str
    window_start: datetime
    window_end: datetime
    attribute_key: str
    attribute_types: tuple[AttributeType, ...]
    normalized_search: str
    query_fingerprint: str
    qualification_fingerprint: str
    value_fingerprint: str
    attribute_type_rank: int


@dataclass(frozen=True, slots=True)
class CatalogKeyPage:
    candidates: tuple[CatalogKeyCandidate, ...]
    has_more: bool
    next_checkpoint: CatalogKeyCheckpoint | None
    qualification: CatalogQualification
    query_count: int = 0
    # Exact distinct-key cardinality for the immutable filtered catalog scope.
    # Search reads keep this unset because Python's Unicode casefold contract is
    # intentionally broader than ClickHouse's indexed ASCII-fold superset.
    total_count: int | None = None


@dataclass(frozen=True, slots=True)
class CatalogValuePage:
    candidates: tuple[CatalogValueCandidate, ...]
    has_more: bool
    next_checkpoint: CatalogValueCheckpoint | None
    qualification: CatalogQualification
    query_count: int = 0


CatalogQualificationResult: TypeAlias = CatalogQualification | CatalogUnavailable
CatalogKeyPageResult: TypeAlias = CatalogKeyPage | CatalogUnavailable
CatalogValuePageResult: TypeAlias = CatalogValuePage | CatalogUnavailable


@dataclass(slots=True)
class _OperationBudget:
    deadline: float
    query_count: int = 0

    @classmethod
    def start(cls) -> _OperationBudget:
        return cls(monotonic() + CATALOG_QUERY_TIMEOUT_MS / 1_000)

    def remaining_ms(self) -> int:
        remaining = int((self.deadline - monotonic()) * 1_000)
        if remaining < 1:
            raise TimeoutError("catalog read deadline exceeded")
        return min(remaining, CATALOG_QUERY_TIMEOUT_MS)


_ACTIVATION_SQL = """
WITH activation_rows AS
(
    SELECT
        *,
        max(_version) OVER (PARTITION BY project_id) AS latest_version
    FROM span_attribute_catalog_activations
    PREWHERE project_id IN %(catalog_project_ids)s
), latest_activations AS
(
    SELECT
        project_id,
        argMax(
            tuple(
                catalog_epoch,
                projection_version,
                handoff_start,
                handoff_end,
                writer_watermark,
                status,
                qualified_at
            ),
            _version
        ) AS state,
        max(_version) AS state_version,
        uniqExactIf(
            tuple(
                catalog_epoch,
                projection_version,
                handoff_start,
                handoff_end,
                writer_watermark,
                status,
                qualified_at
            ),
            _version = latest_version
        ) AS latest_state_variants
    FROM activation_rows
    GROUP BY project_id
)
SELECT
    toString(project_id) AS project_id,
    tupleElement(state, 1) AS catalog_epoch,
    tupleElement(state, 2) AS projection_version,
    tupleElement(state, 3) AS handoff_start,
    tupleElement(state, 4) AS handoff_end,
    tupleElement(state, 5) AS writer_watermark,
    toString(tupleElement(state, 6)) AS status,
    tupleElement(state, 7) AS qualified_at,
    state_version,
    latest_state_variants
FROM latest_activations
ORDER BY project_id ASC
LIMIT %(catalog_activation_limit)s
"""


_SOURCE_STREAM_SQL = """
WITH stream_rows AS
(
    SELECT
        *,
        max(_version) OVER
        (
            PARTITION BY project_id, catalog_epoch, producer_stream_id
        ) AS latest_version
    FROM span_attribute_catalog_source_streams
    PREWHERE project_id IN %(catalog_project_ids)s
      AND catalog_epoch = %(catalog_epoch)s
), latest_streams AS
(
    SELECT
        project_id,
        producer_stream_id,
        argMax(
            tuple(
                envelope_version,
                first_sequence,
                last_sequence,
                frozen_sequence,
                terminal_payload_sha256,
                source_fence_digest,
                status,
                gap_count,
                gap_reasons,
                frozen_at
            ),
            _version
        ) AS state,
        max(_version) AS stream_state_version,
        uniqExactIf(
            tuple(
                envelope_version,
                first_sequence,
                last_sequence,
                frozen_sequence,
                terminal_payload_sha256,
                source_fence_digest,
                status,
                gap_count,
                gap_reasons,
                frozen_at
            ),
            _version = latest_version
        ) AS latest_state_variants
    FROM stream_rows
    GROUP BY project_id, producer_stream_id
), decoded_streams AS
(
    SELECT
        project_id,
        producer_stream_id,
        tupleElement(state, 1) AS envelope_version,
        tupleElement(state, 2) AS first_sequence,
        tupleElement(state, 3) AS last_sequence,
        tupleElement(state, 4) AS frozen_sequence,
        toString(tupleElement(state, 5)) AS terminal_payload_sha256,
        toString(tupleElement(state, 6)) AS source_fence_digest,
        toString(tupleElement(state, 7)) AS status,
        tupleElement(state, 8) AS gap_count,
        tupleElement(state, 9) AS gap_reasons,
        tupleElement(state, 10) AS frozen_at,
        stream_state_version,
        latest_state_variants
    FROM latest_streams
)
SELECT
    toString(project_id) AS project_id,
    count() AS stream_count,
    countIf(status = 'open') AS open_count,
    countIf(status NOT IN ('frozen', 'complete')) AS non_terminal_count,
    countIf(gap_count != 0 OR notEmpty(gap_reasons)) AS declared_gap_count,
    countIf(
        first_sequence = 0
        OR last_sequence < first_sequence
        OR frozen_sequence != last_sequence
    ) AS sequence_invalid_count,
    countIf(
        length(terminal_payload_sha256) != 64
        OR length(source_fence_digest) != 64
    ) AS digest_invalid_count,
    countIf(isNull(frozen_at)) AS missing_frozen_at_count,
    countIf(stream_state_version = 0) AS missing_version_count,
    countIf(latest_state_variants != 1) AS version_conflict_count,
    arraySort(
        groupArray(
            tuple(
                toString(producer_stream_id),
                envelope_version,
                first_sequence,
                last_sequence,
                frozen_sequence,
                terminal_payload_sha256,
                source_fence_digest
            )
        )
    ) AS source_stream_fences
FROM decoded_streams
GROUP BY project_id
ORDER BY project_id ASC
LIMIT %(catalog_source_stream_limit)s
"""


_CHECKPOINT_SQL = """
WITH checkpoint_rows AS
(
    SELECT
        *,
        max(_version) OVER
        (
            PARTITION BY project_id, catalog_epoch, window_start, window_end
        ) AS latest_version
    FROM span_attribute_catalog_checkpoints
    PREWHERE project_id IN %(catalog_project_ids)s
      AND catalog_epoch = %(catalog_epoch)s
    WHERE window_start < fromUnixTimestamp64Micro(%(catalog_window_end_us)s, 'UTC')
      AND window_end > fromUnixTimestamp64Micro(%(catalog_window_start_us)s, 'UTC')
), latest_checkpoints AS
(
    SELECT
        project_id,
        catalog_epoch,
        window_start,
        window_end,
        argMax(
            tuple(
                source_version_fence,
                projection_version,
                status,
                source_rows,
                processed_rows,
                gap_count,
                gap_reasons
            ),
            _version
        ) AS state,
        max(_version) AS checkpoint_state_version,
        uniqExactIf(
            tuple(
                source_version_fence,
                projection_version,
                status,
                source_rows,
                processed_rows,
                gap_count,
                gap_reasons
            ),
            _version = latest_version
        ) AS latest_state_variants
    FROM checkpoint_rows
    GROUP BY project_id, catalog_epoch, window_start, window_end
), ordered_checkpoints AS
(
    SELECT
        project_id,
        window_start,
        window_end,
        tupleElement(state, 1) AS source_version_fence,
        tupleElement(state, 2) AS projection_version,
        toString(tupleElement(state, 3)) AS status,
        tupleElement(state, 4) AS source_rows,
        tupleElement(state, 5) AS processed_rows,
        tupleElement(state, 6) AS gap_count,
        tupleElement(state, 7) AS gap_reasons,
        checkpoint_state_version,
        latest_state_variants,
        max(toNullable(window_end)) OVER
        (
            PARTITION BY project_id
            ORDER BY window_start ASC, window_end ASC
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS prior_coverage_end
    FROM latest_checkpoints
)
SELECT
    toString(project_id) AS project_id,
    count() AS checkpoint_count,
    countIf(status != %(catalog_checkpoint_complete_status)s) AS incomplete_count,
    countIf(gap_count != 0 OR notEmpty(gap_reasons)) AS declared_gap_count,
    countIf(source_rows != processed_rows) AS row_mismatch_count,
    countIf(source_version_fence = 0) AS missing_fence_count,
    min(projection_version) AS projection_version,
    countIf(latest_state_variants != 1) AS version_conflict_count,
    min(window_start) AS coverage_start,
    max(window_end) AS coverage_end,
    arraySort(
        groupArray(
            tuple(
                toUnixTimestamp64Micro(window_start),
                toUnixTimestamp64Micro(window_end),
                source_version_fence,
                checkpoint_state_version
            )
        )
    ) AS checkpoint_fences,
    countIf(
        window_start > greatest(
            fromUnixTimestamp64Micro(%(catalog_window_start_us)s, 'UTC'),
            ifNull(
                prior_coverage_end,
                fromUnixTimestamp64Micro(%(catalog_window_start_us)s, 'UTC')
            )
        )
    ) AS interior_gap_count
FROM ordered_checkpoints
GROUP BY project_id
ORDER BY project_id ASC
LIMIT %(catalog_checkpoint_limit)s
"""


_KEY_PAGE_SQL = """
WITH grouped_keys AS
(
    SELECT
        key_folded,
        attribute_key,
        attribute_type,
        min(first_seen) AS first_seen,
        max(last_seen) AS last_seen
    FROM span_attribute_key_catalog
    PREWHERE project_id IN %(catalog_project_ids)s
      AND catalog_epoch = %(catalog_epoch)s
      AND source_kind = 'custom_attribute'
      AND attribute_type IN %(catalog_key_attribute_types)s
    WHERE key_folded LIKE %(catalog_key_search_pattern)s
       OR length(key_folded) != lengthUTF8(key_folded)
    GROUP BY key_folded, attribute_key, attribute_type
), eligible_keys AS
(
    SELECT
        key_folded,
        attribute_key,
        attribute_type,
        toInt8(attribute_type) AS attribute_type_rank,
        first_seen,
        last_seen,
        uniqExact(attribute_key) OVER () AS total_count
    FROM grouped_keys
    WHERE first_seen < fromUnixTimestamp64Micro(%(catalog_window_end_us)s, 'UTC')
      AND last_seen >= fromUnixTimestamp64Micro(%(catalog_window_start_us)s, 'UTC')
)
SELECT
    key_folded,
    attribute_key,
    toString(attribute_type) AS attribute_type,
    attribute_type_rank,
    first_seen,
    last_seen,
    total_count
FROM eligible_keys
WHERE tuple(key_folded, attribute_key, attribute_type_rank) > tuple(
      %(catalog_after_key_folded)s,
      %(catalog_after_key)s,
      %(catalog_after_key_type_rank)s
  )
ORDER BY key_folded ASC, attribute_key ASC, attribute_type_rank ASC
LIMIT %(catalog_page_limit)s
"""


_VALUE_PAGE_SQL = """
WITH source_values AS
(
    SELECT
        attribute_type,
        value_fingerprint,
        value_json AS raw_value_json,
        value_search_text AS raw_value_search_text,
        first_seen AS raw_first_seen,
        last_seen AS raw_last_seen
    FROM span_attribute_value_catalog
    PREWHERE project_id IN %(catalog_project_ids)s
      AND catalog_epoch = %(catalog_epoch)s
      AND source_kind = %(catalog_source_kind)s
      AND attribute_key = %(catalog_attribute_key)s
    WHERE lower(value_search_text) LIKE %(catalog_value_search_pattern)s
       OR length(value_search_text) != lengthUTF8(value_search_text)
), grouped_values AS
(
    SELECT
        attribute_type,
        value_fingerprint,
        min(raw_value_json) AS value_json,
        min(raw_value_search_text) AS value_search_text,
        uniqExact(raw_value_json) AS value_json_variants,
        uniqExact(raw_value_search_text) AS value_search_variants,
        min(raw_first_seen) AS first_seen,
        max(raw_last_seen) AS last_seen
    FROM source_values
    GROUP BY attribute_type, value_fingerprint
), ordered_values AS
(
    SELECT
        attribute_type,
        value_fingerprint,
        value_json,
        value_search_text,
        value_json_variants,
        value_search_variants,
        first_seen,
        last_seen,
        toInt8(attribute_type) AS attribute_type_rank
    FROM grouped_values
)
SELECT
    toString(attribute_type) AS attribute_type,
    attribute_type_rank,
    value_fingerprint,
    value_json,
    value_search_text,
    value_json_variants,
    value_search_variants,
    first_seen,
    last_seen
FROM ordered_values
WHERE first_seen < fromUnixTimestamp64Micro(%(catalog_window_end_us)s, 'UTC')
  AND last_seen >= fromUnixTimestamp64Micro(%(catalog_window_start_us)s, 'UTC')
  AND (
      attribute_type IN %(catalog_attribute_types)s
  )
  AND tuple(
      attribute_type_rank,
      value_fingerprint
  ) > tuple(
      %(catalog_after_value_type_rank)s,
      %(catalog_after_value_fingerprint)s
  )
ORDER BY
    attribute_type_rank ASC,
    value_fingerprint ASC
LIMIT %(catalog_page_limit)s
"""


class AttributeCatalogReader:
    """Read bounded catalog candidate pages after strict coverage admission."""

    def __init__(
        self,
        executor: CatalogQueryExecutor,
        *,
        project_ids: Iterable[str],
        catalog_epoch: int,
        window_start: datetime,
        window_end: datetime,
        catalog_database: str | None = None,
        required_projection_version: int = 1,
    ) -> None:
        self._executor = executor
        self.project_ids = _canonical_project_ids(project_ids)
        if type(catalog_epoch) is not int or not 1 <= catalog_epoch <= 65_535:
            raise ValueError("catalog_epoch must be a positive UInt16")
        self.catalog_epoch = catalog_epoch
        self.window_start = _aware_utc(window_start, "window_start")
        self.window_end = _aware_utc(window_end, "window_end")
        if self.window_start >= self.window_end:
            raise ValueError("catalog window must be a non-empty half-open interval")
        self.catalog_database = _catalog_database(catalog_database)
        if (
            type(required_projection_version) is not int
            or not 1 <= required_projection_version <= 65_535
        ):
            raise ValueError("required projection version must be a positive UInt16")
        self.required_projection_version = required_projection_version
        self._activation_sql = _qualify_catalog_sql(
            _ACTIVATION_SQL, self.catalog_database
        )
        self._source_stream_sql = _qualify_catalog_sql(
            _SOURCE_STREAM_SQL, self.catalog_database
        )
        self._checkpoint_sql = _qualify_catalog_sql(
            _CHECKPOINT_SQL, self.catalog_database
        )
        self._key_page_sql = _qualify_catalog_sql(_KEY_PAGE_SQL, self.catalog_database)
        self._value_page_sql = _qualify_catalog_sql(
            _VALUE_PAGE_SQL, self.catalog_database
        )
        self.project_scope_fingerprint = _scope_fingerprint(self.project_ids)

    def qualify(
        self,
        *,
        _budget: _OperationBudget | None = None,
    ) -> CatalogQualificationResult:
        """Prove frozen source, activation, and exact window coverage."""

        budget = _budget or _OperationBudget.start()

        params = {
            "catalog_project_ids": self.project_ids,
            "catalog_activation_limit": CATALOG_MAX_PROJECTS + 1,
        }
        try:
            activation_rows = self._execute(
                self._activation_sql,
                params,
                max_result_rows=CATALOG_MAX_PROJECTS + 1,
                budget=budget,
            )
        except Exception:
            return CatalogUnavailable("activation_query_error")

        try:
            activation_failure = self._validate_activations(activation_rows)
        except Exception:
            return CatalogUnavailable("activation_invalid")
        if activation_failure is not None:
            return activation_failure

        params = {
            "catalog_project_ids": self.project_ids,
            "catalog_epoch": self.catalog_epoch,
            "catalog_source_stream_limit": CATALOG_MAX_PROJECTS + 1,
        }
        try:
            source_stream_rows = self._execute(
                self._source_stream_sql,
                params,
                max_result_rows=CATALOG_MAX_PROJECTS + 1,
                budget=budget,
            )
        except Exception:
            return CatalogUnavailable("source_stream_query_error")

        try:
            source_stream_failure = self._validate_source_streams(source_stream_rows)
        except Exception:
            return CatalogUnavailable("source_stream_invalid")
        if source_stream_failure is not None:
            return source_stream_failure

        params = {
            "catalog_project_ids": self.project_ids,
            "catalog_epoch": self.catalog_epoch,
            "catalog_window_start_us": _unix_microseconds(self.window_start),
            "catalog_window_end_us": _unix_microseconds(self.window_end),
            "catalog_checkpoint_complete_status": (
                CatalogCheckpointStatus.COMPLETE.value
            ),
            "catalog_checkpoint_limit": CATALOG_MAX_PROJECTS + 1,
        }
        try:
            checkpoint_rows = self._execute(
                self._checkpoint_sql,
                params,
                max_result_rows=CATALOG_MAX_PROJECTS + 1,
                budget=budget,
            )
        except Exception:
            return CatalogUnavailable("checkpoint_query_error")

        try:
            checkpoint_failure = self._validate_checkpoint_coverage(checkpoint_rows)
        except Exception:
            return CatalogUnavailable("checkpoint_invalid")
        if checkpoint_failure is not None:
            return checkpoint_failure
        try:
            qualification_fingerprint = _qualification_fingerprint(
                activation_rows,
                source_stream_rows,
                checkpoint_rows,
            )
            source_fence_fingerprint = _source_fence_fingerprint(source_stream_rows)
        except Exception:
            return CatalogUnavailable("qualification_invalid")
        return CatalogQualification(
            source=_QUALIFICATION_SOURCE,
            catalog_epoch=self.catalog_epoch,
            project_scope_fingerprint=self.project_scope_fingerprint,
            window_start=self.window_start,
            window_end=self.window_end,
            qualification_fingerprint=qualification_fingerprint,
            source_fence_fingerprint=source_fence_fingerprint,
        )

    def read_key_candidates(
        self,
        *,
        page_size: int,
        search: str | None = None,
        after: CatalogKeyCheckpoint | None = None,
        attribute_types: Iterable[AttributeType] | None = None,
    ) -> CatalogKeyPageResult:
        """Return one immutable-keyset page of catalog key candidates."""

        budget = _OperationBudget.start()
        limit = _page_limit(page_size)
        search_value = _bounded_text(
            search,
            label="key search",
            max_bytes=CATALOG_MAX_SEARCH_BYTES,
            allow_empty=True,
        )
        normalized_search = _normalize_key_search(search_value)
        types = _attribute_types(attribute_types)
        query_fingerprint = self._key_query_fingerprint(
            attribute_types=types,
            normalized_search=normalized_search,
            page_size=limit,
        )
        if after is not None:
            self._validate_key_checkpoint(
                after,
                attribute_types=types,
                normalized_search=normalized_search,
                query_fingerprint=query_fingerprint,
            )
        qualification = self.qualify(_budget=budget)
        if isinstance(qualification, CatalogUnavailable):
            return qualification
        if (
            after is not None
            and after.qualification_fingerprint
            != qualification.qualification_fingerprint
        ):
            return CatalogUnavailable("qualification_changed", _KEY_SOURCE)

        after_position = (
            (after.key_folded, after.attribute_key, after.attribute_type_rank)
            if after is not None
            else ("", "", 0)
        )
        # At most six type rows can share one key.  For unsearched reads this
        # proves a full public page plus the next distinct key in one query.
        # Searched reads may need multiple conservative-superset scans because
        # every non-ASCII key is admitted for the Python casefold recheck.
        scan_limit = max((limit + 1) * len(types), 1)
        if normalized_search:
            scan_limit = max(scan_limit, CATALOG_SEARCH_SCAN_ROWS)
        candidates: list[CatalogKeyCandidate] = []
        matched_keys: list[str] = []
        matched_key_set: set[str] = set()
        last_emitted_position: tuple[str, str, int] | None = None
        # The SQL total is computed before the keyset predicate, so every page
        # of the immutable epoch reports the same distinct-key cardinality.
        # The indexed search predicate deliberately admits every non-ASCII key
        # for Python casefold rechecking; do not mislabel that superset count as
        # exact for searched pages.
        total_count: int | None = None
        try:
            while len(matched_keys) <= limit:
                params = {
                    "catalog_project_ids": self.project_ids,
                    "catalog_epoch": self.catalog_epoch,
                    "catalog_window_start_us": _unix_microseconds(self.window_start),
                    "catalog_window_end_us": _unix_microseconds(self.window_end),
                    "catalog_key_attribute_types": types,
                    "catalog_key_search_pattern": _like_contains_pattern(
                        normalized_search
                    ),
                    "catalog_after_key_folded": after_position[0],
                    "catalog_after_key": after_position[1],
                    "catalog_after_key_type_rank": after_position[2],
                    "catalog_page_limit": scan_limit,
                }
                rows = self._execute(
                    self._key_page_sql,
                    params,
                    max_result_rows=scan_limit,
                    budget=budget,
                )
                previous_position = after_position
                stop = False
                for row in rows:
                    if not normalized_search:
                        row_total_count = _strict_int(row.get("total_count"))
                        if total_count is None:
                            total_count = row_total_count
                        elif total_count != row_total_count:
                            raise ValueError("catalog key total changed within page")
                    candidate = self._decode_key_row(row)
                    if candidate.attribute_type not in types:
                        raise ValueError("catalog key type escaped query filter")
                    position = (
                        _ascii_fold(candidate.attribute_key),
                        candidate.attribute_key,
                        _ATTRIBUTE_TYPE_RANK[candidate.attribute_type],
                    )
                    if position <= previous_position:
                        raise ValueError("catalog key rows are not strictly ordered")
                    previous_position = position
                    if (
                        normalized_search
                        and normalized_search not in candidate.attribute_key.casefold()
                    ):
                        continue
                    if candidate.attribute_key not in matched_key_set:
                        matched_key_set.add(candidate.attribute_key)
                        matched_keys.append(candidate.attribute_key)
                        if len(matched_keys) > limit:
                            stop = True
                            break
                    candidates.append(candidate)
                    last_emitted_position = position
                if stop or len(rows) < scan_limit:
                    break
                if not rows:
                    break
                after_position = previous_position
        except Exception:
            return CatalogUnavailable("key_candidate_query_error", _KEY_SOURCE)

        if not normalized_search and total_count is None and after is None:
            total_count = 0

        has_more = len(matched_keys) > limit
        next_checkpoint = None
        if has_more and candidates and last_emitted_position is not None:
            next_checkpoint = CatalogKeyCheckpoint(
                source=_KEY_SOURCE,
                catalog_epoch=self.catalog_epoch,
                project_scope_fingerprint=self.project_scope_fingerprint,
                window_start=self.window_start,
                window_end=self.window_end,
                attribute_types=types,
                normalized_search=normalized_search,
                query_fingerprint=query_fingerprint,
                qualification_fingerprint=(qualification.qualification_fingerprint),
                key_folded=last_emitted_position[0],
                attribute_key=last_emitted_position[1],
                attribute_type_rank=last_emitted_position[2],
            )
        return CatalogKeyPage(
            candidates=tuple(candidates),
            has_more=has_more,
            next_checkpoint=next_checkpoint,
            qualification=qualification,
            query_count=budget.query_count,
            total_count=total_count,
        )

    def read_value_candidates(
        self,
        attribute_key: str,
        *,
        page_size: int,
        attribute_types: Iterable[AttributeType] | None = None,
        search: str | None = None,
        after: CatalogValueCheckpoint | None = None,
        source_kind: CatalogSourceKind = "custom_attribute",
    ) -> CatalogValuePageResult:
        """Return one strict typed-scalar candidate page for one attribute key."""

        budget = _OperationBudget.start()
        limit = _page_limit(page_size)
        key = _bounded_text(
            attribute_key,
            label="attribute key",
            max_bytes=CATALOG_MAX_ATTRIBUTE_KEY_BYTES,
            allow_empty=False,
        )
        search_value = _bounded_text(
            search,
            label="value search",
            max_bytes=CATALOG_MAX_SEARCH_BYTES,
            allow_empty=True,
        )
        normalized_search = _normalize_value_search(search_value)
        source_kind = _source_kind(source_kind)
        types = _attribute_types(attribute_types)
        query_fingerprint = self._value_query_fingerprint(
            attribute_key=key,
            attribute_types=types,
            normalized_search=normalized_search,
            page_size=limit,
            source_kind=source_kind,
        )
        if after is not None:
            self._validate_value_checkpoint(
                after,
                attribute_key=key,
                attribute_types=types,
                normalized_search=normalized_search,
                query_fingerprint=query_fingerprint,
            )
        qualification = self.qualify(_budget=budget)
        if isinstance(qualification, CatalogUnavailable):
            return qualification
        if (
            after is not None
            and after.qualification_fingerprint
            != qualification.qualification_fingerprint
        ):
            return CatalogUnavailable("qualification_changed", _VALUE_SOURCE)

        after_position = (
            (after.attribute_type_rank, after.value_fingerprint)
            if after is not None
            else (0, "")
        )
        scan_limit = limit + 1
        if normalized_search:
            # One maximum public page keeps the worst-case decoded scalar body
            # below the reader's two-MiB result cap.
            scan_limit = max(scan_limit, CATALOG_MAX_PAGE_SIZE + 1)
        matches: list[CatalogValueCandidate] = []
        try:
            while len(matches) <= limit:
                params = {
                    "catalog_project_ids": self.project_ids,
                    "catalog_epoch": self.catalog_epoch,
                    "catalog_source_kind": source_kind,
                    "catalog_window_start_us": _unix_microseconds(self.window_start),
                    "catalog_window_end_us": _unix_microseconds(self.window_end),
                    "catalog_attribute_key": key,
                    "catalog_attribute_types": types,
                    "catalog_value_search_pattern": _indexed_value_search_pattern(
                        normalized_search
                    ),
                    "catalog_after_value_fingerprint": after_position[1],
                    "catalog_after_value_type_rank": after_position[0],
                    "catalog_page_limit": scan_limit,
                }
                rows = self._execute(
                    self._value_page_sql,
                    params,
                    max_result_rows=scan_limit,
                    budget=budget,
                )
                previous_position = after_position
                for row in rows:
                    candidate = self._decode_value_row(key, row)
                    position = (
                        _ATTRIBUTE_TYPE_RANK[candidate.attribute_type],
                        candidate.value_fingerprint,
                    )
                    if position <= previous_position:
                        raise ValueError("catalog value rows are not strictly ordered")
                    previous_position = position
                    if candidate.attribute_type not in types:
                        raise ValueError("catalog value type escaped query filter")
                    if (
                        normalized_search
                        and normalized_search
                        not in candidate.value_search_text.casefold()
                    ):
                        continue
                    matches.append(candidate)
                    if len(matches) > limit:
                        break
                if len(matches) > limit or len(rows) < scan_limit:
                    break
                if not rows:
                    break
                after_position = previous_position
        except Exception:
            return CatalogUnavailable("value_candidate_query_error", _VALUE_SOURCE)

        has_more = len(matches) > limit
        candidates = tuple(matches[:limit])
        next_checkpoint = None
        if has_more and candidates:
            last = candidates[-1]
            next_checkpoint = CatalogValueCheckpoint(
                source=_VALUE_SOURCE,
                catalog_epoch=self.catalog_epoch,
                project_scope_fingerprint=self.project_scope_fingerprint,
                window_start=self.window_start,
                window_end=self.window_end,
                attribute_key=key,
                attribute_types=types,
                normalized_search=normalized_search,
                query_fingerprint=query_fingerprint,
                qualification_fingerprint=(qualification.qualification_fingerprint),
                value_fingerprint=last.value_fingerprint,
                attribute_type_rank=_ATTRIBUTE_TYPE_RANK[last.attribute_type],
            )
        return CatalogValuePage(
            candidates=candidates,
            has_more=has_more,
            next_checkpoint=next_checkpoint,
            qualification=qualification,
            query_count=budget.query_count,
        )

    def _execute(
        self,
        sql: str,
        params: dict[str, Any],
        *,
        max_result_rows: int,
        budget: _OperationBudget,
    ) -> list[dict[str, Any]]:
        timeout_ms = budget.remaining_ms()
        budget.query_count += 1
        result = self._executor.execute(
            sql,
            params,
            timeout_ms=timeout_ms,
            settings={
                **CATALOG_READ_SETTINGS,
                "max_result_rows": max_result_rows,
                "max_execution_time": timeout_ms / 1_000,
            },
        )
        budget.remaining_ms()
        rows = getattr(result, "data", None)
        if not isinstance(rows, list) or len(rows) > max_result_rows:
            raise ValueError("invalid catalog query result envelope")
        if not all(isinstance(row, dict) for row in rows):
            raise ValueError("invalid catalog query row")
        return rows

    def _validate_activations(
        self, rows: list[dict[str, Any]]
    ) -> CatalogUnavailable | None:
        by_project = _rows_by_project(rows)
        if by_project is None or set(by_project) != set(self.project_ids):
            return CatalogUnavailable("activation_missing")
        for project_id in self.project_ids:
            row = by_project[project_id]
            try:
                epoch = _strict_int(row.get("catalog_epoch"))
                projection_version = _strict_int(row.get("projection_version", 1))
            except (TypeError, ValueError):
                return CatalogUnavailable("activation_invalid")
            if epoch != self.catalog_epoch:
                return CatalogUnavailable("activation_epoch_mismatch")
            if projection_version < self.required_projection_version:
                return CatalogUnavailable("activation_projection_incompatible")
            if row.get("status") != CatalogActivationStatus.ACTIVE.value:
                return CatalogUnavailable("activation_status_not_active")
            try:
                handoff_start = _row_datetime(row.get("handoff_start"))
                handoff_end = _row_datetime(row.get("handoff_end"))
                writer_watermark = _row_datetime(row.get("writer_watermark"))
                _row_datetime(row.get("qualified_at"))
                state_version = _strict_int(row.get("state_version"))
                latest_state_variants = _strict_int(row.get("latest_state_variants"))
            except (TypeError, ValueError):
                return CatalogUnavailable("activation_invalid")
            if latest_state_variants != 1:
                return CatalogUnavailable("activation_version_conflict")
            if (
                state_version <= 0
                or handoff_start >= handoff_end
                or writer_watermark < handoff_end
            ):
                return CatalogUnavailable("activation_handoff_invalid")
            # Schema 025 stores only epoch-global first/last bounds. Those
            # bounds prove membership for the *entire* frozen activation
            # interval, but they cannot prove an occurrence in an arbitrary
            # subwindow (an attribute seen before and after a subwindow may be
            # absent inside it). Never use the catalog for a subset/superset;
            # callers must run their authoritative spans fallback instead.
            if (
                self.window_start != handoff_start
                or self.window_end != handoff_end
                or writer_watermark < self.window_end
            ):
                return CatalogUnavailable("activation_window_not_exact")
        return None

    def _validate_source_streams(
        self, rows: list[dict[str, Any]]
    ) -> CatalogUnavailable | None:
        by_project = _rows_by_project(rows)
        if by_project is None or set(by_project) != set(self.project_ids):
            return CatalogUnavailable("source_stream_missing")
        for project_id in self.project_ids:
            row = by_project[project_id]
            try:
                stream_count = _strict_int(row.get("stream_count"))
                open_count = _strict_int(row.get("open_count"))
                non_terminal_count = _strict_int(row.get("non_terminal_count"))
                declared_gap_count = _strict_int(row.get("declared_gap_count"))
                sequence_invalid_count = _strict_int(row.get("sequence_invalid_count"))
                digest_invalid_count = _strict_int(row.get("digest_invalid_count"))
                missing_frozen_at_count = _strict_int(
                    row.get("missing_frozen_at_count")
                )
                missing_version_count = _strict_int(row.get("missing_version_count"))
                version_conflict_count = _strict_int(row.get("version_conflict_count"))
                source_stream_fences = _source_stream_fences(
                    row.get("source_stream_fences")
                )
            except (TypeError, ValueError):
                return CatalogUnavailable("source_stream_invalid")
            if stream_count <= 0 or len(source_stream_fences) != stream_count:
                return CatalogUnavailable("source_stream_missing")
            if open_count:
                return CatalogUnavailable("source_stream_open")
            if non_terminal_count:
                return CatalogUnavailable("source_stream_not_frozen")
            if declared_gap_count:
                return CatalogUnavailable("source_stream_declared_gap")
            if sequence_invalid_count:
                return CatalogUnavailable("source_stream_sequence_invalid")
            if digest_invalid_count:
                return CatalogUnavailable("source_stream_digest_invalid")
            if missing_frozen_at_count:
                return CatalogUnavailable("source_stream_freeze_missing")
            if missing_version_count:
                return CatalogUnavailable("source_stream_version_missing")
            if version_conflict_count:
                return CatalogUnavailable("source_stream_version_conflict")
            if (
                source_stream_fences != tuple(sorted(source_stream_fences))
                or len({fence[0] for fence in source_stream_fences}) != stream_count
            ):
                return CatalogUnavailable("source_stream_fence_invalid")
        return None

    def _validate_checkpoint_coverage(
        self, rows: list[dict[str, Any]]
    ) -> CatalogUnavailable | None:
        by_project = _rows_by_project(rows)
        if by_project is None or set(by_project) != set(self.project_ids):
            return CatalogUnavailable("checkpoint_missing")
        for project_id in self.project_ids:
            row = by_project[project_id]
            try:
                checkpoint_count = _strict_int(row.get("checkpoint_count"))
                incomplete_count = _strict_int(row.get("incomplete_count"))
                declared_gap_count = _strict_int(row.get("declared_gap_count"))
                row_mismatch_count = _strict_int(row.get("row_mismatch_count"))
                missing_fence_count = _strict_int(row.get("missing_fence_count"))
                projection_version = _strict_int(row.get("projection_version", 1))
                version_conflict_count = _strict_int(row.get("version_conflict_count"))
                interior_gap_count = _strict_int(row.get("interior_gap_count"))
                coverage_start = _row_datetime(row.get("coverage_start"))
                coverage_end = _row_datetime(row.get("coverage_end"))
                checkpoint_fences = _checkpoint_fences(row.get("checkpoint_fences"))
            except (TypeError, ValueError):
                return CatalogUnavailable("checkpoint_invalid")
            if checkpoint_count <= 0:
                return CatalogUnavailable("checkpoint_missing")
            if incomplete_count:
                return CatalogUnavailable("checkpoint_status_incomplete")
            if declared_gap_count:
                return CatalogUnavailable("checkpoint_declared_gap")
            if row_mismatch_count:
                return CatalogUnavailable("checkpoint_row_mismatch")
            if missing_fence_count:
                return CatalogUnavailable("checkpoint_source_fence_missing")
            if projection_version < self.required_projection_version:
                return CatalogUnavailable("checkpoint_projection_incompatible")
            if version_conflict_count:
                return CatalogUnavailable("checkpoint_version_conflict")
            if (
                coverage_start > self.window_start
                or coverage_end < self.window_end
                or interior_gap_count
            ):
                return CatalogUnavailable("checkpoint_window_gap")
            if (
                len(checkpoint_fences) != checkpoint_count
                or checkpoint_fences != tuple(sorted(checkpoint_fences))
                or len({fence[:2] for fence in checkpoint_fences}) != checkpoint_count
                or min(fence[0] for fence in checkpoint_fences)
                != _unix_microseconds(coverage_start)
                or max(fence[1] for fence in checkpoint_fences)
                != _unix_microseconds(coverage_end)
            ):
                return CatalogUnavailable("checkpoint_fence_invalid")
        return None

    def _decode_key_row(self, row: dict[str, Any]) -> CatalogKeyCandidate:
        key = _bounded_text(
            row.get("attribute_key"),
            label="catalog attribute key",
            max_bytes=CATALOG_MAX_ATTRIBUTE_KEY_BYTES,
            allow_empty=False,
        )
        key_folded = row.get("key_folded")
        if key_folded != _ascii_fold(key):
            raise ValueError("invalid folded catalog key")
        attribute_type = _row_attribute_type(row.get("attribute_type"))
        rank = _strict_int(row.get("attribute_type_rank"))
        if rank != _ATTRIBUTE_TYPE_RANK[attribute_type]:
            raise ValueError("invalid catalog attribute type rank")
        first_seen = _row_datetime(row.get("first_seen"))
        last_seen = _row_datetime(row.get("last_seen"))
        if (
            first_seen > last_seen
            or first_seen >= self.window_end
            or last_seen < self.window_start
        ):
            raise ValueError("invalid catalog key time bounds")
        return CatalogKeyCandidate(key, attribute_type, first_seen, last_seen)

    def _decode_value_row(
        self, attribute_key: str, row: dict[str, Any]
    ) -> CatalogValueCandidate:
        attribute_type = _row_attribute_type(row.get("attribute_type"))
        if attribute_type not in _SCALAR_ATTRIBUTE_TYPES:
            raise ValueError("key-only attribute type emitted a catalog value")
        rank = _strict_int(row.get("attribute_type_rank"))
        if rank != _ATTRIBUTE_TYPE_RANK[attribute_type]:
            raise ValueError("invalid catalog attribute type rank")
        if _strict_int(row.get("value_json_variants")) != 1:
            raise ValueError("catalog fingerprint has multiple JSON payloads")
        if _strict_int(row.get("value_search_variants")) != 1:
            raise ValueError("catalog fingerprint has multiple search payloads")

        value_json = row.get("value_json")
        value_search_text = row.get("value_search_text")
        value_json = _bounded_utf8_text(
            value_json,
            label="catalog value JSON",
            max_bytes=CATALOG_MAX_VALUE_JSON_BYTES,
            allow_empty=False,
        )
        value_search_text = _bounded_utf8_text(
            value_search_text,
            label="catalog value search text",
            max_bytes=CATALOG_MAX_VALUE_SEARCH_TEXT_BYTES,
            allow_empty=True,
        )
        try:
            value = json.loads(
                value_json,
                parse_float=Decimal,
                parse_int=int,
                parse_constant=_reject_json_constant,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid catalog scalar JSON") from exc
        if value is None or isinstance(value, (list, dict)):
            raise ValueError("catalog value must be a JSON scalar")
        encoded = encode_catalog_scalar(value)
        if encoded.value_json != value_json or encoded.search_text != value_search_text:
            raise ValueError("non-canonical catalog scalar")

        fingerprint = _fingerprint(row.get("value_fingerprint"))
        if fingerprint != encoded.fingerprint:
            raise ValueError("catalog scalar fingerprint mismatch")
        if attribute_type != "array" and attribute_type != encoded.kind:
            raise ValueError("catalog scalar type mismatch")
        first_seen = _row_datetime(row.get("first_seen"))
        last_seen = _row_datetime(row.get("last_seen"))
        if (
            first_seen > last_seen
            or first_seen >= self.window_end
            or last_seen < self.window_start
        ):
            raise ValueError("invalid catalog value time bounds")
        return CatalogValueCandidate(
            attribute_key=attribute_key,
            attribute_type=attribute_type,
            scalar_kind=encoded.kind,
            value=value,
            value_json=value_json,
            value_search_text=value_search_text,
            value_fingerprint=fingerprint,
            first_seen=first_seen,
            last_seen=last_seen,
        )

    def _key_query_fingerprint(
        self,
        *,
        attribute_types: tuple[AttributeType, ...],
        normalized_search: str,
        page_size: int,
    ) -> str:
        return _identity_fingerprint(
            "key-query-v1",
            {
                "catalog_epoch": self.catalog_epoch,
                "project_scope": self.project_scope_fingerprint,
                "window_start_us": _unix_microseconds(self.window_start),
                "window_end_us": _unix_microseconds(self.window_end),
                "attribute_types": attribute_types,
                "normalized_search": normalized_search,
                "page_size": page_size,
            },
        )

    def _value_query_fingerprint(
        self,
        *,
        attribute_key: str,
        attribute_types: tuple[AttributeType, ...],
        normalized_search: str,
        page_size: int,
        source_kind: CatalogSourceKind = "custom_attribute",
    ) -> str:
        return _identity_fingerprint(
            "value-query-v1",
            {
                "catalog_epoch": self.catalog_epoch,
                "project_scope": self.project_scope_fingerprint,
                "window_start_us": _unix_microseconds(self.window_start),
                "window_end_us": _unix_microseconds(self.window_end),
                "attribute_key": attribute_key,
                "source_kind": source_kind,
                "attribute_types": attribute_types,
                "normalized_search": normalized_search,
                "page_size": page_size,
            },
        )

    def _validate_key_checkpoint(
        self,
        checkpoint: CatalogKeyCheckpoint,
        *,
        attribute_types: tuple[AttributeType, ...],
        normalized_search: str,
        query_fingerprint: str,
    ) -> None:
        self._validate_checkpoint_scope(checkpoint, _KEY_SOURCE)
        if (
            checkpoint.attribute_types != attribute_types
            or checkpoint.normalized_search != normalized_search
            or checkpoint.query_fingerprint != query_fingerprint
            or checkpoint.key_folded != _ascii_fold(checkpoint.attribute_key)
            or checkpoint.attribute_type_rank not in _ATTRIBUTE_TYPE_RANK.values()
        ):
            raise ValueError("catalog key checkpoint query identity mismatch")
        _fingerprint(checkpoint.qualification_fingerprint)

    def _validate_value_checkpoint(
        self,
        checkpoint: CatalogValueCheckpoint,
        *,
        attribute_key: str,
        attribute_types: tuple[AttributeType, ...],
        normalized_search: str,
        query_fingerprint: str,
    ) -> None:
        self._validate_checkpoint_scope(checkpoint, _VALUE_SOURCE)
        if (
            checkpoint.attribute_key != attribute_key
            or checkpoint.attribute_types != attribute_types
            or checkpoint.normalized_search != normalized_search
            or checkpoint.query_fingerprint != query_fingerprint
            or _fingerprint(checkpoint.value_fingerprint)
            != checkpoint.value_fingerprint
            or checkpoint.attribute_type_rank not in _ATTRIBUTE_TYPE_RANK.values()
        ):
            raise ValueError("catalog value checkpoint query identity mismatch")
        _fingerprint(checkpoint.qualification_fingerprint)

    def _validate_checkpoint_scope(
        self,
        checkpoint: CatalogKeyCheckpoint | CatalogValueCheckpoint,
        expected_source: str,
    ) -> None:
        if (
            checkpoint.source != expected_source
            or checkpoint.catalog_epoch != self.catalog_epoch
            or checkpoint.project_scope_fingerprint != self.project_scope_fingerprint
            or checkpoint.window_start != self.window_start
            or checkpoint.window_end != self.window_end
        ):
            raise ValueError("catalog checkpoint does not match the frozen scope")


def _canonical_project_ids(project_ids: Iterable[str]) -> tuple[str, ...]:
    if isinstance(project_ids, (str, bytes)):
        raise ValueError("project_ids must be an iterable of canonical UUID strings")
    ordered: dict[str, None] = {}
    for value in project_ids:
        if not isinstance(value, str):
            raise ValueError("project_ids must contain canonical UUID strings")
        try:
            normalized = str(uuid.UUID(value))
        except (AttributeError, ValueError) as exc:
            raise ValueError("project_ids must contain canonical UUID strings") from exc
        if normalized != value:
            raise ValueError("project_ids must contain canonical UUID strings")
        ordered[value] = None
        if len(ordered) > CATALOG_MAX_PROJECTS:
            raise ValueError(
                f"catalog reads support at most {CATALOG_MAX_PROJECTS} projects"
            )
    if not ordered:
        raise ValueError("catalog reads require at least one project")
    return tuple(sorted(ordered))


def _scope_fingerprint(project_ids: tuple[str, ...]) -> str:
    payload = "\n".join(project_ids).encode("ascii")
    return hashlib.sha256(b"span-attribute-catalog-scope-v1\x00" + payload).hexdigest()


def _identity_fingerprint(domain: str, identity: Any) -> str:
    payload = json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(
        b"futureagi.span-attribute-catalog.reader.v1\x00"
        + domain.encode("ascii")
        + b"\x00"
        + payload
    ).hexdigest()


def _qualification_fingerprint(
    activation_rows: list[dict[str, Any]],
    source_stream_rows: list[dict[str, Any]],
    checkpoint_rows: list[dict[str, Any]],
) -> str:
    activations = []
    for project_id, row in sorted(
        ((str(row["project_id"]), row) for row in activation_rows),
        key=lambda item: item[0],
    ):
        activations.append(
            {
                "project_id": project_id,
                "catalog_epoch": _strict_int(row["catalog_epoch"]),
                "projection_version": _strict_int(row.get("projection_version", 1)),
                "handoff_start_us": _unix_microseconds(
                    _row_datetime(row["handoff_start"])
                ),
                "handoff_end_us": _unix_microseconds(_row_datetime(row["handoff_end"])),
                "writer_watermark_us": _unix_microseconds(
                    _row_datetime(row["writer_watermark"])
                ),
                "status": str(row["status"]),
            }
        )

    source_streams = []
    for project_id, row in sorted(
        ((str(row["project_id"]), row) for row in source_stream_rows),
        key=lambda item: item[0],
    ):
        source_streams.append(
            {
                "project_id": project_id,
                "projection_version": _strict_int(row.get("projection_version", 1)),
                "source_stream_fences": _source_stream_fences(
                    row["source_stream_fences"]
                ),
            }
        )

    checkpoints = []
    for project_id, row in sorted(
        ((str(row["project_id"]), row) for row in checkpoint_rows),
        key=lambda item: item[0],
    ):
        checkpoints.append(
            {
                "project_id": project_id,
                # Replacement-state versions can change after an idempotent
                # controller replay without changing the immutable epoch.
                "checkpoint_fences": tuple(
                    fence[:3] for fence in _checkpoint_fences(row["checkpoint_fences"])
                ),
            }
        )
    return _identity_fingerprint(
        "qualification-v2",
        {
            "activations": activations,
            "source_streams": source_streams,
            "checkpoints": checkpoints,
        },
    )


def _source_fence_fingerprint(source_stream_rows: list[dict[str, Any]]) -> str:
    return _identity_fingerprint(
        "source-fence-v1",
        tuple(
            (
                str(row["project_id"]),
                _source_stream_fences(row["source_stream_fences"]),
            )
            for row in sorted(
                source_stream_rows,
                key=lambda value: str(value["project_id"]),
            )
        ),
    )


def _aware_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{label} must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _catalog_database(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not _DATABASE_RE.fullmatch(value)
        or len(value.encode("utf-8")) > CATALOG_MAX_DATABASE_NAME_BYTES
        or value.lower() in {"system", "information_schema"}
    ):
        raise ValueError("catalog_database must be a simple non-system identifier")
    return value


def _qualify_catalog_sql(sql: str, database: str | None) -> str:
    """Qualify only the closed catalog-table names.

    Dev keeps the additive catalog in an isolated database so neither schema
    application nor catalog credentials need access to an existing table.
    Production may leave this unset when the reviewed replicated tables are
    eventually installed in the CH25 application database.
    """

    database = _catalog_database(database)
    if database is None:
        return sql
    qualified = sql
    replacement_count = 0
    for table in _CATALOG_TABLES:
        table_reference = re.compile(rf"\bFROM[ \t]+{re.escape(table)}(?=[ \t\r\n]|$)")
        qualified, count = table_reference.subn(
            f"FROM `{database}`.`{table}`",
            qualified,
        )
        if count:
            replacement_count += count
    if replacement_count != 1:
        raise ValueError("catalog SQL must reference exactly one allowlisted table")
    return qualified


def _row_datetime(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("catalog timestamp must be a datetime")
    if value.tzinfo is None:
        # ClickHouse DateTime64('UTC') is commonly decoded as a naive UTC
        # datetime by native clients; its schema timezone makes this unambiguous.
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _unix_microseconds(value: datetime) -> int:
    delta = _row_datetime(value) - datetime(1970, 1, 1, tzinfo=UTC)
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def _strict_int(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("catalog count/version must be a non-negative integer")
    return value


def _checkpoint_fences(value: Any) -> tuple[tuple[int, int, int, int], ...]:
    if not isinstance(value, (tuple, list)) or not value:
        raise ValueError("catalog checkpoint fences must be a non-empty array")
    fences: list[tuple[int, int, int, int]] = []
    for item in value:
        if not isinstance(item, (tuple, list)) or len(item) != 4:
            raise ValueError("invalid catalog checkpoint fence")
        start_us, end_us, source_version_fence, state_version = (
            _strict_int(part) for part in item
        )
        if start_us >= end_us or source_version_fence <= 0 or state_version <= 0:
            raise ValueError("invalid catalog checkpoint fence")
        fences.append((start_us, end_us, source_version_fence, state_version))
    return tuple(fences)


def _source_stream_fences(
    value: Any,
) -> tuple[tuple[str, int, int, int, int, str, str], ...]:
    if not isinstance(value, (tuple, list)) or not value:
        raise ValueError("catalog source stream fences must be a non-empty array")
    fences: list[tuple[str, int, int, int, int, str, str]] = []
    for item in value:
        if not isinstance(item, (tuple, list)) or len(item) != 7:
            raise ValueError("invalid catalog source stream fence")
        stream_id = item[0]
        if not isinstance(stream_id, str) or str(uuid.UUID(stream_id)) != stream_id:
            raise ValueError("invalid catalog source stream id")
        envelope_version, first_sequence, last_sequence, frozen_sequence = (
            _strict_int(part) for part in item[1:5]
        )
        terminal_digest = _fingerprint(item[5])
        source_fence_digest = _fingerprint(item[6])
        if (
            envelope_version <= 0
            or first_sequence <= 0
            or last_sequence < first_sequence
            or frozen_sequence != last_sequence
        ):
            raise ValueError("invalid catalog source stream sequence fence")
        fences.append(
            (
                stream_id,
                envelope_version,
                first_sequence,
                last_sequence,
                frozen_sequence,
                terminal_digest,
                source_fence_digest,
            )
        )
    return tuple(fences)


def _rows_by_project(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]] | None:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        project_id = row.get("project_id")
        if not isinstance(project_id, str) or project_id in indexed:
            return None
        indexed[project_id] = row
    return indexed


def _page_limit(value: int) -> int:
    if type(value) is not int or not 1 <= value <= CATALOG_MAX_PAGE_SIZE:
        raise ValueError(
            f"catalog page_size must be between 1 and {CATALOG_MAX_PAGE_SIZE}"
        )
    return value


def _bounded_text(
    value: Any,
    *,
    label: str,
    max_bytes: int,
    allow_empty: bool,
) -> str:
    if value is None and allow_empty:
        return ""
    value = _bounded_utf8_text(
        value,
        label=label,
        max_bytes=max_bytes,
        allow_empty=allow_empty,
    )
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{label} contains control characters")

    return value


def _bounded_utf8_text(
    value: Any,
    *,
    label: str,
    max_bytes: int,
    allow_empty: bool,
) -> str:
    if value is None and allow_empty:
        return ""
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ValueError(f"{label} must be text")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    if len(encoded) > max_bytes:
        raise ValueError(f"{label} exceeds {max_bytes} UTF-8 bytes")
    return value


def _normalize_key_search(value: str) -> str:
    return value.casefold()


def _normalize_value_search(value: str) -> str:
    return value.casefold()


def _like_contains_pattern(value: str) -> str:
    if not value:
        return "%"
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _indexed_value_search_pattern(value: str) -> str:
    return _like_contains_pattern(value)


def _attribute_types(
    values: Iterable[AttributeType] | None,
) -> tuple[AttributeType, ...]:
    if values is None:
        return _ALL_ATTRIBUTE_TYPES
    if isinstance(values, (str, bytes)):
        raise ValueError("attribute_types must be an iterable")
    ordered: dict[AttributeType, None] = {}
    for value in values:
        if not isinstance(value, str) or value not in _ATTRIBUTE_TYPES:
            raise ValueError("unsupported catalog attribute type")
        ordered[cast(AttributeType, value)] = None
    if not ordered:
        raise ValueError("attribute_types must not be empty")
    return tuple(sorted(ordered, key=_ATTRIBUTE_TYPE_RANK.__getitem__))


def _row_attribute_type(value: Any) -> AttributeType:
    if not isinstance(value, str) or value not in _ATTRIBUTE_TYPES:
        raise ValueError("invalid catalog attribute type")
    return cast(AttributeType, value)


def _source_kind(value: Any) -> CatalogSourceKind:
    if value not in {"custom_attribute", "system_attribute"}:
        raise ValueError("unsupported catalog source kind")
    return cast(CatalogSourceKind, value)


def _ascii_fold(value: str) -> str:
    return "".join(
        chr(ord(character) + 32) if "A" <= character <= "Z" else character
        for character in value
    )


def _fingerprint(value: Any) -> str:
    if isinstance(value, bytes):
        try:
            value = value.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("invalid catalog fingerprint") from exc
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("invalid catalog fingerprint")
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")


__all__ = [
    "AttributeCatalogReader",
    "CATALOG_MAX_PAGE_SIZE",
    "CATALOG_MAX_PROJECTS",
    "CatalogKeyCandidate",
    "CatalogKeyCheckpoint",
    "CatalogKeyPage",
    "CatalogQualification",
    "CatalogUnavailable",
    "CatalogValueCandidate",
    "CatalogValueCheckpoint",
    "CatalogValuePage",
]
