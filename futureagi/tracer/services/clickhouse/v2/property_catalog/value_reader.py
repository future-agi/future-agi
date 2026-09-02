"""Activated native-value reader for hot span-backed property definitions.

Only definitions whose active ``value_adapter`` is ``span_attribute_value``
enter this reader.  Every other property continues through its existing native
adapter.  The reader validates the active definition before touching the value
table, pins the exact activation revision, resolves duplicate observations
across projects/revisions by typed fingerprint, and fails closed on every
conflict or incomplete scan.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import monotonic
from typing import Any, Protocol

from tracer.services.clickhouse.v2.attribute_catalog_codec import (
    encode_catalog_scalar,
)
from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ActivationControlSelector,
    ActivationControlTarget,
    ActivationControlUnavailable,
)
from tracer.services.clickhouse.v2.property_catalog.codec import (
    MAX_DEFINITION_JSON_BYTES,
    MAX_IDENTITY_COMPONENT_BYTES,
    canonical_json,
    canonical_json_sha256,
    like_contains_pattern,
)
from tracer.services.clickhouse.v2.property_catalog.connection import (
    validate_property_catalog_database,
)
from tracer.services.clickhouse.v2.property_catalog.cursor import (
    normalize_property_catalog_scope,
)
from tracer.services.clickhouse.v2.property_catalog.database import (
    is_production_property_catalog_database,
)
from tracer.services.clickhouse.v2.property_catalog.reader import (
    PropertyCatalogActivation,
    PropertyCatalogUnavailable,
    _control_target_matches_activation,
    property_catalog_activation_sql,
    require_property_catalog_activation_coverage,
    verify_property_catalog_activation,
)
from tracer.services.clickhouse.v2.property_catalog.runtime_limits import RUNTIME_LIMITS
from tracer.services.clickhouse.v2.property_catalog.value_cursor import (
    PropertyCatalogValueCursor,
    decode_property_catalog_value_cursor,
    encode_property_catalog_value_cursor,
    normalize_property_catalog_value_query,
)
from tracer.utils.attribute_suggestion_contract import (
    TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES,
)
from tracer.utils.property_registry import (
    parse_property_registry_id,
    validate_property_source_binding,
)

PROPERTY_CATALOG_VALUE_ADAPTER = "span_attribute_value"
PROPERTY_CATALOG_VALUE_MAX_PROJECTS = RUNTIME_LIMITS.max_projects
PROPERTY_CATALOG_VALUE_MAX_PAGE_SIZE = RUNTIME_LIMITS.max_page_size
PROPERTY_CATALOG_VALUE_MAX_SEARCH_BYTES = RUNTIME_LIMITS.max_search_bytes
PROPERTY_CATALOG_VALUE_MAX_KEY_BYTES = MAX_IDENTITY_COMPONENT_BYTES
PROPERTY_CATALOG_VALUE_MAX_JSON_BYTES = MAX_DEFINITION_JSON_BYTES
PROPERTY_CATALOG_VALUE_QUERY_WALL_MS = RUNTIME_LIMITS.query_wall_ms

_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_ATTRIBUTE_TYPE_RANK = {
    "string": 1,
    "number": 2,
    "boolean": 3,
    "array": 4,
    "map": 5,
    "json": 6,
}
_SELECTABLE_ATTRIBUTE_TYPES = frozenset({"string", "number", "boolean", "array"})
_ALL_ATTRIBUTE_TYPES = frozenset(_ATTRIBUTE_TYPE_RANK)

_READ_SETTINGS = RUNTIME_LIMITS.clickhouse_read_settings


class _Result(Protocol):
    data: list[dict[str, Any]]


class PropertyCatalogValueQueryExecutor(Protocol):
    def execute(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> _Result: ...


class PropertyCatalogValueUnavailable(PropertyCatalogUnavailable):
    """The active value snapshot cannot safely answer this request."""


class PropertyCatalogValueNotReady(PropertyCatalogValueUnavailable):
    """Typed signal permitting the explicit legacy/native routing fallback."""


@dataclass(frozen=True, slots=True)
class PropertyCatalogValue:
    value: str | int | Decimal | bool
    attribute_type: str
    scalar_kind: str
    value_fingerprint: str
    value_search_text: str
    first_seen: datetime
    last_seen: datetime


@dataclass(frozen=True, slots=True)
class PropertyCatalogValuePage:
    values: tuple[PropertyCatalogValue, ...]
    has_more: bool
    next_cursor: str | None
    catalog_epoch: int
    catalog_revision: int
    activation_fingerprint: str
    window_start: datetime
    window_end: datetime
    attribute_types: tuple[str, ...]
    query_count: int


@dataclass(slots=True)
class _ReadBudget:
    deadline: float
    clock: Callable[[], float]
    query_count: int = 0

    @classmethod
    def start(cls, clock: Callable[[], float]) -> _ReadBudget:
        return cls(clock() + PROPERTY_CATALOG_VALUE_QUERY_WALL_MS / 1_000, clock)

    def remaining_ms(self) -> int:
        remaining = int((self.deadline - self.clock()) * 1_000)
        if remaining < 1:
            raise PropertyCatalogValueUnavailable("deadline_exceeded")
        return min(remaining, PROPERTY_CATALOG_VALUE_QUERY_WALL_MS)


def _definition_ctes(database: str) -> str:
    return f"""
WITH lineage_versioned AS
(
    SELECT
        *,
        max(_version) OVER (
            PARTITION BY organization_id, workspace_id,
                         catalog_epoch, catalog_revision, build_token
        ) AS latest_version
    FROM `{database}`.`property_catalog_activations`
    PREWHERE organization_id = %(catalog_organization_id)s
      AND workspace_id = %(catalog_workspace_id)s
      AND catalog_epoch = %(catalog_epoch)s
      AND catalog_revision >= %(catalog_lineage_anchor_revision)s
      AND catalog_revision <= %(catalog_revision)s
), lineage_states AS
(
    SELECT
        versioned_rows.catalog_epoch,
        versioned_rows.catalog_revision,
        versioned_rows.build_token,
        argMax(versioned_rows.projection_version, versioned_rows._version)
            AS projection_version,
        argMax(versioned_rows.lifecycle_mode, versioned_rows._version)
            AS lifecycle_mode,
        argMax(versioned_rows.lineage_anchor_revision, versioned_rows._version)
            AS lineage_anchor_revision,
        argMax(versioned_rows.status, versioned_rows._version) AS status,
        argMax(versioned_rows.qualified_at, versioned_rows._version) AS qualified_at,
        uniqExactIf(
            tuple(
                versioned_rows.projection_version,
                versioned_rows.lifecycle_mode,
                versioned_rows.lineage_anchor_revision,
                versioned_rows.status,
                versioned_rows.qualified_at
            ),
            versioned_rows._version = versioned_rows.latest_version
        ) AS latest_state_variants
    FROM lineage_versioned AS versioned_rows
    GROUP BY
        versioned_rows.catalog_epoch,
        versioned_rows.catalog_revision,
        versioned_rows.build_token
), active_lineage_candidates AS
(
    SELECT *
    FROM lineage_states
    WHERE latest_state_variants = 1
      AND status = 'active'
      AND qualified_at IS NOT NULL
), active_lineage AS
(
    SELECT
        candidate.catalog_epoch,
        candidate.catalog_revision,
        any(candidate.build_token) AS build_token,
        any(candidate.projection_version) AS projection_version,
        count() AS active_builds
    FROM active_lineage_candidates AS candidate
    GROUP BY candidate.catalog_epoch, candidate.catalog_revision
    HAVING active_builds = 1
), visible_rows AS
(
    SELECT rows.*
    FROM `{database}`.`property_definition_catalog` AS rows
    INNER JOIN active_lineage AS lineage
        ON rows.catalog_epoch = lineage.catalog_epoch
       AND rows.catalog_revision = lineage.catalog_revision
       AND rows.build_token = lineage.build_token
       AND rows.projection_version = lineage.projection_version
    PREWHERE rows.organization_id = %(catalog_organization_id)s
      AND rows.workspace_id = %(catalog_workspace_id)s
      AND rows.catalog_epoch = %(catalog_epoch)s
      AND rows.catalog_revision >= %(catalog_lineage_anchor_revision)s
      AND rows.catalog_revision <= %(catalog_revision)s
      -- Reject unrelated project bindings before reading definition_json.
      AND (
        rows.visibility_scope = 'always'
        OR rows.visibility_scope = 'workspace_default'
        OR (
            rows.visibility_scope = 'project'
            AND (
                %(catalog_include_all_projects)s = 1
                OR toString(rows.visibility_id) IN %(catalog_project_ids)s
            )
        )
      )
), binding_maxima AS
(
    SELECT
        binding.binding_id,
        max(tuple(binding.catalog_revision, binding.source_version))
            AS latest_source_version
    FROM visible_rows AS binding
    GROUP BY binding.binding_id
), latest_binding_rows AS
(
    SELECT rows.*
    FROM visible_rows AS rows
    INNER JOIN binding_maxima AS maxima USING (binding_id)
    WHERE tuple(rows.catalog_revision, rows.source_version)
        = maxima.latest_source_version
), resolved_bindings AS
(
    SELECT
        binding.binding_id,
        any(binding.visibility_scope) AS visibility_scope,
        any(binding.visibility_id) AS visibility_id,
        any(binding.property_id) AS property_id,
        any(binding.property_kind) AS property_kind,
        any(binding.source_adapter) AS source_adapter,
        any(binding.primary_source) AS primary_source,
        any(binding.value_adapter) AS value_adapter,
        any(binding.name) AS name,
        any(binding.definition_json) AS definition_json,
        any(binding.definition_sha256) AS definition_sha256,
        any(binding.is_deleted) AS is_deleted,
        uniqExact(binding.state_sha256) AS state_variants,
        uniqExact(tuple(
            binding.visibility_scope,
            binding.visibility_id,
            binding.property_id,
            binding.property_kind,
            binding.source_adapter,
            binding.primary_source,
            binding.value_adapter,
            binding.name,
            binding.definition_sha256,
            binding.is_deleted,
            binding.state_sha256
        )) AS binding_variants
    FROM latest_binding_rows AS binding
    GROUP BY binding.binding_id
), classified_bindings AS
(
    SELECT
        *,
        state_variants = 1
            AND binding_variants = 1
            AND is_deleted = 0 AS binding_is_live,
        state_variants != 1
            OR binding_variants != 1 AS binding_is_conflicted
    FROM resolved_bindings
), resolved_properties AS
(
    SELECT
        binding.property_id,
        anyIf(binding.property_kind, binding.binding_is_live) AS property_kind,
        anyIf(binding.source_adapter, binding.binding_is_live) AS source_adapter,
        anyIf(binding.primary_source, binding.binding_is_live) AS primary_source,
        anyIf(binding.value_adapter, binding.binding_is_live) AS value_adapter,
        anyIf(binding.name, binding.binding_is_live) AS name,
        anyIf(binding.definition_json, binding.binding_is_live) AS definition_json,
        anyIf(binding.definition_sha256, binding.binding_is_live)
            AS definition_sha256,
        countIf(binding.binding_is_conflicted) AS binding_conflicts,
        countIf(binding.binding_is_live) AS live_binding_count,
        countIf(
            binding.binding_is_live
            AND binding.visibility_scope = 'project'
        ) AS project_binding_count,
        uniqExactIf(tuple(
            binding.property_kind,
            binding.source_adapter,
            binding.primary_source,
            binding.value_adapter,
            binding.name,
            binding.definition_sha256
        ), binding.binding_is_live) AS definition_variants
    FROM classified_bindings AS binding
    GROUP BY binding.property_id
)
"""


_DEFINITION_PROOF_SUFFIX = """
SELECT
    (
        SELECT count()
        FROM lineage_states
        WHERE latest_state_variants != 1
    ) AS activation_state_conflicts,
    (
        SELECT count()
        FROM
        (
            SELECT catalog_epoch, catalog_revision
            FROM active_lineage_candidates
            GROUP BY catalog_epoch, catalog_revision
            HAVING count() != 1
        )
    ) AS activation_lineage_conflicts,
    (
        SELECT if(count() = 0, 0, uniqExact(projection_version) != 1)
        FROM active_lineage
    ) AS activation_projection_conflicts,
    (
        SELECT count()
        FROM active_lineage_candidates
        WHERE lineage_anchor_revision != %(catalog_lineage_anchor_revision)s
           OR lineage_anchor_revision > catalog_revision
           OR (
               catalog_revision = lineage_anchor_revision
               AND lifecycle_mode NOT IN ('initial_backfill', 'full_repair')
           )
           OR (
               catalog_revision > lineage_anchor_revision
               AND lifecycle_mode != 'incremental'
           )
    ) AS activation_anchor_conflicts,
    sum(resolved_property.binding_conflicts) AS binding_conflicts,
    countIf(
        resolved_property.live_binding_count > 0
        AND resolved_property.definition_variants != 1
    )
        AS definition_conflicts,
    countIf(
        resolved_property.property_id = %(catalog_property_id)s
        AND resolved_property.live_binding_count > 0
    ) AS property_rows,
    anyIf(
        toString(resolved_property.property_kind),
        resolved_property.property_id = %(catalog_property_id)s
        AND resolved_property.live_binding_count > 0
    )
        AS property_kind,
    anyIf(
        toString(resolved_property.source_adapter),
        resolved_property.property_id = %(catalog_property_id)s
        AND resolved_property.live_binding_count > 0
    )
        AS source_adapter,
    anyIf(
        resolved_property.primary_source,
        resolved_property.property_id = %(catalog_property_id)s
        AND resolved_property.live_binding_count > 0
    )
        AS primary_source,
    anyIf(
        resolved_property.value_adapter,
        resolved_property.property_id = %(catalog_property_id)s
        AND resolved_property.live_binding_count > 0
    )
        AS value_adapter,
    anyIf(
        resolved_property.name,
        resolved_property.property_id = %(catalog_property_id)s
        AND resolved_property.live_binding_count > 0
    ) AS name,
    anyIf(
        resolved_property.definition_json,
        resolved_property.property_id = %(catalog_property_id)s
        AND resolved_property.live_binding_count > 0
    )
        AS definition_json,
    anyIf(
        resolved_property.definition_sha256,
        resolved_property.property_id = %(catalog_property_id)s
        AND resolved_property.live_binding_count > 0
    )
        AS definition_sha256,
    anyIf(
        resolved_property.live_binding_count,
        resolved_property.property_id = %(catalog_property_id)s
        AND resolved_property.live_binding_count > 0
    )
        AS live_binding_count,
    anyIf(
        resolved_property.project_binding_count,
        resolved_property.property_id = %(catalog_property_id)s
        AND resolved_property.live_binding_count > 0
    )
        AS project_binding_count,
    anyIf(
        resolved_property.definition_variants,
        resolved_property.property_id = %(catalog_property_id)s
        AND resolved_property.live_binding_count > 0
    )
        AS property_definition_variants
FROM resolved_properties AS resolved_property
"""


_VALUE_SOURCE_CTES = """
, eligible_custom_projects AS
(
    SELECT DISTINCT visibility_id AS project_id
    FROM classified_bindings
    WHERE property_id = %(catalog_property_id)s
      AND binding_is_live
      AND visibility_scope = 'project'
), source_values AS
(
    SELECT
        attribute_type,
        value_fingerprint,
        value_json AS raw_value_json,
        value_search_text_folded AS raw_value_search_text_folded,
        first_seen AS raw_first_seen,
        last_seen AS raw_last_seen
    FROM __CATALOG_DATABASE__.span_attribute_value_catalog AS value_rows
    INNER JOIN active_lineage AS lineage
        ON value_rows.catalog_epoch = lineage.catalog_epoch
       AND value_rows.catalog_revision = lineage.catalog_revision
       AND value_rows.build_token = lineage.build_token
    PREWHERE value_rows.organization_id = %(catalog_organization_id)s
      AND value_rows.workspace_id = %(catalog_workspace_id)s
      AND value_rows.catalog_epoch = %(catalog_epoch)s
      AND value_rows.catalog_revision >= %(catalog_lineage_anchor_revision)s
      AND value_rows.catalog_revision <= %(catalog_revision)s
      AND value_rows.source_kind = %(catalog_source_kind)s
      AND value_rows.attribute_key = %(catalog_attribute_key)s
      AND (
        %(catalog_include_all_projects)s = 1
        OR value_rows.project_id IN %(catalog_project_uuid_ids)s
      )
    WHERE (
        %(catalog_source_kind)s = 'system_attribute'
        OR value_rows.project_id IN (
            SELECT project_id FROM eligible_custom_projects
        )
      )
      AND attribute_type IN %(catalog_attribute_types)s
      -- The cursor tuple is identical for every physical row contributing to
      -- one grouped value. Excluding prior tuples here is therefore safe and
      -- prevents later pages from aggregating fingerprints already proved by
      -- the signed, activation-pinned previous page.
      AND tuple(
          toInt8(value_rows.attribute_type),
          value_rows.value_fingerprint
      ) > tuple(
          %(catalog_after_attribute_type_rank)s,
          %(catalog_after_value_fingerprint)s
      )
      -- Apply overlap to each independently emitted observation before
      -- fingerprint dedupe.  Filtering only after min(first_seen)/max(last_seen)
      -- would bridge disjoint January and March rows into a false February hit.
      AND first_seen < fromUnixTimestamp64Micro(%(catalog_window_end_us)s, 'UTC')
      AND last_seen >= fromUnixTimestamp64Micro(%(catalog_window_start_us)s, 'UTC')
), grouped_values AS
(
    SELECT
        attribute_type,
        value_fingerprint,
        min(raw_value_json) AS value_json,
        min(raw_value_search_text_folded) AS value_search_text_folded,
        uniqExact(raw_value_json) AS value_json_variants,
        uniqExact(raw_value_search_text_folded) AS value_search_folded_variants,
        min(raw_first_seen) AS first_seen,
        max(raw_last_seen) AS last_seen
    FROM source_values
    GROUP BY attribute_type, value_fingerprint
), eligible_values AS
(
    SELECT
        -- ClickHouse SELECT aliases are globally visible.  Keep both casts
        -- bound to the raw Enum8 so the String alias cannot shadow this rank.
        toString(grouped_value.attribute_type) AS attribute_type,
        toInt8(grouped_value.attribute_type) AS attribute_type_rank,
        grouped_value.value_fingerprint,
        grouped_value.value_json,
        grouped_value.value_search_text_folded,
        grouped_value.value_json_variants,
        grouped_value.value_search_folded_variants,
        grouped_value.first_seen,
        grouped_value.last_seen
    FROM grouped_values AS grouped_value
    WHERE grouped_value.first_seen
              < fromUnixTimestamp64Micro(%(catalog_window_end_us)s, 'UTC')
      AND grouped_value.last_seen
              >= fromUnixTimestamp64Micro(%(catalog_window_start_us)s, 'UTC')
)
"""


_VALUE_PAGE_SUFFIX = """
, value_rows_with_sentinel AS
(
    SELECT
        attribute_type,
        attribute_type_rank,
        value_fingerprint,
        value_json,
        value_search_text_folded,
        value_json_variants,
        value_search_folded_variants,
        first_seen,
        last_seen,
        toUInt8(0) AS catalog_metadata_only
    FROM eligible_values

    UNION ALL

    SELECT
        'string' AS attribute_type,
        toInt8(1) AS attribute_type_rank,
        repeat('0', 64) AS value_fingerprint,
        '""' AS value_json,
        '' AS value_search_text_folded,
        toUInt64(0) AS value_json_variants,
        toUInt64(0) AS value_search_folded_variants,
        fromUnixTimestamp64Micro(%(catalog_window_start_us)s, 'UTC') AS first_seen,
        fromUnixTimestamp64Micro(%(catalog_window_start_us)s, 'UTC') AS last_seen,
        toUInt8(1) AS catalog_metadata_only
), checked_value_rows AS
(
    SELECT
        *,
        countIf(
            catalog_metadata_only = 0
            AND (
                value_json_variants != 1
                OR value_search_folded_variants != 1
            )
        ) OVER () AS value_conflicts
    FROM value_rows_with_sentinel
)
SELECT
    attribute_type,
    attribute_type_rank,
    value_fingerprint,
    value_json,
    value_search_text_folded,
    value_json_variants,
    value_search_folded_variants,
    first_seen,
    last_seen,
    catalog_metadata_only,
    value_conflicts
FROM checked_value_rows
WHERE catalog_metadata_only = 1
   OR (
       (
           %(catalog_search)s = ''
           OR value_search_text_folded LIKE %(catalog_search_pattern)s
       )
       AND tuple(attribute_type_rank, value_fingerprint) > tuple(
           %(catalog_after_attribute_type_rank)s,
           %(catalog_after_value_fingerprint)s
       )
   )
ORDER BY
    catalog_metadata_only DESC,
    attribute_type_rank ASC,
    value_fingerprint ASC
LIMIT %(catalog_result_limit)s
"""


def _database(value: str) -> str:
    return validate_property_catalog_database(value)


def _qualify(sql: str, database: str) -> str:
    return sql.replace(
        "__CATALOG_DATABASE__.property_catalog_activations",
        f"`{database}`.`property_catalog_activations`",
    ).replace(
        "__CATALOG_DATABASE__.span_attribute_value_catalog",
        f"`{database}`.`span_attribute_value_catalog`",
    )


def _uuid(value: Any, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{label} must be a UUID") from exc


def _strict_uint(value: Any, label: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        raise PropertyCatalogValueUnavailable(f"invalid_{label}")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise PropertyCatalogValueUnavailable(f"invalid_{label}")
    return value


def _aware_utc(value: datetime, label: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _unix_microseconds(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    delta = value - epoch
    return delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds


def _datetime_from_unix_microseconds(value: int) -> datetime:
    try:
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=value)
    except OverflowError as exc:
        raise PropertyCatalogValueUnavailable("activation_scope_invalid") from exc


def _row_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PropertyCatalogValueUnavailable("value_time_invalid") from exc
    else:
        raise PropertyCatalogValueUnavailable("value_time_invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PropertyCatalogValueUnavailable("value_time_invalid")
    return parsed.astimezone(UTC)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"unsupported JSON constant: {value}")


class PropertyCatalogValueReader:
    """Read one immutable typed-value page from the activated catalog."""

    def __init__(
        self,
        executor: PropertyCatalogValueQueryExecutor,
        *,
        catalog_database: str,
        activation_selector: ActivationControlSelector | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._executor = executor
        self._database = _database(catalog_database)
        if is_production_property_catalog_database(self._database) and (
            activation_selector is None
        ):
            raise ValueError("production property catalog requires control selection")
        self._activation_selector = activation_selector
        self._clock = clock
        definition_ctes = _definition_ctes(self._database)
        self._activation_sql = property_catalog_activation_sql(self._database)
        self._definition_sql = definition_ctes + _DEFINITION_PROOF_SUFFIX
        self._value_page_sql = _qualify(
            definition_ctes + _VALUE_SOURCE_CTES + _VALUE_PAGE_SUFFIX,
            self._database,
        )

    def read_page(
        self,
        *,
        scope: dict[str, Any],
        query: dict[str, Any],
        page_size: int,
        window_start: datetime | None = None,
        window_end: datetime | None = None,
        cursor_token: str | None = None,
    ) -> PropertyCatalogValuePage:
        checked_scope = self._validate_scope(scope)
        checked_query, decoded = self._validate_query(query)
        if (
            type(page_size) is not int
            or not 1 <= page_size <= PROPERTY_CATALOG_VALUE_MAX_PAGE_SIZE
        ):
            raise ValueError(
                "page_size must be between 1 and "
                f"{PROPERTY_CATALOG_VALUE_MAX_PAGE_SIZE}"
            )

        cursor: PropertyCatalogValueCursor | None = None
        if cursor_token:
            cursor = decode_property_catalog_value_cursor(
                cursor_token,
                scope=checked_scope,
                query=checked_query,
                page_size=page_size,
            )
            checked_window_start = cursor.window_start
            checked_window_end = cursor.window_end
        else:
            if window_start is None or window_end is None:
                raise ValueError("window_start and window_end are required")
            checked_window_start = _aware_utc(window_start, "window_start")
            checked_window_end = _aware_utc(window_end, "window_end")
            if checked_window_start >= checked_window_end:
                raise ValueError("catalog value window must be non-empty")

        budget = _ReadBudget.start(self._clock)
        activation = self._activation(
            scope=checked_scope,
            cursor=cursor,
            budget=budget,
        )
        if cursor and activation.activation_sha256 != cursor.activation_fingerprint:
            raise PropertyCatalogValueUnavailable("activation_mismatch")
        source_scope = activation.source_scope
        if source_scope is None:
            raise PropertyCatalogValueUnavailable("activation_scope_invalid")
        retained_span_since_us = activation.retained_span_since_us
        retained_span_until_us = activation.retained_span_until_us
        if (
            type(retained_span_since_us) is not int
            or type(retained_span_until_us) is not int
            or retained_span_since_us < 0
            or retained_span_since_us >= retained_span_until_us
        ):
            raise PropertyCatalogValueUnavailable("activation_lineage_scope_invalid")
        covered_window_start_us = _unix_microseconds(checked_window_start)
        covered_window_end_us = _unix_microseconds(checked_window_end)
        if cursor is None:
            # A first page may request the full retained horizon. Incremental
            # activations contribute only their new source window, while the
            # admitted lineage still contains values from the immutable
            # initial/full-repair anchor. Publish the intersection with that
            # cumulative lineage window, never just the newest revision.
            covered_window_start_us = max(
                covered_window_start_us,
                retained_span_since_us,
            )
            covered_window_end_us = min(
                covered_window_end_us,
                retained_span_until_us,
            )
            if covered_window_start_us >= covered_window_end_us:
                raise PropertyCatalogValueUnavailable("activation_scope_incomplete")
            checked_window_start = _datetime_from_unix_microseconds(
                covered_window_start_us
            )
            checked_window_end = _datetime_from_unix_microseconds(covered_window_end_us)
        require_property_catalog_activation_coverage(
            scope=checked_scope,
            activation=activation,
            unavailable_type=PropertyCatalogValueUnavailable,
        )
        if (
            covered_window_start_us < retained_span_since_us
            or covered_window_end_us > retained_span_until_us
        ):
            raise PropertyCatalogValueUnavailable("activation_scope_incomplete")

        base_params = self._base_params(
            scope=checked_scope,
            query=checked_query,
            decoded=decoded,
            activation=activation,
            window_start=checked_window_start,
            window_end=checked_window_end,
        )
        active_types = self._definition(
            params=base_params,
            decoded=decoded,
            budget=budget,
        )
        requested_type = checked_query["attribute_type"]
        if requested_type and requested_type not in active_types:
            return self._empty_page(
                activation=activation,
                window_start=checked_window_start,
                window_end=checked_window_end,
                active_types=active_types,
                budget=budget,
            )
        selected_types = tuple(
            value
            for value in active_types
            if value in _SELECTABLE_ATTRIBUTE_TYPES
            and (not requested_type or value == requested_type)
        )
        if not selected_types:
            return self._empty_page(
                activation=activation,
                window_start=checked_window_start,
                window_end=checked_window_end,
                active_types=active_types,
                budget=budget,
            )
        base_params["catalog_attribute_types"] = selected_types

        after = cursor.order if cursor else (0, "")
        params = {
            **base_params,
            "catalog_after_attribute_type_rank": after[0],
            "catalog_after_value_fingerprint": after[1],
            "catalog_result_limit": page_size + 2,
        }
        rows = self._execute(
            self._value_page_sql,
            params,
            max_result_rows=page_size + 2,
            budget=budget,
        )
        metadata_rows = [row for row in rows if row.get("catalog_metadata_only") == 1]
        if len(metadata_rows) != 1:
            raise PropertyCatalogValueUnavailable("value_conflict_proof_missing")
        if _strict_uint(metadata_rows[0].get("value_conflicts"), "value_conflicts"):
            raise PropertyCatalogValueUnavailable("value_conflict")
        rows = [row for row in rows if row.get("catalog_metadata_only") == 0]
        matches: list[PropertyCatalogValue] = []
        previous_position = after
        for row in rows:
            value = self._decode_value(
                row,
                window_start=checked_window_start,
                window_end=checked_window_end,
            )
            position = (
                _ATTRIBUTE_TYPE_RANK[value.attribute_type],
                value.value_fingerprint,
            )
            if position <= previous_position:
                raise PropertyCatalogValueUnavailable("value_order_invalid")
            previous_position = position
            if checked_query["search"] and checked_query["search"] not in (
                value.value_search_text.casefold()
            ):
                raise PropertyCatalogValueUnavailable("value_search_mismatch")
            matches.append(value)

        has_more = len(matches) > page_size
        published = tuple(matches[:page_size])
        next_cursor = None
        if has_more and published:
            last = published[-1]
            next_cursor = encode_property_catalog_value_cursor(
                scope=checked_scope,
                query=checked_query,
                page_size=page_size,
                catalog_epoch=activation.catalog_epoch,
                catalog_revision=activation.catalog_revision,
                activation_fingerprint=activation.activation_sha256,
                window_start=checked_window_start,
                window_end=checked_window_end,
                order=(
                    _ATTRIBUTE_TYPE_RANK[last.attribute_type],
                    last.value_fingerprint,
                ),
            )
        return PropertyCatalogValuePage(
            values=published,
            has_more=has_more,
            next_cursor=next_cursor,
            catalog_epoch=activation.catalog_epoch,
            catalog_revision=activation.catalog_revision,
            activation_fingerprint=activation.activation_sha256,
            window_start=checked_window_start,
            window_end=checked_window_end,
            attribute_types=active_types,
            query_count=budget.query_count,
        )

    def _empty_page(
        self,
        *,
        activation: PropertyCatalogActivation,
        window_start: datetime,
        window_end: datetime,
        active_types: tuple[str, ...],
        budget: _ReadBudget,
    ) -> PropertyCatalogValuePage:
        return PropertyCatalogValuePage(
            values=(),
            has_more=False,
            next_cursor=None,
            catalog_epoch=activation.catalog_epoch,
            catalog_revision=activation.catalog_revision,
            activation_fingerprint=activation.activation_sha256,
            window_start=window_start,
            window_end=window_end,
            attribute_types=active_types,
            query_count=budget.query_count,
        )

    def _activation(
        self,
        *,
        scope: dict[str, Any],
        cursor: PropertyCatalogValueCursor | None,
        budget: _ReadBudget,
    ) -> PropertyCatalogActivation:
        target: ActivationControlTarget | None = None
        if self._activation_selector is not None:
            try:
                target = self._activation_selector.select_target(
                    scope=scope,
                    timeout_ms=budget.remaining_ms(),
                )
                budget.query_count += 1
            except ActivationControlUnavailable as exc:
                raise PropertyCatalogValueUnavailable(exc.reason) from exc
        rows = self._execute(
            self._activation_sql,
            {
                "catalog_organization_id": scope["organization_id"],
                "catalog_workspace_id": scope["workspace_id"],
                "catalog_exact_activation": int(
                    target is not None or cursor is not None
                ),
                "catalog_epoch": (
                    target.catalog_epoch
                    if target is not None
                    else cursor.catalog_epoch
                    if cursor
                    else 0
                ),
                "catalog_revision": (
                    target.catalog_revision
                    if target is not None
                    else cursor.catalog_revision
                    if cursor
                    else 0
                ),
            },
            max_result_rows=2,
            budget=budget,
        )
        activation = verify_property_catalog_activation(
            rows,
            scope=scope,
            cursor_present=target is not None or cursor is not None,
            unavailable_type=PropertyCatalogValueUnavailable,
        )
        if target is not None and not _control_target_matches_activation(
            target,
            activation,
        ):
            raise PropertyCatalogValueUnavailable("control_target_mismatch")
        return activation

    def _definition(
        self,
        *,
        params: dict[str, Any],
        decoded: dict[str, str],
        budget: _ReadBudget,
    ) -> tuple[str, ...]:
        rows = self._execute(
            self._definition_sql,
            params,
            max_result_rows=1,
            budget=budget,
        )
        if len(rows) != 1:
            raise PropertyCatalogValueUnavailable("definition_proof_missing")
        row = rows[0]
        if (
            _strict_uint(
                row.get("activation_state_conflicts"),
                "activation_state_conflicts",
            )
            or _strict_uint(
                row.get("activation_lineage_conflicts"),
                "activation_lineage_conflicts",
            )
            or _strict_uint(
                row.get("activation_projection_conflicts"),
                "activation_projection_conflicts",
            )
            or _strict_uint(
                row.get("activation_anchor_conflicts"),
                "activation_anchor_conflicts",
            )
            or _strict_uint(row.get("binding_conflicts"), "binding_conflicts")
            or _strict_uint(row.get("definition_conflicts"), "definition_conflicts")
        ):
            raise PropertyCatalogValueUnavailable("definition_conflict")
        if _strict_uint(row.get("property_rows"), "property_rows") != 1:
            raise PropertyCatalogValueUnavailable("definition_missing")
        if (
            _strict_uint(
                row.get("property_definition_variants"), "property_definition_variants"
            )
            != 1
        ):
            raise PropertyCatalogValueUnavailable("definition_conflict")
        if str(row.get("value_adapter") or "") != PROPERTY_CATALOG_VALUE_ADAPTER:
            raise PropertyCatalogValueNotReady("native_value_adapter")
        if (
            str(row.get("property_kind") or "") != decoded["property_kind"]
            or str(row.get("name") or "") != decoded["metric_name"]
            or (
                decoded["property_kind"] == "system_attribute"
                and str(row.get("primary_source") or "") != decoded["definition_source"]
            )
            or (
                decoded["property_kind"] == "custom_attribute"
                and str(row.get("source_adapter") or "") != "span_attribute"
            )
            or (
                decoded["property_kind"] == "system_attribute"
                and str(row.get("source_adapter") or "") != "system_manifest"
            )
        ):
            raise PropertyCatalogValueUnavailable("definition_identity_mismatch")
        live_binding_count = _strict_uint(
            row.get("live_binding_count"), "live_binding_count"
        )
        project_binding_count = _strict_uint(
            row.get("project_binding_count"), "project_binding_count"
        )
        if live_binding_count < 1 or (
            decoded["property_kind"] == "custom_attribute"
            and project_binding_count != live_binding_count
        ):
            raise PropertyCatalogValueUnavailable("definition_visibility_invalid")

        raw = row.get("definition_json")
        if not isinstance(raw, str) or not raw:
            raise PropertyCatalogValueUnavailable("definition_payload_invalid")
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise PropertyCatalogValueUnavailable("definition_payload_invalid") from exc
        if not isinstance(payload, dict) or canonical_json(payload) != raw:
            raise PropertyCatalogValueUnavailable("definition_payload_not_canonical")
        definition_sha = _sha256(row.get("definition_sha256"), "definition")
        if canonical_json_sha256(raw) != definition_sha:
            raise PropertyCatalogValueUnavailable("definition_digest_mismatch")
        if (
            payload.get("property_id") != params["catalog_property_id"]
            or payload.get("property_kind") != decoded["property_kind"]
            or payload.get("value_adapter") != PROPERTY_CATALOG_VALUE_ADAPTER
            or payload.get("name") != decoded["metric_name"]
        ):
            raise PropertyCatalogValueUnavailable("definition_identity_mismatch")
        details = payload.get("details")
        if (
            not isinstance(details, dict)
            or details.get("attribute_types_exact") is not True
        ):
            raise PropertyCatalogValueUnavailable("attribute_types_not_exact")
        raw_types = details.get("attribute_types")
        if not isinstance(raw_types, list) or not raw_types:
            raise PropertyCatalogValueUnavailable("attribute_types_invalid")
        if (
            any(
                type(value) is not str or value not in _ALL_ATTRIBUTE_TYPES
                for value in raw_types
            )
            or len(set(raw_types)) != len(raw_types)
            or raw_types != sorted(raw_types)
        ):
            raise PropertyCatalogValueUnavailable("attribute_types_invalid")
        return tuple(raw_types)

    def _decode_value(
        self,
        row: dict[str, Any],
        *,
        window_start: datetime,
        window_end: datetime,
    ) -> PropertyCatalogValue:
        attribute_type = row.get("attribute_type")
        if attribute_type not in _SELECTABLE_ATTRIBUTE_TYPES:
            raise PropertyCatalogValueUnavailable("value_type_invalid")
        rank = _strict_uint(row.get("attribute_type_rank"), "attribute_type_rank", 6)
        if rank != _ATTRIBUTE_TYPE_RANK[attribute_type]:
            raise PropertyCatalogValueUnavailable("value_type_invalid")
        if _strict_uint(row.get("value_json_variants"), "value_json_variants") != 1:
            raise PropertyCatalogValueUnavailable("value_conflict")
        if (
            _strict_uint(
                row.get("value_search_folded_variants"),
                "value_search_folded_variants",
            )
            != 1
        ):
            raise PropertyCatalogValueUnavailable("value_conflict")
        raw = row.get("value_json")
        search_text_folded = row.get("value_search_text_folded")
        if (
            not isinstance(raw, str)
            or not raw
            or len(raw.encode("utf-8")) > PROPERTY_CATALOG_VALUE_MAX_JSON_BYTES
            or not isinstance(search_text_folded, str)
            or len(search_text_folded.encode("utf-8"))
            > TYPED_STRING_SUGGESTION_MAX_UTF8_BYTES
        ):
            raise PropertyCatalogValueUnavailable("value_payload_invalid")
        try:
            value = json.loads(
                raw,
                parse_float=Decimal,
                parse_int=int,
                parse_constant=_reject_json_constant,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PropertyCatalogValueUnavailable("value_payload_invalid") from exc
        if value is None or isinstance(value, (list, dict)):
            raise PropertyCatalogValueUnavailable("value_payload_invalid")
        try:
            encoded = encode_catalog_scalar(value)
        except (TypeError, ValueError) as exc:
            raise PropertyCatalogValueUnavailable("value_payload_invalid") from exc
        fingerprint = _sha256(row.get("value_fingerprint"), "value_fingerprint")
        if (
            encoded.value_json != raw
            or search_text_folded != encoded.search_text.casefold()
            or encoded.fingerprint != fingerprint
            or (attribute_type != "array" and attribute_type != encoded.kind)
        ):
            raise PropertyCatalogValueUnavailable("value_payload_mismatch")
        first_seen = _row_datetime(row.get("first_seen"))
        last_seen = _row_datetime(row.get("last_seen"))
        if (
            first_seen > last_seen
            or first_seen >= window_end
            or last_seen < window_start
        ):
            raise PropertyCatalogValueUnavailable("value_time_invalid")
        return PropertyCatalogValue(
            value=value,
            attribute_type=attribute_type,
            scalar_kind=encoded.kind,
            value_fingerprint=fingerprint,
            value_search_text=encoded.search_text,
            first_seen=first_seen,
            last_seen=last_seen,
        )

    def _base_params(
        self,
        *,
        scope: dict[str, Any],
        query: dict[str, Any],
        decoded: dict[str, str],
        activation: PropertyCatalogActivation,
        window_start: datetime,
        window_end: datetime,
    ) -> dict[str, Any]:
        projects = tuple(scope["project_ids"])
        return {
            "catalog_organization_id": scope["organization_id"],
            "catalog_workspace_id": scope["workspace_id"],
            "catalog_project_ids": projects,
            # Keep the UUID-typed project key in PREWHERE. Casting the table
            # column to String prevents pruning on the value table's leading
            # (workspace, project) sort-key prefix.
            "catalog_project_uuid_ids": tuple(uuid.UUID(value) for value in projects)
            or (uuid.UUID(int=0),),
            # Workspace reads carry the complete authorized PG project set;
            # using an all-project sentinel would bypass that authorization
            # and make activation coverage impossible to prove exactly.
            "catalog_include_all_projects": 0,
            "catalog_epoch": activation.catalog_epoch,
            "catalog_revision": activation.catalog_revision,
            "catalog_lineage_anchor_revision": activation.lineage_anchor_revision,
            "catalog_property_id": query["property_id"],
            "catalog_source_kind": decoded["property_kind"],
            "catalog_attribute_key": decoded["metric_name"],
            "catalog_window_start_us": _unix_microseconds(window_start),
            "catalog_window_end_us": _unix_microseconds(window_end),
            "catalog_search": query["search"],
            "catalog_search_pattern": like_contains_pattern(query["search"]),
        }

    def _execute(
        self,
        sql: str,
        params: dict[str, Any],
        *,
        max_result_rows: int,
        budget: _ReadBudget,
    ) -> list[dict[str, Any]]:
        timeout_ms = budget.remaining_ms()
        budget.query_count += 1
        try:
            result = self._executor.execute(
                sql,
                params,
                timeout_ms=timeout_ms,
                settings={
                    **_READ_SETTINGS,
                    "max_result_rows": max_result_rows,
                    "max_execution_time": timeout_ms / 1_000,
                },
            )
        except PropertyCatalogValueUnavailable:
            raise
        except Exception as exc:
            raise PropertyCatalogValueUnavailable("query_failed") from exc
        budget.remaining_ms()
        rows = getattr(result, "data", None)
        if (
            not isinstance(rows, list)
            or len(rows) > max_result_rows
            or not all(isinstance(row, dict) for row in rows)
        ):
            raise PropertyCatalogValueUnavailable("invalid_query_result")
        return rows

    @staticmethod
    def _validate_scope(scope: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_property_catalog_scope(scope)
        if not normalized["principal_id"] or not normalized["auth_type"]:
            raise ValueError("authenticated principal scope is required")
        normalized["organization_id"] = _uuid(
            normalized["organization_id"], "organization_id"
        )
        normalized["workspace_id"] = _uuid(normalized["workspace_id"], "workspace_id")
        projects = tuple(
            _uuid(value, "project_id") for value in normalized["project_ids"]
        )
        if len(projects) > PROPERTY_CATALOG_VALUE_MAX_PROJECTS:
            if normalized.get("workspace_scope") is True:
                raise PropertyCatalogValueUnavailable("activation_scope_incomplete")
            raise ValueError(
                "at most "
                f"{PROPERTY_CATALOG_VALUE_MAX_PROJECTS} projects may be searched at once"
            )
        normalized["project_ids"] = projects
        return normalized

    @staticmethod
    def _validate_query(
        query: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, str]]:
        normalized = normalize_property_catalog_value_query(query)
        if not normalized["property_id"]:
            raise PropertyCatalogValueNotReady("property_id_required")
        if (
            len(normalized["property_id"].encode("utf-8"))
            > PROPERTY_CATALOG_VALUE_MAX_KEY_BYTES
        ):
            raise ValueError(
                "property_id exceeds "
                f"{PROPERTY_CATALOG_VALUE_MAX_KEY_BYTES} UTF-8 bytes"
            )
        if (
            len(normalized["search"].encode("utf-8"))
            > PROPERTY_CATALOG_VALUE_MAX_SEARCH_BYTES
        ):
            raise ValueError(
                f"search exceeds {PROPERTY_CATALOG_VALUE_MAX_SEARCH_BYTES} UTF-8 bytes"
            )
        if (
            normalized["attribute_type"]
            and normalized["attribute_type"] not in _ALL_ATTRIBUTE_TYPES
        ):
            raise ValueError("unsupported attribute_type")
        try:
            decoded = parse_property_registry_id(normalized["property_id"])
            validate_property_source_binding(decoded, normalized["source"])
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if decoded["property_kind"] not in {"custom_attribute", "system_attribute"}:
            raise PropertyCatalogValueNotReady("native_value_adapter")
        if (
            len(decoded["metric_name"].encode("utf-8"))
            > PROPERTY_CATALOG_VALUE_MAX_KEY_BYTES
        ):
            raise ValueError("attribute key exceeds 4096 UTF-8 bytes")
        return normalized, decoded


__all__ = [
    "PROPERTY_CATALOG_VALUE_ADAPTER",
    "PropertyCatalogValue",
    "PropertyCatalogValueNotReady",
    "PropertyCatalogValuePage",
    "PropertyCatalogValueReader",
    "PropertyCatalogValueUnavailable",
]
