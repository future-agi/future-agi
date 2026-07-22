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

from typing import Any

from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin
from tracer.services.clickhouse.v2.query_builders.filters import (
    ClickHouseFilterBuilderV2,
)


class TraceListQueryBuilderV2(V2RewriteMixin, TraceListQueryBuilder):
    """Drop-in v2 TraceList builder.

    Callers swap one import line:
        v1: from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
        v2: from tracer.services.clickhouse.v2.query_builders.trace_list  import TraceListQueryBuilderV2

    Or route via the shadow harness in v2/shadow.py.
    """

    _v2_rewrite_exclude = frozenset({"build_eval_query", "build_annotation_query"})

    # Use the v2 filter compiler so filters read the v2 dimension tables
    # (end_users, etc.) instead of the dropped legacy CDC tables.
    _FILTER_BUILDER_CLS = ClickHouseFilterBuilderV2

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
            # V2RewriteMixin appends the v2 SETTINGS to the returned SQL.
            return sql, params

        # Slow path: v1's raw uniq over spans; the mixin rewrites + applies SETTINGS.
        return super().build_count_query()


__all__ = ["TraceListQueryBuilderV2"]
