"""Analytics service layer for Agentcc gateway dashboard.

All aggregation logic lives here as standalone functions. Each function
accepts a pre-filtered queryset (already scoped to the correct
organization/workspace by the ViewSet mixin) plus time range parameters,
and returns a plain Python dictionary ready for JSON serialization.
"""

import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db.models import (
    Aggregate,
    Avg,
    Count,
    DateTimeField,
    DecimalField,
    FloatField,
    Func,
    IntegerField,
    Max,
    Min,
    Q,
    Sum,
    TextField,
    Value,
)
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import (
    Coalesce,
    NullIf,
    Substr,
    TruncDay,
    TruncHour,
    TruncMinute,
    TruncMonth,
    TruncWeek,
)
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from agentcc.models.request_log import RequestLogTag
from agentcc.services.provider_label import (
    billing_mode_label_expression,
    provider_label_expression,
)

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

# Fixed-width buckets, in seconds, aligned to the Unix epoch (so to UTC
# midnight): the sizes the console sends for a range (analytics/UsageCharts.jsx,
# LatencyAnalytics.jsx and ErrorAnalytics.jsx computeGranularity: 5m up to 6 h,
# 15m up to 24 h, 1h up to 7 d, 6h up to 30 d, else 1d; KeyDetailDrawer.jsx 1h).
FIXED_BUCKET_SECONDS = {
    "5m": 5 * 60,
    "15m": 15 * 60,
    "1h": 60 * 60,
    "6h": 6 * 60 * 60,
    "1d": 24 * 60 * 60,
}

GRANULARITY_DELTA = {
    "minute": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
    "month": timedelta(days=30),  # approximate for bucket filling
    **{name: timedelta(seconds=size) for name, size in FIXED_BUCKET_SECONDS.items()},
}

ALLOWED_GRANULARITIES = set(TRUNC_MAP) | set(FIXED_BUCKET_SECONDS)
DEFAULT_GRANULARITY = "hour"


class InvalidGranularity(ValueError):
    """A granularity the analytics endpoints do not bucket by (answered as 400)."""

    def __init__(self, granularity):
        allowed = ", ".join([*FIXED_BUCKET_SECONDS, *TRUNC_MAP])
        super().__init__(
            f"Unknown granularity '{str(granularity)[:32]}'. Use one of: {allowed}."
        )


class EpochBucket(Func):
    """A timestamp floored to a fixed number of seconds since the Unix epoch."""

    template = (
        "TO_TIMESTAMP(FLOOR(EXTRACT(EPOCH FROM %(expressions)s) / %(seconds)d)"
        " * %(seconds)d)"
    )
    output_field = DateTimeField()

    def __init__(self, expression, seconds):
        super().__init__(expression, seconds=int(seconds))


# Calendar buckets start at midnight in the viewer's zone (``tz``), so a
# daily bar is the viewer's day; minute and hour buckets are UTC, and a
# chart labels them by instant. Fixed-size buckets (FIXED_BUCKET_SECONDS)
# stay floored to the Unix epoch, so in UTC, whatever ``tz`` asks.
CALENDAR_GRANULARITIES = {"day", "week", "month"}
UTC_ZONE_NAME = "UTC"
MAX_ZONE_NAME_LENGTH = 64

# The most buckets one chart request may ask for. Every empty bucket is a row
# built here, so an open window at minute size (start=2000-01-01) would be
# millions of rows in one request.
MAX_CHART_BUCKETS = 2000


class ChartWindowTooLarge(ValueError):
    """A chart window with more than MAX_CHART_BUCKETS buckets."""


UNKNOWN_GROUP = "unknown"
OTHER_GROUP = "Other"
TAG_GROUPS = {tag.value for tag in RequestLogTag}
# Caller-set tags are unbounded; grouped usage keeps the busiest series.
MAX_USAGE_TAG_GROUPS = 10
USAGE_SUMMED_FIELDS = (
    "request_count",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "total_cost",
    "unpriced_requests",
    "error_count",
)

# The gateway stores a request it found no price for with cost 0 and
# cost_source "unpriced" (log ingestion copies a v1 entry's metadata
# cost_source to the column). A cost total over such a request is not what
# it cost, so every total also counts them: the console reads a total whose
# requests all had no price as unknown, and one with some as a floor, never
# as free. The totals themselves are unchanged.
UNPRICED_COST_SOURCE = "unpriced"


def unpriced_requests():
    """How many of the aggregated requests the gateway found no price for."""
    return Count("id", filter=Q(cost_source=UNPRICED_COST_SOURCE))


def parse_time_range(query_params, validate_granularity=True):
    """Parse start, end, granularity from query params.

    Returns (period_start, period_end, granularity). An absent granularity is
    hour. An unknown one raises InvalidGranularity, or, for an endpoint that
    does not bucket (validate_granularity=False), is ignored.
    """
    now = timezone.now()

    raw_start = query_params.get("start")
    raw_end = query_params.get("end")
    granularity = query_params.get("granularity") or DEFAULT_GRANULARITY

    period_start = _parse_dt(raw_start, now - timedelta(hours=24))
    period_end = _parse_dt(raw_end, now)

    # Tolerate reversed ranges
    if period_start > period_end:
        period_start, period_end = period_end, period_start

    if granularity not in ALLOWED_GRANULARITIES:
        if validate_granularity:
            raise InvalidGranularity(granularity)
        granularity = DEFAULT_GRANULARITY

    return period_start, period_end, granularity


def _parse_dt(raw, default):
    if not raw:
        return default
    dt = parse_datetime(raw)
    if dt is None:
        return default
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def get_trunc_class(granularity):
    """The bucket expression for a granularity: called with the field name."""
    check_granularity(granularity)
    if granularity in FIXED_BUCKET_SECONDS:
        return partial(EpochBucket, seconds=FIXED_BUCKET_SECONDS[granularity])
    return TRUNC_MAP[granularity]


def check_granularity(granularity):
    if granularity not in ALLOWED_GRANULARITIES:
        raise InvalidGranularity(granularity)


def parse_time_zone(query_params):
    """The zone calendar buckets are cut in: ``tz`` when it names a known IANA
    zone, else UTC."""
    name = (query_params.get("tz") or "").strip()
    if not name or len(name) > MAX_ZONE_NAME_LENGTH or name == UTC_ZONE_NAME:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return UTC


def zone_name(zone):
    """The name a series echoes as ``time_zone``: the IANA key, or UTC."""
    return getattr(zone, "key", None) or UTC_ZONE_NAME


def series_time_zone(granularity, zone):
    """What a series echoes as ``time_zone``: the zone its day buckets start
    in, the asked one or UTC. A fixed-size series (5m to 1d) is cut from the
    Unix epoch, so it names UTC whatever ``tz`` asked."""
    if granularity in FIXED_BUCKET_SECONDS:
        return UTC_ZONE_NAME
    return zone_name(zone)


def bucket_trunc(granularity, zone=UTC, field="started_at"):
    """The database bucket for a granularity: calendar units from midnight in
    ``zone``, minutes and hours in UTC, fixed sizes floored to the Unix epoch.
    An unknown granularity raises InvalidGranularity."""
    trunc_cls = get_trunc_class(granularity)
    if granularity in FIXED_BUCKET_SECONDS:
        return trunc_cls(field)
    if granularity in CALENDAR_GRANULARITIES:
        return trunc_cls(field, tzinfo=zone)
    return trunc_cls(field, tzinfo=UTC)


def check_chart_window(period_start, period_end, granularity):
    """Refuse a window of more than MAX_CHART_BUCKETS buckets, before any
    query runs. An unknown granularity raises InvalidGranularity first."""
    check_granularity(granularity)
    delta = GRANULARITY_DELTA[granularity]
    buckets = int((period_end - period_start) / delta) + 1
    if buckets > MAX_CHART_BUCKETS:
        raise ChartWindowTooLarge(
            f"This window has {buckets:,} {granularity} buckets; a chart takes "
            f"at most {MAX_CHART_BUCKETS:,}. Choose a shorter window or a "
            "larger bucket size."
        )


# ---------------------------------------------------------------------------
# Percentile helper
# ---------------------------------------------------------------------------


class LatencyPercentile(Aggregate):
    """PostgreSQL continuous percentile over the entire filtered/grouped population.

    Sorting stays in the database: no request-level samples are transferred to
    Python, and neither slow requests nor later groups can fall outside a cap.
    """

    function = "PERCENTILE_CONT"
    template = "%(function)s(%(percentile)s) WITHIN GROUP (ORDER BY %(expressions)s)"
    output_field = FloatField()
    window_compatible = False

    def __init__(self, percentile):
        if percentile not in (0.50, 0.90, 0.95, 0.99):
            raise ValueError("Unsupported latency percentile")
        super().__init__("latency_ms", percentile=float(percentile), default=0.0)


def _latency_percentiles(percentiles=(50, 95, 99)):
    return {f"p{p}_ms": LatencyPercentile(p / 100) for p in percentiles}


def _compute_trend(current, previous):
    """Compute % change from previous to current. Returns None if previous is 0."""
    current = float(current) if current is not None else 0.0
    previous = float(previous) if previous is not None else 0.0
    if previous == 0 and current == 0:
        return 0.0
    if previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 2)


def _fill_empty_buckets(
    series, period_start, period_end, granularity, zero_row_fn, zone=UTC
):
    """Fill missing time buckets with zero-valued rows."""
    delta = GRANULARITY_DELTA.get(granularity, timedelta(hours=1))

    # Pre-index series by bucket for O(1) lookup instead of O(n) scan
    series_by_bucket = defaultdict(list)
    for row in series:
        series_by_bucket[row["bucket"]].append(row)

    filled = []
    current = _bucket_start(period_start, granularity, zone)
    while current <= period_end:
        if current in series_by_bucket:
            filled.extend(series_by_bucket[current])
        else:
            filled.append({"bucket": current, **zero_row_fn()})
        current += delta

    return filled


def _annotate_tag_group(queryset, group_by):
    """Expose a metadata tag as a column named after it, so it can be grouped on."""
    if group_by not in TAG_GROUPS:
        return queryset
    return queryset.annotate(
        **{
            group_by: Coalesce(
                NullIf(KeyTextTransform(group_by, "metadata"), Value("")),
                Value(UNKNOWN_GROUP),
                output_field=TextField(),
            )
        }
    )


# Groups named as customer screens name them: provider puts managed calls under "Future AGI managed", whatever
# upstream served them; billing_mode reads "Managed", "Own key" or the stored
# mode. Each is an annotation, which may not reuse a field's name.
LABELLED_GROUPS = {
    "provider": ("provider_shown", provider_label_expression),
    "billing_mode": ("billing_mode_shown", billing_mode_label_expression),
}


def _annotate_group(queryset, group_by):
    """The queryset with group_by as a column to group on, and that column's
    name: a labelled group's annotation, a metadata tag's, or the field."""
    if group_by in LABELLED_GROUPS:
        field, expression = LABELLED_GROUPS[group_by]
        return queryset.annotate(**{field: expression()}), field
    return _annotate_tag_group(queryset, group_by), group_by


def _group_name(row, group_by):
    """Display name of a grouped row; a missing value reads as unknown."""
    return str(row[group_by] or UNKNOWN_GROUP)


def _folds_into_other(name):
    """A caller's own group named Other joins the fold bucket, so one name never
    becomes two series with the same key."""
    return name == OTHER_GROUP


def _fold_small_usage_groups(rows, group_by):
    """Keep the MAX_USAGE_TAG_GROUPS busiest groups; sum the rest per bucket."""
    totals = defaultdict(int)
    for row in rows:
        totals[row[group_by]] += row["request_count"]
    if len(totals) <= MAX_USAGE_TAG_GROUPS:
        return rows

    ranked = sorted(totals, key=totals.get, reverse=True)
    kept = set(
        [name for name in ranked if not _folds_into_other(name)][:MAX_USAGE_TAG_GROUPS]
    )
    folded, other = [], {}
    for row in rows:
        if row[group_by] in kept:
            folded.append(row)
            continue
        acc = other.setdefault(
            row["bucket"],
            {
                "bucket": row["bucket"],
                group_by: OTHER_GROUP,
                "avg_latency_ms": 0.0,
                **dict.fromkeys(USAGE_SUMMED_FIELDS, 0),
            },
        )
        for field in USAGE_SUMMED_FIELDS:
            acc[field] += row[field]
        acc["avg_latency_ms"] += row["avg_latency_ms"] * row["request_count"]

    for acc in other.values():
        if acc["request_count"]:
            acc["avg_latency_ms"] /= acc["request_count"]
    return folded + list(other.values())


def _bucket_start(dt, granularity, zone=UTC):
    """The start of the bucket ``dt`` falls in, as the database cuts it
    (bucket_trunc): calendar units at midnight in ``zone``, minutes and hours
    in UTC, fixed sizes from the Unix epoch. A day added to a local midnight
    is the next local midnight, also across a daylight-saving change."""
    local = dt.astimezone(zone if granularity in CALENDAR_GRANULARITIES else UTC)
    return _truncate_dt(local, granularity)


def _truncate_dt(dt, granularity):
    """Truncate a datetime to the start of its bucket."""
    seconds = FIXED_BUCKET_SECONDS.get(granularity)
    if seconds:
        floored = math.floor(dt.timestamp() / seconds) * seconds
        return datetime.fromtimestamp(floored, tz=UTC)
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
        unpriced_requests=unpriced_requests(),
        avg_latency_ms=Coalesce(Avg("latency_ms"), Value(0), output_field=FloatField()),
        error_count=Count("id", filter=Q(is_error=True)),
        cache_hit_count=Count("id", filter=Q(cache_hit=True)),
        p95_ms=LatencyPercentile(0.95),
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

    p95_latency = current["p95_ms"]

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
        p95_ms=LatencyPercentile(0.95),
    )

    prev_total = prev["total_requests"]
    prev_error_rate = (
        (prev["error_count"] / prev_total * 100) if prev_total > 0 else 0.0
    )
    prev_cache_hit_rate = (
        (prev["cache_hit_count"] / prev_total * 100) if prev_total > 0 else 0.0
    )

    prev_p95 = prev["p95_ms"]

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
            "unpriced_requests": current["unpriced_requests"],
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
    queryset, period_start, period_end, granularity, group_by=None, zone=UTC
):
    """Return time-bucketed usage data for charts."""
    check_chart_window(period_start, period_end, granularity)

    filtered_qs = queryset.filter(
        started_at__gte=period_start, started_at__lte=period_end
    )

    base_annotate = {
        "request_count": Count("id"),
        "total_tokens": Coalesce(
            Sum("total_tokens"), Value(0), output_field=IntegerField()
        ),
        "input_tokens": Coalesce(
            Sum("input_tokens"), Value(0), output_field=IntegerField()
        ),
        "output_tokens": Coalesce(
            Sum("output_tokens"), Value(0), output_field=IntegerField()
        ),
        "total_cost": Coalesce(Sum("cost"), Value(0), output_field=DecimalField()),
        "unpriced_requests": unpriced_requests(),
        "error_count": Count("id", filter=Q(is_error=True)),
        "avg_latency_ms": Coalesce(
            Avg("latency_ms"), Value(0), output_field=FloatField()
        ),
    }

    allowed_groups = {"model", "provider", "billing_mode", *TAG_GROUPS}
    if not group_by or group_by not in allowed_groups:
        group_by = None
    annotated, group_field = _annotate_group(filtered_qs, group_by)
    values_fields = ["bucket", group_field] if group_by else ["bucket"]
    annotated = annotated.annotate(bucket=bucket_trunc(granularity, zone))

    rows = list(
        annotated.values(*values_fields).annotate(**base_annotate).order_by("bucket")
    )
    if group_by in TAG_GROUPS:
        rows = _fold_small_usage_groups(rows, group_by)

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
            "unpriced_requests": 0,
            "error_count": 0,
            "avg_latency_ms": 0.0,
        }

    if group_by is None:
        series = _fill_empty_buckets(
            rows, period_start, period_end, granularity, _zero_row, zone
        )
        return {
            "granularity": granularity,
            "time_zone": series_time_zone(granularity, zone),
            "series": series,
        }
    else:
        groups = defaultdict(list)
        for row in rows:
            key = row.pop(group_field, UNKNOWN_GROUP)
            groups[key].append(row)

        # Fill each group
        for key in groups:
            groups[key] = _fill_empty_buckets(
                groups[key], period_start, period_end, granularity, _zero_row, zone
            )

        return {
            "granularity": granularity,
            "time_zone": series_time_zone(granularity, zone),
            "group_by": group_by,
            "groups": dict(groups),
        }


# The bucket sizes the Logs volume strip asks for (frontend
# logs/requestVolume.js volumeWindow: minute up to 6 hours, hour up to 7
# days, else day).
VOLUME_GRANULARITIES = ("minute", "hour", "day")


def get_request_volume(queryset, period_start, period_end, granularity, zone=UTC):
    """Requests and errors per time bucket over ``queryset``.

    ``queryset`` carries the Logs table's own filters, so the strip counts the
    requests the table lists. Every bucket in the window is present (zero
    when empty), oldest first; day buckets are days in ``zone``.
    """
    check_chart_window(period_start, period_end, granularity)
    rows = list(
        queryset.filter(started_at__gte=period_start, started_at__lte=period_end)
        .annotate(bucket=bucket_trunc(granularity, zone))
        .values("bucket")
        .annotate(
            request_count=Count("id"),
            error_count=Count("id", filter=Q(is_error=True)),
        )
        .order_by("bucket")
    )
    series = _fill_empty_buckets(
        rows,
        period_start,
        period_end,
        granularity,
        lambda: {"request_count": 0, "error_count": 0},
        zone,
    )
    return {
        "granularity": granularity,
        "time_zone": series_time_zone(granularity, zone),
        "series": series,
    }


# ---------------------------------------------------------------------------
# Function 3: Cost Breakdown
# ---------------------------------------------------------------------------


def get_cost_breakdown(queryset, period_start, period_end, group_by="model", top_n=10):
    """Return cost breakdown by a categorical dimension."""
    allowed_groups = {
        "model",
        "provider",
        "api_key_id",
        "user_id",
        "routing_strategy",
        "billing_mode",
        *TAG_GROUPS,
    }
    if group_by not in allowed_groups:
        group_by = "model"

    filtered_qs = queryset.filter(
        started_at__gte=period_start, started_at__lte=period_end
    )

    # Grand total
    grand_total_agg = filtered_qs.aggregate(
        grand_total=Coalesce(Sum("cost"), Value(0), output_field=DecimalField()),
        unpriced_requests=unpriced_requests(),
    )
    grand_total = grand_total_agg["grand_total"]

    # Group and aggregate
    grouped, group_field = _annotate_group(filtered_qs, group_by)
    all_rows = list(
        grouped.values(group_field)
        .annotate(
            total_cost=Coalesce(Sum("cost"), Value(0), output_field=DecimalField()),
            request_count=Count("id"),
            unpriced_requests=unpriced_requests(),
            total_tokens=Coalesce(
                Sum("total_tokens"), Value(0), output_field=IntegerField()
            ),
        )
        .order_by("-total_cost")
    )

    top_rows, remaining_rows = [], []
    for row in all_rows:
        keeps_own_row = len(top_rows) < top_n and not _folds_into_other(
            _group_name(row, group_field)
        )
        (top_rows if keeps_own_row else remaining_rows).append(row)

    breakdown = []
    for row in top_rows:
        cost = row["total_cost"]
        count = row["request_count"]
        pct = float(cost / grand_total * 100) if grand_total > 0 else 0.0
        avg_cost = str(cost / count) if count > 0 else "0.000000"

        breakdown.append(
            {
                "name": _group_name(row, group_field),
                "total_cost": str(cost),
                "percentage": round(pct, 2),
                "request_count": count,
                "unpriced_requests": row["unpriced_requests"],
                "total_tokens": row["total_tokens"],
                "avg_cost_per_request": avg_cost,
            }
        )

    # "Other" bucket
    if remaining_rows:
        other_cost = sum(r["total_cost"] for r in remaining_rows)
        other_count = sum(r["request_count"] for r in remaining_rows)
        other_unpriced = sum(r["unpriced_requests"] for r in remaining_rows)
        other_tokens = sum(r["total_tokens"] for r in remaining_rows)
        pct = float(other_cost / grand_total * 100) if grand_total > 0 else 0.0

        breakdown.append(
            {
                "name": OTHER_GROUP,
                "total_cost": str(other_cost),
                "percentage": round(pct, 2),
                "request_count": other_count,
                "unpriced_requests": other_unpriced,
                "total_tokens": other_tokens,
                "avg_cost_per_request": (
                    str(other_cost / other_count) if other_count > 0 else "0.000000"
                ),
            }
        )

    return {
        "group_by": group_by,
        "total_cost": str(grand_total),
        "unpriced_requests": grand_total_agg["unpriced_requests"],
        "breakdown": breakdown,
    }


# ---------------------------------------------------------------------------
# Function 4: Latency Stats
# ---------------------------------------------------------------------------


def get_latency_stats(
    queryset, period_start, period_end, granularity, group_by=None, zone=UTC
):
    """Return latency stats with percentiles and timeseries."""
    check_chart_window(period_start, period_end, granularity)
    bucket = bucket_trunc(granularity, zone)

    filtered_qs = queryset.filter(
        started_at__gte=period_start, started_at__lte=period_end
    )

    # Overall summary
    summary_agg = filtered_qs.aggregate(
        avg_ms=Coalesce(Avg("latency_ms"), Value(0), output_field=FloatField()),
        min_ms=Coalesce(Min("latency_ms"), Value(0), output_field=IntegerField()),
        max_ms=Coalesce(Max("latency_ms"), Value(0), output_field=IntegerField()),
        total_requests=Count("id"),
        **_latency_percentiles((50, 90, 95, 99)),
    )

    summary = {
        "avg_ms": round(summary_agg["avg_ms"], 2),
        "min_ms": summary_agg["min_ms"],
        "max_ms": summary_agg["max_ms"],
        **{f"p{p}_ms": round(summary_agg[f"p{p}_ms"], 2) for p in (50, 90, 95, 99)},
        "total_requests": summary_agg["total_requests"],
    }

    # Timeseries with basic aggregates
    ts_rows = list(
        filtered_qs.annotate(bucket=bucket)
        .values("bucket")
        .annotate(
            avg_ms=Coalesce(Avg("latency_ms"), Value(0), output_field=FloatField()),
            min_ms=Coalesce(Min("latency_ms"), Value(0), output_field=IntegerField()),
            max_ms=Coalesce(Max("latency_ms"), Value(0), output_field=IntegerField()),
            request_count=Count("id"),
            **_latency_percentiles(),
        )
        .order_by("bucket")
    )

    for ts_row in ts_rows:
        for field in ("p50_ms", "p95_ms", "p99_ms", "avg_ms"):
            ts_row[field] = round(ts_row[field], 2)

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
        ts_rows, period_start, period_end, granularity, _zero_latency, zone
    )

    return {
        "granularity": granularity,
        "time_zone": series_time_zone(granularity, zone),
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
    zone=UTC,
):
    """Return error analysis with breakdown and timeseries."""
    check_chart_window(period_start, period_end, granularity)

    allowed_groups = {
        "status_code",
        "model",
        "provider",
        "billing_mode",
        "error_message",
        *TAG_GROUPS,
    }
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
    error_qs, group_field = _annotate_group(filtered_qs.filter(is_error=True), group_by)

    if group_by == "error_message":
        group_field = "error_truncated"
        error_qs = error_qs.annotate(error_truncated=Substr("error_message", 1, 100))

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
        name = _group_name(row, group_field)
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
        filtered_qs.annotate(bucket=bucket_trunc(granularity, zone))
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
        ts_rows, period_start, period_end, granularity, _zero_error_ts, zone
    )

    return {
        "group_by": group_by,
        "time_zone": series_time_zone(granularity, zone),
        "total_requests": total_requests,
        "total_errors": total_errors,
        "overall_error_rate": round(overall_error_rate, 2),
        "breakdown": breakdown,
        "error_timeseries": error_timeseries,
    }


# ---------------------------------------------------------------------------
# Function 6: Model Comparison
# ---------------------------------------------------------------------------


MODEL_COMPARISON_GROUPS = {
    "model": ("model",),
    "billing_mode": ("model", "billing_mode"),
}


def _dominant_providers(counts, keys):
    """Per group (the values of ``keys``), the provider most of its requests
    show (provider_label) and the billing mode most of those requests had.
    Ties go to the first name in order, so the answer does not depend on the
    database's row order."""
    by_label = defaultdict(lambda: defaultdict(int))
    by_mode = defaultdict(lambda: defaultdict(int))
    for row in counts:
        group = tuple(row[k] for k in keys)
        by_label[group][row["provider_shown"]] += row["cnt"]
        by_mode[(group, row["provider_shown"])][row["billing_mode"]] += row["cnt"]
    dominant = {}
    for group, labels in by_label.items():
        label = max(sorted(labels, key=str), key=labels.get)
        modes = by_mode[(group, label)]
        dominant[group] = (label, max(sorted(modes, key=str), key=modes.get))
    return dominant


def get_model_comparison(
    queryset, period_start, period_end, models_list=None, group_by="model"
):
    """Return side-by-side model performance metrics: a row per model, or
    with group_by=billing_mode a row per model and billing mode (managed, own
    key, or none recorded). Each row names the provider most of its requests
    show, "Future AGI managed" for managed calls,
    and the billing mode of those requests."""
    if group_by not in MODEL_COMPARISON_GROUPS:
        group_by = "model"
    keys = MODEL_COMPARISON_GROUPS[group_by]

    filtered_qs = queryset.filter(
        started_at__gte=period_start, started_at__lte=period_end
    )

    if models_list:
        filtered_qs = filtered_qs.filter(model__in=models_list)

    # Main aggregation per group
    model_rows = list(
        filtered_qs.values(*keys)
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
            unpriced_requests=unpriced_requests(),
            avg_latency_ms=Coalesce(
                Avg("latency_ms"), Value(0), output_field=FloatField()
            ),
            error_count=Count("id", filter=Q(is_error=True)),
            cache_hit_count=Count("id", filter=Q(cache_hit=True)),
            guardrail_trigger_count=Count("id", filter=Q(guardrail_triggered=True)),
            **_latency_percentiles(),
        )
        .order_by("-request_count", *keys)
    )

    # The dominant provider of every group, in a single query
    dominant_provider = _dominant_providers(
        filtered_qs.annotate(provider_shown=provider_label_expression())
        .values("model", "billing_mode", "provider_shown")
        .annotate(cnt=Count("id"))
        .order_by(),
        keys,
    )

    models = []
    for row in model_rows:
        model_name = row["model"]
        request_count = row["request_count"]
        group = tuple(row[k] for k in keys)

        provider, billing_mode = dominant_provider.get(group, (UNKNOWN_GROUP, None))

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
                "billing_mode": billing_mode,
                "request_count": request_count,
                "total_tokens": row["total_tokens"],
                "avg_input_tokens": round(row["avg_input_tokens"], 1),
                "avg_output_tokens": round(row["avg_output_tokens"], 1),
                "total_cost": str(row["total_cost"]),
                "avg_cost_per_request": str(avg_cost),
                "unpriced_requests": row["unpriced_requests"],
                "avg_latency_ms": round(row["avg_latency_ms"], 2),
                "p50_latency_ms": round(row["p50_ms"], 2),
                "p95_latency_ms": round(row["p95_ms"], 2),
                "p99_latency_ms": round(row["p99_ms"], 2),
                "error_count": row["error_count"],
                "error_rate": round(error_rate, 2),
                "cache_hit_rate": round(cache_hit_rate, 2),
                "guardrail_trigger_rate": round(guardrail_rate, 2),
                "tokens_per_second": round(tokens_per_second, 1),
            }
        )

    return {"group_by": group_by, "models": models}


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


def get_guardrail_trends(queryset, start, end, granularity="hour", zone=UTC):
    """Time-bucketed guardrail trigger counts; day buckets are days in ``zone``.
    An unknown granularity raises InvalidGranularity (a 400), as on the other
    series."""
    check_chart_window(start, end, granularity)

    qs = queryset.filter(
        started_at__gte=start,
        started_at__lte=end,
        guardrail_triggered=True,
    )

    buckets = (
        qs.annotate(bucket=bucket_trunc(granularity, zone))
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
    current = _bucket_start(start, granularity, zone)
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
        "time_zone": series_time_zone(granularity, zone),
        "series": series,
    }
