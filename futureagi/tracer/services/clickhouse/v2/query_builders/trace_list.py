"""
v2 TraceList query builder — targets the CH 25.3 spans schema.

Same pattern as v2/span_list.py: SUBCLASS the v1 builder, rewrite the
compiled SQL output. The v1 TraceList builder reads from `spans` (legacy
24.10 columns) plus joins to `tracer_eval_logger` and `model_hub_score`.
We rewrite the `spans` table references; eval and annotation joins are
unchanged.

Methods overridden:
  - `build()` — Phase 1: light trace+root-span page (no input/output)
  - `build_content_query()` — Phase 2: heavy attrs maps + metadata
  - `build_span_attributes_query()` — Phase 3: attributes_extra fetch
  - `build_count_query()` — pagination count
  - `build_span_count_query()` — per-trace span tally
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.v2.query_builders.filters import rewrite_and_apply_v2_settings


class TraceListQueryBuilderV2(TraceListQueryBuilder):
    """Drop-in v2 TraceList builder.

    Callers swap one import line:
        v1: from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
        v2: from tracer.services.clickhouse.v2.query_builders.trace_list  import TraceListQueryBuilderV2

    Or route via the shadow harness in v2/shadow.py.
    """

    def build(self) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build()
        return rewrite_and_apply_v2_settings(sql), params

    def build_content_query(self, trace_ids: List[str]) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build_content_query(trace_ids)
        return rewrite_and_apply_v2_settings(sql), params

    def build_span_attributes_query(self, *args, **kwargs) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build_span_attributes_query(*args, **kwargs)
        return rewrite_and_apply_v2_settings(sql), params

    def build_count_query(self) -> Tuple[str, Dict[str, Any]]:
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
            f for f in (self.filters or [])
            if (f.get("column_id") or f.get("columnId")) not in ("created_at", "start_time")
        ]
        if not non_time_filters and not self.search and not self.project_version_id:
            # Ensure start_date / end_date are bound even if build() wasn't
            # called first (count is sometimes invoked standalone, e.g. for
            # pagination prefetch). parse_time_range honours any time filter
            # the caller passed and defaults to 30d (see base.py).
            start_date, end_date = self.parse_time_range(self.filters or [])
            params = dict(self.params)
            params["start_date"] = start_date
            params["end_date"] = end_date
            # toStartOfHour requires DateTime, not String — explicitly cast
            # the bound %(start_date)s / %(end_date)s. CH's clickhouse-connect
            # binds Python datetime as ISO-8601 String which would otherwise
            # fail toStartOfHour with ILLEGAL_TYPE_OF_ARGUMENT.
            sql = """
        SELECT uniqExactMerge(uniq_traces_state) AS total
        FROM trace_count_rollup
        WHERE project_id = %(project_id)s
          AND hour >= toStartOfHour(toDateTime(%(start_date)s))
          AND hour <  toStartOfHour(toDateTime(%(end_date)s)) + INTERVAL 1 HOUR
            """
            return rewrite_and_apply_v2_settings(sql), params

        sql, params = super().build_count_query()
        return rewrite_and_apply_v2_settings(sql), params

    def build_span_count_query(self, *args, **kwargs) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build_span_count_query(*args, **kwargs)
        return rewrite_and_apply_v2_settings(sql), params


__all__ = ["TraceListQueryBuilderV2"]
