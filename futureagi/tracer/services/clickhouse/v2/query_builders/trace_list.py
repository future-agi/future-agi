"""
v2 TraceList query builder — targets the CH 25.3 spans schema.

Same pattern as v2/span_list.py: SUBCLASS the v1 builder, rewrite the
compiled SQL output. The v1 TraceList builder reads from `spans` (legacy
24.10 columns) plus joins to `tracer_eval_logger` and `model_hub_score`.

`V2RewriteMixin` routes every inherited `build*` method's SQL through the v2
rewriter at one boundary (no per-method overrides). The only locally-defined
method is `build_count_query`, which carries a rollup fast-path; its SQL is
rewritten by the mixin just like every other.

`build_eval_query` / `build_annotation_query` are excluded from the rewrite:
they read the legacy `tracer_eval_logger` / `model_hub_score` tables, which are
NOT part of the CH 25.3 migration and still carry `_peerdb_is_deleted` (the
spans-side `_peerdb_is_deleted` in those joins resolves via the schema-014
ALIAS). Rewriting them would break those tables.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from django.conf import settings

from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
    _append_v2_settings,
    rewrite_v1_sql_to_v2,
)


class TraceListQueryBuilderV2(V2RewriteMixin, TraceListQueryBuilder):
    """Drop-in v2 TraceList builder.

    Callers swap one import line:
        v1: from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
        v2: from tracer.services.clickhouse.v2.query_builders.trace_list  import TraceListQueryBuilderV2

    Or route via the shadow harness in v2/shadow.py.
    """

    _v2_rewrite_exclude = frozenset(
        {
            "build_eval_query",
            "build_annotation_query",
            # Native typed-JSON aggregation below. Applying the generic bare
            # JSON compatibility rewrite inside argMax would produce invalid
            # SQL, so this complete statement appends v2 settings itself.
            "build_span_attributes_query",
            # Candidate classifiers can contain point-scoped FINAL subqueries
            # over mutable span/eval/annotation state. They disable FINAL skip
            # indexes explicitly below so an older value cannot be resurrected.
            "build_latest_filter_match_query",
        }
    )

    # Use the v2 filter compiler so filters read the v2 dimension tables
    # (end_users, etc.) instead of the dropped legacy CDC tables.
    _FILTER_BUILDER_CLS = ClickHouseFilterBuilderV2

    # ``parent_span_id`` is a non-nullable String on the direct-write CH25
    # table.  Keeping the physical equality (instead of ``IS NULL OR``) and the
    # physical ``is_deleted = 0`` predicate emitted by the inherited seed makes
    # the skinny page eligible for ``proj_root_spans``.  It is still only a
    # superset seed; the view performs full-window scalar latest-state checks.
    ROOT_SEED_PARENT_PREDICATE = "parent_span_id = ''"

    # A trace can contain millions of spans. Keep the aggregate state and wire
    # payload bounded; the query separately returns the canonical root bundle,
    # so truncation can fail explicitly without losing final_status/root attrs.
    ATTRIBUTE_ROWS_PER_TRACE = 128

    def build_latest_filter_match_query(
        self,
        candidate_trace_ids: list[str],
        *,
        filters: list[dict[str, Any]] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Rewrite a bounded classifier without mutable FINAL skip indexes."""

        query, params = super().build_latest_filter_match_query(
            candidate_trace_ids,
            filters=filters,
        )
        if not query:
            return query, params
        query = _append_v2_settings(rewrite_v1_sql_to_v2(query))
        return (
            query.replace(
                "use_skip_indexes_if_final = 1",
                "use_skip_indexes_if_final = 0",
            ),
            params,
        )

    def build_span_attributes_query(
        self, trace_ids: list[str]
    ) -> tuple[str, dict[str, Any]]:
        """Read bounded latest attributes for the visible page per trace.

        Returning one row per live span made a single 25-trace page exceed the
        endpoint's 2,000-result-row guard. An unbounded ``groupArray`` moved the
        same whale into one enormous row, retaining every Map in aggregate
        memory and on the wire. Cap the array, report the exact contributing
        row count, and return the newest live root bundle independently. The
        view uses all rows only when the count proves no truncation; otherwise
        it marks enrichment degraded and exposes only exact root attributes.
        """

        bounded_ids = [str(trace_id) for trace_id in trace_ids if trace_id]
        if not bounded_ids:
            return "", {}
        params: dict[str, Any] = {
            **self.params,
            "attr_trace_ids": tuple(bounded_ids),
        }
        span_window = self._span_time_window(params)
        query = f"""
        SELECT
            grouped_trace_id AS trace_id,
            argMaxIf(
                tuple(
                    latest_attributes_extra,
                    latest_attrs_string,
                    latest_attrs_number,
                    latest_attrs_bool
                ),
                tuple(latest_start_time, grouped_id),
                latest_parent_span_id = ''
            ) AS root_attribute_row,
            countIf(latest_parent_span_id = '') AS root_attribute_count,
            groupArrayIf({self.ATTRIBUTE_ROWS_PER_TRACE})(tuple(
                latest_attributes_extra,
                latest_attrs_string,
                latest_attrs_number,
                latest_attrs_bool
            ),
                latest_parent_span_id != ''
                AND (
                    latest_attributes_extra != '{{}}'
                    OR notEmpty(latest_attrs_string)
                    OR notEmpty(latest_attrs_number)
                    OR notEmpty(latest_attrs_bool)
                )
            ) AS attribute_rows,
            countIf(
                latest_parent_span_id != ''
                AND (
                    latest_attributes_extra != '{{}}'
                    OR notEmpty(latest_attrs_string)
                    OR notEmpty(latest_attrs_number)
                    OR notEmpty(latest_attrs_bool)
                )
            ) AS attribute_row_count
        FROM (
            SELECT
                trace_id AS grouped_trace_id,
                id AS grouped_id,
                argMax(parent_span_id, _version) AS latest_parent_span_id,
                argMax(start_time, _version) AS latest_start_time,
                argMax(toJSONString(attributes_extra), _version)
                    AS latest_attributes_extra,
                argMax(attrs_string, _version) AS latest_attrs_string,
                argMax(attrs_number, _version) AS latest_attrs_number,
                argMax(attrs_bool, _version) AS latest_attrs_bool,
                argMax(is_deleted, _version) AS latest_is_deleted
            FROM {self.TABLE}
            PREWHERE {self.project_filter_sql()}
              AND trace_id IN %(attr_trace_ids)s
              {span_window}
            GROUP BY trace_id, id
        )
        WHERE latest_is_deleted = 0
        GROUP BY grouped_trace_id
        """
        return _append_v2_settings(query), params

    def build_content_query(self, trace_ids: list[str]) -> tuple[str, dict[str, Any]]:
        """Hydrate trace-owned content from the compact ``traces`` table.

        The inherited query probes the 1.7 TiB ``spans`` table by trace-id and
        reads its widest columns. At US scale a 25-id page still touched
        millions of span rows and took ~11 seconds. Trace input/output,
        metadata, and tags are written directly to ``traces`` whose key is
        ``(project_id, id)``; using that authoritative compact table turns the
        hydration into point lookups and avoids reading span attribute maps.
        """
        if not trace_ids:
            return "", {}

        valid_trace_ids = []
        for trace_id in trace_ids:
            try:
                valid_trace_ids.append(UUID(str(trace_id)))
            except (TypeError, ValueError, AttributeError):
                continue
        if not valid_trace_ids:
            return "", {}

        params: dict[str, Any] = {
            **self.params,
            "content_trace_ids": tuple(valid_trace_ids),
        }
        time_fragment = ""
        if self.start_date is not None and self.end_date is not None:
            params["content_start_date"] = self.start_date
            params["content_end_date"] = self.end_date
            time_fragment = (
                "AND created_at >= %(content_start_date)s - INTERVAL 1 DAY\n"
                "          AND created_at < %(content_end_date)s + INTERVAL 1 DAY"
            )

        query = f"""
        SELECT
            toString(id) AS trace_id,
            input,
            output,
            metadata,
            tags AS trace_tags
        FROM traces FINAL
        PREWHERE {self.project_filter_sql()}
          AND id IN %(content_trace_ids)s
        WHERE is_deleted = 0
          {time_fragment}
        """
        return query, params

    def build_count_query(self) -> tuple[str, dict[str, Any]]:
        """Pagination count.

        Fast path: when no per-row filter / search / project-version is set,
        read from the pre-aggregated ``trace_count_rollup`` (schema 012). The
        rollup keys on (project_id, hour) and stores ``uniqExactState(trace_id)``
        for root spans, so the count over any time window is O(buckets).

        Empirically: on the 78K-span dev dataset this drops the count from
        ~20ms (raw uniq over spans) to ~3ms. At trillion-row prod scale the
        raw path scales linearly with row count while the rollup stays
        O(hours × projects); the rollup is the only path that survives.

        Slow path (with filters): fall back to v1's uniq over spans. The
        rollup can't answer filtered counts because it doesn't know about
        attribute-level filter predicates.
        """
        # Fast-path: rollup-backed count is safe whenever the only filters
        # the caller supplied are time bounds (the rollup is itself keyed by
        # hour so the time range applies natively). Search/project_version
        # and any attribute filter still require raw scan.
        non_time_filters = [
            f
            for f in (self.filters or [])
            if (f.get("column_id") or f.get("columnId"))
            not in ("created_at", "start_time")
        ]
        if not non_time_filters and not self.search and not self.project_version_id:
            # Ensure start_date / end_date are bound even if build() wasn't
            # called first (count is sometimes invoked standalone, e.g. for
            # pagination prefetch). parse_time_range honours any time filter
            # the caller passed and defaults to 30d (see base.py).
            start_date, end_date = self.parse_time_range(self.filters or [])
            covered_since = getattr(
                settings, "DASHBOARD_ATTR_ROLLUP_COVERED_SINCE", None
            )
            if not isinstance(covered_since, datetime):
                return super().build_count_query()
            if covered_since.tzinfo is None:
                covered_since = covered_since.replace(tzinfo=UTC)
            comparable_start = start_date
            if comparable_start.tzinfo is None:
                comparable_start = comparable_start.replace(tzinfo=UTC)
            else:
                comparable_start = comparable_start.astimezone(UTC)
            if comparable_start < covered_since.astimezone(UTC):
                return super().build_count_query()

            params = dict(self.params)
            params["start_date"] = start_date
            params["end_date"] = end_date

            start_hour = start_date.replace(minute=0, second=0, microsecond=0)
            end_hour = end_date.replace(minute=0, second=0, microsecond=0)
            interior_start = (
                start_hour
                if start_date == start_hour
                else start_hour + timedelta(hours=1)
            )
            interior_end = end_hour
            project_scope = self.project_filter_sql()
            state_queries = []

            if interior_start < interior_end:
                params["interior_start"] = interior_start
                params["interior_end"] = interior_end
                state_queries.append(
                    "SELECT uniq_traces_state AS state "
                    "FROM trace_count_rollup "
                    f"WHERE {project_scope} "
                    "AND hour >= %(interior_start)s "
                    "AND hour < %(interior_end)s"
                )

            # Exact raw reads are restricted to at most the two partial
            # boundary hours. This preserves arbitrary toolbar timestamps
            # without reintroducing a whole-window root scan.
            first_boundary_end = min(interior_start, end_date)
            if start_date < first_boundary_end:
                params["first_boundary_end"] = first_boundary_end
                state_queries.append(
                    "SELECT uniqExactState(trace_id) AS state "
                    "FROM spans FINAL "
                    f"PREWHERE {project_scope} "
                    "AND start_time >= %(start_date)s "
                    "AND start_time < %(first_boundary_end)s "
                    "WHERE is_deleted = 0 "
                    "AND (parent_span_id IS NULL OR parent_span_id = '')"
                )

            last_boundary_start = max(end_hour, start_date)
            if (
                last_boundary_start < end_date
                and last_boundary_start >= first_boundary_end
            ):
                params["last_boundary_start"] = last_boundary_start
                state_queries.append(
                    "SELECT uniqExactState(trace_id) AS state "
                    "FROM spans FINAL "
                    f"PREWHERE {project_scope} "
                    "AND start_time >= %(last_boundary_start)s "
                    "AND start_time < %(end_date)s "
                    "WHERE is_deleted = 0 "
                    "AND (parent_span_id IS NULL OR parent_span_id = '')"
                )

            if not state_queries:
                return "SELECT toUInt64(0) AS total", params

            sql = (
                "SELECT uniqExactMerge(state) AS total FROM (\n"
                + "\nUNION ALL\n".join(state_queries)
                + "\n)"
            )
            # V2RewriteMixin appends the v2 SETTINGS to the returned SQL.
            return sql, params

        # Slow path: v1's raw uniq over spans; the mixin rewrites + applies SETTINGS.
        return super().build_count_query()


__all__ = ["TraceListQueryBuilderV2"]
