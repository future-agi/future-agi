"""
v2 Dashboard query builder — targets the CH 25.3 spans schema.

Subclass + post-rewrite. The v1 dashboard builder emits 1 SQL query per
dashboard metric (latency, p95, model breakdown, custom-attribute pivots,
etc.). Each metric type goes through `build_metric_query()`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from tracer.services.clickhouse.query_builders.dashboard import DashboardQueryBuilder
from tracer.services.clickhouse.v2.query_builders.filters import rewrite_and_apply_v2_settings


class DashboardQueryBuilderV2(DashboardQueryBuilder):
    """Drop-in v2 Dashboard builder.

    The v1 builder is unusual in that it's NOT a subclass of BaseQueryBuilder —
    it owns its own composition logic for the metric → SQL mapping. We
    inherit it the same way and let the rewrite happen at every public
    surface that returns SQL strings.
    """

    def build_metric_query(self, metric: dict) -> Tuple[str, dict]:
        sql, params = super().build_metric_query(metric)
        return rewrite_and_apply_v2_settings(sql), params

    def build_all_queries(self) -> List[Tuple[str, dict, dict]]:
        # v1 returns [(sql, params, meta), …]. Apply the rewrite to each sql.
        results = super().build_all_queries()
        return [(rewrite_and_apply_v2_settings(sql), params, meta) for sql, params, meta in results]


__all__ = ["DashboardQueryBuilderV2"]
