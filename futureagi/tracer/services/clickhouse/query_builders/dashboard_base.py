"""
Base class for Dashboard Query Builders (traces, simulation, dataset).

Extracts shared utilities and methods that are duplicated across
:class:`DashboardQueryBuilder`, :class:`SimulationQueryBuilder`, and
:class:`DatasetQueryBuilder`.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from tracer.services.clickhouse.query_builders.dashboard import (
    AGGREGATIONS,
    FILTER_OPERATORS,
    GRANULARITY_TO_CH,
    PRESET_RANGES,
    _coerce_filter_value,
    _generate_time_buckets,
    _parse_dt,
)

# Re-export for convenience so subclasses can import from this module
__all__ = [
    "AGGREGATIONS",
    "FILTER_OPERATORS",
    "GRANULARITY_TO_CH",
    "PRESET_RANGES",
    "_coerce_filter_value",
    "_generate_time_buckets",
    "_parse_dt",
    "DashboardQueryBuilderBase",
]


class DashboardQueryBuilderBase:
    """Shared base for all dashboard-style query builders.

    Provides ``build_all_queries`` and helpers for the common
    series-building logic in ``format_results``.

    Subclasses must implement:
    - ``build_metric_query(metric) -> (sql, params)``
    - ``parse_time_range() -> (start_datetime, end_datetime)``
    """

    def __init__(self, query_config: dict) -> None:
        self.config = query_config
        self.granularity = query_config.get("granularity", "day")
        self.metrics = query_config.get("metrics", [])
        self.global_filters = query_config.get("filters", [])
        self.breakdowns = query_config.get("breakdowns", [])

    # ------------------------------------------------------------------
    # Build all queries
    # ------------------------------------------------------------------

    def build_all_queries(self) -> List[Tuple[str, dict, dict]]:
        """Build queries for all metrics.

        Returns:
            List of (sql, params, metric_info) tuples.
        """
        results = []
        for metric in self.metrics:
            sql, params = self.build_metric_query(metric)
            metric_info = {
                "id": metric.get("id", ""),
                "name": metric.get("displayName")
                or metric.get("display_name")
                or metric.get("name", ""),
                "type": metric.get("type", "system_metric"),
                "aggregation": metric.get("aggregation", "avg"),
            }
            results.append((sql, params, metric_info))
        return results

    def build_metric_query(self, metric: dict) -> Tuple[str, dict]:
        """Build ClickHouse SQL for a single metric. Subclasses must override."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Shared result formatting helpers
    # ------------------------------------------------------------------

    def _build_series_data(
        self,
        rows: List[dict],
        name_map: Optional[Dict[str, str]] = None,
        name_map_breakdown: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Build the intermediate series_data dict from raw rows.

        Args:
            rows: ClickHouse result rows with ``time_bucket``, ``value``,
                and optionally ``breakdown_value`` keys.
            name_map: Optional mapping to resolve breakdown values
                (e.g. project UUID -> name).
            name_map_breakdown: The breakdown name that triggers name_map
                resolution (e.g. "project", "dataset").

        Returns:
            Dict of ``{series_name: {iso_timestamp: value}}``.
        """
        has_map_breakdown = name_map_breakdown and any(
            bd.get("name") == name_map_breakdown for bd in self.breakdowns
        )

        series_data: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            breakdown_key = str(row.get("breakdown_value", "total"))
            if has_map_breakdown and name_map:
                breakdown_key = name_map.get(breakdown_key, breakdown_key)
            if breakdown_key not in series_data:
                series_data[breakdown_key] = {}
            ts = row.get("time_bucket", "")
            if hasattr(ts, "isoformat"):
                if isinstance(ts, date) and not isinstance(ts, datetime):
                    ts = datetime(ts.year, ts.month, ts.day, tzinfo=timezone.utc)
                elif hasattr(ts, "tzinfo") and ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                ts = ts.isoformat()
            val = row.get("value")
            if isinstance(val, float):
                val = round(val, 6)
            series_data[breakdown_key][ts] = val

        if not series_data:
            series_data["total"] = {}

        # Limit breakdown series
        MAX_SERIES = 100
        if len(series_data) > MAX_SERIES and "total" not in series_data:
            ranked = sorted(
                series_data.items(),
                key=lambda kv: sum(v for v in kv[1].values() if v is not None),
                reverse=True,
            )[:MAX_SERIES]
            series_data = dict(ranked)

        return series_data

    def _format_metric_result(
        self,
        metric_info: dict,
        rows: List[dict],
        all_buckets: List[str],
        unit_map: Dict[str, str],
        name_map: Optional[Dict[str, str]] = None,
        name_map_breakdown: Optional[str] = None,
    ) -> dict:
        """Format a single metric's results into the response structure.

        Args:
            metric_info: Metric metadata dict.
            rows: Raw ClickHouse result rows.
            all_buckets: Pre-generated time bucket ISO strings.
            unit_map: Mapping of metric names to unit strings.
            name_map: Optional name resolution map for breakdowns.
            name_map_breakdown: Breakdown name that triggers name_map usage.

        Returns:
            Formatted metric dict with ``id``, ``name``, ``aggregation``,
            ``unit``, and ``series``.
        """
        metric_name = metric_info.get("name", "")
        unit = unit_map.get(metric_name, "")

        series_data = self._build_series_data(rows, name_map, name_map_breakdown)

        series = []
        for name in sorted(series_data.keys()):
            data_map = series_data[name]
            filled = []
            for bucket_ts in all_buckets:
                filled.append(
                    {
                        "timestamp": bucket_ts,
                        # Preserve missing buckets as null so frontend can
                        # distinguish "no data" from a real 0 value.
                        "value": data_map[bucket_ts]
                        if bucket_ts in data_map
                        else None,
                    }
                )
            series.append({"name": name, "data": filled})

        return {
            "id": metric_info.get("id", ""),
            "name": metric_name,
            "aggregation": metric_info.get("aggregation", "avg"),
            "unit": unit,
            "series": series,
        }
