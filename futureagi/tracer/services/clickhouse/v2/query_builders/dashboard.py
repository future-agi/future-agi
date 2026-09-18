"""
v2 Dashboard query builder — targets the CH 25.3 spans schema.

Subclass + post-rewrite. The v1 dashboard builder emits 1 SQL query per
dashboard metric (latency, p95, model breakdown, custom-attribute pivots,
etc.). Each metric type goes through `build_metric_query()`; `build_all_queries`
fans out over it and returns `[(sql, params, meta), …]`.

Unlike the list builders, the dashboard builder dispatches EVERY metric type
through that ONE polymorphic method. A metric may target the migrated `spans`
schema (system_metric / custom_attribute) OR a non-migrated legacy table
(eval_metric → `usage_apicalllog`, annotation_metric → `model_hub_score`, both
still on `_peerdb_is_deleted` / `deleted`). `V2RewriteMixin`'s blanket auto-wrap
cannot distinguish aliases by physical table. Both dispatch methods are
therefore excluded from the mixin and the rewrite is applied here after
protecting/restoring every legacy-table alias. That matters for mixed queries
too: a system metric can JOIN `model_hub_score` for an annotation breakdown
while its spans columns still need the v2 rewrite.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tracer.services.clickhouse.query_builders.dashboard import (
    AGGREGATIONS,
    DashboardQueryBuilder,
    _sanitize_attr_key,
)
from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    LatestFilterPredicate,
    UnsupportedFilterShapeError,
    compile_span_attribute_row_predicate,
    compile_span_filter_plans,
)
from tracer.services.clickhouse.v2.adapter import CH_INSERT_COLUMNS
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin
from tracer.services.clickhouse.v2.query_builders.filters import (
    _append_v2_settings,
    rewrite_v1_sql_to_v2,
)

# Tables whose columns must NOT be rewritten (they keep `_peerdb_is_deleted`).
_LEGACY_TABLE_RE = re.compile(
    r"(?:usage_apicalllog|model_hub_score)\s+AS\s+(\w+)", re.IGNORECASE
)

# The eval-metric builder uses candidate-scoped subqueries over the legacy
# usage table. Their outer aliases no longer appear immediately after the
# table token, so `_LEGACY_TABLE_RE` cannot discover them. Protect only the
# explicitly generated usage aliases while the spans portion is rewritten.
_USAGE_CDC_COLUMN_RE = re.compile(
    r"\b(?P<alias>e|ev_(?:bd|f)\d+|usage_[A-Za-z0-9_]+)\."
    r"(?P<column>_peerdb_is_deleted|_peerdb_version)\b"
)

_SIMPLE_METRIC_QUERY_RE = re.compile(
    r"\A\s*SELECT\s+(?P<select>.*?)\nFROM\s+(?P<tail>.*?)"
    r"\nSETTINGS\s+(?P<settings>.*)\s*\Z",
    flags=re.IGNORECASE | re.DOTALL,
)
_VALUE_ALIAS_RE = re.compile(
    r"\A(?P<expression>.*)\s+AS\s+value\s*\Z",
    flags=re.IGNORECASE | re.DOTALL,
)
_SELECT_ALIAS_RE = re.compile(r"\s+AS\s+(?P<alias>[A-Za-z_][A-Za-z0-9_]*)\s*\Z")
_EXACT_QUANTILE_RE = re.compile(
    r"\AquantileExact\((?P<level>[^)]+)\)\((?P<column>.*)\)\Z",
    flags=re.DOTALL,
)
_SAFE_CLUSTER_NAME_RE = re.compile(r"\A[A-Za-z0-9_-]+\Z")

# The replay winner reproduces ``SELECT spans.*``: ClickHouse expands ``*`` to
# the ordinary (non-MATERIALIZED, non-ALIAS) columns, which is exactly
# ``CH_INSERT_COLUMNS`` plus the version column.
_EXACT_REPLAY_IDENTITY_COLUMNS = (
    "project_id",
    "observation_type",
    "service_name",
    "trace_id",
    "id",
)
# The candidate CTE's name marks a statement that carries the exact replay;
# ``build_metric_query`` reads it to choose that statement's aggregation.
_EXACT_REPLAY_CANDIDATE_CTE = "dashboard_filter_candidate_identities"

# Fat payload columns that no dashboard metric, filter or breakdown expression
# can name: ``_qualify_span_expression`` does not know them and neither builder
# emits them. Keeping them out of the winner tuple is what makes the aggregate
# state proportional to the candidate count rather than to the payload.
# ``attributes_extra`` is the exception — overflow-JSON attribute filters
# compile against it — so it is packed on demand, never by default.
_EXACT_REPLAY_OVERFLOW_COLUMN = "attributes_extra"
_EXACT_REPLAY_OVERFLOW_TOKEN = "span_attributes_raw"
_EXACT_REPLAY_UNPACKED_COLUMNS = frozenset(
    {
        "status_message",
        "input",
        "output",
        "span_events",
        "resource_attrs",
        "metadata",
        _EXACT_REPLAY_OVERFLOW_COLUMN,
    }
)


@dataclass(frozen=True)
class DashboardMetricGroupQuery:
    """One statement carrying every compatible trace metric."""

    sql: str
    params: dict[str, Any]
    metrics: tuple[dict[str, Any], ...]
    value_columns: tuple[str, ...]
    has_breakdown: bool


def _split_select_expressions(fragment: str) -> tuple[str, ...]:
    """Split a SELECT list without splitting nested function arguments."""

    parts: list[str] = []
    start = 0
    depth = 0
    quote = False
    index = 0
    while index < len(fragment):
        char = fragment[index]
        if quote:
            if char == "'":
                if index + 1 < len(fragment) and fragment[index + 1] == "'":
                    index += 1
                else:
                    quote = False
        elif char == "'":
            quote = True
        elif char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
            if depth < 0:
                raise ValueError("dashboard metric SELECT is unbalanced")
        elif char == "," and depth == 0:
            parts.append(fragment[start:index].strip())
            start = index + 1
        index += 1
    if quote or depth != 0:
        raise ValueError("dashboard metric SELECT is unbalanced")
    parts.append(fragment[start:].strip())
    return tuple(part for part in parts if part)


def _protect_usage_cdc_columns(sql: str) -> str:
    return _USAGE_CDC_COLUMN_RE.sub(
        lambda match: (
            f"{match.group('alias')}.__usage_legacy_"
            f"{match.group('column').removeprefix('_peerdb_')}__"
        ),
        sql,
    )


def _restore_usage_cdc_columns(sql: str) -> str:
    return sql.replace(".__usage_legacy_is_deleted__", "._peerdb_is_deleted").replace(
        ".__usage_legacy_version__", "._peerdb_version"
    )


class DashboardQueryBuilderV2(V2RewriteMixin, DashboardQueryBuilder):
    """Drop-in v2 Dashboard builder.

    Both `build_metric_query` and `build_all_queries` are excluded from the
    mixin's blanket rewrite because they are polymorphic over metric type (see
    module docstring). `build_metric_query` applies the rewrite itself, then
    restores protected legacy aliases. This covers both legacy metrics and
    mixed queries such as a system metric with an annotation/eval breakdown.
    """

    # dashboard_attr_rollup ships only in the v2 schema, so the fast-path is safe only here.
    _attr_rollup_available: bool = True

    # Product reads use the direct-write curated dimension. This avoids a
    # runtime dependency on the optional ClickHouse dictionary (the locked
    # read-only production identity is intentionally not granted dictionary
    # access) while preserving latest-live + id-remap semantics.
    _direct_end_users_available: bool = True

    # Project-scope trace-attached annotations through the direct-write traces
    # table. The locked production read-only identity has no dictionary grants.
    _direct_trace_project_scope_available: bool = True

    # CH25 spans is partitioned by toDate(start_time). Do not inherit the
    # legacy created_at partition hint: it is redundant for correctness and
    # makes root metric queries ineligible for proj_root_spans.
    _spans_partitioned_by_created_at: bool = False

    # Keep raw mode available for explicit legacy/benchmark callers. Public
    # dashboard execution opts into latest-state mode for every trace metric.
    _latest_state_spans_required: bool = False

    _v2_rewrite_exclude = frozenset({"build_metric_query", "build_all_queries"})

    def __init__(self, query_config: dict) -> None:
        super().__init__(query_config)
        # A preset range is relative to ``now``. Freeze it once per request so
        # every concurrent metric uses identical endpoints—even across
        # midnight while an asynchronous dashboard refresh is running.
        self._resolved_time_range = super().parse_time_range()

    def parse_time_range(self) -> tuple[datetime, datetime]:
        return self._resolved_time_range

    @staticmethod
    def _candidate_parameter_namespace(
        predicate: str,
        plan_params: dict[str, object],
        *,
        index: int = 0,
    ) -> tuple[str, dict[str, object]]:
        """Keep candidate bindings separate from the outer exact predicate."""

        renamed_params: dict[str, object] = {}
        for parameter_name, parameter_value in plan_params.items():
            namespace = (
                "dashboard_candidate" if index == 0 else f"dashboard_candidate_{index}"
            )
            candidate_name = f"{namespace}_{parameter_name}"
            predicate = predicate.replace(
                f"%({parameter_name})s",
                f"%({candidate_name})s",
            )
            renamed_params[candidate_name] = parameter_value
        return predicate, renamed_params

    def _exact_filter_candidate_plan(
        self,
        per_metric_filters: list[dict],
    ) -> tuple[LatestFilterPredicate, ...]:
        """Choose exhaustive, index-usable positive attribute witnesses.

        The witness narrows immutable identities only. The replay source still
        resolves the latest physical row for every candidate, and the ordinary
        dashboard WHERE clause reapplies every filter exactly. Filter shapes
        without an exhaustive raw value witness keep the ordinary latest-state
        source (or metric-key discovery for custom metrics).
        """

        candidates: list[LatestFilterPredicate] = []
        for item in self.global_filters + (per_metric_filters or []):
            if item.get("source", "traces") not in ("traces", ""):
                continue
            canonical_filter = item.get("canonical_filter")
            if (
                item.get("metric_type") or item.get("type")
            ) != "custom_attribute" or not isinstance(canonical_filter, dict):
                continue
            try:
                plans = compile_span_filter_plans([canonical_filter])
            except (UnsupportedFilterShapeError, ValueError):
                continue
            if plans and plans[0].raw_graph_value_witness_predicate:
                candidates.append(plans[0])

        return tuple(
            sorted(
                candidates,
                key=lambda plan: (
                    plan.raw_witness_rank
                    if plan.raw_witness_rank is not None
                    else 10_000
                ),
            )
        )

    def _exact_filter_replay_source(
        self,
        per_metric_filters: list[dict],
        alias: str,
        params: dict[str, object] | None,
        *,
        root_only: bool = False,
    ) -> str | None:
        """Narrow identities only; mutable conditions never constrain replay.

        A final matching physical row necessarily satisfies every positive
        witness and required metric/breakdown key together. Their conjunction
        is therefore exhaustive even when earlier versions carried different
        values. Deleted/key-removed/corrected versions must still win replay.
        """

        if params is None:
            return None
        witnesses = []
        for index, plan in enumerate(
            self._exact_filter_candidate_plan(per_metric_filters)
        ):
            witness, candidate_params = self._candidate_parameter_namespace(
                plan.raw_graph_value_witness_predicate,
                plan.params,
                index=index,
            )
            params.update(candidate_params)
            witnesses.append(
                self._qualify_span_expression(
                    witness,
                    alias="dashboard_candidate_source",
                )
            )

        # These predicates are already required by the outer custom metric
        # query. Use their key indexes only during identity discovery, never
        # against the version stream consumed by LIMIT 1 BY.
        metric_presence = getattr(self, "_exact_metric_presence", None)
        if metric_presence is not None:
            attribute_map, attribute_key = metric_presence
            params["dashboard_candidate_metric_key"] = attribute_key
            presences = [(attribute_map, "dashboard_candidate_metric_key")]
            for index, breakdown in enumerate(self.breakdowns):
                if breakdown.get("type") != "custom_attribute" or breakdown.get(
                    "source", "traces"
                ) in {"datasets", "simulation"}:
                    continue
                attribute_map = {
                    "number": "attrs_number",
                    "boolean": "attrs_bool",
                    "string": "attrs_string",
                    "text": "attrs_string",
                }.get(breakdown.get("attribute_type", "string"))
                if attribute_map is None:
                    continue
                key = f"dashboard_candidate_breakdown_key_{index}"
                params[key] = _sanitize_attr_key(breakdown.get("name", ""))
                presences.append((attribute_map, key))
            for attribute_map, key in presences:
                column = f"dashboard_candidate_source.{attribute_map}"
                witnesses.extend(
                    (
                        f"indexHint(has(mapKeys({column}), %({key})s))",
                        f"mapContains({column}, %({key})s)",
                    )
                )
        if not witnesses:
            return None
        attribute_keys = self._exact_replay_attribute_keys(per_metric_filters)
        if attribute_keys is None:
            return None
        witness = " AND ".join(f"({predicate})" for predicate in witnesses)
        packed = self._exact_replay_packed_columns(
            attribute_keys,
            overflow=self._exact_replay_reads_overflow_json(per_metric_filters),
        )
        packed_sql = ",\n                        ".join(
            expression for _, expression in packed
        )
        unpacked_sql = ",\n                dashboard_candidate_winner.".join(
            f"{position} AS {column}"
            for position, (column, _) in enumerate(packed, start=1)
        )
        # Latency already requires a root in the outer winner predicate. Use
        # that necessary condition only for raw identity discovery, before
        # reading child attribute maps; parent/root status is NOT immutable.
        # All versions (including later children/tombstones) still enter replay.
        root_witness = (
            "\n                  AND (dashboard_candidate_source.parent_span_id IS NULL "
            "OR dashboard_candidate_source.parent_span_id = '')"
            if root_only else ""
        )
        source_alias = "spans" if alias == "spans" else alias
        # Two legs, one Map read. The candidate leg already decompresses
        # attrs_string for the witness, so it also elects the witness-matching
        # winner with a packed argMax and reports that winner's version. The
        # replay leg then reads nothing but the identity columns and the
        # version, and keeps an identity only when its newest version IS the
        # witness-matching one — so a later tombstone, a cleared key, a
        # corrected value or a re-parented child excludes the identity exactly
        # as a full version replay would, without ever materialising a Map.
        # One physical row supplies every published column (packed tuple), and
        # the tuple is unpacked one level above the aggregate that builds it:
        # unpacking it in the same SELECT aliases a winner element to a name
        # argMax itself reads, which the CH 25.3 analyzer rejects.
        return f"""(
            WITH {_EXACT_REPLAY_CANDIDATE_CTE} AS (
                SELECT
                    dashboard_candidate_source.project_id AS project_id,
                    dashboard_candidate_source.observation_type
                        AS observation_type,
                    dashboard_candidate_source.service_name AS service_name,
                    toStartOfHour(
                        dashboard_candidate_source.start_time
                    ) AS identity_hour,
                    dashboard_candidate_source.trace_id AS trace_id,
                    dashboard_candidate_source.id AS id,
                    max(dashboard_candidate_source._peerdb_version)
                        AS dashboard_witness_version,
                    argMax(
                        tuple(
                        {packed_sql}
                        ),
                        dashboard_candidate_source._peerdb_version
                    ) AS dashboard_candidate_winner
                FROM spans AS dashboard_candidate_source
                PREWHERE dashboard_candidate_source.project_id
                            IN %(project_ids)s
                  AND dashboard_candidate_source.start_time
                            >= %(start_date)s
                  AND dashboard_candidate_source.start_time
                            < %(end_date)s{root_witness}
                WHERE {witness}
                GROUP BY
                    dashboard_candidate_source.project_id,
                    dashboard_candidate_source.observation_type,
                    dashboard_candidate_source.service_name,
                    identity_hour,
                    dashboard_candidate_source.trace_id,
                    dashboard_candidate_source.id
            )
            SELECT
                project_id,
                observation_type,
                service_name,
                trace_id,
                id,
                dashboard_replay_version AS _peerdb_version,
                dashboard_candidate_winner.{unpacked_sql}
            FROM (
                SELECT
                    dashboard_replay_source.project_id AS project_id,
                    dashboard_replay_source.observation_type
                        AS observation_type,
                    dashboard_replay_source.service_name AS service_name,
                    dashboard_replay_source.trace_id AS trace_id,
                    dashboard_replay_source.id AS id,
                    max(dashboard_replay_source._peerdb_version)
                        AS dashboard_replay_version,
                    any(dashboard_candidate_state.dashboard_candidate_winner)
                        AS dashboard_candidate_winner
                FROM spans AS dashboard_replay_source
                INNER JOIN {_EXACT_REPLAY_CANDIDATE_CTE}
                        AS dashboard_candidate_state
                    ON dashboard_replay_source.project_id
                        = dashboard_candidate_state.project_id
                   AND dashboard_replay_source.observation_type
                        = dashboard_candidate_state.observation_type
                   AND dashboard_replay_source.service_name
                        = dashboard_candidate_state.service_name
                   AND toStartOfHour(dashboard_replay_source.start_time)
                        = dashboard_candidate_state.identity_hour
                   AND dashboard_replay_source.trace_id
                        = dashboard_candidate_state.trace_id
                   AND dashboard_replay_source.id
                        = dashboard_candidate_state.id
                PREWHERE dashboard_replay_source.project_id IN %(project_ids)s
                  AND dashboard_replay_source.start_time
                        >= toStartOfHour(toDateTime64(%(start_date)s, 6, 'UTC'))
                  AND dashboard_replay_source.start_time
                        < toStartOfHour(toDateTime64(%(end_date)s, 6, 'UTC'))
                            + INTERVAL 1 HOUR
                GROUP BY
                    dashboard_replay_source.project_id,
                    dashboard_replay_source.observation_type,
                    dashboard_replay_source.service_name,
                    toStartOfHour(dashboard_replay_source.start_time),
                    dashboard_replay_source.trace_id,
                    dashboard_replay_source.id
                HAVING max(dashboard_replay_source._peerdb_version)
                    = any(dashboard_candidate_state.dashboard_witness_version)
            ) AS dashboard_replay_identities
        ) AS {source_alias}"""

    @staticmethod
    def _exact_replay_attribute_key(item: dict) -> str | None:
        """The span-attribute Map key one filter or breakdown item reads."""

        canonical_filter = item.get("canonical_filter")
        raw_key = None
        if isinstance(canonical_filter, dict):
            raw_key = canonical_filter.get("column_id") or canonical_filter.get(
                "columnId"
            )
        if not raw_key:
            raw_key = (
                item.get("metric_name") or item.get("name") or item.get("id") or ""
            )
        if not isinstance(raw_key, str):
            return None
        try:
            return _sanitize_attr_key(raw_key)
        except ValueError:
            return None

    def _exact_replay_attribute_keys(
        self,
        per_metric_filters: list[dict],
    ) -> tuple[str, ...] | None:
        """Every ``attrs_string`` key the outer statement can read, or None.

        The winner carries a key-narrowed ``attrs_string`` because that Map is
        the statement's entire read cost. A key the outer statement reads but
        the narrowing dropped would silently read as absent, so enumeration
        must be exhaustive and a custom attribute whose key cannot be
        enumerated abandons this source instead of narrowing a Map blind.
        Number and boolean attribute items are enumerated too: every
        custom-attribute breakdown renders through ``span_attr_str`` whatever
        its declared type. Over-inclusion only keeps a key nothing reads.
        ``attrs_number`` and ``attrs_bool`` are carried whole — they are a
        rounding error beside ``attrs_string``, and a system metric can name a
        literal ``attrs_number`` key no filter or breakdown enumerates.
        """

        keys: list[str] = []
        for item in self.global_filters + (per_metric_filters or []):
            item_type = item.get("metric_type") or item.get("type")
            if item_type == "system_metric":
                continue
            key = self._exact_replay_attribute_key(item)
            if key is None:
                if item_type == "custom_attribute":
                    return None
                continue
            keys.append(key)
        for breakdown in self.breakdowns:
            if breakdown.get("type") != "custom_attribute":
                continue
            key = self._exact_replay_attribute_key(breakdown)
            if key is None:
                return None
            keys.append(key)
        metric_presence = getattr(self, "_exact_metric_presence", None)
        if metric_presence is not None:
            keys.append(metric_presence[1])
        return tuple(dict.fromkeys(keys))

    def _exact_replay_reads_overflow_json(
        self,
        per_metric_filters: list[dict],
    ) -> bool:
        """Does any attribute filter compile against the overflow JSON column?

        Array and map attribute filters read ``span_attributes_raw``, which the
        v2 rewrite retargets to ``attributes_extra``. Those shapes need the
        column in the winner tuple; every other shape is better off without it.
        """

        for item in self.global_filters + (per_metric_filters or []):
            canonical_filter = item.get("canonical_filter")
            if not isinstance(canonical_filter, dict):
                continue
            try:
                predicate, _ = compile_span_attribute_row_predicate(canonical_filter)
            except (UnsupportedFilterShapeError, ValueError):
                return True
            if _EXACT_REPLAY_OVERFLOW_TOKEN in predicate:
                return True
        return False

    def _exact_replay_packed_columns(
        self,
        attribute_keys: tuple[str, ...],
        *,
        overflow: bool,
    ) -> tuple[tuple[str, str], ...]:
        """The winner tuple: ``SELECT *`` minus identity minus fat payload.

        The narrowing keys are inlined rather than bound. This source is built
        once per alias against one shared parameter dict — the annotation
        metric builds a span-filter source and a metric source from different
        filter lists — so an indexed binding would be reassigned and one
        derived table would narrow to the other's key with no error at all.
        ``_sanitize_attr_key`` already restricts keys to ``[A-Za-z0-9._-]``,
        which is why the v1 builder inlines the very same keys.
        """

        quoted_keys = [f"'{attribute_key}'" for attribute_key in attribute_keys]
        narrowed_strings = (
            "mapFilter((k, v) -> (k IN ("
            + ", ".join(quoted_keys)
            + ")), dashboard_candidate_source.attrs_string)"
            if quoted_keys
            else "mapFilter((k, v) -> 0, dashboard_candidate_source.attrs_string)"
        )
        packed: list[tuple[str, str]] = []
        for column in CH_INSERT_COLUMNS:
            if column in _EXACT_REPLAY_IDENTITY_COLUMNS:
                continue
            if column in _EXACT_REPLAY_UNPACKED_COLUMNS and not (
                overflow and column == _EXACT_REPLAY_OVERFLOW_COLUMN
            ):
                continue
            packed.append(
                (column, narrowed_strings)
                if column == "attrs_string"
                else (column, f"dashboard_candidate_source.{column}")
            )
        return tuple(packed)

    def _spans_source(
        self,
        metric_name: str | None,
        per_metric_filters: list[dict],
        alias: str,
        params: dict[str, object] | None = None,
    ) -> str:
        if not self._latest_state_spans_required:
            return super()._spans_source(
                metric_name,
                per_metric_filters,
                alias,
                params=params,
            )

        # ID-remapped user/session dimensions need the dedicated resolved
        # source. Keep that exact path unchanged until it has an equivalent
        # immutable-identity proof.
        if not self._query_references_id(metric_name, per_metric_filters):
            candidate_source = self._exact_filter_replay_source(
                per_metric_filters,
                alias,
                params,
                root_only=metric_name == "latency",
            )
            if candidate_source is not None:
                return candidate_source
            if params is not None:
                # Exact start_time is mutable within its replacement-key hour.
                # Even FINAL can prune a corrected version when the outer range
                # reaches the storage scan. Fence that time through a singleton
                # ARRAY JOIN, retaining column pruning and one row per winner.
                return f"""(
                    SELECT finalized.* REPLACE(
                        arrayJoin([finalized.start_time]) AS start_time
                    )
                    FROM spans AS finalized FINAL
                    PREWHERE finalized.project_id IN %(project_ids)s
                        AND toStartOfHour(finalized.start_time) >= toStartOfHour(
                            toDateTime64(%(start_date)s, 6, 'UTC'))
                        AND toStartOfHour(finalized.start_time) < toStartOfHour(
                            toDateTime64(%(end_date)s, 6, 'UTC')) + INTERVAL 1 HOUR
                ) AS {alias}"""
        return super()._spans_source(
            metric_name,
            per_metric_filters,
            alias,
            params=params,
        )

    def _annotation_filter_spans_source(
        self,
        span_filters: list[dict],
        params: dict[str, object],
    ) -> str:
        return self._spans_source(
            None,
            span_filters,
            "s",
            params=params,
        )

    def _is_redundant_time_filter(self, item: dict) -> bool:
        canonical = item.get("canonical_filter")
        config = canonical.get("filter_config") if isinstance(canonical, dict) else None
        if (
            not isinstance(config, dict)
            or item.get("source", "traces") not in ("traces", "")
            or item.get("metric_type") != "system_metric"
            or item.get("metric_name") != "created_at"
            or canonical.get("column_id") != "created_at"
            or config.get("col_type") != "SYSTEM_METRIC"
            or config.get("filter_type") != "datetime"
            or config.get("filter_op") != "between"
        ):
            return False
        values = config.get("filter_value")
        if not isinstance(values, list) or len(values) != 2:
            return False
        try:
            return tuple(
                datetime.fromisoformat(value) if isinstance(value, str) else value
                for value in values
            ) == self.parse_time_range()
        except (TypeError, ValueError):
            return False

    def _build_custom_attr_query(
        self,
        metric: dict,
        aggregation: str,
        bucket_fn: str,
        per_metric_filters: list[dict],
        params: dict,
    ) -> tuple[str, dict]:
        """Resolve custom metrics through the selected spans snapshot mode.

        Public dashboard execution selects latest-state semantics; explicit
        raw-mode callers retain the legacy one-pass aggregation.
        """

        if not self._latest_state_spans_required:
            return super()._build_custom_attr_query(
                metric,
                aggregation,
                bucket_fn,
                per_metric_filters,
                params,
            )

        attribute_maps = {
            "number": "attrs_number",
            "boolean": "attrs_bool",
            "string": "attrs_string",
            "text": "attrs_string",
        }
        scalar_breakdown = self.breakdowns[0] if len(self.breakdowns) == 1 else None
        breakdown_map = (
            attribute_maps.get(scalar_breakdown.get("attribute_type", "string"))
            if scalar_breakdown
            and scalar_breakdown.get("type") == "custom_attribute"
            and scalar_breakdown.get("source", "traces")
            not in {"datasets", "simulation"}
            else None
        )
        scalar_filters = [
            item for item in self.global_filters + per_metric_filters
            if not self._is_redundant_time_filter(item)
        ]
        local_scalar_filters = all(
            item.get("source", "traces") in ("traces", "")
            and item.get("metric_type") == "custom_attribute"
            and isinstance(item.get("canonical_filter"), dict)
            and item["canonical_filter"].get("filter_config", {}).get("filter_type")
            in {"number", "text", "boolean"}
            for item in scalar_filters
        )
        if (
            metric.get("attribute_type", "number") != "number"
            or (self.breakdowns and breakdown_map is None)
            or not local_scalar_filters
        ):
            previous = getattr(self, "_exact_metric_presence", None)
            attribute_map = attribute_maps.get(metric.get("attribute_type", "number"))
            self._exact_metric_presence = (
                (attribute_map, _sanitize_attr_key(metric.get("attribute_key", "")))
                if attribute_map is not None
                else None
            )
            try:
                return super()._build_custom_attr_query(
                    metric,
                    aggregation,
                    bucket_fn,
                    per_metric_filters,
                    params,
                )
            finally:
                self._exact_metric_presence = previous

        attr_key = _sanitize_attr_key(metric.get("attribute_key", ""))
        params = dict(params)
        params["custom_metric_attr_key"] = attr_key
        exact_filters = []
        for index, item in enumerate(scalar_filters):
            predicate, filter_params = compile_span_attribute_row_predicate(
                item["canonical_filter"], index=index
            )
            params.update(filter_params)
            exact_filters.append(
                self._qualify_span_expression(predicate, "custom_metric_source")
            )
        filter_state = (
            ", (" + " AND ".join(f"({p})" for p in exact_filters) + ")"
            if exact_filters
            else ""
        )
        filter_live_predicate = (
            f"AND tupleElement(latest_metric_state, {7 if breakdown_map else 5}) = 1"
            if exact_filters
            else ""
        )
        aggregate = AGGREGATIONS.get(aggregation, "avg({col})").format(
            col="metric_value"
        )
        # Project compact FINAL winners before mutable predicates. Keep
        # presence and values together, including cleared keys and nulls.
        breakdown_state = ""
        breakdown_select = ""
        breakdown_live_predicate = ""
        dimensions = [f"{bucket_fn}(start_time) AS time_bucket"]
        group_columns = ["time_bucket"]
        live_columns = ["start_time", "metric_value"]
        if breakdown_map is not None:
            params["_custom_bd_key_0"] = _sanitize_attr_key(scalar_breakdown["name"])
            breakdown_state = f""",
                            mapContains(custom_metric_source.{breakdown_map}, %(_custom_bd_key_0)s),
                            custom_metric_source.{breakdown_map}[%(_custom_bd_key_0)s]"""
            breakdown_select = (
                ", tupleElement(latest_metric_state, 6) AS breakdown_value"
            )
            breakdown_live_predicate = "AND tupleElement(latest_metric_state, 5) = 1"
            dimensions.append("breakdown_value AS breakdown_value")
            group_columns.append("breakdown_value")
            live_columns.append("breakdown_value")
        # Singleton ARRAY JOIN fences mutable predicates without accumulating
        # whole-window winners. FINAL resolves the full replacement key; only
        # immutable key hours may prune its input.
        scalar_source = f"""
            WITH latest_custom_metric_spans AS (
                SELECT
                        tuple(
                            custom_metric_source.is_deleted,
                            custom_metric_source.start_time,
                            mapContains(
                                custom_metric_source.attrs_number,
                                %(custom_metric_attr_key)s
                            ),
                            custom_metric_source.attrs_number[
                                %(custom_metric_attr_key)s
                            ]{breakdown_state}{filter_state}
                        ) AS metric_winner
                FROM spans AS custom_metric_source FINAL
                PREWHERE custom_metric_source.project_id IN %(project_ids)s
                  /*
                   * Widen exact event bounds to their storage-key hours so a
                   * corrected start_time still participates in FINAL. The
                   * native driver renders datetime parameters as SQL string
                   * literals, so type them before applying date functions.
                   */
                  AND toStartOfHour(custom_metric_source.start_time)
                            >= toStartOfHour(toDateTime64(
                                %(start_date)s, 6, 'UTC'
                            ))
                  AND toStartOfHour(custom_metric_source.start_time)
                            < toStartOfHour(toDateTime64(
                                %(end_date)s, 6, 'UTC'
                            )) + INTERVAL 1 HOUR
            ), live_custom_metric_spans AS (
                SELECT
                    tupleElement(latest_metric_state, 2) AS start_time,
                    tupleElement(latest_metric_state, 4) AS metric_value{breakdown_select}
                FROM latest_custom_metric_spans
                ARRAY JOIN [metric_winner] AS latest_metric_state
                WHERE tupleElement(latest_metric_state, 1) = 0
                  AND tupleElement(latest_metric_state, 3) = 1
                  {breakdown_live_predicate}
                  {filter_live_predicate}
                  AND tupleElement(latest_metric_state, 2) >= %(start_date)s
                  AND tupleElement(latest_metric_state, 2) < %(end_date)s
            )
            SELECT {", ".join(live_columns)}
            FROM live_custom_metric_spans
        """
        # Keep the outer metric SELECT ordinary so compatible aggregations
        # share this exact source and exact percentile state in one statement.
        sql = (
            "SELECT "
            + ", ".join([*dimensions, f"{aggregate} AS value"])
            + "\nFROM ("
            + scalar_source
            + ") AS custom_metric_rows"
            + "\nGROUP BY "
            + ", ".join(group_columns)
            + "\nORDER BY "
            + ", ".join(group_columns)
            + "\nSETTINGS optimize_move_to_prewhere=0, optimize_move_to_prewhere_if_final=0"
            + ", enable_optimize_predicate_expression=1, enable_optimize_predicate_expression_to_final_subquery=1"
        )
        return sql, params

    def _build_metric_query_for_snapshot_mode(
        self,
        metric: dict[str, Any],
        *,
        latest_state: bool,
    ) -> tuple[str, dict[str, Any]]:
        """Build one metric while locally selecting raw or latest-state spans."""

        previous = self._latest_state_spans_required
        self._latest_state_spans_required = bool(latest_state)
        try:
            return self.build_metric_query(metric)
        finally:
            self._latest_state_spans_required = previous

    @staticmethod
    def _parse_simple_metric_query(
        sql: str,
    ) -> tuple[tuple[str, ...], str, str, str] | None:
        """Return dimensions, value expression, FROM tail, and settings.

        The optimizer intentionally accepts only the ordinary one-level metric
        statement emitted by the existing builder. CTE-heavy identity, user,
        eval, annotation, and candidate-replay paths keep their established
        query unchanged.
        """

        match = _SIMPLE_METRIC_QUERY_RE.match(sql)
        if match is None:
            return None
        try:
            select_parts = _split_select_expressions(match.group("select"))
        except ValueError:
            return None
        value_matches = [
            (index, _VALUE_ALIAS_RE.match(part))
            for index, part in enumerate(select_parts)
            if _VALUE_ALIAS_RE.match(part) is not None
        ]
        if len(value_matches) != 1:
            return None
        value_index, value_match = value_matches[0]
        assert value_match is not None
        dimensions = tuple(
            part for index, part in enumerate(select_parts) if index != value_index
        )
        aliases = []
        for expression in dimensions:
            alias_match = _SELECT_ALIAS_RE.search(expression)
            if alias_match is None:
                return None
            aliases.append(alias_match.group("alias"))
        if not aliases or aliases[0] != "time_bucket":
            return None
        if aliases[1:] not in ([], ["breakdown_value"]):
            return None
        return (
            dimensions,
            value_match.group("expression").strip(),
            match.group("tail").strip(),
            match.group("settings").strip(),
        )

    @staticmethod
    def _render_metric_group_sql(
        *,
        dimensions: tuple[str, ...],
        value_expressions: tuple[str, ...],
        tail: str,
        query_settings: str,
    ) -> tuple[str, tuple[str, ...]]:
        """Render one scan and share exact percentile state where possible."""

        value_columns = tuple(
            f"dashboard_metric_value_{index}" for index in range(len(value_expressions))
        )
        quantile_groups: dict[str, list[tuple[int, str]]] = {}
        for index, expression in enumerate(value_expressions):
            match = _EXACT_QUANTILE_RE.match(expression)
            if match is not None:
                quantile_groups.setdefault(match.group("column"), []).append(
                    (index, match.group("level").strip())
                )
        shared_groups = {
            column: members
            for column, members in quantile_groups.items()
            if len(members) > 1
        }
        if not shared_groups:
            values = [
                f"{expression} AS {value_columns[index]}"
                for index, expression in enumerate(value_expressions)
            ]
            return (
                "SELECT "
                + ", ".join([*dimensions, *values])
                + "\nFROM "
                + tail
                + "\nSETTINGS "
                + query_settings,
                value_columns,
            )

        order_marker = "\nORDER BY "
        if order_marker not in tail:
            raise ValueError("dashboard metric query has no stable output order")
        grouped_tail, order_by = tail.rsplit(order_marker, 1)
        shared_members = {
            metric_index
            for members in shared_groups.values()
            for metric_index, _level in members
        }
        inner_values = [
            f"{expression} AS {value_columns[index]}"
            for index, expression in enumerate(value_expressions)
            if index not in shared_members
        ]
        shared_index: dict[int, tuple[str, int]] = {}
        for group_index, (column, members) in enumerate(shared_groups.items()):
            alias = f"dashboard_metric_quantiles_{group_index}"
            levels: list[str] = []
            for _metric_index, level in members:
                if level not in levels:
                    levels.append(level)
            inner_values.append(
                f"quantilesExact({', '.join(levels)})({column}) AS {alias}"
            )
            for metric_index, level in members:
                shared_index[metric_index] = (alias, levels.index(level) + 1)

        dimension_aliases = [
            _SELECT_ALIAS_RE.search(expression).group("alias")
            for expression in dimensions
        ]
        outer_values = []
        for index, column in enumerate(value_columns):
            if index in shared_index:
                alias, array_index = shared_index[index]
                outer_values.append(f"{alias}[{array_index}] AS {column}")
            else:
                outer_values.append(column)
        inner_sql = (
            "SELECT "
            + ", ".join([*dimensions, *inner_values])
            + "\nFROM "
            + grouped_tail
        )
        return (
            "SELECT "
            + ", ".join([*dimension_aliases, *outer_values])
            + "\nFROM (\n"
            + inner_sql
            + "\n) AS dashboard_metric_group\nORDER BY "
            + order_by
            + "\nSETTINGS "
            + query_settings,
            value_columns,
        )

    def group_prepared_metric_queries(
        self,
        prepared: tuple[tuple[dict, str, dict], ...],
    ) -> list[tuple[tuple[int, ...], DashboardMetricGroupQuery | None]]:
        """Partition compiled metrics without changing their populations or order."""
        groups = []
        annotation = any(bd.get("type") == "annotation" for bd in self.breakdowns)
        for index, (metric, sql, params) in enumerate(prepared):
            parsed = (
                self._parse_simple_metric_query(sql)
                if not annotation and metric.get("type", "system_metric")
                in {"system_metric", "custom_attribute"}
                else None
            )
            if parsed is None:
                groups.append((None, [index], []))
                continue
            dimensions, value, tail, query_settings = parsed
            key = (dimensions, tail, query_settings, params)
            for reference, indices, values in groups:
                if reference is not None and key == reference:
                    indices.append(index)
                    values.append(value)
                    break
            else:
                groups.append((key, [index], [value]))

        plans = []
        for key, indices, values in groups:
            plan = None
            if len(indices) > 1:
                dimensions, tail, query_settings, params = key
                sql, columns = self._render_metric_group_sql(
                    dimensions=dimensions, value_expressions=tuple(values),
                    tail=tail, query_settings=query_settings,
                )
                plan = DashboardMetricGroupQuery(
                    sql=sql, params=dict(params),
                    metrics=tuple(prepared[index][0] for index in indices),
                    value_columns=columns, has_breakdown=len(dimensions) == 2,
                )
            plans.append((tuple(indices), plan))
        return plans


    def build_compatible_metric_group_query(
        self,
        *,
        latest_state: bool,
    ) -> DashboardMetricGroupQuery | None:
        """Preserve the all-or-none group API for existing callers."""
        metrics = tuple(self.metrics)
        if len(metrics) < 2 or any(
            metric.get("type", "system_metric") not in {"system_metric", "custom_attribute"}
            for metric in metrics
        ) or any(bd.get("type") == "annotation" for bd in self.breakdowns):
            return None
        prepared = tuple(
            (metric, *self._build_metric_query_for_snapshot_mode(metric, latest_state=latest_state))
            for metric in metrics
        )
        groups = self.group_prepared_metric_queries(prepared)
        return groups[0][1] if len(groups) == 1 else None

    def build_raw_metric_group_query(
        self,
        *,
        replica_shard_cluster: str = "",
        replica_shard_count: int = 1,
    ) -> DashboardMetricGroupQuery | None:
        """Return one raw scan, optionally split over physical replicas."""

        raw = self.build_compatible_metric_group_query(latest_state=False)
        if raw is None:
            return None
        cluster = str(replica_shard_cluster or "").strip()
        if cluster and _SAFE_CLUSTER_NAME_RE.fullmatch(cluster) is None:
            raise ValueError("invalid dashboard replica-shard cluster")
        if not 1 <= int(replica_shard_count) <= 16:
            raise ValueError("invalid dashboard replica-shard count")
        if not cluster:
            return raw

        body, separator, query_settings = raw.sql.rpartition("\nSETTINGS ")
        if not separator or body.count("\nFROM spans\n") != 1:
            return None
        params = dict(raw.params)
        params["dashboard_replica_shard_count"] = int(replica_shard_count)
        body = body.replace(
            "\nFROM spans\n",
            f"\nFROM cluster('{cluster}', currentDatabase(), spans) AS spans\n",
            1,
        )
        group_marker = "\nGROUP BY "
        if "\nWHERE " not in body or group_marker not in body:
            return None
        body = body.replace(
            group_marker,
            " AND modulo(toRelativeDayNum(start_time), "
            "%(dashboard_replica_shard_count)s) = shardNum() - 1" + group_marker,
            1,
        )
        return DashboardMetricGroupQuery(
            sql=body + "\nSETTINGS " + query_settings,
            params=params,
            metrics=raw.metrics,
            value_columns=raw.value_columns,
            has_breakdown=raw.has_breakdown,
        )

    def metric_group_results(
        self,
        plan: DashboardMetricGroupQuery,
        rows: list[dict[str, Any]],
    ) -> tuple[bool, list[tuple[dict[str, Any], list[dict[str, Any]]]]]:
        """Split a combined statement back into the established metric shape."""

        metric_results = []
        for metric, value_column in zip(plan.metrics, plan.value_columns, strict=True):
            metric_info = self.metric_info(metric)
            metric_info.update(
                {
                    "source": "traces",
                    "query_complete": True,
                    "query_status": "complete",
                    "query_sampled": False,
                }
            )
            metric_rows = []
            for row in rows:
                if value_column not in row or "time_bucket" not in row:
                    raise ValueError("dashboard metric group result is malformed")
                metric_row = {
                    "time_bucket": row["time_bucket"],
                    "value": row[value_column],
                }
                if plan.has_breakdown:
                    metric_row["breakdown_value"] = row.get("breakdown_value")
                metric_rows.append(metric_row)
            metric_results.append((metric_info, metric_rows))
        return True, metric_results

    def build_metric_query(self, metric: dict) -> tuple[str, dict]:
        sql, params = super().build_metric_query(metric)
        sql = _protect_usage_cdc_columns(sql)
        # The exact replay groups both of its legs by the full spans sorting
        # key, so ordered aggregation streams one merged stream per selected
        # part - 700 to 1,400 parts a window on a mid-size tenant - and that
        # machinery is about three quarters of the statement's peak, for a
        # set operation whose result cannot depend on the execution strategy.
        # That statement states hash execution; every other dashboard shape
        # keeps the v2 default.
        sql = _append_v2_settings(
            rewrite_v1_sql_to_v2(sql),
            aggregation_in_order=_EXACT_REPLAY_CANDIDATE_CTE not in sql,
        )
        sql = _restore_usage_cdc_columns(sql)
        # Mixed-table query: rewrite already fixed spans refs, now restore
        # _peerdb_is_deleted for every legacy-table alias.
        for alias in _LEGACY_TABLE_RE.findall(sql):
            sql = sql.replace(f"{alias}.is_deleted", f"{alias}._peerdb_is_deleted")
        return sql, params


__all__ = ["DashboardQueryBuilderV2"]
