"""
v2 MonitorMetrics query builder — targets the CH 25.3 spans schema.

Subclass + post-rewrite. Monitors poll metric values (current + historical
+ time series) for the alerting/monitoring surface; they're high-frequency
read-only and hit `spans` heavily, so the v2 typed-Map columns reduce
per-poll cost. `V2RewriteMixin` routes every inherited `build*` method's SQL
through the v2 rewriter at one boundary.
"""

from __future__ import annotations

from tracer.services.clickhouse.query_builders.monitor_metrics import (
    MonitorMetricsQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders._rewrite import V2RewriteMixin


class MonitorMetricsQueryBuilderV2(V2RewriteMixin, MonitorMetricsQueryBuilder):
    """Drop-in v2 MonitorMetrics builder."""


__all__ = ["MonitorMetricsQueryBuilderV2"]
