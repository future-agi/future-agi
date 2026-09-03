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
    compile_span_filter_plans,
)
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin
from tracer.services.clickhouse.v2.query_builders.filters import (
    rewrite_and_apply_v2_settings,
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

    # Dashboard charts prioritize bounded interactive reads over waiting for a
    # table-wide ReplacingMergeTree collapse. An unmerged replacement can
    # temporarily overcount a historical bucket until the background merge.
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
    ) -> tuple[str, dict[str, object]]:
        """Keep candidate bindings separate from the outer exact predicate."""

        renamed_params: dict[str, object] = {}
        for parameter_name, parameter_value in plan_params.items():
            candidate_name = f"dashboard_candidate_{parameter_name}"
            predicate = predicate.replace(
                f"%({parameter_name})s",
                f"%({candidate_name})s",
            )
            renamed_params[candidate_name] = parameter_value
        return predicate, renamed_params

    def _exact_filter_candidate_plan(
        self,
        per_metric_filters: list[dict],
    ) -> LatestFilterPredicate | None:
        """Choose one exhaustive, index-usable positive attribute witness.

        The witness narrows immutable identities only. The replay source still
        resolves the latest physical row for every candidate, and the ordinary
        dashboard WHERE clause reapplies every filter exactly. Filter shapes
        without an exhaustive raw value witness use the ordinary raw spans
        source.
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

        if not candidates:
            return None
        return min(
            candidates,
            key=lambda plan: (
                plan.raw_witness_rank if plan.raw_witness_rank is not None else 10_000
            ),
        )

    def _exact_filter_replay_source(
        self,
        per_metric_filters: list[dict],
        alias: str,
        params: dict[str, object] | None,
    ) -> str | None:
        """Return an exact latest-row source seeded by one safe value witness."""

        if params is None:
            return None
        plan = self._exact_filter_candidate_plan(per_metric_filters)
        if plan is None or plan.raw_graph_value_witness_predicate is None:
            return None

        witness, candidate_params = self._candidate_parameter_namespace(
            plan.raw_graph_value_witness_predicate,
            plan.params,
        )
        params.update(candidate_params)
        witness = self._qualify_span_expression(
            witness,
            alias="dashboard_candidate_source",
        )
        source_alias = "spans" if alias == "spans" else alias
        return f"""(
            WITH dashboard_filter_candidate_identities AS (
                SELECT
                    dashboard_candidate_source.project_id AS project_id,
                    dashboard_candidate_source.observation_type
                        AS observation_type,
                    dashboard_candidate_source.service_name AS service_name,
                    toStartOfHour(
                        dashboard_candidate_source.start_time
                    ) AS identity_hour,
                    dashboard_candidate_source.trace_id AS trace_id,
                    dashboard_candidate_source.id AS id
                FROM spans AS dashboard_candidate_source
                PREWHERE dashboard_candidate_source.project_id
                            IN %(project_ids)s
                  AND dashboard_candidate_source.start_time
                            >= %(start_date)s
                  AND dashboard_candidate_source.start_time
                            < %(end_date)s
                WHERE {witness}
                GROUP BY
                    dashboard_candidate_source.project_id,
                    dashboard_candidate_source.observation_type,
                    dashboard_candidate_source.service_name,
                    identity_hour,
                    dashboard_candidate_source.trace_id,
                    dashboard_candidate_source.id
            )
            SELECT dashboard_replay_source.*
            FROM spans AS dashboard_replay_source
            PREWHERE tuple(
                dashboard_replay_source.project_id,
                dashboard_replay_source.observation_type,
                dashboard_replay_source.service_name,
                toStartOfHour(dashboard_replay_source.start_time),
                dashboard_replay_source.trace_id,
                dashboard_replay_source.id
            ) IN (
                SELECT
                    project_id,
                    observation_type,
                    service_name,
                    identity_hour,
                    trace_id,
                    id
                FROM dashboard_filter_candidate_identities
            )
            ORDER BY dashboard_replay_source._peerdb_version DESC
            LIMIT 1 BY
                dashboard_replay_source.project_id,
                dashboard_replay_source.observation_type,
                dashboard_replay_source.service_name,
                toStartOfHour(dashboard_replay_source.start_time),
                dashboard_replay_source.trace_id,
                dashboard_replay_source.id
        ) AS {source_alias}"""

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
            )
            if candidate_source is not None:
                return candidate_source
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

    def _build_custom_attr_query(
        self,
        metric: dict,
        aggregation: str,
        bucket_fn: str,
        per_metric_filters: list[dict],
        params: dict,
    ) -> tuple[str, dict]:
        """Resolve custom metrics through the selected spans snapshot mode.

        Interactive dashboard reads use the base builder's one-pass raw
        aggregation. The candidate/replay path remains available only when a
        caller explicitly requests latest-state span semantics.
        """

        if not self._latest_state_spans_required:
            return super()._build_custom_attr_query(
                metric,
                aggregation,
                bucket_fn,
                per_metric_filters,
                params,
            )

        if (
            metric.get("attribute_type", "number") != "number"
            or self.breakdowns
            or self.global_filters
            or per_metric_filters
        ):
            return super()._build_custom_attr_query(
                metric,
                aggregation,
                bucket_fn,
                per_metric_filters,
                params,
            )

        attr_key = _sanitize_attr_key(metric.get("attribute_key", ""))
        params = dict(params)
        params["custom_metric_attr_key"] = attr_key
        aggregate = AGGREGATIONS.get(aggregation, "avg({col})").format(
            col="metric_value"
        )
        sql = f"""
            WITH custom_metric_candidate_identities AS (
                /*
                 * Use the deployed typed-Map key bloom index only to discover
                 * immutable span identities that have carried this metric. The
                 * latest-state stage below replays every version of those
                 * identities. Applying the mutable predicate there could hide
                 * a tombstone or key removal and resurrect an older value.
                 */
                SELECT
                    custom_metric_candidate_source.project_id AS project_id,
                    custom_metric_candidate_source.observation_type
                        AS observation_type,
                    custom_metric_candidate_source.service_name AS service_name,
                    toStartOfHour(
                        custom_metric_candidate_source.start_time
                    ) AS identity_hour,
                    custom_metric_candidate_source.trace_id AS trace_id,
                    custom_metric_candidate_source.id AS id
                FROM spans AS custom_metric_candidate_source
                PREWHERE custom_metric_candidate_source.project_id
                            IN %(project_ids)s
                  AND custom_metric_candidate_source.start_time
                            >= %(start_date)s
                  AND custom_metric_candidate_source.start_time
                            < %(end_date)s
                WHERE indexHint(has(mapKeys(
                          custom_metric_candidate_source.attrs_number
                      ), %(custom_metric_attr_key)s))
                  AND mapContains(
                          custom_metric_candidate_source.attrs_number,
                          %(custom_metric_attr_key)s
                      )
                GROUP BY
                    custom_metric_candidate_source.project_id,
                    custom_metric_candidate_source.observation_type,
                    custom_metric_candidate_source.service_name,
                    identity_hour,
                    custom_metric_candidate_source.trace_id,
                    custom_metric_candidate_source.id
            ), latest_custom_metric_spans AS (
                SELECT
                    custom_metric_source.project_id,
                    custom_metric_source.observation_type,
                    custom_metric_source.service_name,
                    toStartOfHour(
                        custom_metric_source.start_time
                    ) AS identity_hour,
                    custom_metric_source.trace_id,
                    custom_metric_source.id,
                    argMax(
                        tuple(
                            custom_metric_source.is_deleted,
                            custom_metric_source.start_time,
                            mapContains(
                                custom_metric_source.attrs_number,
                                %(custom_metric_attr_key)s
                            ),
                            custom_metric_source.attrs_number[
                                %(custom_metric_attr_key)s
                            ]
                        ),
                        custom_metric_source._version
                    ) AS latest_metric_state
                FROM spans AS custom_metric_source
                INNER JOIN custom_metric_candidate_identities
                    AS custom_metric_candidate
                  ON custom_metric_candidate.project_id
                        = custom_metric_source.project_id
                 AND custom_metric_candidate.observation_type
                        = custom_metric_source.observation_type
                 AND custom_metric_candidate.service_name
                        = custom_metric_source.service_name
                 AND custom_metric_candidate.identity_hour
                        = toStartOfHour(custom_metric_source.start_time)
                 AND custom_metric_candidate.trace_id
                        = custom_metric_source.trace_id
                 AND custom_metric_candidate.id = custom_metric_source.id
                PREWHERE custom_metric_source.project_id IN %(project_ids)s
                  /*
                   * Widen exact event bounds to their storage-key hours so a
                   * corrected start_time still participates in argMax. The
                   * native driver renders datetime parameters as SQL string
                   * literals, so type them before applying date functions.
                   */
                  AND custom_metric_source.start_time
                            >= toStartOfHour(toDateTime64(
                                %(start_date)s, 6, 'UTC'
                            ))
                  AND custom_metric_source.start_time
                            < toStartOfHour(toDateTime64(
                                %(end_date)s, 6, 'UTC'
                            )) + INTERVAL 1 HOUR
                GROUP BY
                    custom_metric_source.project_id,
                    custom_metric_source.observation_type,
                    custom_metric_source.service_name,
                    identity_hour,
                    custom_metric_source.trace_id,
                    custom_metric_source.id
            ), live_custom_metric_spans AS (
                SELECT
                    tupleElement(latest_metric_state, 2) AS start_time,
                    tupleElement(latest_metric_state, 4) AS metric_value
                FROM latest_custom_metric_spans
                WHERE tupleElement(latest_metric_state, 1) = 0
                  AND tupleElement(latest_metric_state, 3) = 1
                  AND tupleElement(latest_metric_state, 2) >= %(start_date)s
                  AND tupleElement(latest_metric_state, 2) < %(end_date)s
            )
            SELECT
                {bucket_fn}(start_time) AS time_bucket,
                {aggregate} AS value
            FROM live_custom_metric_spans
            GROUP BY time_bucket
            ORDER BY time_bucket
        """
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

    def build_compatible_metric_group_query(
        self,
        *,
        latest_state: bool,
    ) -> DashboardMetricGroupQuery | None:
        """Combine all compatible span metrics into one physical scan.

        Returning ``None`` is deliberate and fail-closed. The caller then uses
        the unchanged one-query-per-metric path; this optimizer never guesses
        across different filters, metric keys, relation joins, or row scopes.
        """

        metrics = tuple(self.metrics)
        if len(metrics) < 2 or any(
            metric.get("type", "system_metric")
            not in {"system_metric", "custom_attribute"}
            for metric in metrics
        ):
            return None
        if any(breakdown.get("type") == "annotation" for breakdown in self.breakdowns):
            return None

        reference_dimensions: tuple[str, ...] | None = None
        reference_tail: str | None = None
        reference_settings: str | None = None
        reference_params: dict[str, Any] | None = None
        value_expressions: list[str] = []
        for metric in metrics:
            sql, params = self._build_metric_query_for_snapshot_mode(
                metric,
                latest_state=latest_state,
            )
            parsed = self._parse_simple_metric_query(sql)
            if parsed is None:
                return None
            dimensions, value_expression, tail, query_settings = parsed
            if reference_dimensions is None:
                reference_dimensions = dimensions
                reference_tail = tail
                reference_settings = query_settings
                reference_params = dict(params)
            elif (
                dimensions != reference_dimensions
                or tail != reference_tail
                or query_settings != reference_settings
                or params != reference_params
            ):
                return None
            value_expressions.append(value_expression)

        assert reference_dimensions is not None
        assert reference_tail is not None
        assert reference_settings is not None
        assert reference_params is not None
        sql, value_columns = self._render_metric_group_sql(
            dimensions=reference_dimensions,
            value_expressions=tuple(value_expressions),
            tail=reference_tail,
            query_settings=reference_settings,
        )
        return DashboardMetricGroupQuery(
            sql=sql,
            params=reference_params,
            metrics=metrics,
            value_columns=value_columns,
            has_breakdown=len(reference_dimensions) == 2,
        )

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
        sql = rewrite_and_apply_v2_settings(sql)
        sql = _restore_usage_cdc_columns(sql)
        # Mixed-table query: rewrite already fixed spans refs, now restore
        # _peerdb_is_deleted for every legacy-table alias.
        for alias in _LEGACY_TABLE_RE.findall(sql):
            sql = sql.replace(f"{alias}.is_deleted", f"{alias}._peerdb_is_deleted")
        return sql, params


__all__ = ["DashboardQueryBuilderV2"]
