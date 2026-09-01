"""Single-table reader for activated unified property-definition revisions."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol

from tracer.services.clickhouse.v2.property_catalog.activation import (
    BuildPlanSourceScope,
    RevisionBuildPlan,
)
from tracer.services.clickhouse.v2.property_catalog.activation_control import (
    ActivationControlSelector,
    ActivationControlTarget,
    ActivationControlUnavailable,
)
from tracer.services.clickhouse.v2.property_catalog.codec import (
    MAX_DEFINITION_JSON_BYTES,
    canonical_json,
    canonical_json_sha256,
    casefold_text,
    combine_search_text,
    like_contains_pattern,
)
from tracer.services.clickhouse.v2.property_catalog.connection import (
    validate_property_catalog_database,
)
from tracer.services.clickhouse.v2.property_catalog.cursor import (
    PropertyCatalogCursor,
    decode_property_catalog_cursor,
    encode_property_catalog_cursor,
    normalize_property_catalog_query,
    normalize_property_catalog_scope,
)
from tracer.services.clickhouse.v2.property_catalog.database import (
    is_production_property_catalog_database,
)
from tracer.services.clickhouse.v2.property_catalog.runtime_limits import RUNTIME_LIMITS
from tracer.utils.property_registry import (
    normalize_custom_attribute_source,
    parse_property_registry_id,
)

PROPERTY_CATALOG_MAX_PROJECTS = RUNTIME_LIMITS.max_projects
PROPERTY_CATALOG_MAX_PAGE_SIZE = RUNTIME_LIMITS.max_page_size
PROPERTY_CATALOG_MAX_SEARCH_BYTES = RUNTIME_LIMITS.max_search_bytes
PROPERTY_CATALOG_MAX_DEFINITION_JSON_BYTES = MAX_DEFINITION_JSON_BYTES
PROPERTY_CATALOG_QUERY_WALL_MS = RUNTIME_LIMITS.query_wall_ms
PROPERTY_CATALOG_REQUIRED_PROJECTION_VERSION = 1
PROPERTY_CATALOG_MAX_LINEAGE_REVISIONS = RUNTIME_LIMITS.max_lineage_revisions
PROPERTY_CATALOG_LIFECYCLE_MODES = frozenset(
    {"initial_backfill", "incremental", "full_repair"}
)
PROPERTY_CATALOG_NOT_READY_REASONS = frozenset(
    {
        "activation_missing",
        "activation_not_active",
        "projection_incompatible",
        "activation_scope_incomplete",
    }
)

_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_API_DETAIL_KEYS = frozenset(
    {
        "allowed_aggregations",
        "attribute_types",
        "attribute_types_exact",
        "choice_options",
        "choices",
        "data_type",
        "eval_template_id",
        "unit",
    }
)

_READ_SETTINGS = RUNTIME_LIMITS.clickhouse_read_settings


class _Result(Protocol):
    data: list[dict[str, Any]]


class PropertyCatalogQueryExecutor(Protocol):
    def execute(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout_ms: int,
        settings: dict[str, Any],
    ) -> _Result: ...


class PropertyCatalogUnavailable(RuntimeError):
    """Fail-closed, sanitized property-catalog availability signal."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__("The property catalog is temporarily unavailable.")


def is_property_catalog_not_ready_error(error: BaseException) -> bool:
    """Return whether a sanitized availability failure permits legacy fallback."""

    return (
        isinstance(error, PropertyCatalogUnavailable)
        and error.reason in PROPERTY_CATALOG_NOT_READY_REASONS
    )


@dataclass(frozen=True, slots=True)
class PropertyCatalogActivation:
    catalog_epoch: int
    catalog_revision: int
    build_token: str
    projection_version: int
    lifecycle_mode: str
    lineage_anchor_revision: int
    activation_sequence: int
    source_manifest_sha256: str
    activation_sha256: str
    source_scope: BuildPlanSourceScope | None = None
    retained_span_since_us: int | None = None
    retained_span_until_us: int | None = None


@dataclass(frozen=True, slots=True)
class PropertyCatalogPage:
    metrics: tuple[dict[str, Any], ...]
    has_more: bool
    next_cursor: str | None
    catalog_epoch: int
    catalog_revision: int
    activation_fingerprint: str
    total: None = None
    total_is_exact: bool = False
    category_counts: dict[str, int] | None = None
    category_counts_exact: bool = False


@dataclass(slots=True)
class _ReadBudget:
    deadline: float

    @classmethod
    def start(cls) -> _ReadBudget:
        return cls(monotonic() + PROPERTY_CATALOG_QUERY_WALL_MS / 1_000)

    def remaining_ms(self) -> int:
        remaining = int((self.deadline - monotonic()) * 1_000)
        if remaining < 1:
            raise PropertyCatalogUnavailable("deadline_exceeded")
        return min(remaining, PROPERTY_CATALOG_QUERY_WALL_MS)


_ACTIVATION_SQL = """
WITH versioned AS
(
    SELECT
        *,
        max(_version) OVER (
            PARTITION BY organization_id, workspace_id,
                         catalog_epoch, catalog_revision, build_token
        ) AS latest_version
    FROM __CATALOG_DATABASE__.property_catalog_activations
    PREWHERE organization_id = %(catalog_organization_id)s
      AND workspace_id = %(catalog_workspace_id)s
), activation_states AS
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
        argMax(versioned_rows.activation_sequence, versioned_rows._version)
            AS activation_sequence,
        argMax(versioned_rows.source_manifest_sha256, versioned_rows._version)
            AS source_manifest_sha256,
        argMax(versioned_rows.activation_sha256, versioned_rows._version)
            AS activation_sha256,
        argMax(versioned_rows.status, versioned_rows._version) AS status,
        argMax(versioned_rows.qualified_at, versioned_rows._version) AS qualified_at,
        max(versioned_rows._version) AS state_version,
        uniqExactIf(
            tuple(
                versioned_rows.projection_version,
                versioned_rows.lifecycle_mode,
                versioned_rows.lineage_anchor_revision,
                versioned_rows.activation_sequence,
                versioned_rows.source_manifest_sha256,
                versioned_rows.activation_sha256,
                versioned_rows.status,
                versioned_rows.qualified_at
            ),
            versioned_rows._version = versioned_rows.latest_version
        ) AS latest_state_variants,
        countIf(
            versioned_rows._version = versioned_rows.latest_version
            AND versioned_rows.status = 'active'
            AND versioned_rows.qualified_at IS NOT NULL
        ) AS latest_active_states
    FROM versioned AS versioned_rows
    GROUP BY
        versioned_rows.catalog_epoch,
        versioned_rows.catalog_revision,
        versioned_rows.build_token
), active_candidates AS
(
    SELECT *
    FROM activation_states
    WHERE latest_active_states > 0
), active_revision_counts AS
(
    SELECT catalog_epoch, catalog_revision, count() AS active_builds
    FROM active_candidates
    GROUP BY catalog_epoch, catalog_revision
), reservation_versioned AS
(
    SELECT
        *,
        max(_version) OVER (
            PARTITION BY organization_id, workspace_id,
                         catalog_epoch, catalog_revision, build_token,
                         source_adapter, producer_stream_id, envelope_version
        ) AS latest_version
    FROM __CATALOG_DATABASE__.property_catalog_source_streams
    PREWHERE organization_id = %(catalog_organization_id)s
      AND workspace_id = %(catalog_workspace_id)s
    WHERE source_adapter = 'system_manifest'
      AND producer_stream_id = build_token
      AND envelope_version = 0
), reservation_states AS
(
    SELECT
        reservation_rows.catalog_epoch,
        reservation_rows.catalog_revision,
        reservation_rows.build_token,
        argMax(reservation_rows.projection_version, reservation_rows._version)
            AS reservation_projection_version,
        argMax(reservation_rows.build_plan_json, reservation_rows._version)
            AS build_plan_json,
        argMax(reservation_rows.build_lease_sha256, reservation_rows._version)
            AS build_lease_sha256,
        uniqExactIf(
            tuple(
                reservation_rows.projection_version,
                reservation_rows.build_plan_json,
                reservation_rows.build_lease_sha256
            ),
            reservation_rows._version = reservation_rows.latest_version
        ) AS latest_reservation_variants
    FROM reservation_versioned AS reservation_rows
    GROUP BY
        reservation_rows.catalog_epoch,
        reservation_rows.catalog_revision,
        reservation_rows.build_token
)
SELECT
    active_candidates.catalog_epoch AS catalog_epoch,
    active_candidates.catalog_revision AS catalog_revision,
    active_candidates.build_token AS build_token,
    active_candidates.projection_version AS projection_version,
    active_candidates.lifecycle_mode AS lifecycle_mode,
    active_candidates.lineage_anchor_revision AS lineage_anchor_revision,
    active_candidates.activation_sequence AS activation_sequence,
    active_candidates.source_manifest_sha256 AS source_manifest_sha256,
    active_candidates.activation_sha256 AS activation_sha256,
    active_candidates.status AS status,
    active_candidates.qualified_at AS qualified_at,
    active_candidates.state_version AS state_version,
    active_candidates.latest_state_variants AS latest_state_variants,
    active_candidates.latest_active_states AS latest_active_states,
    active_revision_counts.active_builds AS active_builds,
    reservation_states.reservation_projection_version
        AS reservation_projection_version,
    reservation_states.build_plan_json AS build_plan_json,
    reservation_states.build_lease_sha256 AS build_lease_sha256,
    reservation_states.latest_reservation_variants
        AS latest_reservation_variants,
    anchor_candidates.catalog_revision AS anchor_catalog_revision,
    anchor_candidates.build_token AS anchor_build_token,
    anchor_candidates.projection_version AS anchor_projection_version,
    anchor_candidates.lifecycle_mode AS anchor_lifecycle_mode,
    anchor_candidates.lineage_anchor_revision AS anchor_lineage_anchor_revision,
    anchor_candidates.latest_state_variants AS anchor_latest_state_variants,
    anchor_candidates.latest_active_states AS anchor_latest_active_states,
    anchor_revision_counts.active_builds AS anchor_active_builds,
    anchor_reservations.reservation_projection_version
        AS anchor_reservation_projection_version,
    anchor_reservations.build_plan_json AS anchor_build_plan_json,
    anchor_reservations.build_lease_sha256 AS anchor_build_lease_sha256,
    anchor_reservations.latest_reservation_variants
        AS anchor_latest_reservation_variants
FROM active_candidates
INNER JOIN active_revision_counts
    ON active_candidates.catalog_epoch = active_revision_counts.catalog_epoch
   AND active_candidates.catalog_revision = active_revision_counts.catalog_revision
LEFT JOIN reservation_states
    ON active_candidates.catalog_epoch = reservation_states.catalog_epoch
   AND active_candidates.catalog_revision = reservation_states.catalog_revision
   AND active_candidates.build_token = reservation_states.build_token
LEFT JOIN active_candidates AS anchor_candidates
    ON active_candidates.catalog_epoch = anchor_candidates.catalog_epoch
   AND active_candidates.lineage_anchor_revision
       = anchor_candidates.catalog_revision
LEFT JOIN active_revision_counts AS anchor_revision_counts
    ON anchor_candidates.catalog_epoch = anchor_revision_counts.catalog_epoch
   AND anchor_candidates.catalog_revision
       = anchor_revision_counts.catalog_revision
LEFT JOIN reservation_states AS anchor_reservations
    ON anchor_candidates.catalog_epoch = anchor_reservations.catalog_epoch
   AND anchor_candidates.catalog_revision
       = anchor_reservations.catalog_revision
   AND anchor_candidates.build_token = anchor_reservations.build_token
WHERE (
    %(catalog_exact_activation)s = 0
    OR (
        active_candidates.catalog_epoch = %(catalog_epoch)s
        AND active_candidates.catalog_revision = %(catalog_revision)s
    )
)
ORDER BY
    active_candidates.catalog_epoch DESC,
    active_candidates.catalog_revision DESC,
    active_candidates.activation_sequence DESC
LIMIT 2
"""


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
      -- Visibility is authorization-owned and uses only narrow scalar
      -- columns. Apply it in PREWHERE so ClickHouse does not read the large
      -- definition_json payload for every unrelated project in a workspace.
      AND (
        rows.visibility_scope = 'always'
        OR (
            rows.visibility_scope = 'workspace_default'
            AND %(catalog_include_workspace_default)s = 1
        )
        OR (
            rows.visibility_scope = 'project'
            AND (
                %(catalog_include_all_projects)s = 1
                OR toString(rows.visibility_id) IN %(catalog_project_ids)s
            )
        )
        OR (
            rows.visibility_scope = 'agent_definition'
            AND %(catalog_agent_definition_id)s != ''
            AND toString(rows.visibility_id) = %(catalog_agent_definition_id)s
        )
        OR (
            rows.visibility_scope = 'dataset'
            AND (
                %(catalog_dataset_id)s = ''
                OR toString(rows.visibility_id) = %(catalog_dataset_id)s
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
        any(binding.property_id) AS property_id,
        any(binding.property_kind) AS property_kind,
        any(binding.category) AS category,
        any(binding.category_rank) AS category_rank,
        any(binding.source_rank) AS source_rank,
        any(binding.source_adapter) AS source_adapter,
        any(binding.definition_source) AS definition_source,
        any(binding.primary_source) AS primary_source,
        any(binding.primary_source_folded) AS primary_source_folded,
        any(binding.source_tokens) AS source_tokens,
        any(binding.value_adapter) AS value_adapter,
        any(binding.name) AS name,
        any(binding.display_name) AS display_name,
        any(binding.sort_name_folded) AS sort_name_folded,
        any(binding.search_text_folded) AS search_text_folded,
        any(binding.role) AS role,
        any(binding.definition_sha256) AS definition_sha256,
        any(binding.catalog_revision) AS payload_catalog_revision,
        any(toString(binding.build_token)) AS payload_build_token,
        any(binding.source_version) AS payload_source_version,
        any(binding.is_deleted) AS is_deleted,
        uniqExact(binding.state_sha256) AS state_variants,
        -- definition_sha256 binds the canonical definition_json without
        -- repeatedly hashing the potentially large JSON payload in ClickHouse.
        -- Published page rows still verify the digest against the JSON bytes.
        uniqExact(tuple(
            binding.property_id,
            binding.property_kind,
            binding.category,
            binding.category_rank,
            binding.source_rank,
            binding.source_adapter,
            binding.definition_source,
            binding.primary_source,
            binding.primary_source_folded,
            binding.source_tokens,
            binding.value_adapter,
            binding.name,
            binding.display_name,
            binding.sort_name_folded,
            binding.search_text_folded,
            binding.role,
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
        anyIf(binding.category, binding.binding_is_live) AS category,
        anyIf(binding.category_rank, binding.binding_is_live) AS category_rank,
        anyIf(binding.source_rank, binding.binding_is_live) AS source_rank,
        anyIf(binding.source_adapter, binding.binding_is_live) AS source_adapter,
        anyIf(binding.definition_source, binding.binding_is_live)
            AS definition_source,
        anyIf(binding.primary_source, binding.binding_is_live) AS primary_source,
        anyIf(binding.primary_source_folded, binding.binding_is_live)
            AS primary_source_folded,
        anyIf(binding.source_tokens, binding.binding_is_live) AS source_tokens,
        anyIf(binding.value_adapter, binding.binding_is_live) AS value_adapter,
        anyIf(binding.name, binding.binding_is_live) AS name,
        anyIf(binding.display_name, binding.binding_is_live) AS display_name,
        anyIf(binding.sort_name_folded, binding.binding_is_live)
            AS sort_name_folded,
        anyIf(binding.search_text_folded, binding.binding_is_live)
            AS search_text_folded,
        anyIf(binding.role, binding.binding_is_live) AS role,
        anyIf(binding.definition_sha256, binding.binding_is_live)
            AS definition_sha256,
        anyIf(binding.binding_id, binding.binding_is_live) AS payload_binding_id,
        anyIf(binding.payload_catalog_revision, binding.binding_is_live)
            AS payload_catalog_revision,
        anyIf(binding.payload_build_token, binding.binding_is_live)
            AS payload_build_token,
        anyIf(binding.payload_source_version, binding.binding_is_live)
            AS payload_source_version,
        countIf(binding.binding_is_conflicted) AS binding_conflicts,
        countIf(binding.binding_is_live) AS live_binding_count,
        -- The canonical JSON digest is the definition payload identity; keep
        -- conflict proof narrow while the API re-verifies every returned JSON.
        uniqExactIf(tuple(
            binding.property_kind,
            binding.category,
            binding.category_rank,
            binding.source_rank,
            binding.source_adapter,
            binding.definition_source,
            binding.primary_source,
            binding.primary_source_folded,
            binding.source_tokens,
            binding.value_adapter,
            binding.name,
            binding.display_name,
            binding.sort_name_folded,
            binding.search_text_folded,
            binding.role,
            binding.definition_sha256
        ), binding.binding_is_live) AS definition_variants
    FROM classified_bindings AS binding
    GROUP BY binding.property_id
)
"""


_PROPERTY_SUMMARY_CTE = """
, property_summary AS
(
    SELECT
        *,
        live_binding_count > 0
        AND definition_variants = 1
        AND (
            %(catalog_source)s = ''
            OR primary_source = %(catalog_source)s
            OR has(source_tokens, %(catalog_source)s)
            OR primary_source IN ('all', 'both')
            OR has(source_tokens, 'all')
            OR has(source_tokens, 'both')
            OR (
                category = 'custom_attribute'
                AND %(catalog_custom_attribute_source)s = 'traces'
                AND (
                    primary_source = 'traces'
                    OR has(source_tokens, 'traces')
                )
            )
            OR (
                %(catalog_source)s = 'spans'
                AND (
                    primary_source = 'traces'
                    OR has(source_tokens, 'traces')
                )
            )
            OR (
                %(catalog_source)s = 'voice_calls'
                AND category IN (
                    'eval_metric',
                    'annotation_metric',
                    'custom_attribute'
                )
                AND (
                    primary_source = 'traces'
                    OR has(source_tokens, 'traces')
                )
            )
        )
        AND (
            %(catalog_property_kind)s = ''
            OR property_kind = %(catalog_property_kind)s
        )
        AND (
            %(catalog_role)s = ''
            OR role = %(catalog_role)s
        )
        AND (
            property_kind NOT IN ('eval_config', 'eval_template')
            OR source_adapter = 'simulation_eval_config'
            OR (
                %(catalog_per_eval_config)s = 1
                AND property_kind = 'eval_config'
            )
            OR (
                %(catalog_per_eval_config)s = 0
                AND property_kind = 'eval_template'
            )
        )
        AND (
            %(catalog_search)s = ''
            OR search_text_folded LIKE %(catalog_search_pattern)s
        ) AS catalog_visible
    FROM resolved_properties
)
"""


_CONFLICT_SQL_SUFFIX = (
    _PROPERTY_SUMMARY_CTE
    + """
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
    sum(binding_conflicts) AS binding_conflicts,
    countIf(live_binding_count > 0 AND definition_variants != 1)
        AS definition_conflicts,
    countIf(catalog_visible) AS catalog_count_all,
    countIf(catalog_visible AND category = 'system_metric')
        AS catalog_count_system_metric,
    countIf(catalog_visible AND category = 'eval_metric')
        AS catalog_count_eval_metric,
    countIf(catalog_visible AND category = 'annotation_metric')
        AS catalog_count_annotation_metric,
    countIf(catalog_visible AND category = 'custom_attribute')
        AS catalog_count_custom_attribute,
    countIf(catalog_visible AND category = 'custom_column')
        AS catalog_count_custom_column
FROM property_summary
"""
)


_PAGE_SQL_SUFFIX = (
    _PROPERTY_SUMMARY_CTE
    + """
, catalog_rows_with_sentinel AS
(
    SELECT
        property_id,
        property_kind,
        category,
        category_rank,
        source_rank,
        definition_source,
        primary_source,
        primary_source_folded,
        source_tokens,
        value_adapter,
        name,
        display_name,
        sort_name_folded,
        search_text_folded,
        role,
        definition_sha256,
        payload_binding_id,
        payload_catalog_revision,
        payload_build_token,
        payload_source_version,
        binding_conflicts,
        live_binding_count,
        definition_variants,
        catalog_visible,
        toUInt8(0) AS catalog_metadata_only
    FROM property_summary

    UNION ALL

    SELECT
        '' AS property_id,
        'system_attribute' AS property_kind,
        'system_metric' AS category,
        toUInt8(0) AS category_rank,
        toUInt16(0) AS source_rank,
        '' AS definition_source,
        '' AS primary_source,
        '' AS primary_source_folded,
        CAST([], 'Array(String)') AS source_tokens,
        '' AS value_adapter,
        '' AS name,
        '' AS display_name,
        '' AS sort_name_folded,
        '' AS search_text_folded,
        'dimension' AS role,
        repeat('0', 64) AS definition_sha256,
        repeat('0', 64) AS payload_binding_id,
        toUInt64(0) AS payload_catalog_revision,
        '' AS payload_build_token,
        toUInt64(0) AS payload_source_version,
        toUInt64(0) AS binding_conflicts,
        toUInt64(0) AS live_binding_count,
        toUInt64(0) AS definition_variants,
        toUInt8(0) AS catalog_visible,
        toUInt8(1) AS catalog_metadata_only
), catalog_checked_rows AS
(
    SELECT
        *,
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
        sum(binding_conflicts) OVER () AS catalog_binding_conflicts,
        countIf(live_binding_count > 0 AND definition_variants != 1) OVER ()
            AS catalog_definition_conflicts,
        countIf(catalog_visible) OVER () AS catalog_count_all,
        countIf(catalog_visible AND category = 'system_metric') OVER ()
            AS catalog_count_system_metric,
        countIf(catalog_visible AND category = 'eval_metric') OVER ()
            AS catalog_count_eval_metric,
        countIf(catalog_visible AND category = 'annotation_metric') OVER ()
            AS catalog_count_annotation_metric,
        countIf(catalog_visible AND category = 'custom_attribute') OVER ()
            AS catalog_count_custom_attribute,
        countIf(catalog_visible AND category = 'custom_column') OVER ()
            AS catalog_count_custom_column
    FROM catalog_rows_with_sentinel
)
SELECT
    property_id,
    property_kind,
    category,
    category_rank,
    source_rank,
    definition_source,
    primary_source,
    primary_source_folded,
    source_tokens,
    value_adapter,
    name,
    display_name,
    sort_name_folded,
    search_text_folded,
    role,
    definition_sha256,
    payload_binding_id,
    payload_catalog_revision,
    payload_build_token,
    payload_source_version,
    catalog_metadata_only,
    activation_state_conflicts,
    activation_lineage_conflicts,
    activation_projection_conflicts,
    activation_anchor_conflicts,
    catalog_binding_conflicts AS binding_conflicts,
    catalog_definition_conflicts AS definition_conflicts,
    catalog_count_all,
    catalog_count_system_metric,
    catalog_count_eval_metric,
    catalog_count_annotation_metric,
    catalog_count_custom_attribute,
    catalog_count_custom_column
FROM catalog_checked_rows
WHERE catalog_metadata_only = 1
   OR (
       catalog_visible
       AND (%(catalog_category)s = '' OR category = %(catalog_category)s)
       AND tuple(
           category_rank,
           source_rank,
           primary_source_folded,
           sort_name_folded,
           name,
           property_id
       ) > tuple(
           %(catalog_after_category_rank)s,
           %(catalog_after_source_rank)s,
           %(catalog_after_primary_source)s,
           %(catalog_after_sort_name)s,
           %(catalog_after_name)s,
           %(catalog_after_property_id)s
       )
   )
ORDER BY
    catalog_metadata_only DESC,
    category_rank ASC,
    source_rank ASC,
    primary_source_folded ASC,
    sort_name_folded ASC,
    name ASC,
    property_id ASC
LIMIT %(catalog_result_limit)s
"""
)


_PAYLOAD_SQL = """
SELECT
    property_id,
    toString(binding_id) AS payload_binding_id,
    catalog_revision AS payload_catalog_revision,
    toString(build_token) AS payload_build_token,
    source_version AS payload_source_version,
    any(definition_json) AS payload_definition_json,
    any(definition_sha256) AS payload_definition_sha256,
    uniqExact(tuple(
        property_id,
        definition_json,
        definition_sha256,
        state_sha256,
        is_deleted
    )) AS payload_variants,
    max(is_deleted) AS payload_deleted
FROM __CATALOG_DATABASE__.property_definition_catalog
PREWHERE organization_id = %(catalog_organization_id)s
  AND workspace_id = %(catalog_workspace_id)s
  AND catalog_epoch = %(catalog_epoch)s
  AND projection_version = %(catalog_projection_version)s
  -- The property bloom filter and exact immutable binding tuple keep the
  -- large JSON column bounded by the requested page instead of reading it
  -- while resolving every visible definition and category count.
  AND property_id IN %(catalog_payload_property_ids)s
  AND tuple(
      property_id,
      toString(binding_id),
      catalog_revision,
      toString(build_token),
      source_version
  ) IN %(catalog_payload_keys)s
GROUP BY
    property_id,
    binding_id,
    catalog_revision,
    build_token,
    source_version
"""


def _database(value: str) -> str:
    return validate_property_catalog_database(value)


def _qualified_activation_sql(database: str) -> str:
    database = _database(database)
    return _ACTIVATION_SQL.replace(
        "__CATALOG_DATABASE__.property_catalog_activations",
        f"`{database}`.`property_catalog_activations`",
    ).replace(
        "__CATALOG_DATABASE__.property_catalog_source_streams",
        f"`{database}`.`property_catalog_source_streams`",
    )


def property_catalog_activation_sql(database: str) -> str:
    """Return the one immutable activation/reservation proof query."""

    return _qualified_activation_sql(database)


def _uuid(value: Any, label: str, *, allow_empty: bool = False) -> str:
    if allow_empty and value in (None, ""):
        return ""
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{label} must be a UUID") from exc


def _strict_uint(value: Any, label: str, maximum: int | None = None) -> int:
    if type(value) is not int or value < 0 or (maximum is not None and value > maximum):
        raise PropertyCatalogUnavailable(f"invalid_{label}")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise PropertyCatalogUnavailable(f"invalid_{label}")
    return value


def verify_property_catalog_activation(
    rows: list[dict[str, Any]],
    *,
    scope: dict[str, Any],
    cursor_present: bool,
    unavailable_type: type[PropertyCatalogUnavailable] = PropertyCatalogUnavailable,
) -> PropertyCatalogActivation:
    """Verify one active revision and its immutable reservation/build plan."""

    def unavailable(reason: str) -> None:
        raise unavailable_type(reason)

    def strict_uint(value: Any, label: str, maximum: int | None = None) -> int:
        if (
            type(value) is not int
            or value < 0
            or (maximum is not None and value > maximum)
        ):
            unavailable(f"invalid_{label}")
        return value

    def strict_uuid(value: Any, label: str) -> str:
        try:
            return str(uuid.UUID(str(value)))
        except (TypeError, ValueError, AttributeError):
            unavailable(f"invalid_{label}")

    def strict_sha256(value: Any, label: str) -> str:
        if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
            unavailable(f"invalid_{label}")
        return value

    if not rows:
        unavailable("activation_mismatch" if cursor_present else "activation_missing")
    row = rows[0]
    catalog_epoch = strict_uint(row.get("catalog_epoch"), "epoch", 65_535)
    sequence = strict_uint(row.get("activation_sequence"), "activation_sequence")
    if sequence < 1:
        unavailable("activation_invalid")
    if (
        len(rows) > 1
        and strict_uint(rows[1].get("catalog_epoch"), "epoch", 65_535) == catalog_epoch
        and strict_uint(rows[1].get("activation_sequence"), "activation_sequence")
        == sequence
    ):
        unavailable("activation_sequence_conflict")
    if strict_uint(row.get("active_builds"), "active_builds") != 1:
        unavailable("activation_conflict")
    if strict_uint(row.get("latest_state_variants"), "activation_variants") != 1:
        unavailable("activation_conflict")
    if row.get("status") != "active" or row.get("qualified_at") is None:
        unavailable("activation_not_active")
    if strict_uint(row.get("state_version"), "activation_state_version") < 1:
        unavailable("activation_invalid")

    projection_version = strict_uint(
        row.get("projection_version"), "projection_version", 65_535
    )
    catalog_revision = strict_uint(row.get("catalog_revision"), "revision")
    if catalog_epoch < 1 or catalog_revision < 1:
        unavailable("activation_invalid")
    lifecycle_mode = row.get("lifecycle_mode")
    if lifecycle_mode not in PROPERTY_CATALOG_LIFECYCLE_MODES:
        unavailable("activation_lifecycle_invalid")
    lineage_anchor_revision = strict_uint(
        row.get("lineage_anchor_revision"), "lineage_anchor_revision"
    )
    if (
        lineage_anchor_revision < 1
        or lineage_anchor_revision > catalog_revision
        or catalog_revision - lineage_anchor_revision
        > PROPERTY_CATALOG_MAX_LINEAGE_REVISIONS
        or (
            lineage_anchor_revision == catalog_revision
            and lifecycle_mode not in {"initial_backfill", "full_repair"}
        )
        or (
            lineage_anchor_revision < catalog_revision
            and lifecycle_mode != "incremental"
        )
    ):
        unavailable("activation_lineage_invalid")

    build_token = strict_uuid(row.get("build_token"), "build_token")
    source_manifest_sha256 = strict_sha256(
        row.get("source_manifest_sha256"), "source_manifest"
    )
    activation_sha256 = strict_sha256(row.get("activation_sha256"), "activation")
    if (
        strict_uint(
            row.get("latest_reservation_variants"),
            "activation_reservation_variants",
        )
        != 1
    ):
        unavailable("activation_scope_conflict")
    if (
        strict_uint(
            row.get("reservation_projection_version"),
            "activation_reservation_projection_version",
            65_535,
        )
        != projection_version
    ):
        unavailable("activation_scope_invalid")
    build_lease_sha256 = strict_sha256(row.get("build_lease_sha256"), "build_lease")
    try:
        build_plan = RevisionBuildPlan.from_json(row.get("build_plan_json"))
    except (TypeError, ValueError) as exc:
        raise unavailable_type("activation_scope_invalid") from exc
    if (
        build_plan.organization_id != scope["organization_id"]
        or build_plan.workspace_id != scope["workspace_id"]
        or build_plan.catalog_epoch != catalog_epoch
        or build_plan.catalog_revision != catalog_revision
        or build_plan.build_token != build_token
        or build_plan.projection_version != projection_version
        or build_plan.sha256 != build_lease_sha256
    ):
        unavailable("activation_scope_invalid")
    retained_span_since_us = build_plan.source_scope.span_since_us
    retained_span_until_us = build_plan.source_scope.span_until_us
    if lineage_anchor_revision < catalog_revision:
        if (
            strict_uint(row.get("anchor_catalog_revision"), "anchor_revision")
            != lineage_anchor_revision
            or strict_uint(
                row.get("anchor_projection_version"),
                "anchor_projection_version",
                65_535,
            )
            != projection_version
            or row.get("anchor_lifecycle_mode")
            not in {"initial_backfill", "full_repair"}
            or strict_uint(
                row.get("anchor_lineage_anchor_revision"),
                "anchor_lineage_anchor_revision",
            )
            != lineage_anchor_revision
            or strict_uint(
                row.get("anchor_latest_state_variants"),
                "anchor_state_variants",
            )
            != 1
            or strict_uint(
                row.get("anchor_latest_active_states"),
                "anchor_active_states",
            )
            < 1
            or strict_uint(row.get("anchor_active_builds"), "anchor_active_builds") != 1
            or strict_uint(
                row.get("anchor_latest_reservation_variants"),
                "anchor_reservation_variants",
            )
            != 1
            or strict_uint(
                row.get("anchor_reservation_projection_version"),
                "anchor_reservation_projection_version",
                65_535,
            )
            != projection_version
        ):
            unavailable("activation_lineage_scope_invalid")
        anchor_build_token = strict_uuid(
            row.get("anchor_build_token"), "anchor_build_token"
        )
        anchor_build_lease_sha256 = strict_sha256(
            row.get("anchor_build_lease_sha256"), "anchor_build_lease"
        )
        try:
            anchor_build_plan = RevisionBuildPlan.from_json(
                row.get("anchor_build_plan_json")
            )
        except (TypeError, ValueError) as exc:
            raise unavailable_type("activation_lineage_scope_invalid") from exc
        if (
            anchor_build_plan.organization_id != scope["organization_id"]
            or anchor_build_plan.workspace_id != scope["workspace_id"]
            or anchor_build_plan.catalog_epoch != catalog_epoch
            or anchor_build_plan.catalog_revision != lineage_anchor_revision
            or anchor_build_plan.build_token != anchor_build_token
            or anchor_build_plan.projection_version != projection_version
            or anchor_build_plan.sha256 != anchor_build_lease_sha256
        ):
            unavailable("activation_lineage_scope_invalid")
        retained_span_since_us = min(
            retained_span_since_us,
            anchor_build_plan.source_scope.span_since_us,
        )
        retained_span_until_us = max(
            retained_span_until_us,
            anchor_build_plan.source_scope.span_until_us,
        )
    if projection_version < PROPERTY_CATALOG_REQUIRED_PROJECTION_VERSION:
        unavailable("projection_incompatible")
    return PropertyCatalogActivation(
        catalog_epoch=catalog_epoch,
        catalog_revision=catalog_revision,
        build_token=build_token,
        projection_version=projection_version,
        lifecycle_mode=lifecycle_mode,
        lineage_anchor_revision=lineage_anchor_revision,
        activation_sequence=sequence,
        source_manifest_sha256=source_manifest_sha256,
        activation_sha256=activation_sha256,
        source_scope=build_plan.source_scope,
        retained_span_since_us=retained_span_since_us,
        retained_span_until_us=retained_span_until_us,
    )


def require_property_catalog_activation_coverage(
    *,
    scope: dict[str, Any],
    activation: PropertyCatalogActivation,
    unavailable_type: type[PropertyCatalogUnavailable] = PropertyCatalogUnavailable,
    requested_span_since_us: int | None = None,
    requested_span_until_us: int | None = None,
) -> None:
    """Require the immutable build to cover the authorized project/time scope."""

    source_scope = activation.source_scope
    if source_scope is None:
        raise unavailable_type("activation_scope_invalid")
    requested_projects = frozenset(scope["project_ids"])
    covered_projects = frozenset(source_scope.project_ids)
    # The API materializes the complete *live* authorized project set. A full
    # repair deliberately freezes soft-deleted projects too so it can publish
    # their tombstones, therefore its immutable source scope may be a strict
    # superset. The query predicate still contains only requested_projects, so
    # accepting that proven superset never widens visibility. A newly-created
    # live project remains fail-closed because it is absent from covered_projects.
    if not requested_projects or not requested_projects.issubset(covered_projects):
        raise unavailable_type("activation_scope_incomplete")

    if (requested_span_since_us is None) != (requested_span_until_us is None):
        raise unavailable_type("activation_scope_invalid")
    if requested_span_since_us is None:
        return
    if (
        type(requested_span_since_us) is not int
        or type(requested_span_until_us) is not int
        or requested_span_since_us < 0
        or requested_span_since_us >= requested_span_until_us
    ):
        raise unavailable_type("activation_scope_invalid")
    if (
        requested_span_since_us < source_scope.span_since_us
        or requested_span_until_us > source_scope.span_until_us
    ):
        raise unavailable_type("activation_scope_incomplete")


def _control_target_matches_activation(
    target: ActivationControlTarget,
    activation: PropertyCatalogActivation,
) -> bool:
    return (
        target.catalog_epoch == activation.catalog_epoch
        and target.catalog_revision == activation.catalog_revision
        and target.build_token == activation.build_token
        and target.projection_version == activation.projection_version
        and target.activation_sha256 == activation.activation_sha256
    )


def _custom_attribute_transport_source(source: str) -> str:
    try:
        return normalize_custom_attribute_source(source, allow_blank=True)
    except ValueError:
        # A generic mixed-family query may legitimately target a source that
        # has no custom attributes. The explicit custom-attribute contract is
        # rejected by ``_validate_query`` before any ClickHouse read.
        return ""


def _query_params(
    *,
    scope: dict[str, Any],
    query: dict[str, Any],
    activation: PropertyCatalogActivation,
    after: tuple[int, int, str, str, str, str] | None,
    page_size: int,
) -> dict[str, Any]:
    projects = tuple(scope["project_ids"])
    workspace_scope = scope.get("workspace_scope") is True
    order = after or (0, 0, "", "", "", "")
    return {
        "catalog_organization_id": scope["organization_id"],
        "catalog_workspace_id": scope["workspace_id"],
        "catalog_epoch": activation.catalog_epoch,
        "catalog_revision": activation.catalog_revision,
        "catalog_lineage_anchor_revision": activation.lineage_anchor_revision,
        # An explicit project scope is a strict visibility boundary.  Keeping
        # workspace defaults in a project picker can surface labels/templates
        # that have no relationship to the selected project.  Workspace-wide
        # consumers still receive defaults when they intentionally omit a
        # project scope; universal system fields use ``always`` visibility.
        "catalog_include_workspace_default": int(workspace_scope),
        # Workspace reads carry the complete authorized PG project set. Never
        # widen the ClickHouse predicate to every catalog row in the tenant.
        "catalog_include_all_projects": 0,
        "catalog_project_ids": projects,
        "catalog_agent_definition_id": scope["agent_definition_id"],
        "catalog_dataset_id": scope["dataset_id"],
        "catalog_category": query["category"],
        "catalog_source": query["source"],
        "catalog_custom_attribute_source": _custom_attribute_transport_source(
            query["source"]
        ),
        "catalog_property_kind": query["property_kind"],
        "catalog_role": query.get("role", ""),
        "catalog_per_eval_config": int(query["per_eval_config"]),
        "catalog_search": query["search"],
        "catalog_search_pattern": like_contains_pattern(query["search"]),
        "catalog_after_category_rank": order[0],
        "catalog_after_source_rank": order[1],
        "catalog_after_primary_source": order[2],
        "catalog_after_sort_name": order[3],
        "catalog_after_name": order[4],
        "catalog_after_property_id": order[5],
        "catalog_page_limit": page_size + 1,
        # One metadata sentinel carries the exact conflict proof and category
        # counts alongside the requested keyset page.  Keeping it in the same
        # SELECT avoids resolving the full activated lineage twice per API
        # request while still reserving one look-ahead property row.
        "catalog_result_limit": page_size + 2,
    }


class PropertyCatalogReader:
    def __init__(
        self,
        executor: PropertyCatalogQueryExecutor,
        *,
        catalog_database: str,
        activation_selector: ActivationControlSelector | None = None,
    ) -> None:
        self._executor = executor
        self._database = _database(catalog_database)
        if is_production_property_catalog_database(self._database) and (
            activation_selector is None
        ):
            raise ValueError("production property catalog requires control selection")
        self._activation_selector = activation_selector
        ctes = _definition_ctes(self._database)
        self._activation_sql = property_catalog_activation_sql(self._database)
        self._conflict_sql = ctes + _CONFLICT_SQL_SUFFIX
        self._page_sql = ctes + _PAGE_SQL_SUFFIX
        self._payload_sql = _PAYLOAD_SQL.replace(
            "__CATALOG_DATABASE__.property_definition_catalog",
            f"`{self._database}`.`property_definition_catalog`",
        )

    def read_page(
        self,
        *,
        scope: dict[str, Any],
        query: dict[str, Any],
        page_size: int,
        cursor_token: str | None = None,
    ) -> PropertyCatalogPage:
        checked_scope = self._validate_scope(scope)
        checked_query = self._validate_query(query)
        if (
            type(page_size) is not int
            or not 1 <= page_size <= PROPERTY_CATALOG_MAX_PAGE_SIZE
        ):
            raise ValueError(
                f"page_size must be between 1 and {PROPERTY_CATALOG_MAX_PAGE_SIZE}"
            )

        cursor: PropertyCatalogCursor | None = None
        if cursor_token:
            cursor = decode_property_catalog_cursor(
                cursor_token,
                scope=checked_scope,
                query=checked_query,
                page_size=page_size,
            )
        budget = _ReadBudget.start()
        activation = self._activation(
            scope=checked_scope,
            cursor=cursor,
            budget=budget,
        )
        if cursor and activation.activation_sha256 != cursor.activation_fingerprint:
            raise PropertyCatalogUnavailable("activation_mismatch")
        self._require_activation_coverage(
            scope=checked_scope,
            activation=activation,
        )

        params = _query_params(
            scope=checked_scope,
            query=checked_query,
            activation=activation,
            after=cursor.order if cursor else None,
            page_size=page_size,
        )
        combined_rows = self._execute(
            self._page_sql,
            params,
            max_result_rows=page_size + 2,
            budget=budget,
        )
        metadata_rows = [
            row for row in combined_rows if row.get("catalog_metadata_only") == 1
        ]
        if len(metadata_rows) != 1:
            raise PropertyCatalogUnavailable("conflict_proof_missing")
        conflicts = metadata_rows[0]
        if (
            _strict_uint(
                conflicts.get("activation_state_conflicts"),
                "activation_state_conflicts",
            )
            or _strict_uint(
                conflicts.get("activation_lineage_conflicts"),
                "activation_lineage_conflicts",
            )
            or _strict_uint(
                conflicts.get("activation_projection_conflicts"),
                "activation_projection_conflicts",
            )
            or _strict_uint(
                conflicts.get("activation_anchor_conflicts"),
                "activation_anchor_conflicts",
            )
            or _strict_uint(conflicts.get("binding_conflicts"), "binding_conflicts")
            or _strict_uint(
                conflicts.get("definition_conflicts"), "definition_conflicts"
            )
        ):
            raise PropertyCatalogUnavailable("definition_conflict")

        category_counts = {
            "all": _strict_uint(
                conflicts.get("catalog_count_all"), "catalog_count_all"
            ),
            "system_metric": _strict_uint(
                conflicts.get("catalog_count_system_metric"),
                "catalog_count_system_metric",
            ),
            "eval_metric": _strict_uint(
                conflicts.get("catalog_count_eval_metric"),
                "catalog_count_eval_metric",
            ),
            "annotation_metric": _strict_uint(
                conflicts.get("catalog_count_annotation_metric"),
                "catalog_count_annotation_metric",
            ),
            "custom_attribute": _strict_uint(
                conflicts.get("catalog_count_custom_attribute"),
                "catalog_count_custom_attribute",
            ),
            "custom_column": _strict_uint(
                conflicts.get("catalog_count_custom_column"),
                "catalog_count_custom_column",
            ),
        }
        if category_counts["all"] != sum(
            count for category, count in category_counts.items() if category != "all"
        ):
            raise PropertyCatalogUnavailable("category_count_mismatch")

        rows = [row for row in combined_rows if row.get("catalog_metadata_only") == 0]
        has_more = len(rows) > page_size
        published_rows = rows[:page_size]
        published_rows = self._hydrate_definition_payloads(
            rows=published_rows,
            scope=checked_scope,
            activation=activation,
            budget=budget,
        )
        metrics = tuple(self._metric(row) for row in published_rows)
        if len({metric["property_id"] for metric in metrics}) != len(metrics):
            raise PropertyCatalogUnavailable("duplicate_property")

        next_cursor = None
        if has_more and published_rows:
            last = published_rows[-1]
            order = self._row_order(last)
            next_cursor = encode_property_catalog_cursor(
                scope=checked_scope,
                query=checked_query,
                page_size=page_size,
                catalog_epoch=activation.catalog_epoch,
                catalog_revision=activation.catalog_revision,
                activation_fingerprint=activation.activation_sha256,
                order=order,
            )
        return PropertyCatalogPage(
            metrics=metrics,
            has_more=has_more,
            next_cursor=next_cursor,
            catalog_epoch=activation.catalog_epoch,
            catalog_revision=activation.catalog_revision,
            activation_fingerprint=activation.activation_sha256,
            category_counts=category_counts,
            category_counts_exact=True,
        )

    def _hydrate_definition_payloads(
        self,
        *,
        rows: list[dict[str, Any]],
        scope: dict[str, Any],
        activation: PropertyCatalogActivation,
        budget: _ReadBudget,
    ) -> list[dict[str, Any]]:
        if not rows:
            return []

        expected_by_key: dict[tuple[str, str, int, str, int], dict[str, Any]] = {}
        ordered_keys: list[tuple[str, str, int, str, int]] = []
        for row in rows:
            property_id = str(row.get("property_id") or "")
            binding_id = str(row.get("payload_binding_id") or "")
            catalog_revision = _strict_uint(
                row.get("payload_catalog_revision"), "payload_catalog_revision"
            )
            build_token = _uuid(row.get("payload_build_token"), "payload_build_token")
            source_version = _strict_uint(
                row.get("payload_source_version"), "payload_source_version"
            )
            if (
                not property_id
                or _SHA256_RE.fullmatch(binding_id) is None
                or catalog_revision < activation.lineage_anchor_revision
                or catalog_revision > activation.catalog_revision
            ):
                raise PropertyCatalogUnavailable("definition_payload_identity_invalid")
            key = (
                property_id,
                binding_id,
                catalog_revision,
                build_token,
                source_version,
            )
            if key in expected_by_key:
                raise PropertyCatalogUnavailable("definition_payload_identity_conflict")
            expected_by_key[key] = row
            ordered_keys.append(key)

        payload_rows = self._execute(
            self._payload_sql,
            {
                "catalog_organization_id": scope["organization_id"],
                "catalog_workspace_id": scope["workspace_id"],
                "catalog_epoch": activation.catalog_epoch,
                "catalog_projection_version": activation.projection_version,
                "catalog_payload_property_ids": tuple(key[0] for key in ordered_keys),
                "catalog_payload_keys": tuple(ordered_keys),
            },
            max_result_rows=len(ordered_keys),
            budget=budget,
        )

        payload_by_key: dict[tuple[str, str, int, str, int], dict[str, Any]] = {}
        for payload in payload_rows:
            key = (
                str(payload.get("property_id") or ""),
                str(payload.get("payload_binding_id") or ""),
                _strict_uint(
                    payload.get("payload_catalog_revision"),
                    "payload_catalog_revision",
                ),
                _uuid(payload.get("payload_build_token"), "payload_build_token"),
                _strict_uint(
                    payload.get("payload_source_version"),
                    "payload_source_version",
                ),
            )
            if (
                key not in expected_by_key
                or key in payload_by_key
                or _strict_uint(payload.get("payload_variants"), "payload_variants")
                != 1
                or _strict_uint(payload.get("payload_deleted"), "payload_deleted") != 0
            ):
                raise PropertyCatalogUnavailable("definition_payload_conflict")
            expected_digest = str(expected_by_key[key].get("definition_sha256") or "")
            if str(payload.get("payload_definition_sha256") or "") != expected_digest:
                raise PropertyCatalogUnavailable("definition_payload_digest_mismatch")
            payload_by_key[key] = payload

        if set(payload_by_key) != set(expected_by_key):
            raise PropertyCatalogUnavailable("definition_payload_missing")
        return [
            {
                **expected_by_key[key],
                "definition_json": payload_by_key[key].get("payload_definition_json"),
                "definition_sha256": payload_by_key[key].get(
                    "payload_definition_sha256"
                ),
            }
            for key in ordered_keys
        ]

    def _activation(
        self,
        *,
        scope: dict[str, Any],
        cursor: PropertyCatalogCursor | None,
        budget: _ReadBudget,
    ) -> PropertyCatalogActivation:
        target: ActivationControlTarget | None = None
        if self._activation_selector is not None:
            try:
                target = self._activation_selector.select_target(
                    scope=scope,
                    timeout_ms=budget.remaining_ms(),
                )
            except ActivationControlUnavailable as exc:
                raise PropertyCatalogUnavailable(exc.reason) from exc
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
        )
        if target is not None and not _control_target_matches_activation(
            target,
            activation,
        ):
            raise PropertyCatalogUnavailable("control_target_mismatch")
        return activation

    @staticmethod
    def _require_activation_coverage(
        *,
        scope: dict[str, Any],
        activation: PropertyCatalogActivation,
    ) -> None:
        require_property_catalog_activation_coverage(
            scope=scope,
            activation=activation,
        )

    def _execute(
        self,
        sql: str,
        params: dict[str, Any],
        *,
        max_result_rows: int,
        budget: _ReadBudget,
    ) -> list[dict[str, Any]]:
        timeout_ms = budget.remaining_ms()
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
        except PropertyCatalogUnavailable:
            raise
        except Exception as exc:
            raise PropertyCatalogUnavailable("query_failed") from exc
        budget.remaining_ms()
        rows = getattr(result, "data", None)
        if (
            not isinstance(rows, list)
            or len(rows) > max_result_rows
            or not all(isinstance(row, dict) for row in rows)
        ):
            raise PropertyCatalogUnavailable("invalid_query_result")
        return rows

    @staticmethod
    def _validate_scope(scope: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_property_catalog_scope(scope)
        normalized["organization_id"] = _uuid(
            normalized["organization_id"], "organization_id"
        )
        normalized["workspace_id"] = _uuid(normalized["workspace_id"], "workspace_id")
        projects = tuple(
            _uuid(item, "project_id") for item in normalized["project_ids"]
        )
        if len(projects) > PROPERTY_CATALOG_MAX_PROJECTS:
            if normalized.get("workspace_scope") is True:
                raise PropertyCatalogUnavailable("activation_scope_incomplete")
            raise ValueError(
                "at most "
                f"{PROPERTY_CATALOG_MAX_PROJECTS} projects may be searched at once"
            )
        normalized["project_ids"] = projects
        normalized["agent_definition_id"] = _uuid(
            normalized["agent_definition_id"],
            "agent_definition_id",
            allow_empty=True,
        )
        normalized["dataset_id"] = _uuid(
            normalized["dataset_id"], "dataset_id", allow_empty=True
        )
        return normalized

    @staticmethod
    def _validate_query(query: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_property_catalog_query(query)
        if normalized["source"] and (
            normalized["category"] == "custom_attribute"
            or normalized["property_kind"] == "custom_attribute"
        ):
            normalized["source"] = normalize_custom_attribute_source(
                normalized["source"]
            )
        if (
            len(normalized["search"].encode("utf-8"))
            > PROPERTY_CATALOG_MAX_SEARCH_BYTES
        ):
            raise ValueError(
                f"search exceeds {PROPERTY_CATALOG_MAX_SEARCH_BYTES} UTF-8 bytes"
            )
        return normalized

    @staticmethod
    def _row_order(row: dict[str, Any]) -> tuple[int, int, str, str, str, str]:
        return (
            _strict_uint(row.get("category_rank"), "category_rank", 255),
            _strict_uint(row.get("source_rank"), "source_rank", 65_535),
            str(row.get("primary_source_folded") or ""),
            str(row.get("sort_name_folded") or ""),
            str(row.get("name") or ""),
            str(row.get("property_id") or ""),
        )

    @staticmethod
    def _metric(row: dict[str, Any]) -> dict[str, Any]:
        raw = row.get("definition_json")
        if (
            not isinstance(raw, str)
            or not raw
            or len(raw.encode("utf-8")) > PROPERTY_CATALOG_MAX_DEFINITION_JSON_BYTES
        ):
            raise PropertyCatalogUnavailable("definition_payload_invalid")
        try:
            metric = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise PropertyCatalogUnavailable("definition_payload_invalid") from exc
        if not isinstance(metric, dict):
            raise PropertyCatalogUnavailable("definition_payload_invalid")

        try:
            if canonical_json(metric) != raw:
                raise PropertyCatalogUnavailable("definition_payload_not_canonical")
        except PropertyCatalogUnavailable:
            raise
        except Exception as exc:
            raise PropertyCatalogUnavailable(
                "definition_payload_not_canonical"
            ) from exc
        definition_sha256 = str(row.get("definition_sha256") or "")
        if (
            _SHA256_RE.fullmatch(definition_sha256) is None
            or canonical_json_sha256(raw) != definition_sha256
        ):
            raise PropertyCatalogUnavailable("definition_digest_mismatch")

        property_id = str(row.get("property_id") or "")
        property_kind = str(row.get("property_kind") or "")
        category = str(row.get("category") or "")
        if (
            metric.get("property_id") != property_id
            or metric.get("property_kind") != property_kind
            or metric.get("category") != category
            or metric.get("category_rank")
            != _strict_uint(row.get("category_rank"), "category_rank", 255)
            or metric.get("source_rank")
            != _strict_uint(row.get("source_rank"), "source_rank", 65_535)
            or metric.get("definition_source")
            != str(row.get("definition_source") or "")
            or metric.get("primary_source") != str(row.get("primary_source") or "")
            or list(metric.get("source_tokens") or [])
            != list(row.get("source_tokens") or [])
            or metric.get("value_adapter") != str(row.get("value_adapter") or "")
            or metric.get("display_name") != str(row.get("display_name") or "")
            or metric.get("role") != str(row.get("role") or "")
        ):
            raise PropertyCatalogUnavailable("definition_identity_mismatch")
        try:
            decoded = parse_property_registry_id(property_id)
        except ValueError as exc:
            raise PropertyCatalogUnavailable("definition_identity_invalid") from exc
        if decoded["property_kind"] != property_kind:
            raise PropertyCatalogUnavailable("definition_identity_mismatch")
        if str(metric.get("name") or "") != str(row.get("name") or ""):
            raise PropertyCatalogUnavailable("definition_identity_mismatch")
        try:
            expected_primary_fold = (
                casefold_text(metric["primary_source"], field="primary_source")
                if metric["primary_source"]
                else ""
            )
            expected_sort_fold = casefold_text(metric["name"], field="name")
            expected_search_fold = combine_search_text(
                metric["name"],
                metric["display_name"],
                metric["primary_source"],
                metric["definition_source"],
                source_tokens=tuple(metric["source_tokens"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PropertyCatalogUnavailable("definition_identity_invalid") from exc
        if (
            str(row.get("primary_source_folded") or "") != expected_primary_fold
            or str(row.get("sort_name_folded") or "") != expected_sort_fold
            or str(row.get("search_text_folded") or "") != expected_search_fold
        ):
            raise PropertyCatalogUnavailable("definition_fold_mismatch")
        details = metric.get("details") or {}
        if not isinstance(details, dict) or set(details) - _API_DETAIL_KEYS:
            raise PropertyCatalogUnavailable("definition_details_invalid")
        api_metric = dict(details)
        api_metric.update(
            {
                "name": metric["name"],
                "property_id": property_id,
                "property_kind": property_kind,
                "display_name": metric["display_name"],
                "category": category,
                "source": metric["primary_source"],
                "sources": list(metric["source_tokens"]),
                "role": metric["role"],
            }
        )
        if metric.get("value_type"):
            api_metric["type"] = metric["value_type"]
        if metric.get("output_type"):
            api_metric["output_type"] = metric["output_type"]
        return api_metric


__all__ = [
    "PROPERTY_CATALOG_MAX_PAGE_SIZE",
    "PROPERTY_CATALOG_NOT_READY_REASONS",
    "PropertyCatalogActivation",
    "PropertyCatalogPage",
    "PropertyCatalogQueryExecutor",
    "PropertyCatalogReader",
    "PropertyCatalogUnavailable",
    "is_property_catalog_not_ready_error",
    "property_catalog_activation_sql",
    "require_property_catalog_activation_coverage",
    "verify_property_catalog_activation",
]
