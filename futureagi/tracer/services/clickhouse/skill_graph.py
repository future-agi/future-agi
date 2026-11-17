"""Exact skill-version cohorts behind the existing Observe graph endpoint."""
import hashlib
import json
from datetime import UTC, datetime

from django.core.cache import cache

from tracer.services.clickhouse.query_builders.latest_filter_predicates import (
    UnsupportedFilterShapeError,
)
from tracer.services.clickhouse.query_builders.time_series import TimeSeriesQueryBuilder
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.query_builders.skill_runtime import (
    RuntimeReadSession,
)

# Deliberately separate from span-graph snapshots: these metrics have different
# populations and denominators. No ordinary graph cache can satisfy this read.
CACHE_NAMESPACE = "observe-skill-version-graph-v1"
METRICS = {
    "latency": ("latency_p95", "Root latency p95 (ms)", "p95"),
    "cost": ("cost_per_trace", "Cost per trace", "mean"),
    "tokens": ("tokens_per_trace", "Tokens per trace", "mean"),
    "total_tokens": ("tokens_per_trace", "Tokens per trace", "mean"),
    "traffic": ("traces", "Traces", "count"),
    "error_rate": ("failed_request_rate", "Failed requests", "percentage"),
}


def skill_graph_cache_key(*, project_id, organization_id, workspace_id, filters, interval, metric_id):
    identity = {"project": str(project_id), "organization": str(organization_id), "workspace": str(workspace_id),
                "filters": filters, "interval": interval, "metric": metric_id, "split_by": "skill_version"}
    return CACHE_NAMESPACE + ":" + hashlib.sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()


def fetch_skill_version_graph(*, analytics, project_id, organization_id, workspace_id, filters,
                              interval, metric_id, timeout_ms, refresh=False):
    metric_id = str(metric_id).lower()
    if metric_id not in METRICS:
        raise UnsupportedFilterShapeError("This metric does not support skill-version cohorts")
    key = skill_graph_cache_key(project_id=project_id, organization_id=organization_id, workspace_id=workspace_id,
                               filters=filters, interval=interval, metric_id=metric_id)
    if not refresh:
        cached = cache.get(key)
        if cached is not None:
            return cached
    builder = TimeSeriesQueryBuilder(project_id=str(project_id), filters=filters, interval=interval,
                                    observe_type="trace", split_by="skill_version")
    session = RuntimeReadSession(analytics, deadline=ReadDeadline.start(min(timeout_ms, 3000)))
    sql, params = builder.build()
    # A sentinel prevents publishing a silently truncated cohort population.
    result = session.execute((sql + " LIMIT 10001", params))
    if result.row_count > 10000:
        from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
        raise ReadDeadlineExceeded("Skill graph exceeds interactive read size")
    cohorts = builder.format_result(result.data, [])
    column, name, statistic = METRICS[metric_id]
    timestamps = [timestamp.replace(tzinfo=UTC) for timestamp in
                  builder._generate_timestamp_range(builder.start_date, builder.end_date, interval)]
    if len(cohorts["series"]) > 50 or len(timestamps) * len(cohorts["series"]) > 10000:
        from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
        raise ReadDeadlineExceeded("Skill graph exceeds interactive response size")
    series = []
    for cohort in cohorts["series"]:
        points = []
        for row in cohort["data"]:
            value = row[column]
            if metric_id == "error_rate" and value is not None:
                value *= 100
            timestamp = datetime.fromisoformat(row["timestamp"])
            timestamp = timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC)
            points.append({"timestamp": timestamp.isoformat(), "value": value, "primary_traffic": row["traces"],
                           "traces": row["traces"], "incomplete": row["incomplete"]})
        by_time = {point["timestamp"]: point for point in points}
        points = [by_time.get(timestamp.isoformat(), {
            "timestamp": timestamp.isoformat(), "value": 0 if metric_id == "traffic" else None,
            "primary_traffic": 0, "traces": 0, "incomplete": 0,
        }) for timestamp in timestamps]
        series.append({"version_id": cohort["version_id"], "label": cohort["label"], "data": points})
    response = {
        "metric_name": name, "metric_statistic": statistic,
        # Do not sum overlapping cohorts into the legacy single-series slot.
        "data": [], "series": series, "split_by": "skill_version",
        "totals_note": "cohorts overlap; not additive",
        "query_complete": True, "query_exact": True, "query_sampled": False, "query_status": "complete",
        "query_provenance": "bounded_candidates",
    }
    cache.set(key, response, timeout=30)
    return response
