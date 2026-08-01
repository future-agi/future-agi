"""
Base class for Dashboard Query Builders (traces, simulation, dataset).

Extracts shared utilities and methods that are duplicated across
:class:`DashboardQueryBuilder`, :class:`SimulationQueryBuilder`, and
:class:`DatasetQueryBuilder`.
"""

from datetime import UTC, date, datetime
from typing import Any

from tracer.services.clickhouse.query_builders.dashboard import (
    AGGREGATIONS,
    AVERAGING_AGGREGATIONS,
    FILTER_OPERATORS,
    GRANULARITY_TO_CH,
    PRESET_RANGES,
    _coerce_filter_value,
    _generate_time_buckets,
    _parse_dt,
    rescale_rate_to_percent,
)

# Re-export for convenience so subclasses can import from this module.
# ``rescale_rate_to_percent`` and ``AVERAGING_AGGREGATIONS`` live in
# ``dashboard.py`` (the import root) to avoid the cycle that would
# otherwise force inline imports here.
__all__ = [
    "AGGREGATIONS",
    "AVERAGING_AGGREGATIONS",
    "FILTER_OPERATORS",
    "GRANULARITY_TO_CH",
    "PRESET_RANGES",
    "rescale_rate_to_percent",
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

    def build_all_queries(self) -> list[tuple[str, dict, dict]]:
        """Build queries for all metrics.

        Returns:
            List of (sql, params, metric_info) tuples.
        """
        results = []
        for metric in self.metrics:
            sql, params = self.build_metric_query(metric)
            results.append((sql, params, self.metric_info(metric)))
        return results

    def metric_info(self, metric: dict) -> dict:
        """Build the response metadata for a single metric.

        Exposed so callers can construct a metric's ``metric_info`` without
        building its SQL — e.g. to attach a per-metric error when the build or
        execution fails, keeping the rest of the dashboard's widgets intact.
        """
        return {
            "id": metric.get("id", ""),
            "name": metric.get("display_name")
            or metric.get("displayName")
            or metric.get("name", ""),
            "type": metric.get("type", "system_metric"),
            "aggregation": metric.get("aggregation", "avg"),
        }

    def build_metric_query(self, metric: dict) -> tuple[str, dict]:
        """Build ClickHouse SQL for a single metric. Subclasses must override."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Shared result formatting helpers
    # ------------------------------------------------------------------

    def _build_series_data(
        self,
        rows: list[dict],
        name_map: dict[str, str] | None = None,
        name_map_breakdown: str | None = None,
    ) -> dict[str, dict[str, Any]]:
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

        series_data: dict[str, dict[str, Any]] = {}
        for row in rows:
            breakdown_key = str(row.get("breakdown_value", "total"))
            if has_map_breakdown and name_map:
                breakdown_key = name_map.get(breakdown_key, breakdown_key)
            if breakdown_key not in series_data:
                series_data[breakdown_key] = {}
            ts = row.get("time_bucket", "")
            if hasattr(ts, "isoformat"):
                if isinstance(ts, date) and not isinstance(ts, datetime):
                    ts = datetime(ts.year, ts.month, ts.day, tzinfo=UTC)
                elif hasattr(ts, "tzinfo") and ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                ts = ts.isoformat()
            val = row.get("value")
            if isinstance(val, float):
                val = round(val, 6)
            series_data[breakdown_key][ts] = val

        if not series_data:
            series_data["total"] = {}

        # Keep the highest-volume series first.
        MAX_SERIES = 100
        if "total" not in series_data:
            ranked = sorted(
                series_data.items(),
                key=lambda kv: sum(v for v in kv[1].values() if v is not None),
                reverse=True,
            )
            if len(ranked) > MAX_SERIES:
                ranked = ranked[:MAX_SERIES]
            series_data = dict(ranked)

        return series_data

    def _format_metric_result(
        self,
        metric_info: dict,
        rows: list[dict],
        all_buckets: list[str],
        unit_map: dict[str, str],
        name_map: dict[str, str] | None = None,
        name_map_breakdown: str | None = None,
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
        metric_key = metric_info.get("id") or metric_name
        unit = unit_map.get(metric_key, unit_map.get(metric_name, ""))

        series_data = self._build_series_data(rows, name_map, name_map_breakdown)

        series = []
        for name, data_map in series_data.items():
            filled = []
            for bucket_ts in all_buckets:
                filled.append(
                    {
                        "timestamp": bucket_ts,
                        # Preserve missing buckets as null so frontend can
                        # distinguish "no data" from a real 0 value.
                        "value": data_map[bucket_ts] if bucket_ts in data_map else None,
                    }
                )
            series.append({"name": name, "data": filled})

        result = {
            "id": metric_info.get("id", ""),
            "name": metric_name,
            "aggregation": metric_info.get("aggregation", "avg"),
            "unit": unit,
            "series": series,
        }
        # Surface a per-metric error (e.g. an invalid metric/aggregation combo)
        # so one bad widget doesn't fail the whole dashboard query.
        if metric_info.get("error"):
            result["error"] = metric_info["error"]
        for status_key in (
            "query_complete",
            "query_status",
            "query_error_code",
        ):
            if status_key in metric_info:
                result[status_key] = metric_info[status_key]
        return result
