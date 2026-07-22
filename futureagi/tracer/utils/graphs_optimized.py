"""
Optimized Graph Data Engine for handling 1M+ datapoints
Industry best practices implementation for scalable time-series aggregation

Key Optimizations:
1. Database-level aggregation using PostgreSQL functions
2. Subquery-based filtering (no IN clauses with huge ID lists)
3. Efficient time bucketing with date_trunc
4. Minimal memory footprint
5. Query result caching support
6. Composite index utilization
"""

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Dict, Generator, List, Optional

import structlog
from django.db import models
from django.db.models import (
    Avg,
    Case,
    Count,
    F,
    FloatField,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Coalesce, TruncDay, TruncHour, TruncMonth

logger = structlog.get_logger(__name__)
from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from tracer.models.custom_eval_config import CustomEvalConfig, EvalOutputType
from tracer.models.observation_span import EvalLogger, ObservationSpan
from tracer.models.trace import Trace
from tracer.utils.filters import FilterEngine


CHART_SPAN_FIELD_MAP = {
    "avg_cost": "cost",
    "cost": "cost",
    "total_cost": "cost",
    "avg_latency": "latency_ms",
    "latency": "latency_ms",
    "latency_ms": "latency_ms",
    "tokens": "total_tokens",
    "total_tokens": "total_tokens",
    "input_tokens": "prompt_tokens",
    "prompt_tokens": "prompt_tokens",
    "output_tokens": "completion_tokens",
    "completion_tokens": "completion_tokens",
    "node_type": "observation_type",
    "trace_id": "trace_id",
    "span_id": "id",
    "session_id": "trace__session_id",
    "created_at": "created_at",
    "start_time": "start_time",
    "name": "name",
    "run_name": "trace__name",
    "span_name": "name",
    "trace_name": "trace__name",
    "user_id": "end_user__user_id",
    "status": "status",
    "prompt_template_version": "prompt_version__template_version",
    "prompt_label_id": "prompt_label_id",
    "prompt_label_name": "prompt_label__name",
}


def parse_time_filters(filters: List[Dict]) -> tuple:
    """
    Extract start and end dates from filter configuration.

    Args:
        filters: List of filter dictionaries

    Returns:
        Tuple of (start_date, end_date)
    """
    start_date = None
    end_date = None

    for filter_item in filters:
        filter_config = filter_item.get("filter_config", {})
        if filter_config.get("filter_type") == "datetime":
            filter_value = filter_config.get("filter_value")
            if isinstance(filter_value, list) and len(filter_value) >= 2:
                start_date = datetime.strptime(filter_value[0], "%Y-%m-%dT%H:%M:%S.%fZ")
                end_date = datetime.strptime(filter_value[1], "%Y-%m-%dT%H:%M:%S.%fZ")
                break

    # Default to last 7 days if no filters
    if not start_date:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=7)

    return start_date, end_date


def apply_chart_span_filters(base_queryset, filters: List[Dict]):
    """
    Apply non-date Observe chart filters to ObservationSpan querysets.

    The date window is still parsed separately by parse_time_filters so chart
    bucketing can fill empty timestamps over the requested range.
    """
    if not filters:
        return base_queryset

    system_metric_q = FilterEngine.get_filter_conditions_for_system_metrics(
        filters,
        field_map=CHART_SPAN_FIELD_MAP,
    )
    span_attribute_q = FilterEngine.get_filter_conditions_for_span_attributes(filters)
    return base_queryset.filter(system_metric_q).filter(span_attribute_q)


def get_truncate_function(interval: str):
    """
    Get the appropriate Django ORM truncate function for time bucketing.

    Args:
        interval: Time interval ('hour', 'day', 'week', 'month')

    Returns:
        Django truncate function
    """
    interval_map = {
        "hour": TruncHour,
        "day": TruncDay,
        "week": TruncDay,  # We'll group by day and then aggregate weeks
        "month": TruncMonth,
    }

    trunc_func = interval_map.get(interval.lower())
    if not trunc_func:
        raise ValueError(f"Unsupported interval: {interval}")

    return trunc_func


def get_eval_graph_data(
    interval: str,
    filters: List[Dict],
    property: str,
    observe_type: str,
    req_data_config: Dict,
    eval_logger_filters: Dict,
) -> Any:
    """
    Optimized version of get_eval_graph_data using database-level aggregation.

    Handles 1M+ datapoints efficiently by:
    1. Using subqueries instead of loading IDs into memory
    2. Database-level time bucketing and aggregation
    3. Query result caching
    4. Minimal memory footprint

    Args:
        interval: Time interval ('hour', 'day', 'week', 'month')
        filters: List of filter configurations
        property: Aggregation property (e.g., 'average')
        observe_type: Type of observation ('trace' or 'span')
        req_data_config: Request data configuration
        eval_logger_filters: Filters containing:
            - trace_ids_queryset: Lazy queryset for trace filtering (for observe_type='trace')
            - span_ids_queryset: Lazy queryset for span filtering (for observe_type='span')

    Returns:
        Graph data dictionary or list
    """
    # Extract configuration
    custom_eval_config_id = req_data_config.get("id")
    if not custom_eval_config_id:
        raise ValueError("Custom eval config ID is required")

    # Get custom eval config
    try:
        custom_eval_config = CustomEvalConfig.objects.get(id=custom_eval_config_id)
    except CustomEvalConfig.DoesNotExist:
        raise ValueError("Custom eval config does not exist")

    # --- ClickHouse dispatch ---
    # Try CH if a project_id is available in eval_logger_filters
    ch_project_id = eval_logger_filters.get("project_id")
    if ch_project_id:
        try:
            from tracer.services.clickhouse.query_builders import (
                EvalMetricsQueryBuilder,
            )
            from tracer.services.clickhouse.query_service import (
                AnalyticsQueryService,
            )

            analytics = AnalyticsQueryService()
            eval_output_type_ch = custom_eval_config.eval_template.config.get(
                "output", "SCORE"
            )
            choices = []
            if eval_output_type_ch == "CHOICES":
                choices = custom_eval_config.eval_template.choices or []

            ch_start, ch_end = parse_time_filters(filters)
            # v1↔v2 dispatch — flips with CH25_QUERY_TYPES_V2_PRIMARY=EVAL_METRICS
            from tracer.services.clickhouse.v2.dispatch import (
                get_query_builder_class,
            )
            EvalBuilderCls = get_query_builder_class("EVAL_METRICS")  # noqa: N806
            builder = EvalBuilderCls(
                project_id=str(ch_project_id),
                custom_eval_config_id=str(custom_eval_config_id),
                start_date=ch_start,
                end_date=ch_end,
                interval=interval,
                eval_output_type=eval_output_type_ch,
                eval_name=custom_eval_config.name,
                choices=choices,
            )
            query, params = builder.build()
            result = analytics.execute_ch_query(query, params, timeout_ms=5000)
            ch_data = builder.format_result(result.data, result.columns or [])
            # For observe_type="charts" with non-CHOICES types, the PG code
            # wraps single-series results in a list. Match that behavior.
            if observe_type == "charts" and eval_output_type_ch != "CHOICES":
                if isinstance(ch_data, dict):
                    ch_data = [ch_data]
            return ch_data
        except Exception as e:
            logger.warning(
                "ch_eval_graph_dispatch_failed",
                error=str(e),
                eval_config_id=str(custom_eval_config_id),
            )
            # Fall through to existing PG code below

    # Parse time filters
    start_date, end_date = parse_time_filters(filters)

    # Get output type for processing
    eval_output_type = custom_eval_config.eval_template.config.get("output")

    # Build base queryset using subqueries - NO ID MATERIALIZATION
    # This is the key optimization: we filter using subqueries
    # instead of evaluating IDs into memory

    if observe_type == "trace":
        # For trace-level filtering, use trace_ids_queryset as a subquery
        trace_ids_queryset = eval_logger_filters.get("trace_ids_queryset")
        if trace_ids_queryset is None:
            return _empty_result(
                custom_eval_config.name, start_date, end_date, interval
            )

        # ✅ Use subquery filter - PostgreSQL will optimize this efficiently
        # Never evaluates the trace IDs into Python memory
        base_queryset = EvalLogger.objects.filter(
            trace_id__in=trace_ids_queryset.values("id"),
            custom_eval_config_id=custom_eval_config_id,
            created_at__gte=start_date,
            created_at__lte=end_date,
        )

        # Perform aggregation based on output type
        result = _aggregate_for_observe_screen(
            base_queryset,
            custom_eval_config,
            eval_output_type,
            req_data_config,
            interval,
            start_date,
            end_date,
        )

        return result

    elif observe_type == "span":
        # For span-level filtering, use span_ids_queryset as a subquery
        span_ids_queryset = eval_logger_filters.get("span_ids_queryset")
        if span_ids_queryset is None:
            return _empty_result(
                custom_eval_config.name, start_date, end_date, interval
            )

        # ✅ Use subquery filter - PostgreSQL handles this as a JOIN internally
        # Memory efficient even with 1M+ records
        base_queryset = EvalLogger.objects.filter(
            observation_span_id__in=span_ids_queryset.values("id"),
            custom_eval_config_id=custom_eval_config_id,
            created_at__gte=start_date,
            created_at__lte=end_date,
        )

        # Perform aggregation based on output type
        result = _aggregate_for_observe_screen(
            base_queryset,
            custom_eval_config,
            eval_output_type,
            req_data_config,
            interval,
            start_date,
            end_date,
        )

        return result

    elif observe_type == "charts":

        project_id = eval_logger_filters.get("project_id")
        if project_id is None:
            return _empty_result(
                custom_eval_config.name, start_date, end_date, interval
            )

        # CH25-TODO(PG-fallback / Django-subquery-shape): the CH primary
        # path at L142-186 already uses EvalMetricsQueryBuilder which
        # reads the CH-side tracer_eval_logger CDC table — EvalLogger
        # IS available in CH. This ORM block is the PG fallback, and the
        # `observation_span__in=ObservationSpan.objects.filter(project_id
        # =project_id)` Django subquery wraps every span for the project
        # (potentially millions of rows). Replacing the inner subquery
        # with a CH list would materialize the full span-id set in
        # Python, defeating the existing memory benefit. Defer until the
        # PG fallback can be removed entirely.
        span_subquery = ObservationSpan.objects.filter(project_id=project_id).values(
            "id"
        )

        base_queryset = EvalLogger.objects.filter(
            observation_span__in=span_subquery,
            custom_eval_config_id=custom_eval_config_id,
            created_at__gte=start_date,
            created_at__lte=end_date,
        )

        # Perform aggregation based on output type
        result = _aggregate_for_observe_screen(
            base_queryset,
            custom_eval_config,
            eval_output_type,
            req_data_config,
            interval,
            start_date,
            end_date,
            screen_type="charts",
        )

        return result

    else:
        raise ValueError(f"Invalid observe type: {observe_type}")


def _aggregate_for_standard_view(
    queryset,
    custom_eval_config: CustomEvalConfig,
    eval_output_type: str,
    req_data_config: Dict,
    interval: str,
    start_date: datetime,
    end_date: datetime,
) -> Dict:
    """
    Aggregate evaluation data for standard view (single metric).

    Uses database-level aggregation for optimal performance.
    """
    trunc_func = get_truncate_function(interval)
    # Determine aggregation field and calculation based on output type
    if eval_output_type == EvalOutputType.SCORE:
        # For float scores, average the output_float
        aggregated_data = (
            queryset.annotate(time_bucket=trunc_func("created_at"))
            .values("time_bucket")
            .annotate(
                value=Avg("output_float") * 100,  # Convert to percentage
                count=Count("id"),
            )
            .order_by("time_bucket")
        )

    elif eval_output_type == EvalOutputType.PASS_FAIL:
        # For pass/fail, calculate percentage of passes
        value_to_match = req_data_config.get("value", True)
        if isinstance(value_to_match, str):
            value_to_match = value_to_match.lower() == "true"

        aggregated_data = (
            queryset.annotate(time_bucket=trunc_func("created_at"))
            .values("time_bucket")
            .annotate(
                value=Avg(
                    Case(
                        When(output_bool=value_to_match, then=Value(100.0)),
                        default=Value(0.0),
                        output_field=FloatField(),
                    )
                ),
                count=Count("id"),
            )
            .order_by("time_bucket")
        )

    elif eval_output_type == EvalOutputType.CHOICES:
        # For choices, calculate percentage of selected choice
        choice = req_data_config.get("value")
        if not choice:
            return _empty_result(
                custom_eval_config.name, start_date, end_date, interval
            )

        aggregated_data = (
            queryset.annotate(time_bucket=trunc_func("created_at"))
            .values("time_bucket")
            .annotate(
                value=Avg(
                    Case(
                        When(output_str_list__contains=[choice], then=Value(100.0)),
                        default=Value(0.0),
                        output_field=FloatField(),
                    )
                ),
                count=Count("id"),
            )
            .order_by("time_bucket")
        )
    else:
        return _empty_result(custom_eval_config.name, start_date, end_date, interval)

    # Format results
    data_points = [
        {
            "timestamp": (
                item["time_bucket"].isoformat() if item["time_bucket"] else None
            ),
            "value": round(item["value"], 2) if item["value"] is not None else 0,
        }
        for item in aggregated_data
    ]

    # Fill in missing timestamps with zero values
    (data_points,) = fill_missing_timestamps_bulk(
        datasets={"data": (data_points, ["value"])},
        start_date=start_date,
        end_date=end_date,
        interval=interval,
    )

    # Add choice name if applicable
    name = custom_eval_config.name
    if eval_output_type == EvalOutputType.CHOICES:
        choice = req_data_config.get("value")
        if choice:
            name = f"{name} - {choice}"

    return {
        "name": name,
        "data": data_points,
        "id": str(custom_eval_config.id),
    }


def _aggregate_for_observe_screen(
    queryset,
    custom_eval_config: CustomEvalConfig,
    eval_output_type: str,
    req_data_config: Dict,
    interval: str,
    start_date: datetime,
    end_date: datetime,
    screen_type="observe",
) -> List[Dict]:
    """
    Aggregate evaluation data for monitor screen (multiple series for choices/bool).

    For bool/choices output types, returns multiple series (one per option).
    For float output type, returns single series.
    """
    if eval_output_type == EvalOutputType.SCORE:
        # Single series for float scores
        result = _aggregate_for_standard_view(
            queryset,
            custom_eval_config,
            eval_output_type,
            req_data_config,
            interval,
            start_date,
            end_date,
        )

        if screen_type == "charts":
            return [result]
        else:
            return result

    elif eval_output_type == EvalOutputType.PASS_FAIL:

        if screen_type == "charts":
            results = []
            for value in [True, False]:
                config_copy = req_data_config.copy()
                config_copy["value"] = value
                result = _aggregate_for_standard_view(
                    queryset,
                    custom_eval_config,
                    eval_output_type,
                    config_copy,
                    interval,
                    start_date,
                    end_date,
                )
                result["name"] = (
                    f"{custom_eval_config.name} - {'Passed' if value else 'Failed'}"
                )
                results.append(result)
            return results
        else:
            value_to_match = req_data_config.get("value")

            if isinstance(value_to_match, str):
                value_to_match = value_to_match.lower() == "true"

            config_copy = req_data_config.copy()
            config_copy["value"] = value_to_match
            result = _aggregate_for_standard_view(
                queryset,
                custom_eval_config,
                eval_output_type,
                config_copy,
                interval,
                start_date,
                end_date,
            )
            result["name"] = (
                f"{custom_eval_config.name} - {'Passed' if value_to_match else 'Failed'}"
            )
            return result

    elif eval_output_type == EvalOutputType.CHOICES:
        # Multiple series: one per choice

        choices = custom_eval_config.eval_template.choices or []

        if screen_type == "charts":
            results = []
            for choice in choices:
                config_copy = req_data_config.copy()
                config_copy["value"] = choice
                result = _aggregate_for_standard_view(
                    queryset,
                    custom_eval_config,
                    eval_output_type,
                    config_copy,
                    interval,
                    start_date,
                    end_date,
                )
                results.append(result)
            return results

        else:
            value_to_match = req_data_config.get("value")

            if value_to_match not in choices:
                return _empty_result(
                    custom_eval_config.name, start_date, end_date, interval
                )

            result = _aggregate_for_standard_view(
                queryset,
                custom_eval_config,
                eval_output_type,
                req_data_config,
                interval,
                start_date,
                end_date,
            )
            return result

    return []


def _empty_result(
    name: str, start_date: datetime, end_date: datetime, interval: str
) -> Dict:
    """Generate empty result structure."""
    return {
        "name": name or "Unknown",
        "data": [],
    }


def get_all_system_metrics(
    interval: str,
    filters: List[Dict],
    property: str,
    system_metric_filters: Dict,
) -> Dict:
    """
    Get ALL system metrics (latency, tokens, cost) in a single optimized query.

    More efficient than making 3 separate requests.

    Args:
        interval: Time interval
        filters: Filter configurations
        property: Metric property (not used, kept for compatibility)
        system_metric_filters: System metric filters (must contain project_id or span_ids)
        use_cache: Whether to use caching

    Returns:
        Dictionary with all three metrics:
        {
            "latency": {"metric_name": "latency", "data": [...]},
            "tokens": {"metric_name": "tokens", "data": [...]},
            "cost": {"metric_name": "cost", "data": [...]}
        }

    CH25-TODO(semantic-mismatch-prevents-1-line-migration): the new
    time_bucket_aggregate reader returns the right output shape
    ({bucket, span_count, tokens, cost, latency_ms}) but with TWO
    silent semantic drifts versus this PG helper:
      (1) buckets on `start_time` (the spans column used in CH) whereas
          this PG path uses Trunc("created_at"). For just-completed
          spans these are usually within seconds, but for backfilled or
          replayed spans they can diverge.
      (2) emits `cost = sum(cost)` whereas this PG path returns
          `cost_value = Avg("cost")` per bucket. Cost-per-bucket vs
          total-cost-per-bucket — semantically different.
    The sibling helper get_system_metric_graph_data (below) dispatches
    to TimeSeriesQueryBuilder; that builder has two internal branches:
      • the preaggregated `span_metrics_hourly.hour` path (built from
        `created_at` in the materialized view), which matches the PG
        `created_at` semantics here
      • the raw filtered `start_time`-bucketed path (matches drift 1)
    So drift (1) already exists internally inside the sibling CH
    builder. Drift (2) is unique to `time_bucket_aggregate`.
    A safe migration of THIS helper needs either:
      (a) a `time_bucket_aggregate(...,  cost_agg="avg"|"sum",
          bucket_field="start_time"|"created_at")` reader signature,
      (b) accepting the drift and writing a product decision doc that
          replays both paths through `start_time` + `avg(cost)`.
    Defer.
    """
    # Parse time filters
    start_date, end_date = parse_time_filters(filters)

    # Build base queryset
    project_id = system_metric_filters.get("project_id")

    if project_id:
        base_queryset = ObservationSpan.objects.filter(
            project_id=project_id,
            created_at__gte=start_date,
            created_at__lte=end_date,
        )
    else:
        raise ValueError("Either project_id or span_ids must be provided")

    base_queryset = apply_chart_span_filters(base_queryset, filters)

    # Get truncate function
    trunc_func = get_truncate_function(interval)

    aggregated_data = (
        base_queryset.annotate(time_bucket=trunc_func("created_at"))
        .values("time_bucket")
        .annotate(
            latency_value=Avg("latency_ms"),
            tokens_value=models.Sum("total_tokens"),
            cost_value=Avg("cost"),
            count=Count("id"),
        )
        .order_by("time_bucket")
    )

    # Separate the combined data into individual metric responses
    latency_data = []
    tokens_data = []
    cost_data = []
    traffic_data = []

    for item in aggregated_data:
        timestamp = item["time_bucket"].isoformat() if item["time_bucket"] else None
        primary_traffic = item["count"] if item["count"] is not None else 0

        latency_data.append(
            {
                "timestamp": timestamp,
                "value": (
                    round(item["latency_value"], 2)
                    if item["latency_value"] is not None
                    else 0
                ),
                "latency": (
                    round(item["latency_value"], 2)
                    if item["latency_value"] is not None
                    else 0
                ),
            }
        )

        tokens_data.append(
            {
                "timestamp": timestamp,
                "value": (
                    round(item["tokens_value"], 2)
                    if item["tokens_value"] is not None
                    else 0
                ),
                "tokens": (
                    round(item["tokens_value"], 2)
                    if item["tokens_value"] is not None
                    else 0
                ),
            }
        )

        cost_data.append(
            {
                "timestamp": timestamp,
                "value": (
                    round(item["cost_value"], 9)
                    if item["cost_value"] is not None
                    else 0
                ),
                "cost": (
                    round(item["cost_value"], 9)
                    if item["cost_value"] is not None
                    else 0
                ),
            }
        )

        traffic_data.append(
            {
                "timestamp": timestamp,
                "traffic": primary_traffic,
            }
        )

    # Fill in missing timestamps with zero values for all metrics
    # Optimized: Fill all 4 metrics in a single pass instead of 4 separate passes
    (
        latency_data,
        tokens_data,
        cost_data,
        traffic_data,
    ) = fill_missing_timestamps_bulk(
        datasets={
            "latency": (latency_data, ["value", "latency"]),
            "tokens": (tokens_data, ["value", "tokens"]),
            "cost": (cost_data, ["value", "cost"]),
            "traffic": (traffic_data, ["traffic"]),
        },
        start_date=start_date,
        end_date=end_date,
        interval=interval,
    )

    result = {
        "latency": latency_data,
        "tokens": tokens_data,
        "cost": cost_data,
        "traffic": traffic_data,
    }

    return result


def fill_missing_timestamps_bulk(
    datasets: Dict[str, tuple],
    start_date: datetime,
    end_date: datetime,
    interval: str,
) -> tuple:
    """
    Fill missing timestamps for multiple datasets in a SINGLE pass.

    This is 4x more efficient than calling fill_missing_timestamps separately
    for each dataset, as it generates timestamps only once and reuses them.

    Args:
        datasets: Dictionary of {name: (data_points, value_keys)}
                 Example: {
                     "latency": ([...], ["value", "latency"]),
                     "tokens": ([...], ["value", "tokens"]),
                 }
        start_date: Start of time range
        end_date: End of time range
        interval: Time interval ('hour', 'day', 'week', 'month', 'year')

    Returns:
        Tuple of filled datasets in the same order as input dictionary keys

    Example:
        >>> datasets = {
        ...     "latency": (latency_data, ["value", "latency"]),
        ...     "tokens": (tokens_data, ["value", "tokens"]),
        ... }
        >>> latency_filled, tokens_filled = fill_missing_timestamps_bulk(
        ...     datasets, start_date, end_date, "day"
        ... )

    Performance:
        - Old approach: 4 calls × 365 timestamps = 1,460 timestamp generations
        - New approach: 1 call × 365 timestamps = 365 timestamp generations
        - Speedup: 4x faster! ⚡
    """
    # Build lookups for all datasets
    existing_data_per_dataset = {}

    for name, (data_points, value_keys) in datasets.items():
        if not data_points:
            data_points = []

        existing_data = {}
        for point in data_points:
            if not point.get("timestamp"):
                continue

            try:
                ts_str = point["timestamp"]
                if isinstance(ts_str, str):
                    # Parse timezone-aware timestamps
                    if "+" in ts_str or ts_str.endswith("Z"):
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    else:
                        ts = datetime.fromisoformat(ts_str)
                else:
                    ts = ts_str

                # Use datetime object as key for fast lookup
                normalized_ts = normalize_timestamp_by_interval(ts, interval)
                existing_data[normalized_ts] = point

            except (ValueError, AttributeError, TypeError) as e:
                logger.warning(
                    f"Invalid timestamp in {name} data: {str(ts_str)[:50]} - {type(e).__name__}: {str(e)}"
                )
                continue

        existing_data_per_dataset[name] = (existing_data, value_keys)

    # Generate timestamps once and fill all datasets
    # This is the key optimization - single pass through timestamps
    results_per_dataset = {name: [] for name in datasets.keys()}

    for ts in generate_timestamp_range(start_date, end_date, interval):
        ts_iso = ts.isoformat()

        # Fill each dataset for this timestamp
        for name, (existing_data, value_keys) in existing_data_per_dataset.items():
            if ts in existing_data:
                # Use existing data point
                results_per_dataset[name].append(existing_data[ts])
            else:
                # Create zero-filled data point
                zero_point = {"timestamp": ts_iso}
                zero_point.update({key: 0 for key in value_keys})
                results_per_dataset[name].append(zero_point)

    # Return results in the same order as input dictionary
    return tuple(results_per_dataset[name] for name in datasets.keys())


def normalize_timestamp_by_interval(ts: datetime, interval: str) -> datetime:
    """
    Normalize a timestamp to the start of its interval bucket.

    Args:
        ts: Timestamp to normalize
        interval: Time interval ('hour', 'day', 'week', 'month', 'year')

    Returns:
        Normalized timestamp
    """
    # Remove timezone info for comparison
    if ts.tzinfo:
        ts = ts.replace(tzinfo=None)

    interval = interval.lower()

    if interval == "hour":
        return ts.replace(minute=0, second=0, microsecond=0)
    elif interval == "day":
        return ts.replace(hour=0, minute=0, second=0, microsecond=0)
    elif interval == "week":
        # Start of week (Monday)
        days_since_monday = ts.weekday()
        week_start = ts - timedelta(days=days_since_monday)
        return week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    elif interval == "month":
        return ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif interval == "year":
        return ts.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        # Default to day
        return ts.replace(hour=0, minute=0, second=0, microsecond=0)


def generate_timestamp_range(
    start_date: datetime, end_date: datetime, interval: str
) -> Generator[datetime, None, None]:
    """
    Generate timestamps from start_date to end_date at the specified interval.

    Uses a generator pattern to avoid memory explosion with large time ranges.
    For example, 1 year of hourly data (8,760 timestamps) uses <1KB instead of ~420KB.

    Args:
        start_date: Start of time range
        end_date: End of time range
        interval: Time interval ('hour', 'day', 'week', 'month', 'year')

    Yields:
        datetime objects representing each timestamp in the range

    Example:
        >>> # Memory efficient - generates on demand
        >>> for ts in generate_timestamp_range(start, end, "hour"):
        >>>     process(ts)
    """
    interval = interval.lower()

    # Normalize start date to beginning of interval
    current = normalize_timestamp_by_interval(start_date, interval)

    # Ensure end_date is timezone-naive for comparison
    if end_date.tzinfo:
        end_date = end_date.replace(tzinfo=None)

    while current <= end_date:
        yield current  # ✅ Yield instead of append - memory efficient

        # Increment by interval
        if interval == "hour":
            current += timedelta(hours=1)
        elif interval == "day":
            current += timedelta(days=1)
        elif interval == "week":
            current += timedelta(weeks=1)
        elif interval == "month":
            # Handle month increment (accounting for varying month lengths)
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)
        elif interval == "year":
            current = current.replace(year=current.year + 1)
        else:
            # Default to day
            current += timedelta(days=1)


def get_system_metric_data(
    interval: str,
    filters: List[Dict],
    property: str,
    req_data_config: Dict,
    system_metric_filters: Dict,
    observe_type: str = "span",
) -> Dict:
    """
    Optimized version of get_system_metric_data using database-level aggregation.

    Handles 1M+ datapoints efficiently by using subqueries instead of IN clauses.
    NEVER materializes ID lists in memory - uses lazy querysets throughout.

    Args:
        interval: Time interval
        filters: Filter configurations
        property: Metric property
        req_data_config: Request configuration
        system_metric_filters: System metric filters containing:
            - trace_ids_queryset: Lazy queryset for trace filtering (for observe_type='trace')
            - span_ids_queryset: Lazy queryset for span filtering (for observe_type='span')
        observe_type: Type of observation ('trace' or 'span')
        use_cache: Whether to use caching

    Returns:
        Graph data dictionary
    """
    metric_name = req_data_config.get("id")
    if not metric_name:
        raise ValueError("Metric name is required")

    # --- ClickHouse dispatch ---
    # Try CH if a project_id is available in system_metric_filters
    ch_project_id = system_metric_filters.get("project_id")
    if ch_project_id:
        try:
            from tracer.services.clickhouse.query_builders import TimeSeriesQueryBuilder
            from tracer.services.clickhouse.query_service import (
                AnalyticsQueryService,
            )

            analytics = AnalyticsQueryService()
            builder = TimeSeriesQueryBuilder(
                project_id=str(ch_project_id),
                filters=filters,
                interval=interval,
            )
            query, params = builder.build()
            result = analytics.execute_ch_query(query, params, timeout_ms=5000)
            ch_data = builder.format_result(result.data, result.columns or [])
            # Transform CH all-metrics format to match PG single-metric format
            # CH returns: {latency: [...], tokens: [...], cost: [...], traffic: [...]}
            # PG returns: {metric_name: "latency", data: [{timestamp, value, primary_traffic}]}
            metric_key = metric_name if metric_name in ch_data else "latency"
            metric_points = ch_data.get(metric_key, [])
            traffic_points = ch_data.get("traffic", [])
            traffic_by_ts = {
                t.get("timestamp"): t.get("traffic", 0) for t in traffic_points
            }
            return {
                "metric_name": metric_name,
                "data": [
                    {
                        "timestamp": p.get("timestamp"),
                        "value": p.get("value", 0),
                        "primary_traffic": traffic_by_ts.get(p.get("timestamp"), 0),
                    }
                    for p in metric_points
                ],
            }
        except Exception as e:
            logger.warning(
                "ch_system_metric_dispatch_failed",
                error=str(e),
                metric=metric_name,
            )
            # Fall through to existing PG code below

    # Parse time filters
    start_date, end_date = parse_time_filters(filters)

    # Build base queryset using subqueries - NO ID MATERIALIZATION
    # This is the key optimization: we filter using EXISTS/IN with subqueries
    # instead of evaluating IDs into memory
    #
    # CH25-TODO(PG-fallback-stays-PG): all three branches below
    # (trace/span/charts) sit downstream of the CH dispatch at L940-987
    # which uses TimeSeriesQueryBuilder. The Django subquery
    # `trace_id__in=trace_ids_queryset.values("id")` / `id__in=
    # span_ids_queryset.values("id")` is a memory-efficient pattern that
    # depends on Django's query compiler — switching to CH here would
    # require materializing the id list (loss of the "NO ID
    # MATERIALIZATION" optimization called out in the inline comment).
    # CH primary path covers the optimized case; this PG fallback is the
    # safety net.

    if observe_type == "trace":
        # For trace-level filtering, use trace_ids_queryset as a subquery
        trace_ids_queryset = system_metric_filters.get("trace_ids_queryset")
        if trace_ids_queryset is None:
            return {
                "metric_name": metric_name,
                "data": [],
            }

        # Use subquery filter - PostgreSQL will optimize this efficiently
        # Never evaluates the trace IDs into Python memory
        base_queryset = ObservationSpan.objects.filter(
            trace_id__in=trace_ids_queryset.values("id"),
            created_at__gte=start_date,
            created_at__lte=end_date,
        )

    elif observe_type == "span":
        # For span-level filtering, use span_ids_queryset as a subquery
        span_ids_queryset = system_metric_filters.get("span_ids_queryset")
        if span_ids_queryset is None:
            return {
                "metric_name": metric_name,
                "data": [],
            }

        # Use subquery filter - PostgreSQL handles this as a JOIN internally
        # Memory efficient even with 1M+ records
        base_queryset = ObservationSpan.objects.filter(
            id__in=span_ids_queryset.values("id"),
            created_at__gte=start_date,
            created_at__lte=end_date,
        )

    elif observe_type == "charts":
        project_id = system_metric_filters.get("project_id")
        if project_id is None:
            return {
                "metric_name": metric_name,
                "data": [],
            }

        base_queryset = ObservationSpan.objects.filter(
            project_id=project_id,
            created_at__gte=start_date,
            created_at__lte=end_date,
        )
        base_queryset = apply_chart_span_filters(base_queryset, filters)

    else:
        raise ValueError(f"Unsupported observe_type: {observe_type}")

    # Get truncate function
    trunc_func = get_truncate_function(interval)

    # Aggregate based on metric type
    if metric_name == "latency":
        aggregated_data = (
            base_queryset.annotate(time_bucket=trunc_func("created_at"))
            .values("time_bucket")
            .annotate(value=Avg("latency_ms"), count=Count("id"))
            .order_by("time_bucket")
        )

    elif metric_name == "tokens":
        aggregated_data = (
            base_queryset.annotate(time_bucket=trunc_func("created_at"))
            .values("time_bucket")
            .annotate(value=models.Sum("total_tokens"), count=Count("id"))
            .order_by("time_bucket")
        )

    elif metric_name == "cost":
        aggregated_data = (
            base_queryset.annotate(time_bucket=trunc_func("created_at"))
            .values("time_bucket")
            .annotate(value=Avg("cost"), count=Count("id"))
            .order_by("time_bucket")
        )
    else:
        raise ValueError(f"Unsupported metric: {metric_name}")

    # Format results
    data_points = [
        {
            "timestamp": (
                item["time_bucket"].isoformat() if item["time_bucket"] else None
            ),
            "value": (
                round(item["value"], 9 if metric_name == "cost" else 2)
                if item["value"] is not None
                else 0
            ),
            "primary_traffic": item["count"] if item["count"] is not None else 0,
        }
        for item in aggregated_data
    ]

    (data_points,) = fill_missing_timestamps_bulk(
        datasets={"data": (data_points, ["value", "primary_traffic"])},
        start_date=start_date,
        end_date=end_date,
        interval=interval,
    )

    result = {
        "metric_name": metric_name,
        "data": data_points,
    }

    return result


def get_annotation_graph_data(
    interval: str,
    filters: List[Dict],
    property: str,
    observe_type: str,
    req_data_config: Dict,
    annotation_logger_filters: Dict,
) -> Dict:
    """
    Optimized version of get_annotation_graph_data using database-level aggregation.

    Handles 1M+ datapoints efficiently by:
    1. Using subqueries instead of loading IDs into memory
    2. Database-level time bucketing and aggregation
    3. Minimal memory footprint
    4. Supporting all annotation types (bool, float, str_list, text, etc.)

    Args:
        interval: Time interval ('hour', 'day', 'week', 'month')
        filters: List of filter configurations
        property: Aggregation property (e.g., 'average')
        observe_type: Type of observation ('trace' or 'span')
        req_data_config: Request data configuration containing:
            - id: annotation_label_id (required)
            - output_type: Type of annotation output ('bool', 'float', 'str_list', 'text')
            - value: Value to filter for (for bool/str_list types)
            - type: 'ANNOTATION' (for compatibility)
        annotation_logger_filters: Filters containing:
            - trace_ids_queryset: Lazy queryset for trace filtering (for observe_type='trace')
            - span_ids_queryset: Lazy queryset for span filtering (for observe_type='span')

    Returns:
        Graph data dictionary with name and data
    """
    # Extract configuration
    annotation_label_id = req_data_config.get("id")
    if not annotation_label_id:
        raise ValueError("Annotation label ID is required")

    # Get annotation label
    try:
        annotation_label = AnnotationsLabels.objects.get(id=annotation_label_id)
    except AnnotationsLabels.DoesNotExist:
        raise Exception("Annotation label does not exist")

    # Parse time filters
    start_date, end_date = parse_time_filters(filters)

    # Determine output type from annotation label settings
    annotation_type = annotation_label.type
    output_type = req_data_config.get("output_type")

    # Auto-detect output type based on annotation type if not provided
    if not output_type:
        output_type = _get_output_type_from_annotation_type(annotation_type)

    # --- ClickHouse dispatch ---
    # Try CH if a project_id is available in annotation_logger_filters
    ch_project_id = annotation_logger_filters.get("project_id")
    if ch_project_id:
        try:
            from tracer.services.clickhouse.query_builders import (
                AnnotationGraphQueryBuilder,
            )
            from tracer.services.clickhouse.query_service import (
                AnalyticsQueryService,
            )

            analytics = AnalyticsQueryService()
            builder = AnnotationGraphQueryBuilder(
                project_id=str(ch_project_id),
                annotation_label_id=str(annotation_label_id),
                annotation_name=annotation_label.name,
                start_date=start_date,
                end_date=end_date,
                interval=interval,
                output_type=output_type,
                value=req_data_config.get("value"),
            )
            query, params = builder.build()
            result = analytics.execute_ch_query(query, params, timeout_ms=5000)
            ch_data = builder.format_result(result.data, result.columns or [])
            return ch_data
        except Exception as e:
            logger.warning(
                "ch_annotation_graph_dispatch_failed",
                error=str(e),
                annotation_label_id=str(annotation_label_id),
            )
            # Fall through to existing PG code below

    # Build base queryset using subqueries - NO ID MATERIALIZATION
    # This is the key optimization: we filter using subqueries
    # instead of evaluating IDs into memory

    if observe_type == "trace":
        # For trace-level filtering, use trace_ids_queryset as a subquery
        trace_ids_queryset = annotation_logger_filters.get("trace_ids_queryset")
        if trace_ids_queryset is None:
            return _empty_result(annotation_label.name, start_date, end_date, interval)

        # ✅ Use subquery filter - PostgreSQL will optimize this efficiently
        # Capture both trace-level scores AND span-level scores for these traces
        trace_id_values = trace_ids_queryset.values("id")
        base_queryset = Score.objects.filter(
            Q(trace_id__in=trace_id_values)
            | Q(
                observation_span_id__in=ObservationSpan.objects.filter(
                    trace_id__in=trace_id_values
                ).values("id")
            ),
            label_id=annotation_label_id,
            deleted=False,
            created_at__gte=start_date,
            created_at__lte=end_date,
        )

    elif observe_type == "span":
        # For span-level filtering, use span_ids_queryset as a subquery
        span_ids_queryset = annotation_logger_filters.get("span_ids_queryset")
        if span_ids_queryset is None:
            return _empty_result(annotation_label.name, start_date, end_date, interval)

        # ✅ Use subquery filter - PostgreSQL handles this as a JOIN internally
        # Memory efficient even with 1M+ records
        base_queryset = Score.objects.filter(
            observation_span_id__in=span_ids_queryset.values("id"),
            label_id=annotation_label_id,
            deleted=False,
            created_at__gte=start_date,
            created_at__lte=end_date,
        )

    else:
        raise ValueError(f"Invalid observe type: {observe_type}")

    # Perform aggregation based on output type
    result = _aggregate_annotation_data(
        base_queryset,
        annotation_label,
        output_type,
        req_data_config,
        interval,
        start_date,
        end_date,
    )

    return result


def _get_output_type_from_annotation_type(annotation_type: str) -> str:
    """
    Map annotation type to output type for database field selection.

    Args:
        annotation_type: Annotation type from AnnotationTypeChoices

    Returns:
        Output type string ('bool', 'float', 'str_list', 'text')
    """
    type_mapping = {
        AnnotationTypeChoices.THUMBS_UP_DOWN.value: "bool",
        AnnotationTypeChoices.NUMERIC.value: "float",
        AnnotationTypeChoices.STAR.value: "float",
        AnnotationTypeChoices.CATEGORICAL.value: "str_list",
        AnnotationTypeChoices.TEXT.value: "text",
    }

    output_type = type_mapping.get(annotation_type, "float")
    return output_type


def _aggregate_annotation_data(
    queryset,
    annotation_label: AnnotationsLabels,
    output_type: str,
    req_data_config: Dict,
    interval: str,
    start_date: datetime,
    end_date: datetime,
) -> Dict:
    """
    Aggregate annotation data based on output type.

    Uses database-level aggregation for optimal performance.

    Args:
        queryset: Base queryset of Score objects
        annotation_label: AnnotationsLabels model instance
        output_type: Type of output ('bool', 'float', 'str_list', 'text')
        req_data_config: Request data configuration
        interval: Time interval
        start_date: Start date
        end_date: End date

    Returns:
        Formatted graph data dictionary
    """
    trunc_func = get_truncate_function(interval)

    # Determine aggregation based on output type
    if output_type == "float":
        # For float annotations (NUMERIC, STAR), calculate average.
        # Score stores NUMERIC as {"value": float} and STAR as {"rating": float}.
        value_key = (
            "rating"
            if annotation_label.type == AnnotationTypeChoices.STAR.value
            else "value"
        )
        score_field = Cast(KeyTextTransform(value_key, "value"), FloatField())
        aggregated_data = (
            queryset.annotate(time_bucket=trunc_func("created_at"))
            .values("time_bucket")
            .annotate(value=Avg(score_field), count=Count("id"))
            .order_by("time_bucket")
        )

        # Format results
        data_points = [
            {
                "timestamp": (
                    item["time_bucket"].isoformat() if item["time_bucket"] else None
                ),
                "value": round(item["value"], 2) if item["value"] is not None else 0,
            }
            for item in aggregated_data
        ]

    elif output_type == "bool":
        # For boolean annotations (THUMBS_UP_DOWN), calculate percentage.
        # Score stores thumbs as {"value": "up"} or {"value": "down"}.
        value_to_match = req_data_config.get("value", True)
        if isinstance(value_to_match, str):
            value_to_match = value_to_match.lower() in ("true", "up")
        # Map bool to the string stored in Score.value
        thumbs_str = "up" if value_to_match else "down"

        aggregated_data = (
            queryset.annotate(time_bucket=trunc_func("created_at"))
            .values("time_bucket")
            .annotate(
                value=Avg(
                    Case(
                        When(value__value=thumbs_str, then=Value(100.0)),
                        default=Value(0.0),
                        output_field=FloatField(),
                    )
                ),
                count=Count("id"),
            )
            .order_by("time_bucket")
        )

        # Format results
        data_points = [
            {
                "timestamp": (
                    item["time_bucket"].isoformat() if item["time_bucket"] else None
                ),
                "value": round(item["value"], 2) if item["value"] is not None else 0,
            }
            for item in aggregated_data
        ]

    elif output_type == "str_list":
        # For categorical annotations (CATEGORICAL), calculate percentage of selected choice.
        # Score stores categorical as {"selected": ["choice1", ...]}.
        choice = req_data_config.get("value")
        if not choice:
            return _empty_result(annotation_label.name, start_date, end_date, interval)

        aggregated_data = (
            queryset.annotate(time_bucket=trunc_func("created_at"))
            .values("time_bucket")
            .annotate(
                value=Avg(
                    Case(
                        When(
                            value__selected__contains=[choice],
                            then=Value(100.0),
                        ),
                        default=Value(0.0),
                        output_field=FloatField(),
                    )
                ),
                count=Count("id"),
            )
            .order_by("time_bucket")
        )

        # Format results
        data_points = [
            {
                "timestamp": (
                    item["time_bucket"].isoformat() if item["time_bucket"] else None
                ),
                "value": round(item["value"], 2) if item["value"] is not None else 0,
            }
            for item in aggregated_data
        ]

    elif output_type == "text":
        # For text annotations, we can count non-empty annotations
        # or return count of annotations
        aggregated_data = (
            queryset.annotate(time_bucket=trunc_func("created_at"))
            .values("time_bucket")
            .annotate(value=Count("id"), count=Count("id"))  # Count of annotations
            .order_by("time_bucket")
        )

        # Format results
        data_points = [
            {
                "timestamp": (
                    item["time_bucket"].isoformat() if item["time_bucket"] else None
                ),
                "value": item["value"] if item["value"] is not None else 0,
            }
            for item in aggregated_data
        ]

    else:
        return _empty_result(annotation_label.name, start_date, end_date, interval)

    (data_points,) = fill_missing_timestamps_bulk(
        datasets={"data": (data_points, ["value"])},
        start_date=start_date,
        end_date=end_date,
        interval=interval,
    )

    # Add choice name if applicable
    name = annotation_label.name
    if output_type == "str_list":
        choice = req_data_config.get("value")
        if choice:
            name = f"{name} - {choice}"
    elif output_type == "bool":
        value = req_data_config.get("value", True)
        if isinstance(value, str):
            value = value.lower() == "true"
        name = f"{name} - {'True' if value else 'False'}"

    return {
        "name": name,
        "data": data_points,
    }
