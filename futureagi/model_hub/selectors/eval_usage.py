from __future__ import annotations

import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, NoReturn

from tracer.services.clickhouse.client import get_clickhouse_client
from tracer.services.clickhouse.read_budget import (
    is_clickhouse_query_error,
    is_read_budget_error,
)
from tracer.services.clickhouse.trace_project_scope import (
    latest_live_trace_projects_sql,
)

READ_TIMEOUT_MS = 9_500
QUERY_TIMEOUT_MS = 9_500
MAX_PAGE_SIZE = 100
_USAGE_TABLE = "usage_apicalllog"
_MAX_PAGE_SELECTION_ROWS = 10_000
_MIN_PAGE_WINDOW = timedelta(microseconds=1)

_READ_SETTINGS = {
    "max_threads": 2,
    "read_overflow_mode": "throw",
    "max_bytes_to_read": 36 * 1024 * 1024 * 1024,
    "max_memory_usage": 36 * 1024 * 1024 * 1024,
    "timeout_overflow_mode": "throw",
}


@dataclass(frozen=True)
class EvalUsageLog:
    log_id: str
    config: dict[str, Any]
    status: str
    created_at: datetime | None


@dataclass(frozen=True)
class EvalUsageChartBucket:
    bucket: datetime
    calls: int
    avg_duration: float | None
    avg_score: float | None
    pass_count: int
    fail_count: int


class EvalUsageReadCompleteness(StrEnum):
    """Whether returned fields are exact and unsampled.

    ``COMPLETE`` does not assert transaction-level snapshot identity between
    the total, chart, and page statements. ClickHouse 25.3 has no shared
    snapshot primitive for these independent reads.
    """

    COMPLETE = "complete"
    DEGRADED = "degraded"


class EvalUsageReadErrorCode(StrEnum):
    DEADLINE_EXCEEDED = "deadline_exceeded"
    QUERY_FAILED = "query_failed"


class EvalUsageReadError(RuntimeError):
    """Sanitized, typed failure for a bounded eval-usage CH read."""

    def __init__(
        self,
        code: EvalUsageReadErrorCode,
        *,
        operations: tuple[str, ...] = (),
    ) -> None:
        self.code = code
        self.operations = operations
        super().__init__(code.value)


@dataclass(frozen=True)
class EvalUsageRead:
    total_runs: int
    runs_period: int
    success_count: int
    error_count: int
    chart: list[EvalUsageChartBucket]
    logs: list[EvalUsageLog]
    completeness: EvalUsageReadCompleteness
    unavailable_fields: tuple[str, ...]


def _decode_config(value: Any) -> dict[str, Any]:
    decoded = value
    # Historical rows include both normal JSON objects and JSONB values mirrored
    # as a JSON string. Decode at most twice; anything else is malformed input,
    # not a reason to fail the whole usage page.
    for _ in range(2):
        if not isinstance(decoded, str):
            break
        try:
            decoded = json.loads(decoded)
        except (TypeError, ValueError):
            return {}
    return decoded if isinstance(decoded, dict) else {}


def _finite_float_or_none(value: Any) -> float | None:
    """Normalize ClickHouse empty-aggregate sentinels at the read boundary.

    ClickHouse returns ``NaN`` for an empty ``avgIf`` over non-nullable
    floating-point inputs.  Non-finite values are not meaningful usage
    metrics and must not reach response formatting, where rounding ``NaN``
    raises for integer precision.
    """

    if value is None:
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return normalized if math.isfinite(normalized) else None


def _scope(
    *,
    organization_id: str,
    workspace_id: str | None,
) -> tuple[list[str], dict[str, Any]]:
    predicates = ["organization_id = toUUID(%(organization_id)s)"]
    params: dict[str, Any] = {"organization_id": organization_id}
    if workspace_id is not None:
        predicates.append("workspace_id = toUUID(%(workspace_id)s)")
        params["workspace_id"] = workspace_id
    return predicates, params


def _latest_usage_slice(
    *,
    projection: str,
    scope: list[str],
    source_id_param: str = "template_id",
    start_param: str | None,
    end_param: str | None,
    project_scoped: bool = True,
    extra_predicates: tuple[str, ...] = (),
) -> str:
    candidate_scope = [
        *scope,
        f"source_id = %({source_id_param})s",
        *extra_predicates,
    ]
    if start_param:
        candidate_scope.append(f"created_at >= %({start_param})s")
    if end_param:
        candidate_scope.append(f"created_at < %({end_param})s")
    latest_usage = f"""
        SELECT {projection}
        FROM {_USAGE_TABLE}
        PREWHERE {" AND ".join(candidate_scope)}
        ORDER BY _peerdb_version DESC
        LIMIT 1 BY id
    """
    if not project_scoped:
        return latest_usage

    # Apply project membership only after collapsing physical usage versions.
    # Filtering before LIMIT 1 BY could otherwise resurrect an older version
    # when the newest row no longer belongs to an allowed project.  Restrict
    # the trace relation to IDs present in this exact tenant/template/window
    # candidate slice; a project-wide trace dictionary is unbounded for large
    # tenants and caused the eval usage page to time out even when only a few
    # eval rows were relevant.  The repeated usage reference remains inside
    # this one statement; it is not described as a request-wide snapshot on
    # ClickHouse 25.3.
    trace_candidates = f"""
        SELECT DISTINCT toUUIDOrZero(eval_trace_id) AS trace_id
        FROM {_USAGE_TABLE}
        PREWHERE {" AND ".join(candidate_scope)}
        WHERE eval_trace_id != ''
    """
    trace_projects = latest_live_trace_projects_sql(
        candidate_trace_ids_sql=trace_candidates
    )
    return f"""
        SELECT current_usage.*
        FROM ({latest_usage}) AS current_usage
        LEFT JOIN ({trace_projects}) AS allowed_trace_projects
          ON allowed_trace_projects.trace_id =
             toUUIDOrZero(current_usage.eval_trace_id)
        WHERE (
            current_usage.eval_trace_id = ''
            OR allowed_trace_projects.project_id IN %(project_ids)s
        )
    """


def _split_page_window(
    start: datetime,
    end: datetime,
) -> tuple[tuple[datetime, datetime], tuple[datetime, datetime]] | None:
    """Split a DateTime64(6) range into exact older/newer halves."""

    if end - start <= _MIN_PAGE_WINDOW:
        return None
    midpoint = start + (end - start) / 2
    if midpoint <= start or midpoint >= end:
        return None
    return (start, midpoint), (midpoint, end)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def read_eval_usage(
    *,
    organization_id: str,
    workspace_id: str | None,
    project_ids: list[str] | tuple[str, ...],
    template_id: str,
    start_date: datetime,
    end_date: datetime,
    bucket_minutes: int,
    page: int,
    page_size: int,
) -> EvalUsageRead:
    """Read one eval template's stats, chart, and page from ClickHouse.

    Every query bounds work and narrows to the tenant/template candidate slice
    before collapsing physical versions. Production's historical table is
    ordered by ``id`` (new installs may use a tenant/time-aware key), so this
    path must remain safe without assuming tenant predicates are primary-key
    pruning. Tombstone predicates are deliberately outside the collapse so a
    deleted newest version cannot resurrect an older row.
    """

    if page < 0:
        raise ValueError("page must be non-negative")
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")

    scope, params = _scope(
        organization_id=organization_id,
        workspace_id=workspace_id,
    )
    params.update(
        {
            "template_id": template_id,
            # Keep IN syntactically valid and fail closed for trace-attached
            # rows when this template has no project config in the workspace.
            "project_ids": tuple(project_ids)
            or ("00000000-0000-0000-0000-000000000000",),
            "start_date": _utc(start_date),
            # The public contract treats end_date as inclusive. ClickHouse
            # reads use a half-open range, so advance by one storage tick.
            "end_date": _utc(end_date) + timedelta(microseconds=1),
            "bucket_minutes": bucket_minutes,
            "success_status": "success",
            "error_status": "error",
        }
    )

    live = "_peerdb_is_deleted = 0 AND deleted = 0"
    # Preserve the pre-CH contract exactly: total_runs is all live runs for the
    # organization/workspace/template, independent of the requested period.
    # Unlike period rendering, that contract never had project membership in
    # its scope. Avoiding the trace dictionary here keeps this exact count on
    # the table's organization/source ordering while the shared finite budget
    # turns an unprovable count into a typed failure, never a partial success.
    total_slice = _latest_usage_slice(
        projection="id, deleted, _peerdb_is_deleted",
        scope=scope,
        start_param=None,
        end_param=None,
        project_scoped=False,
    )
    total_query = f"""
        SELECT count() AS total_runs
        FROM ({total_slice}) AS latest_usage
        WHERE {live}
    """

    period_projection = (
        "id, log_id, config, status, created_at, eval_trace_id, "
        "deleted, _peerdb_is_deleted"
    )
    period_slice = _latest_usage_slice(
        projection=period_projection,
        scope=scope,
        start_param="start_date",
        end_param="end_date",
    )

    # ``config`` has existed in two encodings: a JSON object and a JSON string
    # containing that object. Normalize once in SQL before extracting chart
    # values. Page rows still return the original config for lossless details.
    config_expr = (
        "if(isValidJSON(config) AND JSONType(config) = 'String', "
        "JSONExtractString(config), config)"
    )
    output_raw = f"JSONExtractRaw({config_expr}, 'output', 'output')"
    output_type = f"JSONType({output_raw})"
    output_text = f"lowerUTF8(JSONExtractString({config_expr}, 'output', 'output'))"
    output_label = (
        f"lowerUTF8(JSONExtractString({config_expr}, 'output', 'output', 'label'))"
    )
    score_expr = (
        "multiIf("
        f"{output_type} IN ('Int64', 'UInt64', 'Float64'), "
        f"toFloat64OrNull({output_raw}), "
        f"{output_type} = 'Object' AND "
        f"JSONHas({config_expr}, 'output', 'output', 'score'), "
        f"toNullable(JSONExtractFloat({config_expr}, 'output', 'output', 'score')), "
        f"{output_text} IN ('passed', 'pass'), toNullable(1.0), "
        f"{output_text} IN ('failed', 'fail'), toNullable(0.0), "
        "CAST(NULL, 'Nullable(Float64)'))"
    )
    duration_present = (
        f"JSONHas({config_expr}, 'duration') OR JSONHas({config_expr}, 'response_time')"
    )
    duration_expr = (
        f"if(JSONHas({config_expr}, 'duration'), "
        f"JSONExtractFloat({config_expr}, 'duration'), "
        f"JSONExtractFloat({config_expr}, 'response_time'))"
    )
    aggregate_pass_present = f"JSONHas({config_expr}, 'output', 'aggregate_pass')"
    aggregate_pass = f"JSONExtractBool({config_expr}, 'output', 'aggregate_pass')"
    pass_expr = (
        f"({aggregate_pass_present} AND {aggregate_pass} = 1) "
        f"OR {output_label} IN ('passed', 'pass') "
        f"OR {output_text} IN ('passed', 'pass')"
    )
    fail_expr = (
        f"({aggregate_pass_present} AND {aggregate_pass} = 0) "
        f"OR {output_label} IN ('failed', 'fail') "
        f"OR {output_text} IN ('failed', 'fail')"
    )
    chart_query = f"""
        SELECT
            toStartOfInterval(
                created_at,
                toIntervalMinute(%(bucket_minutes)s),
                'UTC'
            ) AS bucket,
            count() AS calls,
            sumKahanIf({duration_expr}, {duration_present}) AS duration_sum,
            countIf({duration_present}) AS duration_count,
            sumKahanIf({score_expr}, {score_expr} IS NOT NULL) AS score_sum,
            countIf({score_expr} IS NOT NULL) AS score_count,
            countIf({pass_expr}) AS pass_count,
            countIf({fail_expr}) AS fail_count,
            countIf(status = %(success_status)s) AS success_count,
            countIf(status = %(error_status)s) AS error_count
        FROM ({period_slice}) AS latest_usage
        WHERE {live}
        GROUP BY bucket
        ORDER BY bucket
    """

    def page_slice(
        *,
        projection: str = period_projection,
        extra_predicates: tuple[str, ...] = (),
    ) -> str:
        return _latest_usage_slice(
            projection=projection,
            scope=scope,
            start_param="page_window_start",
            end_param="page_window_end",
            extra_predicates=extra_predicates,
        )

    def page_count_query(*, extra_predicates: tuple[str, ...] = ()) -> str:
        return f"""
            SELECT count() AS page_window_count
            FROM ({page_slice(projection="id, created_at, eval_trace_id, deleted, _peerdb_is_deleted", extra_predicates=extra_predicates)}) AS latest_usage
            WHERE {live}
        """

    def page_split_count_query() -> str:
        return f"""
            SELECT
                countIf(created_at < %(page_window_midpoint)s)
                    AS older_count,
                countIf(created_at >= %(page_window_midpoint)s)
                    AS newer_count
            FROM ({page_slice(projection="id, created_at, eval_trace_id, deleted, _peerdb_is_deleted")}) AS latest_usage
            WHERE {live}
        """

    def page_rows_query(*, extra_predicates: tuple[str, ...] = ()) -> str:
        return f"""
            SELECT
                toString(log_id) AS log_id,
                config,
                status,
                created_at
            FROM ({page_slice(extra_predicates=extra_predicates)}) AS latest_usage
            WHERE {live}
            ORDER BY created_at DESC, id DESC
            LIMIT %(page_selection_limit)s
        """

    deadline_at = time.monotonic() + (READ_TIMEOUT_MS / 1000.0)

    def remaining_ms(operation: str, *, cap_ms: int = QUERY_TIMEOUT_MS) -> int:
        remaining = int((deadline_at - time.monotonic()) * 1000)
        if remaining <= 0:
            raise EvalUsageReadError(
                EvalUsageReadErrorCode.DEADLINE_EXCEEDED,
                operations=(operation,),
            )
        return min(cap_ms, remaining)

    def raise_typed(operation: str, exc: Exception) -> NoReturn:
        if isinstance(exc, EvalUsageReadError):
            raise exc
        if is_read_budget_error(exc):
            raise EvalUsageReadError(
                EvalUsageReadErrorCode.DEADLINE_EXCEEDED,
                operations=(operation,),
            ) from exc
        if is_clickhouse_query_error(exc):
            raise EvalUsageReadError(
                EvalUsageReadErrorCode.QUERY_FAILED,
                operations=(operation,),
            ) from exc
        raise exc

    worker_pool = ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="eval-usage-exact-ch",
    )

    def execute_read(
        operation: str,
        query: str,
        query_params: dict[str, Any],
        query_settings: dict[str, Any],
    ) -> list[tuple]:
        def read():
            client = get_clickhouse_client()
            rows, _columns, _elapsed = client.execute_read(
                query,
                query_params,
                timeout_ms=remaining_ms(operation),
                settings=query_settings,
            )
            return rows

        future = worker_pool.submit(read)
        try:
            return future.result(timeout=max(deadline_at - time.monotonic(), 0))
        except FutureTimeoutError as exc:
            future.cancel()
            raise EvalUsageReadError(
                EvalUsageReadErrorCode.DEADLINE_EXCEEDED,
                operations=(operation,),
            ) from exc

    def execute_typed(
        operation: str,
        query: str,
        query_params: dict[str, Any],
        query_settings: dict[str, Any],
    ) -> list[tuple]:
        try:
            return execute_read(operation, query, query_params, query_settings)
        except Exception as exc:
            raise_typed(operation, exc)

    try:
        total_rows = execute_typed(
            "total",
            total_query,
            params,
            {**_READ_SETTINGS, "max_result_rows": 1},
        )
        if len(total_rows) != 1:
            raise EvalUsageReadError(
                EvalUsageReadErrorCode.QUERY_FAILED,
                operations=("total",),
            )
        total_runs = int(total_rows[0][0] or 0)
        if total_runs == 0:
            return EvalUsageRead(
                total_runs=0,
                runs_period=0,
                success_count=0,
                error_count=0,
                chart=[],
                logs=[],
                completeness=EvalUsageReadCompleteness.COMPLETE,
                unavailable_fields=(),
            )

        chart_rows = execute_typed(
            "chart",
            chart_query,
            params,
            {
                **_READ_SETTINGS,
                "max_result_rows": 50_000,
                "max_result_bytes": 16 * 1024 * 1024,
                "result_overflow_mode": "throw",
            },
        )

        chart: list[EvalUsageChartBucket] = []
        runs_period = 0
        success_count = 0
        error_count = 0
        for row in chart_rows:
            calls = int(row[1] or 0)
            duration_sum = _finite_float_or_none(row[2])
            duration_count = int(row[3] or 0)
            score_sum = _finite_float_or_none(row[4])
            score_count = int(row[5] or 0)
            avg_duration = (
                duration_sum / duration_count
                if duration_count and duration_sum is not None
                else None
            )
            avg_score = (
                score_sum / score_count
                if score_count and score_sum is not None
                else None
            )
            chart.append(
                EvalUsageChartBucket(
                    bucket=row[0],
                    calls=calls,
                    avg_duration=avg_duration,
                    avg_score=avg_score,
                    pass_count=int(row[6] or 0),
                    fail_count=int(row[7] or 0),
                )
            )
            runs_period += calls
            success_count += int(row[8] or 0)
            error_count += int(row[9] or 0)

        def execute_page_count(
            query: str,
            query_params: dict[str, Any],
            *,
            expected_columns: int,
        ) -> tuple[int, ...]:
            rows = execute_typed(
                "page_seek",
                query,
                query_params,
                {**_READ_SETTINGS, "max_result_rows": 1},
            )
            if len(rows) != 1 or len(rows[0]) != expected_columns:
                raise EvalUsageReadError(
                    EvalUsageReadErrorCode.QUERY_FAILED,
                    operations=("page_seek",),
                )
            return tuple(int(value or 0) for value in rows[0])

        def execute_page_rows(
            *,
            start: datetime,
            end: datetime,
            limit: int,
            extra_predicates: tuple[str, ...] = (),
            extra_params: dict[str, Any] | None = None,
        ) -> list[tuple]:
            bounded_limit = max(0, int(limit))
            if bounded_limit == 0:
                return []
            query_params = {
                **params,
                "page_window_start": start,
                "page_window_end": end,
                "page_selection_limit": bounded_limit,
                **(extra_params or {}),
            }
            return execute_typed(
                "page",
                page_rows_query(extra_predicates=extra_predicates),
                query_params,
                {
                    **_READ_SETTINGS,
                    "max_result_rows": bounded_limit,
                    "max_result_bytes": 16 * 1024 * 1024,
                    "result_overflow_mode": "throw",
                },
            )

        period_start = params["start_date"]
        period_end = params["end_date"]
        page_skip = page * page_size
        page_rows: list[tuple] = []

        if runs_period > 0 and page_skip == 0:
            # Healthy path: newest page is one bounded statement over the
            # requested range.  There is no OFFSET and no preliminary count.
            page_rows = execute_page_rows(
                start=period_start,
                end=period_end,
                limit=page_size,
            )
        elif runs_period > 0:
            # The wire contract exposes page numbers, not cursors.  Resolve a
            # deep page to a small deterministic (created_at, id) leaf using
            # exact counts, then fetch at most _MAX_PAGE_SELECTION_ROWS plus
            # one page.  This avoids materializing page*page_size JSON configs
            # or using a deep ClickHouse OFFSET.  Each statement is exact and
            # unsampled; CH 25.3 does not provide one shared snapshot across
            # these statements, and this code deliberately makes no such
            # metadata claim.
            current_start = period_start
            current_end = period_end
            current_count: int | None = None
            local_skip = page_skip
            id_bounds: tuple[int, int] | None = None

            while current_count is None or current_count > _MAX_PAGE_SELECTION_ROWS:
                split = _split_page_window(current_start, current_end)
                if split is None:
                    count_params = {
                        **params,
                        "page_window_start": current_start,
                        "page_window_end": current_end,
                    }
                    (current_count,) = execute_page_count(
                        page_count_query(),
                        count_params,
                        expected_columns=1,
                    )
                    break

                (older_start, older_end), (newer_start, newer_end) = split
                split_params = {
                    **params,
                    "page_window_start": current_start,
                    "page_window_end": current_end,
                    "page_window_midpoint": newer_start,
                }
                older_count, newer_count = execute_page_count(
                    page_split_count_query(),
                    split_params,
                    expected_columns=2,
                )
                current_total = older_count + newer_count
                if local_skip >= current_total:
                    current_count = 0
                    break
                if current_total <= _MAX_PAGE_SELECTION_ROWS:
                    current_count = current_total
                    break
                if local_skip < newer_count:
                    current_start, current_end = newer_start, newer_end
                    current_count = newer_count
                else:
                    local_skip -= newer_count
                    current_start, current_end = older_start, older_end
                    current_count = older_count

            # A DateTime64(6) leaf can still contain more than the bounded
            # selection size when many rows share the same microsecond.  Seek
            # through the unique Int64 logical ID range instead of falling
            # back to an unbounded result or OFFSET.
            if current_count and current_count > _MAX_PAGE_SELECTION_ROWS:
                bounds_query = f"""
                    SELECT min(id), max(id), count()
                    FROM ({page_slice(projection="id, created_at, eval_trace_id, deleted, _peerdb_is_deleted")}) AS latest_usage
                    WHERE {live}
                """
                bounds_params = {
                    **params,
                    "page_window_start": current_start,
                    "page_window_end": current_end,
                }
                bound_rows = execute_typed(
                    "page_seek",
                    bounds_query,
                    bounds_params,
                    {**_READ_SETTINGS, "max_result_rows": 1},
                )
                if len(bound_rows) != 1 or len(bound_rows[0]) != 3:
                    raise EvalUsageReadError(
                        EvalUsageReadErrorCode.QUERY_FAILED,
                        operations=("page_seek",),
                    )
                current_count = int(bound_rows[0][2] or 0)
                if current_count == 0 or local_skip >= current_count:
                    current_count = 0
                    id_low = 0
                    id_high = 0
                elif bound_rows[0][0] is None or bound_rows[0][1] is None:
                    raise EvalUsageReadError(
                        EvalUsageReadErrorCode.QUERY_FAILED,
                        operations=("page_seek",),
                    )
                else:
                    id_low = int(bound_rows[0][0])
                    id_high = int(bound_rows[0][1])
                while current_count > _MAX_PAGE_SELECTION_ROWS and id_low < id_high:
                    id_midpoint = id_low + (id_high - id_low) // 2
                    id_predicates = (
                        "id >= %(page_id_low)s",
                        "id <= %(page_id_high)s",
                    )
                    id_split_query = f"""
                        SELECT
                            countIf(id <= %(page_id_midpoint)s) AS lower_count,
                            countIf(id > %(page_id_midpoint)s) AS upper_count
                        FROM (
                            {page_slice(projection="id, created_at, eval_trace_id, deleted, _peerdb_is_deleted", extra_predicates=id_predicates)}
                        ) AS latest_usage
                        WHERE {live}
                    """
                    id_split_params = {
                        **params,
                        "page_window_start": current_start,
                        "page_window_end": current_end,
                        "page_id_low": id_low,
                        "page_id_high": id_high,
                        "page_id_midpoint": id_midpoint,
                    }
                    lower_count, upper_count = execute_page_count(
                        id_split_query,
                        id_split_params,
                        expected_columns=2,
                    )
                    if local_skip < upper_count:
                        id_low = id_midpoint + 1
                        current_count = upper_count
                    else:
                        local_skip -= upper_count
                        id_high = id_midpoint
                        current_count = lower_count
                id_bounds = (id_low, id_high)

            if current_count and local_skip < current_count:
                leaf_predicates: tuple[str, ...] = ()
                leaf_params: dict[str, Any] = {}
                if id_bounds is not None:
                    leaf_predicates = (
                        "id >= %(page_id_low)s",
                        "id <= %(page_id_high)s",
                    )
                    leaf_params = {
                        "page_id_low": id_bounds[0],
                        "page_id_high": id_bounds[1],
                    }
                leaf_rows = execute_page_rows(
                    start=current_start,
                    end=current_end,
                    limit=local_skip + page_size,
                    extra_predicates=leaf_predicates,
                    extra_params=leaf_params,
                )
                if len(leaf_rows) < local_skip:
                    raise EvalUsageReadError(
                        EvalUsageReadErrorCode.QUERY_FAILED,
                        operations=("page_seek",),
                    )
                page_rows = leaf_rows[local_skip : local_skip + page_size]

                remaining = page_size - len(page_rows)
                if remaining:
                    if id_bounds is None:
                        older_predicates = ("created_at < %(page_seek_created_at)s",)
                        older_params = {
                            "page_seek_created_at": current_start,
                        }
                    else:
                        older_predicates = (
                            "(created_at < %(page_seek_created_at)s OR "
                            "(created_at = %(page_seek_created_at)s "
                            "AND id < %(page_seek_id)s))",
                        )
                        older_params = {
                            "page_seek_created_at": current_start,
                            "page_seek_id": id_bounds[0],
                        }
                    page_rows.extend(
                        execute_page_rows(
                            start=period_start,
                            end=current_end,
                            limit=remaining,
                            extra_predicates=older_predicates,
                            extra_params=older_params,
                        )
                    )

        logs = [
            EvalUsageLog(
                log_id=str(row[0]),
                config=_decode_config(row[1]),
                status=str(row[2] or ""),
                created_at=row[3],
            )
            for row in page_rows
        ]
        return EvalUsageRead(
            total_runs=total_runs,
            runs_period=runs_period,
            success_count=success_count,
            error_count=error_count,
            chart=chart,
            logs=logs,
            completeness=EvalUsageReadCompleteness.COMPLETE,
            unavailable_fields=(),
        )
    except Exception as exc:
        if isinstance(exc, EvalUsageReadError):
            raise
        raise_typed("eval_usage", exc)
    finally:
        worker_pool.shutdown(wait=False, cancel_futures=True)


__all__ = [
    "EvalUsageChartBucket",
    "EvalUsageLog",
    "EvalUsageRead",
    "EvalUsageReadCompleteness",
    "EvalUsageReadError",
    "EvalUsageReadErrorCode",
    "read_eval_usage",
]
