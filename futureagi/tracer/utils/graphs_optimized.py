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

from collections.abc import Generator
from datetime import datetime, timedelta
from typing import Any

import structlog
from django.db.models import (
    Avg,
    Case,
    Count,
    FloatField,
    Q,
    Value,
    When,
)
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, TruncDay, TruncHour, TruncMonth

from model_hub.models.choices import AnnotationTypeChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from tracer.models.custom_eval_config import CustomEvalConfig, EvalOutputType
from tracer.models.observation_span import ObservationSpan
from tracer.services.clickhouse.read_budget import (
    is_clickhouse_api_read_unavailable_error,
)

logger = structlog.get_logger(__name__)


class EvalGraphConfigurationError(ValueError):
    """The requested eval is missing or outside the authorized project."""


class EvalGraphReadError(RuntimeError):
    """The direct-write ClickHouse eval read could not be completed."""


class SystemMetricGraphReadError(RuntimeError):
    """The direct-write ClickHouse system-metric read could not be completed."""


def parse_time_filters(filters: list[dict]) -> tuple:
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
    filters: list[dict],
    property: str,
    observe_type: str,
    req_data_config: dict,
    eval_logger_filters: dict,
    refresh: bool = False,
    organization_id: str | None = None,
    workspace_id: str | None = None,
) -> Any:
    """Read an eval graph from the authoritative direct-write CH25 tables."""
    del property

    custom_eval_config_id = req_data_config.get("id")
    if not custom_eval_config_id:
        raise EvalGraphConfigurationError(
            "Evaluation config is not available for this project"
        )

    # The raw eval logger has no project column, so config ownership must be
    # established before a config-scoped ClickHouse read can run. Direct-write
    # deployments have no authoritative PostgreSQL telemetry fallback: every
    # caller must supply the request-owned project or fail before any read.
    ch_project_id = eval_logger_filters.get("project_id")
    if not ch_project_id:
        raise EvalGraphConfigurationError(
            "Evaluation config is not available for this project"
        )

    config_lookup = {
        "id": custom_eval_config_id,
        "deleted": False,
        "project_id": ch_project_id,
    }

    try:
        custom_eval_config = CustomEvalConfig.objects.select_related(
            "eval_template"
        ).get(**config_lookup)
    except CustomEvalConfig.DoesNotExist:
        raise EvalGraphConfigurationError(
            "Evaluation config is not available for this project"
        ) from None

    try:
        from tracer.services.clickhouse.graph_dispatch import (
            fetch_eval_chart_series_ch,
        )
        from tracer.services.clickhouse.v2.query_service import (
            V2AnalyticsQueryService,
        )

        output_type = custom_eval_config.eval_template.config.get("output", "SCORE")
        choices = custom_eval_config.eval_template.choices or []
        tenant_scope = {}
        if organization_id is not None:
            tenant_scope["organization_id"] = organization_id
        if workspace_id is not None:
            tenant_scope["workspace_id"] = workspace_id
        return fetch_eval_chart_series_ch(
            analytics=V2AnalyticsQueryService(),
            project_id=str(ch_project_id),
            filters=filters,
            interval=interval,
            req_data_config={
                **req_data_config,
                "eval_output_type": output_type,
                "choices": choices,
            },
            eval_name=custom_eval_config.name,
            refresh=refresh,
            **tenant_scope,
        )
    except Exception as exc:
        logger.exception(
            "ch_eval_graph_read_failed",
            error_type=type(exc).__name__,
            eval_config_id=str(custom_eval_config_id),
        )
        raise EvalGraphReadError(
            "Evaluation graph data is temporarily unavailable"
        ) from None


def _aggregate_for_standard_view(
    queryset,
    custom_eval_config: CustomEvalConfig,
    eval_output_type: str,
    req_data_config: dict,
    interval: str,
    start_date: datetime,
    end_date: datetime,
) -> dict:
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
    req_data_config: dict,
    interval: str,
    start_date: datetime,
    end_date: datetime,
    screen_type="observe",
) -> list[dict]:
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
) -> dict:
    """Generate empty result structure."""
    return {
        "name": name or "Unknown",
        "data": [],
    }


def _read_direct_system_metrics(
    *,
    project_id: str,
    filters: list[dict],
    interval: str,
    refresh: bool = False,
    organization_id: str | None = None,
    workspace_id: str | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Execute the direct-write system-metric builder on the CH25 service."""
    try:
        from tracer.services.clickhouse.graph_dispatch import (
            fetch_all_system_metrics_ch,
        )
        from tracer.services.clickhouse.v2.query_service import (
            V2AnalyticsQueryService,
        )

        tenant_scope = {}
        if organization_id is not None:
            tenant_scope["organization_id"] = organization_id
        if workspace_id is not None:
            tenant_scope["workspace_id"] = workspace_id
        return fetch_all_system_metrics_ch(
            analytics=V2AnalyticsQueryService(),
            project_id=project_id,
            filters=filters,
            interval=interval,
            refresh=refresh,
            **tenant_scope,
        )
    except Exception as exc:
        logger.exception(
            "ch_system_metric_graph_read_failed",
            error_type=type(exc).__name__,
        )
        if is_clickhouse_api_read_unavailable_error(exc):
            raise SystemMetricGraphReadError(
                "System metric graph data is temporarily unavailable"
            ) from None
        raise


def get_all_system_metrics(
    interval: str,
    filters: list[dict],
    property: str,
    system_metric_filters: dict,
    refresh: bool = False,
    organization_id: str | None = None,
    workspace_id: str | None = None,
) -> dict:
    """Read latency, token, cost, and traffic series in one CH25 query."""
    del property

    project_id = system_metric_filters.get("project_id")
    if not project_id:
        raise ValueError("project_id must be provided")

    metrics = _read_direct_system_metrics(
        project_id=str(project_id),
        filters=filters,
        interval=interval,
        refresh=refresh,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )
    # Preserve the historical public response exactly; the shared builder also
    # exposes additional aliases used by newer dashboard endpoints.
    return {
        **{
            key: metrics.get(key, [])
            for key in ("latency", "tokens", "cost", "traffic")
        },
        **{key: value for key, value in metrics.items() if key.startswith("query_")},
    }


def fill_missing_timestamps_bulk(
    datasets: dict[str, tuple],
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
                zero_point.update(dict.fromkeys(value_keys, 0))
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
    filters: list[dict],
    property: str,
    req_data_config: dict,
    system_metric_filters: dict,
    observe_type: str = "span",
    refresh: bool = False,
    organization_id: str | None = None,
    workspace_id: str | None = None,
) -> dict:
    """Read one public system-metric series from direct-write CH25."""
    del property

    metric_name = req_data_config.get("id")
    if not metric_name:
        raise ValueError("Metric name is required")

    if observe_type != "charts":
        raise ValueError("Only project-scoped chart metrics are supported")

    project_id = system_metric_filters.get("project_id")
    if not project_id:
        raise ValueError("project_id must be provided")

    metrics = _read_direct_system_metrics(
        project_id=str(project_id),
        filters=filters,
        interval=interval,
        refresh=refresh,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )
    metric_key = metric_name if metric_name in metrics else "latency"
    traffic_by_timestamp = {
        point.get("timestamp"): point.get("traffic", 0)
        for point in metrics.get("traffic", [])
    }
    return {
        "metric_name": metric_name,
        "data": [
            {
                "timestamp": point.get("timestamp"),
                "value": point.get("value", 0),
                "primary_traffic": traffic_by_timestamp.get(point.get("timestamp"), 0),
            }
            for point in metrics.get(metric_key, [])
        ],
        **{key: value for key, value in metrics.items() if key.startswith("query_")},
    }


def get_annotation_graph_data(
    interval: str,
    filters: list[dict],
    property: str,
    observe_type: str,
    req_data_config: dict,
    annotation_logger_filters: dict,
) -> dict:
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
        raise Exception("Annotation label does not exist") from None

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
    req_data_config: dict,
    interval: str,
    start_date: datetime,
    end_date: datetime,
) -> dict:
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
