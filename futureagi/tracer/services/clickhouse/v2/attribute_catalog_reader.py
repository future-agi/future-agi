"""Inactive, fail-closed reader for span-attribute catalog candidates.

This module is deliberately not wired to an API.  It reads only the independent
catalog tables created by schema 025 and returns *candidates* for a later exact
``spans`` verification/fallback layer.  A fresh unsearched catalog page is
available only when every already-authorized project has the requested active
epoch, a valid handoff, a writer watermark at or beyond the frozen window, and
complete, gap-free checkpoint coverage for that whole half-open window.

Schema 025 has no source/content fence on key/value rows.  Qualification state
therefore cannot freeze catalog contents against a late same-epoch insert.  All
continuations fail closed until a later schema/writer contract supplies such a
fence.  Searched pages also fail closed: ``key_folded``/ClickHouse ``lower`` do
not implement the authoritative Python Unicode-casefold contract, and a finite
SQL page cannot prove that conservative non-ASCII false positives did not hide
a later match.

All pagination state below is internal.  The explicit source and scope fields
are intended to be wrapped by the existing signed-cursor boundary before this
reader is activated.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal, Protocol, TypeAlias, cast

from tracer.services.clickhouse.v2.attribute_catalog_codec import (
    encode_catalog_scalar,
)

CATALOG_MAX_PROJECTS = 64
CATALOG_MAX_PAGE_SIZE = 50
CATALOG_MAX_ATTRIBUTE_KEY_BYTES = 512
CATALOG_MAX_SEARCH_BYTES = 512
CATALOG_MAX_VALUE_SEARCH_TEXT_BYTES = 4_096
CATALOG_MAX_VALUE_JSON_BYTES = 32 * 1024
CATALOG_QUERY_TIMEOUT_MS = 2_000

# Schema 025 has no authoritative contiguous producer/source fence. Keep all
# qualification unavailable until a later schema+writer change supplies and
# validates one; tests temporarily override this module-private rollout fuse to
# exercise the otherwise dormant parser/query contracts.
_CONTIGUOUS_SOURCE_FENCE_SUPPORTED = False

CATALOG_READ_SETTINGS: dict[str, Any] = {
    "max_threads": 2,
    "max_bytes_to_read": 512 * 1024 * 1024,
    "read_overflow_mode": "throw",
    "max_memory_usage": 512 * 1024 * 1024,
    "max_result_bytes": 2 * 1024 * 1024,
    "result_overflow_mode": "throw",
    "timeout_overflow_mode": "throw",
}

AttributeType = Literal["string", "number", "boolean", "array", "map", "json"]
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


@dataclass(frozen=True, slots=True)
class CatalogValuePage:
    candidates: tuple[CatalogValueCandidate, ...]
    has_more: bool
    next_checkpoint: CatalogValueCheckpoint | None
    qualification: CatalogQualification


CatalogQualificationResult: TypeAlias = CatalogQualification | CatalogUnavailable
CatalogKeyPageResult: TypeAlias = CatalogKeyPage | CatalogUnavailable
CatalogValuePageResult: TypeAlias = CatalogValuePage | CatalogUnavailable


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
    tupleElement(state, 2) AS handoff_start,
    tupleElement(state, 3) AS handoff_end,
    tupleElement(state, 4) AS writer_watermark,
    toString(tupleElement(state, 5)) AS status,
    tupleElement(state, 6) AS qualified_at,
    state_version,
    latest_state_variants
FROM latest_activations
ORDER BY project_id ASC
LIMIT %(catalog_activation_limit)s
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
    WHERE window_start < %(catalog_window_end)s
      AND window_end > %(catalog_window_start)s
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
        toString(tupleElement(state, 2)) AS status,
        tupleElement(state, 3) AS source_rows,
        tupleElement(state, 4) AS processed_rows,
        tupleElement(state, 5) AS gap_count,
        tupleElement(state, 6) AS gap_reasons,
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
            toDateTime64(%(catalog_window_start)s, 6, 'UTC'),
            ifNull(
                prior_coverage_end,
                toDateTime64(%(catalog_window_start)s, 6, 'UTC')
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
    WHERE key_folded LIKE %(catalog_key_search_pattern)s
       OR length(key_folded) != lengthUTF8(key_folded)
    GROUP BY key_folded, attribute_key, attribute_type
), ordered_keys AS
(
    SELECT
        key_folded,
        attribute_key,
        attribute_type,
        toInt8(attribute_type) AS attribute_type_rank,
        first_seen,
        last_seen
    FROM grouped_keys
)
SELECT
    key_folded,
    attribute_key,
    toString(attribute_type) AS attribute_type,
    attribute_type_rank,
    first_seen,
    last_seen
FROM ordered_keys
WHERE first_seen < %(catalog_window_end)s
  AND last_seen >= %(catalog_window_start)s
  AND tuple(key_folded, attribute_key, attribute_type_rank) > tuple(
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
WHERE first_seen < %(catalog_window_end)s
  AND last_seen >= %(catalog_window_start)s
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
        self.project_scope_fingerprint = _scope_fingerprint(self.project_ids)

    def qualify(self) -> CatalogQualificationResult:
        """Prove epoch, handoff, watermark, and window coverage for every project."""

        if not _CONTIGUOUS_SOURCE_FENCE_SUPPORTED:
            return CatalogUnavailable("activation_requires_contiguous_source_fence")

        params = {
            "catalog_project_ids": self.project_ids,
            "catalog_activation_limit": CATALOG_MAX_PROJECTS + 1,
        }
        try:
            activation_rows = self._execute(
                _ACTIVATION_SQL,
                params,
                max_result_rows=CATALOG_MAX_PROJECTS + 1,
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
            "catalog_window_start": self.window_start,
            "catalog_window_end": self.window_end,
            "catalog_checkpoint_complete_status": (
                CatalogCheckpointStatus.COMPLETE.value
            ),
            "catalog_checkpoint_limit": CATALOG_MAX_PROJECTS + 1,
        }
        try:
            checkpoint_rows = self._execute(
                _CHECKPOINT_SQL,
                params,
                max_result_rows=CATALOG_MAX_PROJECTS + 1,
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
                checkpoint_rows,
            )
        except Exception:
            return CatalogUnavailable("qualification_invalid")
        return CatalogQualification(
            source=_QUALIFICATION_SOURCE,
            catalog_epoch=self.catalog_epoch,
            project_scope_fingerprint=self.project_scope_fingerprint,
            window_start=self.window_start,
            window_end=self.window_end,
            qualification_fingerprint=qualification_fingerprint,
        )

    def read_key_candidates(
        self,
        *,
        page_size: int,
        search: str | None = None,
        after: CatalogKeyCheckpoint | None = None,
    ) -> CatalogKeyPageResult:
        """Return one immutable-keyset page of catalog key candidates."""

        limit = _page_limit(page_size)
        search_value = _bounded_text(
            search,
            label="key search",
            max_bytes=CATALOG_MAX_SEARCH_BYTES,
            allow_empty=True,
        )
        normalized_search = _normalize_key_search(search_value)
        query_fingerprint = self._key_query_fingerprint(
            normalized_search=normalized_search,
            page_size=limit,
        )
        if after is not None:
            self._validate_key_checkpoint(
                after,
                normalized_search=normalized_search,
                query_fingerprint=query_fingerprint,
            )
            return CatalogUnavailable(
                "continuation_requires_immutable_snapshot",
                _KEY_SOURCE,
            )
        if normalized_search:
            return CatalogUnavailable("search_requires_unicode_parity", _KEY_SOURCE)
        qualification = self.qualify()
        if isinstance(qualification, CatalogUnavailable):
            return qualification

        params = {
            "catalog_project_ids": self.project_ids,
            "catalog_epoch": self.catalog_epoch,
            "catalog_window_start": self.window_start,
            "catalog_window_end": self.window_end,
            "catalog_key_search_pattern": _like_contains_pattern(normalized_search),
            "catalog_after_key_folded": "",
            "catalog_after_key": "",
            "catalog_after_key_type_rank": 0,
            "catalog_page_limit": limit + 1,
        }
        try:
            rows = self._execute(
                _KEY_PAGE_SQL,
                params,
                max_result_rows=limit + 1,
            )
            decoded = tuple(self._decode_key_row(row) for row in rows)
        except Exception:
            return CatalogUnavailable("key_candidate_query_error", _KEY_SOURCE)

        if len(decoded) > limit:
            return CatalogUnavailable(
                "multi_page_requires_immutable_snapshot",
                _KEY_SOURCE,
            )
        has_more = False
        candidates = decoded[:limit]
        next_checkpoint = None
        if has_more and candidates:
            last = candidates[-1]
            next_checkpoint = CatalogKeyCheckpoint(
                source=_KEY_SOURCE,
                catalog_epoch=self.catalog_epoch,
                project_scope_fingerprint=self.project_scope_fingerprint,
                window_start=self.window_start,
                window_end=self.window_end,
                normalized_search=normalized_search,
                query_fingerprint=query_fingerprint,
                qualification_fingerprint=(qualification.qualification_fingerprint),
                key_folded=_ascii_fold(last.attribute_key),
                attribute_key=last.attribute_key,
                attribute_type_rank=_ATTRIBUTE_TYPE_RANK[last.attribute_type],
            )
        return CatalogKeyPage(
            candidates=candidates,
            has_more=has_more,
            next_checkpoint=next_checkpoint,
            qualification=qualification,
        )

    def read_value_candidates(
        self,
        attribute_key: str,
        *,
        page_size: int,
        attribute_types: Iterable[AttributeType] | None = None,
        search: str | None = None,
        after: CatalogValueCheckpoint | None = None,
    ) -> CatalogValuePageResult:
        """Return one strict typed-scalar candidate page for one attribute key."""

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
        types = _attribute_types(attribute_types)
        query_fingerprint = self._value_query_fingerprint(
            attribute_key=key,
            attribute_types=types,
            normalized_search=normalized_search,
            page_size=limit,
        )
        if after is not None:
            self._validate_value_checkpoint(
                after,
                attribute_key=key,
                attribute_types=types,
                normalized_search=normalized_search,
                query_fingerprint=query_fingerprint,
            )
            return CatalogUnavailable(
                "continuation_requires_immutable_snapshot",
                _VALUE_SOURCE,
            )
        if normalized_search:
            return CatalogUnavailable("search_requires_unicode_parity", _VALUE_SOURCE)
        qualification = self.qualify()
        if isinstance(qualification, CatalogUnavailable):
            return qualification

        params = {
            "catalog_project_ids": self.project_ids,
            "catalog_epoch": self.catalog_epoch,
            "catalog_window_start": self.window_start,
            "catalog_window_end": self.window_end,
            "catalog_attribute_key": key,
            "catalog_attribute_types": types,
            "catalog_value_search_pattern": _indexed_value_search_pattern(
                normalized_search
            ),
            "catalog_after_value_fingerprint": "",
            "catalog_after_value_type_rank": 0,
            "catalog_page_limit": limit + 1,
        }
        try:
            rows = self._execute(
                _VALUE_PAGE_SQL,
                params,
                max_result_rows=limit + 1,
            )
            decoded = tuple(self._decode_value_row(key, row) for row in rows)
        except Exception:
            return CatalogUnavailable("value_candidate_query_error", _VALUE_SOURCE)

        if len(decoded) > limit:
            return CatalogUnavailable(
                "multi_page_requires_immutable_snapshot",
                _VALUE_SOURCE,
            )
        has_more = False
        candidates = decoded[:limit]
        if any(candidate.attribute_type not in types for candidate in candidates):
            return CatalogUnavailable("value_candidate_query_error", _VALUE_SOURCE)
        if any(
            candidate.attribute_type == "array" and candidate.scalar_kind == "number"
            for candidate in candidates
        ):
            return CatalogUnavailable("unsupported_array_numeric", _VALUE_SOURCE)
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
        )

    def _execute(
        self,
        sql: str,
        params: dict[str, Any],
        *,
        max_result_rows: int,
    ) -> list[dict[str, Any]]:
        result = self._executor.execute(
            sql,
            params,
            timeout_ms=CATALOG_QUERY_TIMEOUT_MS,
            settings={
                **CATALOG_READ_SETTINGS,
                "max_result_rows": max_result_rows,
            },
        )
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
            except (TypeError, ValueError):
                return CatalogUnavailable("activation_invalid")
            if epoch != self.catalog_epoch:
                return CatalogUnavailable("activation_epoch_mismatch")
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
            if writer_watermark < self.window_end:
                return CatalogUnavailable("activation_writer_lag")
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
    ) -> str:
        return _identity_fingerprint(
            "value-query-v1",
            {
                "catalog_epoch": self.catalog_epoch,
                "project_scope": self.project_scope_fingerprint,
                "window_start_us": _unix_microseconds(self.window_start),
                "window_end_us": _unix_microseconds(self.window_end),
                "attribute_key": attribute_key,
                "attribute_types": attribute_types,
                "normalized_search": normalized_search,
                "page_size": page_size,
            },
        )

    def _validate_key_checkpoint(
        self,
        checkpoint: CatalogKeyCheckpoint,
        *,
        normalized_search: str,
        query_fingerprint: str,
    ) -> None:
        self._validate_checkpoint_scope(checkpoint, _KEY_SOURCE)
        if (
            checkpoint.normalized_search != normalized_search
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
                "handoff_start_us": _unix_microseconds(
                    _row_datetime(row["handoff_start"])
                ),
                "handoff_end_us": _unix_microseconds(_row_datetime(row["handoff_end"])),
                "writer_watermark_us": _unix_microseconds(
                    _row_datetime(row["writer_watermark"])
                ),
                "status": str(row["status"]),
                "qualified_at_us": _unix_microseconds(
                    _row_datetime(row["qualified_at"])
                ),
                "state_version": _strict_int(row["state_version"]),
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
                "checkpoint_fences": _checkpoint_fences(row["checkpoint_fences"]),
            }
        )
    return _identity_fingerprint(
        "qualification-v1",
        {"activations": activations, "checkpoints": checkpoints},
    )


def _aware_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{label} must be a timezone-aware datetime")
    return value.astimezone(UTC)


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
