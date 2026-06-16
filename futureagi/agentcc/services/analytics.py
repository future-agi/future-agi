"""Analytics service layer for Agentcc gateway dashboard.

All aggregation logic lives here as standalone functions. Each function
accepts a pre-filtered queryset (already scoped to the correct
organization/workspace by the ViewSet mixin) plus time range parameters,
and returns a plain Python dictionary ready for JSON serialization.
"""

import math
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import (
    Avg,
    Count,
    DecimalField,
    FloatField,
    IntegerField,
    Max,
    Min,
    Q,
    Sum,
    Value,
)
from django.db.models.functions import (
    Coalesce,
    Substr,
    TruncDay,
    TruncHour,
    TruncMinute,
    TruncMonth,
    TruncWeek,
)
from django.utils import timezone
from django.utils.dateparse import parse_datetime

# ---------------------------------------------------------------------------
# Time range utilities
# ---------------------------------------------------------------------------

TRUNC_MAP = {
    "minute": TruncMinute,
    "hour": TruncHour,
    "day": TruncDay,
    "week": TruncWeek,
    "month": TruncMonth,
}

GRANULARITY_DELTA = {
    "minute": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
    "month": timedelta(days=30),  # approximate for bucket filling
}

ALLOWED_GRANULARITIES = set(TRUNC_MAP.keys())

# The frontend (UsageCharts.computeGranularity) sends duration-style buckets
# such as "5m" / "15m" / "1h" / "6h" / "1d" based on the selected range. The
# backend buckets by Django Trunc units, so map each duration to the closest
# supported unit. Without this every frontend value falls outside
# ALLOWED_GRANULARITIES and silently degrades to "hour", which squeezes a wide
# range (e.g. 30 days) into hour buckets and renders the chart as a sliver.
GRANULARITY_ALIASES = {
    "5m": "minute",
    "15m": "minute",
    "1h": "hour",
    "6h": "hour",
    "1d": "day",
}

MAX_PERCENTILE_SAMPLE = 50_000


def parse_time_range(query_params):
    """Parse start, end, granularity from query params.

    Returns (period_start, period_end, granularity).
    """
    now = timezone.now()

    raw_start = query_params.get("start")
    raw_end = query_params.get("end")
    granularity = query_params.get("granularity", "hour")

    period_start = _parse_dt(raw_start, now - timedelta(hours=24))
    period_end = _parse_dt(raw_end, now)

    # Tolerate reversed ranges
    if period_start > period_end:
        period_start, period_end = period_end, period_start

    # Normalize duration-style aliases (e.g. "6h") to a supported Trunc unit
    # before validating, so the frontend's buckets are honored instead of
    # silently collapsing to "hour".
    granularity = GRANULARITY_ALIASES.get(granularity, granularity)
    if granularity not in ALLOWED_GRANULARITIES:
        granularity = "hour"

    return period_start, period_end, granularity


def _parse_dt(raw, default):
    if not raw:
        return default
    dt = parse_datetime(raw)
    if dt is None:
        return default
    if dt.tzinfo is None:
        from datetime import timezone as dt_tz

        dt = dt.replace(tzinfo=dt_tz.utc)
    return dt


def get_trunc_class(granularity):
    return TRUNC_MAP.get(granularity, TruncHour)


# ---------------------------------------------------------------------------
# Percentile helper
# ---------------------------------------------------------------------------


def _compute_percentiles(values_list, percentiles=(0.50, 0.90, 0.95, 0.99)):
    """Compute percentiles from a sorted list of numeric values."""
    if not values_list:
        return {p: 0.0 for p in percentiles}
    n = len(values_list)
    result = {}
    for p in percentiles:
        idx = min(int(p * n), n - 1)
        result[p] = float(values_list[idx])
    return result


def _compute_trend(current, previous):
    """Compute % change from previous to current. Returns None if previous is 0."""
    current = float(current) if current is not None else 0.0
    previous = float(previous) if previous is not None else 0.0
    if previous == 0 and current == 0:
        return 0.0
    if previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 2)


def _fill_empty_buckets(series, period_start, period_end, granularity, zero_row_fn):
    """Fill missing time buckets with zero-valued rows."""
    delta = GRANULARITY_DELTA.get(granularity, timedelta(hours=1))

    # Pre-index series by bucket for O(1) lookup instead of O(n) scan
    series_by_bucket = defaultdict(list)
    for row in series:
        series_by_bucket[row["bucket"]].append(row)

    filled = []
    current = _truncate_dt(period_start, granularity)
    while current <= period_end:
        if current in series_by_bucket:
            filled.extend(series_by_bucket[current])
        else:
            filled.append({"bucket": current, **zero_row_fn()})
        current += delta

    return filled


def _truncate_dt(dt, granularity):
    """Truncate a datetime to the start of its bucket."""
    if granularity == "minute":
        return dt.replace(second=0, microsecond=0)
    elif granularity == "hour":
        return dt.replace(minute=0, second=0, microsecond=0)
    elif granularity == "day":
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    elif granularity == "week":
        # Monday-aligned
        days_since_monday = dt.weekday()
        start = dt - timedelta(days=days_since_monday)
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    elif granularity == "month":
        return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return dt


# ---------------------------------------------------------------------------
# Function 1: Overview KPIs
# ---------------------------------------------------------------------------


def get_overview_kpis(queryset, period_start, period_end):
    """Return high-level KPIs with trend comparison vs previous period."""
    current_qs = queryset.filter(
        started_at__gte=period_start, started_at__lte=period_end
    )

    # Current period aggregates (single query)
    current = current_qs.aggregate(
        total_requests=Count("id"),
        total_tokens=Coalesce(
            Sum("total_tokens"), Value(0), output_field=IntegerField()
        ),
        total_cost=Coalesce(Sum("cost"), Value(0), output_field=DecimalField()),
        avg_latency_ms=Coalesce(Avg("latency_ms"), Value(0), output_field=FloatField()),
        error_count=Count("id", filter=Q(is_error=True)),
        cache_hit_count=Count("id", filter=Q(cache_hit=True)),
    )

    total_requests = current["total_requests"]
    error_rate = (
        (current["error_count"] / total_requests * 100) if total_requests > 0 else 0.0
    )
    cache_hit_rate = (
        (current["cache_hit_count"] / total_requests * 100)
        if total_requests > 0
        else 0.0
    )

    # P95 latency (Python-side)
    latency_values = list(
        current_qs.order_by("latency_ms").values_list("latency_ms", flat=True)[
            :MAX_PERCENTILE_SAMPLE
        ]
    )
    percentiles = _compute_percentiles(latency_values, (0.95,))
    p95_latency = percentiles[0.95]

    # Active models
    active_models = current_qs.values("model").distinct().count()

    # Previous period
    delta = period_end - period_start
    prev_start = period_start - delta
    prev_end = period_start

    prev_qs = queryset.filter(started_at__gte=prev_start, started_at__lte=prev_end)
    prev = prev_qs.aggregate(
        total_requests=Count("id"),
        total_tokens=Coalesce(
            Sum("total_tokens"), Value(0), output_field=IntegerField()
        ),
        total_cost=Coalesce(Sum("cost"), Value(0), output_field=DecimalField()),
        avg_latency_ms=Coalesce(Avg("latency_ms"), Value(0), output_field=FloatField()),
        error_count=Count("id", filter=Q(is_error=True)),
        cache_hit_count=Count("id", filter=Q(cache_hit=True)),
    )

    prev_total = prev["total_requests"]
    prev_error_rate = (
        (prev["error_count"] / prev_total * 100) if prev_total > 0 else 0.0
    )
    prev_cache_hit_rate = (
        (prev["cache_hit_count"] / prev_total * 100) if prev_total > 0 else 0.0
    )

    prev_latency_values = list(
        prev_qs.order_by("latency_ms").values_list("latency_ms", flat=True)[
            :MAX_PERCENTILE_SAMPLE
        ]
    )
    prev_percentiles = _compute_percentiles(prev_latency_values, (0.95,))
    prev_p95 = prev_percentiles[0.95]

    prev_active_models = prev_qs.values("model").distinct().count()

    return {
        "total_requests": {
            "value": total_requests,
            "trend": _compute_trend(total_requests, prev_total),
        },
        "total_tokens": {
            "value": current["total_tokens"],
            "trend": _compute_trend(current["total_tokens"], prev["total_tokens"]),
        },
        "total_cost": {
            "value": str(current["total_cost"]),
            "trend": _compute_trend(current["total_cost"], prev["total_cost"]),
        },
        "avg_latency_ms": {
            "value": round(current["avg_latency_ms"], 2),
            "trend": _compute_trend(current["avg_latency_ms"], prev["avg_latency_ms"]),
        },
        "error_rate": {
            "value": round(error_rate, 2),
            "trend": _compute_trend(error_rate, prev_error_rate),
        },
        "cache_hit_rate": {
            "value": round(cache_hit_rate, 2),
            "trend": _compute_trend(cache_hit_rate, prev_cache_hit_rate),
        },
        "p95_latency_ms": {
            "value": round(p95_latency, 2),
            "trend": _compute_trend(p95_latency, prev_p95),
        },
        "active_models": {
            "value": active_models,
            "trend": _compute_trend(active_models, prev_active_models),
        },
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
    }


# ---------------------------------------------------------------------------
# Function 2: Usage Timeseries
# ---------------------------------------------------------------------------


def get_usage_timeseries(
    queryset, period_start, period_end, granularity, group_by=None
):
    """Return time-bucketed usage data for charts."""
    TruncClass = get_trunc_class(granularity)

    filtered_qs = queryset.filter(
        started_at__gte=period_start, started_at__lte=period_end
    )

    base_annotate = dict(
        request_count=Count("id"),
        total_tokens=Coalesce(
            Sum("total_tokens"), Value(0), output_field=IntegerField()
        ),
        input_tokens=Coalesce(
            Sum("input_tokens"), Value(0), output_field=IntegerField()
        ),
        output_tokens=Coalesce(
            Sum("output_tokens"), Value(0), output_field=IntegerField()
        ),
        total_cost=Coalesce(Sum("cost"), Value(0), output_field=DecimalField()),
        error_count=Count("id", filter=Q(is_error=True)),
        avg_latency_ms=Coalesce(Avg("latency_ms"), Value(0), output_field=FloatField()),
    )

    annotated = filtered_qs.annotate(bucket=TruncClass("started_at"))

    allowed_groups = {"model", "provider"}
    if group_by and group_by in allowed_groups:
        values_fields = ["bucket", group_by]
    else:
        group_by = None
        values_fields = ["bucket"]

    rows = list(
        annotated.values(*values_fields).annotate(**base_annotate).order_by("bucket")
    )

    # Format decimal values
    for row in rows:
        row["total_cost"] = str(row["total_cost"])
        row["avg_latency_ms"] = round(row["avg_latency_ms"], 2)

    def _zero_row():
        return {
            "request_count": 0,
            "total_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_cost": "0",
            "error_count": 0,
            "avg_latency_ms": 0.0,
        }

    if group_by is None:
        series = _fill_empty_buckets(
            rows, period_start, period_end, granularity, _zero_row
        )
        return {"granularity": granularity, "series": series}
    else:
        groups = defaultdict(list)
        for row in rows:
            key = row.pop(group_by, "unknown")
            groups[key].append(row)

        # Fill each group
        for key in groups:
            groups[key] = _fill_empty_buckets(
                groups[key], period_start, period_end, granularity, _zero_row
            )

        return {
            "granularity": granularity,
            "group_by": group_by,
            "groups": dict(groups),
        }


# ---------------------------------------------------------------------------
# Function 3: Cost Breakdown
# ---------------------------------------------------------------------------


def get_cost_breakdown(queryset, period_start, period_end, group_by="model", top_n=10):
    """Return cost breakdown by a categorical dimension."""
    allowed_groups = {"model", "provider", "api_key_id", "user_id", "routing_strategy"}
    if group_by not in allowed_groups:
        group_by = "model"

    filtered_qs = queryset.filter(
        started_at__gte=period_start, started_at__lte=period_end
    )

    # Grand total
    grand_total_agg = filtered_qs.aggregate(
        grand_total=Coalesce(Sum("cost"), Value(0), output_field=DecimalField())
    )
    grand_total = grand_total_agg["grand_total"]

    # Group and aggregate
    all_rows = list(
        filtered_qs.values(group_by)
        .annotate(
            total_cost=Coalesce(Sum("cost"), Value(0), output_field=DecimalField()),
            request_count=Count("id"),
            total_tokens=Coalesce(
                Sum("total_tokens"), Value(0), output_field=IntegerField()
            ),
        )
        .order_by("-total_cost")
    )

    top_rows = all_rows[:top_n]
    remaining_rows = all_rows[top_n:]

    breakdown = []
    for row in top_rows:
        cost = row["total_cost"]
        count = row["request_count"]
        pct = float(cost / grand_total * 100) if grand_total > 0 else 0.0
        avg_cost = str(cost / count) if count > 0 else "0.000000"

        breakdown.append(
            {
                "name": str(row[group_by] or "unknown"),
                "total_cost": str(cost),
                "percentage": round(pct, 2),
                "request_count": count,
                "total_tokens": row["total_tokens"],
                "avg_cost_per_request": avg_cost,
            }
        )

    # "Other" bucket
    if remaining_rows:
        other_cost = sum(r["total_cost"] for r in remaining_rows)
        other_count = sum(r["request_count"] for r in remaining_rows)
        other_tokens = sum(r["total_tokens"] for r in remaining_rows)
        pct = float(other_cost / grand_total * 100) if grand_total > 0 else 0.0

        breakdown.append(
            {
                "name": "Other",
                "total_cost": str(other_cost),
                "percentage": round(pct, 2),
                "request_count": other_count,
                "total_tokens": other_tokens,
                "avg_cost_per_request": (
                    str(other_cost / other_count) if other_count > 0 else "0.000000"
                ),
            }
        )

    return {
        "group_by": group_by,
        "total_cost": str(grand_total),
        "breakdown": breakdown,
    }


# ---------------------------------------------------------------------------
# Function 4: Latency Stats
# ---------------------------------------------------------------------------


def get_latency_stats(queryset, period_start, period_end, granularity, group_by=None):
    """Return latency stats with percentiles and timeseries."""
    TruncClass = get_trunc_class(granularity)

    filtered_qs = queryset.filter(
        started_at__gte=period_start, started_at__lte=period_end
    )

    # Overall summary
    summary_agg = filtered_qs.aggregate(
        avg_ms=Coalesce(Avg("latency_ms"), Value(0), output_field=FloatField()),
        min_ms=Coalesce(Min("latency_ms"), Value(0), output_field=IntegerField()),
        max_ms=Coalesce(Max("latency_ms"), Value(0), output_field=IntegerField()),
        total_requests=Count("id"),
    )

    # Overall percentiles (Python-side)
    latency_values = list(
        filtered_qs.order_by("latency_ms").values_list("latency_ms", flat=True)[
            :MAX_PERCENTILE_SAMPLE
        ]
    )
    pcts = _compute_percentiles(latency_values, (0.50, 0.90, 0.95, 0.99))

    summary = {
        "avg_ms": round(summary_agg["avg_ms"], 2),
        "min_ms": summary_agg["min_ms"],
        "max_ms": summary_agg["max_ms"],
        "p50_ms": round(pcts[0.50], 2),
        "p90_ms": round(pcts[0.90], 2),
        "p95_ms": round(pcts[0.95], 2),
        "p99_ms": round(pcts[0.99], 2),
        "total_requests": summary_agg["total_requests"],
    }

    # Timeseries with basic aggregates
    ts_rows = list(
        filtered_qs.annotate(bucket=TruncClass("started_at"))
        .values("bucket")
        .annotate(
            avg_ms=Coalesce(Avg("latency_ms"), Value(0), output_field=FloatField()),
            min_ms=Coalesce(Min("latency_ms"), Value(0), output_field=IntegerField()),
            max_ms=Coalesce(Max("latency_ms"), Value(0), output_field=IntegerField()),
            request_count=Count("id"),
        )
        .order_by("bucket")
    )

    # Per-bucket percentiles (Python-side)
    raw_bucket_data = list(
        filtered_qs.annotate(bucket=TruncClass("started_at"))
        .values("bucket", "latency_ms")
        .order_by("bucket", "latency_ms")[:100_000]
    )

    bucket_latencies = defaultdict(list)
    for row in raw_bucket_data:
        bucket_latencies[row["bucket"]].append(row["latency_ms"])

    for ts_row in ts_rows:
        b = ts_row["bucket"]
        vals = bucket_latencies.get(b, [])
        bpcts = _compute_percentiles(vals, (0.50, 0.95, 0.99))
        ts_row["p50_ms"] = round(bpcts[0.50], 2)
        ts_row["p95_ms"] = round(bpcts[0.95], 2)
        ts_row["p99_ms"] = round(bpcts[0.99], 2)
        ts_row["avg_ms"] = round(ts_row["avg_ms"], 2)

    def _zero_latency():
        return {
            "avg_ms": 0.0,
            "min_ms": 0,
            "max_ms": 0,
            "request_count": 0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
        }

    timeseries = _fill_empty_buckets(
        ts_rows, period_start, period_end, granularity, _zero_latency
    )

    return {
        "granularity": granularity,
        "summary": summary,
        "timeseries": timeseries,
    }


# ---------------------------------------------------------------------------
# Function 5: Error Breakdown
# ---------------------------------------------------------------------------


def get_error_breakdown(
    queryset,
    period_start,
    period_end,
    granularity="hour",
    group_by="status_code",
    top_n=10,
):
    """Return error analysis with breakdown and timeseries."""
    TruncClass = get_trunc_class(granularity)

    allowed_groups = {"status_code", "model", "provider", "error_message"}
    if group_by not in allowed_groups:
        group_by = "status_code"

    filtered_qs = queryset.filter(
        started_at__gte=period_start, started_at__lte=period_end
    )

    # Overall counts
    overall = filtered_qs.aggregate(
        total_requests=Count("id"),
        total_errors=Count("id", filter=Q(is_error=True)),
    )
    total_requests = overall["total_requests"]
    total_errors = overall["total_errors"]
    overall_error_rate = (
        (total_errors / total_requests * 100) if total_requests > 0 else 0.0
    )

    # Breakdown
    error_qs = filtered_qs.filter(is_error=True)

    if group_by == "error_message":
        group_field = "error_truncated"
        error_qs = error_qs.annotate(error_truncated=Substr("error_message", 1, 100))
    else:
        group_field = group_by

    breakdown_rows = list(
        error_qs.values(group_field)
        .annotate(
            error_count=Count("id"),
            first_seen=Min("started_at"),
            last_seen=Max("started_at"),
        )
        .order_by("-error_count")[:top_n]
    )

    breakdown = []
    for row in breakdown_rows:
        name = str(row[group_field] or "unknown")
        error_count = row["error_count"]
        pct_of_errors = (error_count / total_errors * 100) if total_errors > 0 else 0.0
        err_rate = (error_count / total_requests * 100) if total_requests > 0 else 0.0

        # Sample error message
        sample_msg = ""
        if group_by != "error_message":
            sample = (
                error_qs.filter(**{group_field: row[group_field]})
                .values_list("error_message", flat=True)
                .first()
            )
            sample_msg = sample or ""
        else:
            sample_msg = name

        breakdown.append(
            {
                "name": name,
                "error_count": error_count,
                "percentage_of_errors": round(pct_of_errors, 2),
                "error_rate": round(err_rate, 2),
                "first_seen": (
                    row["first_seen"].isoformat() if row["first_seen"] else None
                ),
                "last_seen": row["last_seen"].isoformat() if row["last_seen"] else None,
                "sample_error_message": sample_msg,
            }
        )

    # Error timeseries
    ts_rows = list(
        filtered_qs.annotate(bucket=TruncClass("started_at"))
        .values("bucket")
        .annotate(
            error_count=Count("id", filter=Q(is_error=True)),
            total_count=Count("id"),
        )
        .order_by("bucket")
    )

    for row in ts_rows:
        row["error_rate"] = round(
            (
                (row["error_count"] / row["total_count"] * 100)
                if row["total_count"] > 0
                else 0.0
            ),
            2,
        )

    def _zero_error_ts():
        return {"error_count": 0, "total_count": 0, "error_rate": 0.0}

    error_timeseries = _fill_empty_buckets(
        ts_rows, period_start, period_end, granularity, _zero_error_ts
    )

    return {
        "group_by": group_by,
        "total_requests": total_requests,
        "total_errors": total_errors,
        "overall_error_rate": round(overall_error_rate, 2),
        "breakdown": breakdown,
        "error_timeseries": error_timeseries,
    }


# ---------------------------------------------------------------------------
# Function 6: Model Comparison
# ---------------------------------------------------------------------------


def get_model_comparison(queryset, period_start, period_end, models_list=None):
    """Return side-by-side model performance metrics."""
    filtered_qs = queryset.filter(
        started_at__gte=period_start, started_at__lte=period_end
    )

    if models_list:
        filtered_qs = filtered_qs.filter(model__in=models_list)

    # Main aggregation per model
    model_rows = list(
        filtered_qs.values("model")
        .annotate(
            request_count=Count("id"),
            total_tokens=Coalesce(
                Sum("total_tokens"), Value(0), output_field=IntegerField()
            ),
            avg_input_tokens=Coalesce(
                Avg("input_tokens"), Value(0), output_field=FloatField()
            ),
            avg_output_tokens=Coalesce(
                Avg("output_tokens"), Value(0), output_field=FloatField()
            ),
            total_cost=Coalesce(Sum("cost"), Value(0), output_field=DecimalField()),
            avg_latency_ms=Coalesce(
                Avg("latency_ms"), Value(0), output_field=FloatField()
            ),
            error_count=Count("id", filter=Q(is_error=True)),
            cache_hit_count=Count("id", filter=Q(cache_hit=True)),
            guardrail_trigger_count=Count("id", filter=Q(guardrail_triggered=True)),
        )
        .order_by("-request_count")
    )

    # Pre-fetch dominant provider for all models in a single query
    provider_counts = list(
        filtered_qs.values("model", "provider")
        .annotate(cnt=Count("id"))
        .order_by("model", "-cnt")
    )
    # Build model -> dominant provider map (first entry per model has highest count)
    dominant_provider = {}
    for pc in provider_counts:
        if pc["model"] not in dominant_provider:
            dominant_provider[pc["model"]] = pc["provider"]

    # Pre-fetch all latency values grouped by model in a single query
    all_latencies = list(
        filtered_qs.values("model", "latency_ms").order_by("model", "latency_ms")[
            :MAX_PERCENTILE_SAMPLE
        ]
    )
    model_latencies = defaultdict(list)
    for row in all_latencies:
        model_latencies[row["model"]].append(row["latency_ms"])

    models = []
    for row in model_rows:
        model_name = row["model"]
        request_count = row["request_count"]

        provider = dominant_provider.get(model_name, "unknown")

        # Per-model percentiles from pre-fetched data
        latency_values = model_latencies.get(model_name, [])
        pcts = _compute_percentiles(latency_values, (0.50, 0.95, 0.99))

        # Derived fields
        avg_cost = (
            row["total_cost"] / request_count if request_count > 0 else Decimal("0")
        )
        error_rate = (
            (row["error_count"] / request_count * 100) if request_count > 0 else 0.0
        )
        cache_hit_rate = (
            (row["cache_hit_count"] / request_count * 100) if request_count > 0 else 0.0
        )
        guardrail_rate = (
            (row["guardrail_trigger_count"] / request_count * 100)
            if request_count > 0
            else 0.0
        )

        # Tokens per second
        avg_latency_s = row["avg_latency_ms"] / 1000 if row["avg_latency_ms"] > 0 else 0
        avg_total_tokens = (
            (row["total_tokens"] / request_count) if request_count > 0 else 0
        )
        tokens_per_second = (
            (avg_total_tokens / avg_latency_s) if avg_latency_s > 0 else 0.0
        )

        models.append(
            {
                "model": model_name,
                "provider": provider,
                "request_count": request_count,
                "total_tokens": row["total_tokens"],
                "avg_input_tokens": round(row["avg_input_tokens"], 1),
                "avg_output_tokens": round(row["avg_output_tokens"], 1),
                "total_cost": str(row["total_cost"]),
                "avg_cost_per_request": str(avg_cost),
                "avg_latency_ms": round(row["avg_latency_ms"], 2),
                "p50_latency_ms": round(pcts[0.50], 2),
                "p95_latency_ms": round(pcts[0.95], 2),
                "p99_latency_ms": round(pcts[0.99], 2),
                "error_count": row["error_count"],
                "error_rate": round(error_rate, 2),
                "cache_hit_rate": round(cache_hit_rate, 2),
                "guardrail_trigger_rate": round(guardrail_rate, 2),
                "tokens_per_second": round(tokens_per_second, 1),
            }
        )

    return {"models": models}


# ---------------------------------------------------------------------------
# Function 7: Guardrail Overview
# ---------------------------------------------------------------------------


def get_guardrail_overview(queryset, start, end):
    """Aggregate guardrail KPIs for the time range."""
    qs = queryset.filter(started_at__gte=start, started_at__lte=end)

    total = qs.count()
    triggered = qs.filter(guardrail_triggered=True).count()
    trigger_rate = (triggered / total * 100) if total > 0 else 0.0

    # Parse guardrail_results JSON for block/warn breakdown
    blocks = 0
    warns = 0
    total_latency = 0.0
    latency_count = 0
    rule_counts = defaultdict(int)

    triggered_qs = qs.filter(guardrail_triggered=True).values_list(
        "guardrail_results", flat=True
    )[:10000]

    for result in triggered_qs:
        # Gateway sends guardrail_results as a list of check objects:
        # [{"name": "pii-detection", "score": 1, "action": "block", ...}]
        checks = []
        if isinstance(result, list):
            checks = result
        elif isinstance(result, dict):
            checks = result.get("checks", [])
        else:
            continue

        for check in checks:
            if not isinstance(check, dict):
                continue
            action = check.get("action", "")
            if action == "block":
                blocks += 1
            elif action == "warn":
                warns += 1
            name = check.get("name", "unknown")
            rule_counts[name] += 1
            lat = check.get("latency_ms")
            if lat is not None:
                total_latency += float(lat)
                latency_count += 1

    top_rule = max(rule_counts, key=rule_counts.get) if rule_counts else None
    avg_latency = round(total_latency / latency_count, 2) if latency_count > 0 else 0.0

    return {
        "total_requests": total,
        "guardrail_triggered": triggered,
        "trigger_rate": round(trigger_rate, 2),
        "block_count": blocks,
        "warn_count": warns,
        "block_rate": round(blocks / total * 100, 2) if total > 0 else 0.0,
        "warn_rate": round(warns / total * 100, 2) if total > 0 else 0.0,
        "avg_guardrail_latency_ms": avg_latency,
        "top_triggered_rule": top_rule,
        "top_triggered_rule_count": rule_counts.get(top_rule, 0) if top_rule else 0,
    }


# ---------------------------------------------------------------------------
# Function 8: Guardrail Per-Rule Breakdown
# ---------------------------------------------------------------------------


def get_guardrail_rules(queryset, start, end):
    """Per-rule breakdown of guardrail triggers."""
    qs = queryset.filter(
        started_at__gte=start,
        started_at__lte=end,
        guardrail_triggered=True,
    ).values_list("guardrail_results", flat=True)[:10000]

    rules = defaultdict(
        lambda: {
            "trigger_count": 0,
            "block_count": 0,
            "warn_count": 0,
            "total_latency_ms": 0.0,
            "latency_samples": 0,
        }
    )

    for result in qs:
        # Gateway sends guardrail_results as a list of check objects
        checks = []
        if isinstance(result, list):
            checks = result
        elif isinstance(result, dict):
            checks = result.get("checks", [])
        else:
            continue

        for check in checks:
            if not isinstance(check, dict):
                continue
            name = check.get("name", "unknown")
            rules[name]["trigger_count"] += 1
            action = check.get("action", "")
            if action == "block":
                rules[name]["block_count"] += 1
            elif action == "warn":
                rules[name]["warn_count"] += 1
            lat = check.get("latency_ms")
            if lat is not None:
                rules[name]["total_latency_ms"] += float(lat)
                rules[name]["latency_samples"] += 1

    result_list = []
    for name, data in sorted(rules.items(), key=lambda x: -x[1]["trigger_count"]):
        avg_lat = (
            round(data["total_latency_ms"] / data["latency_samples"], 2)
            if data["latency_samples"] > 0
            else 0.0
        )
        result_list.append(
            {
                "rule": name,
                "trigger_count": data["trigger_count"],
                "block_count": data["block_count"],
                "warn_count": data["warn_count"],
                "avg_latency_ms": avg_lat,
            }
        )

    return {"rules": result_list}


# ---------------------------------------------------------------------------
# Function 9: Guardrail Trends
# ---------------------------------------------------------------------------


def get_guardrail_trends(queryset, start, end, granularity="hour"):
    """Time-bucketed guardrail trigger counts."""
    if granularity not in ALLOWED_GRANULARITIES:
        granularity = "hour"
    trunc_cls = get_trunc_class(granularity)

    qs = queryset.filter(
        started_at__gte=start,
        started_at__lte=end,
        guardrail_triggered=True,
    )

    buckets = (
        qs.annotate(bucket=trunc_cls("started_at"))
        .values("bucket")
        .annotate(
            trigger_count=Count("id"),
        )
        .order_by("bucket")
    )

    # Fill empty buckets
    delta = GRANULARITY_DELTA.get(granularity, timedelta(hours=1))
    bucket_map = {row["bucket"]: row["trigger_count"] for row in buckets}

    series = []
    current = _truncate_dt(start, granularity)
    while current <= end:
        series.append(
            {
                "timestamp": current.isoformat(),
                "trigger_count": bucket_map.get(current, 0),
            }
        )
        current += delta

    return {
        "granularity": granularity,
        "series": series,
    }
