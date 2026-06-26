"""
v2 Dashboard query builder — targets the CH 25.3 spans schema.

Subclass + post-rewrite. The v1 dashboard builder emits 1 SQL query per
dashboard metric (latency, p95, model breakdown, custom-attribute pivots,
etc.). Each metric type goes through `build_metric_query()`; `build_all_queries`
returns `[(sql, params, meta), …]`. `V2RewriteMixin` routes both through the v2
rewriter at one boundary (it handles both the `(sql, params)` and the
list-of-triples return shapes).
"""

from __future__ import annotations

from tracer.services.clickhouse.query_builders.dashboard import DashboardQueryBuilder
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin


class DashboardQueryBuilderV2(V2RewriteMixin, DashboardQueryBuilder):
    """Drop-in v2 Dashboard builder.

    The v1 builder is unusual in that it's NOT a subclass of BaseQueryBuilder —
    it owns its own composition logic for the metric → SQL mapping. We inherit
    it the same way and let the mixin rewrite every public surface that returns
    SQL strings.
    """


__all__ = ["DashboardQueryBuilderV2"]
