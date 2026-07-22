from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from statistics import mean
from typing import Any

from django.db import models
from django.utils import timezone

from tracer.models.observation_span import EndUser, ObservationSpan
from tracer.models.project import Project
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.user_list import UserListQueryBuilder


def build_user_list_pg(
    *,
    organization_id: str,
    workspace,
    project_id: str | None,
    search: str | None,
    limit: int | None,
    offset: int | None,
    filters: list[dict[str, Any]],
    sort_params: list[dict[str, Any]],
) -> dict[str, Any]:
    """PostgreSQL fallback for the Observe Users table."""
    start_date, end_date = _time_range(filters)
    end_users = _end_user_queryset(
        organization_id=organization_id,
        workspace=workspace,
        project_id=project_id,
        search=search,
    )
    end_users = _apply_user_filters(end_users, filters)

    spans = _span_queryset(
        organization_id=organization_id,
        workspace=workspace,
        project_id=project_id,
        filters=filters,
        start_date=start_date,
        end_date=end_date,
    ).filter(end_user_id__in=end_users.values("id"))
    visible_user_ids = spans.values("end_user_id").distinct()
    end_users = end_users.filter(id__in=visible_user_ids)

    metrics_by_user = _aggregate_span_metrics(spans)
    rows = [_row_for_end_user(end_user, metrics_by_user) for end_user in end_users]
    rows = _apply_output_filters(rows, filters)
    rows = _sort_rows(rows, sort_params)

    total_count = len(rows)
    if limit is not None and offset is not None:
        rows = rows[int(offset) : int(offset) + int(limit)]
    for row in rows:
        row["total_count"] = total_count
    return UserListQueryBuilder.format_rows(rows)


def build_user_metrics_pg(
    *,
    organization_id: str,
    workspace,
    project_id: str,
    end_user_id: str,
    filters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """PostgreSQL fallback for the selected-user metric cards."""
    user_rows = build_user_list_pg(
        organization_id=organization_id,
        workspace=workspace,
        project_id=project_id,
        search=None,
        limit=None,
        offset=None,
        filters=[
            *filters,
            {
                "column_id": "end_user_id",
                "filter_config": {
                    "filter_type": "text",
                    "filter_op": "equals",
                    "filter_value": end_user_id,
                },
            },
        ],
        sort_params=[],
    )["table"]
    return [
        {
            "user_id": row.get("user_id"),
            "user_id_type": row.get("user_id_type"),
            "user_id_hash": row.get("user_id_hash"),
            "active_days": row.get("num_active_days", 0),
            "last_active": row.get("last_active"),
            "total_cost": row.get("total_cost", 0),
            "total_tokens": row.get("total_tokens", 0),
            "avg_session_duration": row.get("avg_session_duration", 0),
            "avg_trace_latency": row.get("avg_trace_latency", 0),
            "num_llm_calls": row.get("num_llm_calls", 0),
            "num_guardrails_triggered": row.get("num_guardrails_triggered", 0),
            "num_traces_with_errors": row.get("num_traces_with_errors", 0),
            "num_sessions": row.get("num_sessions", 0),
        }
        for row in user_rows
    ]


def build_users_aggregate_graph_pg(
    *,
    organization_id: str,
    workspace,
    project_id: str,
    filters: list[dict[str, Any]],
    interval: str,
    metric_id: str,
) -> dict[str, Any]:
    start_date, end_date = _time_range(filters)
    spans = _span_queryset(
        organization_id=organization_id,
        workspace=workspace,
        project_id=project_id,
        filters=filters,
        start_date=start_date,
        end_date=end_date,
    ).filter(end_user__isnull=False)
    buckets: dict[datetime, dict[str, set[str]]] = defaultdict(
        lambda: {"users": set(), "traces": set()}
    )
    for row in spans.values("end_user_id", "trace_id", "start_time", "created_at"):
        ts = row.get("start_time") or row.get("created_at")
        if not ts:
            continue
        bucket = _bucket(ts, interval)
        buckets[bucket]["users"].add(str(row["end_user_id"]))
        if row.get("trace_id"):
            buckets[bucket]["traces"].add(str(row["trace_id"]))

    data = []
    for ts in _timestamp_range(start_date, end_date, interval):
        data.append(
            {
                "timestamp": ts.isoformat(),
                "value": len(buckets[ts]["users"]),
                "primary_traffic": len(buckets[ts]["traces"]),
            }
        )
    return {"metric_name": metric_id, "data": data}


def build_user_graph_pg(
    *,
    organization_id: str,
    workspace,
    project_id: str,
    end_user_id: str,
    filters: list[dict[str, Any]],
    interval: str,
) -> dict[str, list[dict[str, Any]]]:
    start_date, end_date = _time_range(filters)
    if (
        not _end_user_queryset(
            organization_id=organization_id,
            workspace=workspace,
            project_id=project_id,
            search=None,
        )
        .filter(id=end_user_id)
        .exists()
    ):
        return {
            "session": [],
            "trace": [],
            "cost": [],
            "input_tokens": [],
            "output_tokens": [],
        }

    spans = _span_queryset(
        organization_id=organization_id,
        workspace=workspace,
        project_id=project_id,
        filters=filters,
        start_date=start_date,
        end_date=end_date,
    ).filter(end_user_id=end_user_id)
    buckets: dict[datetime, dict[str, Any]] = defaultdict(
        lambda: {
            "sessions": set(),
            "traces": set(),
            "cost": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
        }
    )
    for row in spans.values(
        "trace__session_id",
        "trace_id",
        "start_time",
        "created_at",
        "cost",
        "prompt_tokens",
        "completion_tokens",
    ):
        ts = row.get("start_time") or row.get("created_at")
        if not ts:
            continue
        bucket = _bucket(ts, interval)
        if row.get("trace__session_id"):
            buckets[bucket]["sessions"].add(str(row["trace__session_id"]))
        if row.get("trace_id"):
            buckets[bucket]["traces"].add(str(row["trace_id"]))
        buckets[bucket]["cost"] += float(row.get("cost") or 0)
        buckets[bucket]["input_tokens"] += int(row.get("prompt_tokens") or 0)
        buckets[bucket]["output_tokens"] += int(row.get("completion_tokens") or 0)

    output = {
        "session": [],
        "trace": [],
        "cost": [],
        "input_tokens": [],
        "output_tokens": [],
    }
    for ts in _timestamp_range(start_date, end_date, interval):
        values = buckets[ts]
        output["session"].append(
            {"timestamp": ts.isoformat(), "session": len(values["sessions"])}
        )
        output["trace"].append(
            {"timestamp": ts.isoformat(), "trace": len(values["traces"])}
        )
        output["cost"].append(
            {"timestamp": ts.isoformat(), "cost": round(values["cost"], 9)}
        )
        output["input_tokens"].append(
            {"timestamp": ts.isoformat(), "input_tokens": values["input_tokens"]}
        )
        output["output_tokens"].append(
            {"timestamp": ts.isoformat(), "output_tokens": values["output_tokens"]}
        )
    return output


def _time_range(filters: list[dict[str, Any]]) -> tuple[datetime, datetime]:
    start_date, end_date = BaseQueryBuilder.parse_time_range(filters)
    return _aware(start_date), _aware(end_date)


def _aware(value: datetime | None) -> datetime:
    dt = value or timezone.now()
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, UTC)
    return dt


def _end_user_queryset(
    *,
    organization_id: str,
    workspace,
    project_id: str | None,
    search: str | None,
):
    qs = EndUser.no_workspace_objects.filter(
        organization_id=organization_id,
        deleted=False,
    )
    if workspace:
        qs = qs.filter(_workspace_q(workspace))
    if project_id:
        qs = qs.filter(project_id=project_id)
    if search:
        qs = qs.filter(user_id__icontains=search)
    return qs


def _span_queryset(
    *,
    organization_id: str,
    workspace,
    project_id: str | None,
    filters: list[dict[str, Any]],
    start_date: datetime,
    end_date: datetime,
):
    qs = ObservationSpan.no_workspace_objects.filter(
        project__organization_id=organization_id,
        end_user__isnull=False,
    ).filter(_span_time_q(start_date, end_date))
    if workspace:
        qs = qs.filter(
            project__in=_project_ids_for_workspace(organization_id, workspace)
        )
    if project_id:
        qs = qs.filter(project_id=project_id)
    qs = _apply_span_attribute_filters(qs, filters)
    qs = _apply_user_relation_filters(qs, filters)
    return qs


def _project_ids_for_workspace(organization_id: str, workspace):
    workspace_q = models.Q(workspace=workspace)
    if getattr(workspace, "is_default", False):
        workspace_q |= models.Q(
            workspace__is_default=True,
            workspace__organization_id=workspace.organization_id,
        ) | models.Q(workspace__isnull=True, organization_id=organization_id)
    return Project.no_workspace_objects.filter(
        workspace_q,
        organization_id=organization_id,
        deleted=False,
    ).values("id")


def _workspace_q(workspace) -> models.Q:
    q = models.Q(workspace=workspace)
    if getattr(workspace, "is_default", False):
        q |= models.Q(
            workspace__is_default=True,
            workspace__organization_id=workspace.organization_id,
        ) | models.Q(workspace__isnull=True, organization_id=workspace.organization_id)
    return q


def _span_time_q(start_date: datetime, end_date: datetime) -> models.Q:
    return models.Q(start_time__gte=start_date, start_time__lt=end_date) | models.Q(
        start_time__isnull=True,
        created_at__gte=start_date,
        created_at__lt=end_date,
    )


def _aggregate_span_metrics(spans) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "total_cost": 0.0,
            "total_tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "trace_ids": set(),
            "session_ids": set(),
            "session_bounds": defaultdict(lambda: {"start": None, "end": None}),
            "latencies": [],
            "num_llm_calls": 0,
            "guardrail_trace_ids": set(),
            "active_days": set(),
            "error_trace_ids": set(),
            "last_active": None,
        }
    )
    for row in spans.values(
        "end_user_id",
        "trace_id",
        "trace__session_id",
        "observation_type",
        "status",
        "start_time",
        "end_time",
        "latency_ms",
        "cost",
        "total_tokens",
        "prompt_tokens",
        "completion_tokens",
    ):
        user_metrics = metrics[str(row["end_user_id"])]
        user_metrics["total_cost"] += float(row.get("cost") or 0)
        user_metrics["total_tokens"] += int(row.get("total_tokens") or 0)
        user_metrics["input_tokens"] += int(row.get("prompt_tokens") or 0)
        user_metrics["output_tokens"] += int(row.get("completion_tokens") or 0)
        if row.get("trace_id"):
            user_metrics["trace_ids"].add(str(row["trace_id"]))
        if row.get("trace__session_id"):
            session_id = str(row["trace__session_id"])
            user_metrics["session_ids"].add(session_id)
            _update_session_bounds(
                user_metrics["session_bounds"][session_id],
                row.get("start_time"),
                row.get("end_time"),
            )
        if row.get("latency_ms") is not None:
            user_metrics["latencies"].append(float(row["latency_ms"]))
        if row.get("observation_type") == "llm":
            user_metrics["num_llm_calls"] += 1
        if row.get("observation_type") == "guardrail" and row.get("trace_id"):
            user_metrics["guardrail_trace_ids"].add(str(row["trace_id"]))
        if row.get("status") == "ERROR" and row.get("trace_id"):
            user_metrics["error_trace_ids"].add(str(row["trace_id"]))
        active_at = row.get("start_time") or row.get("end_time")
        if active_at:
            user_metrics["active_days"].add(active_at.date())
        if row.get("end_time") and (
            not user_metrics["last_active"]
            or row["end_time"] > user_metrics["last_active"]
        ):
            user_metrics["last_active"] = row["end_time"]
    return metrics


def _update_session_bounds(bounds: dict[str, Any], start, end) -> None:
    if start and (bounds["start"] is None or start < bounds["start"]):
        bounds["start"] = start
    if end and (bounds["end"] is None or end > bounds["end"]):
        bounds["end"] = end


def _row_for_end_user(end_user: EndUser, metrics_by_user: dict[str, dict[str, Any]]):
    metrics = metrics_by_user.get(str(end_user.id), {})
    session_durations = [
        (bounds["end"] - bounds["start"]).total_seconds()
        for bounds in metrics.get("session_bounds", {}).values()
        if bounds["start"] and bounds["end"] and bounds["end"] >= bounds["start"]
    ]
    return {
        "user_id": end_user.user_id,
        "total_cost": metrics.get("total_cost", 0),
        "total_tokens": metrics.get("total_tokens", 0),
        "input_tokens": metrics.get("input_tokens", 0),
        "output_tokens": metrics.get("output_tokens", 0),
        "num_traces": len(metrics.get("trace_ids", set())),
        "num_sessions": len(metrics.get("session_ids", set())),
        "avg_session_duration": round(mean(session_durations), 2)
        if session_durations
        else 0,
        "avg_trace_latency": round(mean(metrics.get("latencies", [])), 2)
        if metrics.get("latencies")
        else 0,
        "num_llm_calls": metrics.get("num_llm_calls", 0),
        "num_guardrails_triggered": len(metrics.get("guardrail_trace_ids", set())),
        "activated_at": end_user.created_at,
        "last_active": metrics.get("last_active"),
        "num_active_days": len(metrics.get("active_days", set())),
        "num_traces_with_errors": len(metrics.get("error_trace_ids", set())),
        "bool_eval_pass_rate": 0,
        "avg_output_float": 0,
        "project_id": end_user.project_id,
        "user_id_type": end_user.user_id_type,
        "user_id_hash": end_user.user_id_hash,
        "end_user_id": end_user.id,
    }


def _apply_user_filters(qs, filters: list[dict[str, Any]]):
    for item in filters:
        column_id = item.get("column_id")
        config = item.get("filter_config") or {}
        op = config.get("filter_op")
        value = config.get("filter_value")
        if column_id == "user_id":
            qs = _apply_text_queryset_filter(qs, "user_id", op, value)
        elif column_id == "end_user_id":
            qs = _apply_text_queryset_filter(qs, "id", op, value)
        elif column_id == "user_id_type":
            qs = _apply_text_queryset_filter(qs, "user_id_type", op, value)
        elif column_id == "user_id_hash":
            qs = _apply_text_queryset_filter(qs, "user_id_hash", op, value)
    return qs


def _apply_user_relation_filters(qs, filters: list[dict[str, Any]]):
    for item in filters:
        column_id = item.get("column_id")
        config = item.get("filter_config") or {}
        op = config.get("filter_op")
        value = config.get("filter_value")
        if column_id == "user_id":
            qs = _apply_text_queryset_filter(qs, "end_user__user_id", op, value)
        elif column_id == "end_user_id":
            qs = _apply_text_queryset_filter(qs, "end_user_id", op, value)
    return qs


def _apply_span_attribute_filters(qs, filters: list[dict[str, Any]]):
    for item in filters:
        config = item.get("filter_config") or {}
        if config.get("col_type") != "SPAN_ATTRIBUTE":
            continue
        key = item.get("column_id")
        if not key:
            continue
        op = config.get("filter_op")
        value = config.get("filter_value")
        if op == "equals":
            qs = qs.filter(span_attributes__contains={key: value})
        elif op == "not_equals":
            qs = qs.exclude(span_attributes__contains={key: value})
    return qs


def _apply_text_queryset_filter(qs, field_name: str, op: str | None, value: Any):
    if op in ("equals", "in"):
        values = value if isinstance(value, list) else [value]
        return qs.filter(**{f"{field_name}__in": values})
    if op == "not_equals":
        return qs.exclude(**{field_name: value})
    if op == "contains" and value is not None:
        return qs.filter(**{f"{field_name}__icontains": value})
    if op == "not_contains" and value is not None:
        return qs.exclude(**{f"{field_name}__icontains": value})
    if op == "is_null":
        return qs.filter(**{f"{field_name}__isnull": True})
    if op == "is_not_null":
        return qs.filter(**{f"{field_name}__isnull": False})
    return qs


def _apply_output_filters(
    rows: list[dict[str, Any]], filters: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    output_rows = rows
    for item in filters:
        column_id = item.get("column_id")
        if column_id not in UserListQueryBuilder.OUTPUT_FILTER_MAP:
            continue
        if column_id in {"user_id", "end_user_id", "user_id_type", "user_id_hash"}:
            continue
        key = UserListQueryBuilder.OUTPUT_FILTER_MAP[column_id]
        config = item.get("filter_config") or {}
        op = config.get("filter_op")
        value = config.get("filter_value")
        output_rows = [row for row in output_rows if _matches(row.get(key), op, value)]
    return output_rows


def _matches(candidate: Any, op: str | None, value: Any) -> bool:
    if op == "is_null":
        return candidate is None
    if op == "is_not_null":
        return candidate is not None
    if op == "equals":
        return str(candidate) == str(value)
    if op == "not_equals":
        return str(candidate) != str(value)
    if op == "contains":
        return str(value).lower() in str(candidate).lower()
    if op == "not_contains":
        return str(value).lower() not in str(candidate).lower()
    if op in (
        "greater_than",
        "greater_than_or_equal",
        "less_than",
        "less_than_or_equal",
    ):
        try:
            left = float(candidate or 0)
            right = float(value)
        except (TypeError, ValueError):
            return False
        if op == "greater_than":
            return left > right
        if op == "greater_than_or_equal":
            return left >= right
        if op == "less_than":
            return left < right
        return left <= right
    return True


def _sort_rows(
    rows: list[dict[str, Any]], sort_params: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not sort_params:
        non_null = [row for row in rows if row.get("last_active") is not None]
        null = [row for row in rows if row.get("last_active") is None]
        return sorted(non_null, key=lambda row: row["last_active"], reverse=True) + null
    sorted_rows = rows
    for sort in reversed(sort_params):
        key = UserListQueryBuilder.OUTPUT_FILTER_MAP.get(sort.get("column_id"))
        if not key:
            continue
        reverse = str(sort.get("direction") or "desc").lower() != "asc"
        non_null = [row for row in sorted_rows if row.get(key) is not None]
        null = [row for row in sorted_rows if row.get(key) is None]
        sorted_rows = (
            sorted(non_null, key=lambda row: _sortable(row.get(key)), reverse=reverse)
            + null
        )
    return sorted_rows


def _sortable(value: Any) -> Any:
    if isinstance(value, (datetime, int, float)):
        return value
    return str(value)


def _bucket(value: datetime, interval: str) -> datetime:
    if timezone.is_aware(value):
        value = value.astimezone(UTC).replace(tzinfo=None)
    return BaseQueryBuilder._normalize_timestamp(value, interval)


def _timestamp_range(start_date: datetime, end_date: datetime, interval: str):
    start = start_date.astimezone(UTC).replace(tzinfo=None)
    end = end_date.astimezone(UTC).replace(tzinfo=None)
    return list(BaseQueryBuilder._generate_timestamp_range(start, end, interval))
