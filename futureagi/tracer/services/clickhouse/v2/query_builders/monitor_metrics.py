"""
v2 MonitorMetrics query builder — targets the CH 25.3 spans schema.

Subclass + post-rewrite. Monitors poll metric values (current + historical
+ time series) for the alerting/monitoring surface; they're high-frequency
read-only and hit `spans` heavily, so the v2 typed-Map columns reduce
per-poll cost.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

from tracer.services.clickhouse.query_builders.monitor_metrics import (
    MonitorMetricsQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.filters import rewrite_and_apply_v2_settings


class MonitorMetricsQueryBuilderV2(MonitorMetricsQueryBuilder):
    """Drop-in v2 MonitorMetrics builder."""

    def build(self) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build()
        return rewrite_and_apply_v2_settings(sql), params

    def build_metric_value_query(self, *args, **kwargs) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build_metric_value_query(*args, **kwargs)
        return rewrite_and_apply_v2_settings(sql), params

    def build_historical_stats_query(self, *args, **kwargs) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build_historical_stats_query(*args, **kwargs)
        return rewrite_and_apply_v2_settings(sql), params

    def build_time_series_query(self, *args, **kwargs) -> Tuple[str, Dict[str, Any]]:
        sql, params = super().build_time_series_query(*args, **kwargs)
        return rewrite_and_apply_v2_settings(sql), params


__all__ = ["MonitorMetricsQueryBuilderV2"]
