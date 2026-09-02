from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, NotRequired, Protocol, TypedDict, cast
from uuid import UUID

import structlog
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from tfc.constants.api_calls import APICallStatusChoices
from tracer.services.clickhouse.client import get_clickhouse_client
from tracer.services.clickhouse.read_budget import (
    is_clickhouse_query_error,
    is_read_budget_error,
)

if TYPE_CHECKING:
    from accounts.models.organization import Organization
    from accounts.models.workspace import Workspace

logger = structlog.get_logger(__name__)

MAX_TEMPLATE_IDS = 100
READ_TIMEOUT_MS = 2_000
FRESH_CACHE_SECONDS = 30
STALE_CACHE_SECONDS = 6 * 60 * 60

QueryStatus = Literal["complete", "stale", "degraded"]
QueryErrorCode = Literal[
    "read_budget_exceeded",
    "template_limit_exceeded",
    "query_failed",
]


class ChartPoint(TypedDict):
    timestamp: str
    value: int | float


class EvalTemplateChart(TypedDict):
    chart: list[ChartPoint]
    error_rate: list[ChartPoint]
    run_count: int


EvalTemplateCharts = dict[str, EvalTemplateChart]


class EvalListChartsResult(TypedDict):
    charts: EvalTemplateCharts
    query_complete: bool
    query_status: QueryStatus
    query_sampled: Literal[False]
    data_stale: bool
    query_error_code: NotRequired[QueryErrorCode]


class CacheBackend(Protocol):
    def get(self, key: str) -> object | None: ...

    def set(self, key: str, value: object, *, timeout: int) -> object: ...


def _cache_get(cache_backend: CacheBackend, key: str) -> EvalTemplateCharts | None:
    try:
        return cast(EvalTemplateCharts | None, cache_backend.get(key))
    except Exception:
        logger.warning(
            "eval_list_chart_cache_read_failed",
            cache_key=key[:64],
            exc_info=True,
        )
        return None


def _cache_set(
    cache_backend: CacheBackend,
    key: str,
    value: EvalTemplateCharts,
    *,
    timeout: int,
) -> None:
    try:
        cache_backend.set(key, value, timeout=timeout)
    except Exception:
        logger.warning(
            "eval_list_chart_cache_write_failed",
            cache_key=key[:64],
            exc_info=True,
        )


def _empty_charts(template_ids: list[str], *, start_day: date) -> EvalTemplateCharts:
    result: EvalTemplateCharts = {}
    for template_id in template_ids:
        chart: list[ChartPoint] = []
        error_rate: list[ChartPoint] = []
        for day_offset in range(31):
            day = start_day + timedelta(days=day_offset)
            timestamp = day.strftime("%Y-%m-%dT00:00:00")
            chart.append({"timestamp": timestamp, "value": 0})
            error_rate.append({"timestamp": timestamp, "value": 0})
        result[template_id] = {
            "chart": chart,
            "error_rate": error_rate,
            "run_count": 0,
        }
    return result


def _result(
    charts: EvalTemplateCharts,
    *,
    query_complete: bool,
    query_status: QueryStatus,
    data_stale: bool = False,
    query_error_code: QueryErrorCode | None = None,
) -> EvalListChartsResult:
    result = EvalListChartsResult(
        charts=charts,
        query_complete=query_complete,
        query_status=query_status,
        query_sampled=False,
        data_stale=data_stale,
    )
    if query_error_code is not None:
        result["query_error_code"] = query_error_code
    return result


def read_eval_list_charts(
    organization: Organization,
    workspace: Workspace | None,
    template_ids: Iterable[UUID | str],
) -> EvalListChartsResult:
    """Read the bounded 30-day aggregate for the eval-list chart page."""

    normalized_ids = list(dict.fromkeys(str(value) for value in template_ids))
    if len(normalized_ids) > MAX_TEMPLATE_IDS:
        return _result(
            {},
            query_complete=False,
            query_status="degraded",
            query_error_code="template_limit_exceeded",
        )

    today = timezone.now().astimezone(UTC).date()
    start_day = today - timedelta(days=30)
    end_day = today + timedelta(days=1)
    empty = _empty_charts(normalized_ids, start_day=start_day)
    if not normalized_ids:
        return _result(empty, query_complete=True, query_status="complete")

    workspace_id = str(workspace.id) if workspace is not None else None
    fingerprint = {
        "organization_id": str(organization.id),
        "workspace_id": workspace_id,
        "workspace_is_default": bool(
            workspace is not None and getattr(workspace, "is_default", False)
        ),
        "template_ids": sorted(normalized_ids),
        "start_day": start_day.isoformat(),
    }
    digest = hashlib.sha256(
        json.dumps(fingerprint, sort_keys=True).encode()
    ).hexdigest()
    fresh_key = f"eval-list-charts:v2:fresh:{digest}"
    stale_key = f"eval-list-charts:v2:stale:{digest}"
    cached = _cache_get(cache, fresh_key)
    if cached is not None:
        return _result(cached, query_complete=True, query_status="complete")

    clickhouse_settings = getattr(settings, "CLICKHOUSE", {})
    if not (
        clickhouse_settings.get("CH_ENABLED", False)
        and clickhouse_settings.get("CH_HOST")
    ):
        logger.warning(
            "eval_list_chart_clickhouse_unavailable",
            organization_id=str(organization.id),
            template_count=len(normalized_ids),
        )
        stale = _cache_get(cache, stale_key)
        if stale is not None:
            return _result(
                stale,
                query_complete=False,
                query_status="stale",
                data_stale=True,
                query_error_code="query_failed",
            )
        return _result(
            empty,
            query_complete=False,
            query_status="degraded",
            query_error_code="query_failed",
        )

    params: dict[str, Any] = {
        "organization_id": str(organization.id),
        "template_ids": tuple(normalized_ids),
        "start_date": datetime.combine(start_day, datetime.min.time(), tzinfo=UTC),
        "end_date": datetime.combine(end_day, datetime.min.time(), tzinfo=UTC),
        "error_status": APICallStatusChoices.ERROR.value,
    }
    scope = [
        "organization_id = toUUID(%(organization_id)s)",
        "source_id IN %(template_ids)s",
        "created_at >= %(start_date)s",
        "created_at < %(end_date)s",
    ]
    if workspace is not None:
        params["workspace_id"] = workspace_id
        if getattr(workspace, "is_default", False):
            scope.append(
                "(workspace_id = toUUID(%(workspace_id)s) OR workspace_id IS NULL)"
            )
        else:
            scope.append("workspace_id = toUUID(%(workspace_id)s)")

    failure = (
        "status = %(error_status)s "
        "OR lowerUTF8(eval_output_str) IN ('failed', 'fail') "
        "OR eval_score = 0"
    )
    query = f"""
        SELECT
            source_id,
            toStartOfDay(created_at, 'UTC') AS bucket,
            count() AS total,
            countIf({failure}) AS failures
        FROM (
            SELECT
                source_id,
                created_at,
                status,
                eval_score,
                eval_output_str,
                deleted,
                _peerdb_is_deleted
            FROM usage_apicalllog
            PREWHERE {" AND ".join(scope)}
            ORDER BY _peerdb_version DESC
            LIMIT 1 BY id
        )
        WHERE deleted = 0
          AND _peerdb_is_deleted = 0
        GROUP BY source_id, bucket
        ORDER BY source_id, bucket
    """

    try:
        rows, _column_types, _query_time_ms = get_clickhouse_client().execute_read(
            query,
            params,
            timeout_ms=READ_TIMEOUT_MS,
            settings={
                "max_threads": 2,
                "read_overflow_mode": "throw",
                "max_bytes_to_read": 36 * 1024 * 1024 * 1024,
                "max_memory_usage": 36 * 1024 * 1024 * 1024,
                "max_result_rows": MAX_TEMPLATE_IDS * 31,
                "max_result_bytes": 2 * 1024 * 1024,
                "result_overflow_mode": "throw",
                "timeout_overflow_mode": "throw",
            },
        )
    except Exception as exc:
        budget_error = is_read_budget_error(exc)
        if not budget_error and not is_clickhouse_query_error(exc):
            raise
        logger.warning(
            "eval_list_chart_clickhouse_read_degraded",
            organization_id=str(organization.id),
            template_count=len(normalized_ids),
            error_type=type(exc).__name__,
            error_code=getattr(exc, "code", None),
        )
        error_code: QueryErrorCode = (
            "read_budget_exceeded" if budget_error else "query_failed"
        )
        if budget_error:
            stale = _cache_get(cache, stale_key)
            if stale is not None:
                return _result(
                    stale,
                    query_complete=False,
                    query_status="stale",
                    data_stale=True,
                    query_error_code=error_code,
                )
        return _result(
            empty,
            query_complete=False,
            query_status="degraded",
            query_error_code=error_code,
        )

    by_template: defaultdict[str, dict[date, tuple[int, int]]] = defaultdict(dict)
    for source_id, bucket, total, failures in rows:
        bucket_day = bucket.date() if hasattr(bucket, "date") else bucket
        by_template[str(source_id)][bucket_day] = (
            int(total or 0),
            int(failures or 0),
        )

    result = _empty_charts(normalized_ids, start_day=start_day)
    for template_id in normalized_ids:
        run_count = 0
        for day_offset in range(31):
            day = start_day + timedelta(days=day_offset)
            total, failures = by_template.get(template_id, {}).get(day, (0, 0))
            run_count += total
            result[template_id]["chart"][day_offset]["value"] = total
            result[template_id]["error_rate"][day_offset]["value"] = (
                round(failures * 100.0 / total, 1) if total else 0
            )
        result[template_id]["run_count"] = run_count

    _cache_set(cache, fresh_key, result, timeout=FRESH_CACHE_SECONDS)
    _cache_set(cache, stale_key, result, timeout=STALE_CACHE_SECONDS)
    return _result(result, query_complete=True, query_status="complete")
