import structlog

from tracer.utils.filters import FilterEngine

logger = structlog.get_logger(__name__)


def _get_navigation_query_data(request, query_data=None):
    if query_data is not None:
        return query_data

    from tracer.serializers.trace_session import TraceSessionRetrieveQuerySerializer

    serializer = TraceSessionRetrieveQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _try_session_navigation_ch(
    request,
    project_id,
    current_session_id,
    query_data=None,
    *,
    deadline=None,
):
    """Attempt to compute session navigation using ClickHouse.

    Returns ``(next_session_id, previous_session_id)`` on success, or
    ``None`` if ClickHouse is disabled or the query failed.
    """
    from tracer.services.clickhouse.read_budget import ReadDeadline
    from tracer.services.clickhouse.v2.query_builders.session_analytics import (
        SessionAnalyticsQueryBuilderV2,
    )
    from tracer.services.clickhouse.v2.query_service import V2AnalyticsQueryService

    try:
        service = V2AnalyticsQueryService()
        deadline = deadline or ReadDeadline.start(3000)
        query_data = _get_navigation_query_data(request, query_data)
        filters = query_data.get("filters", [])
        sort_params = query_data.get("sort_params", [])

        end_user_ids = []
        user_id = query_data.get("user_id")
        if user_id:
            from tracer.services.clickhouse.v2.end_user_dict_reader import (
                resolve_end_user_ids_by_user_id,
            )

            end_user_ids = resolve_end_user_ids_by_user_id(
                user_id,
                project_id=project_id,
                timeout_ms=deadline.remaining_ms(),
            )
            if not end_user_ids:
                return None

        builder = SessionAnalyticsQueryBuilderV2(
            project_id=str(project_id),
            filters=filters,
            end_user_ids=end_user_ids,
        )

        # Latest-live session metrics and first/last root messages are returned
        # together, avoiding the legacy raw-RMT scan and two extra round trips.
        nav_query, nav_params = builder.build_session_navigation_query()

        nav_result = service.execute_ch_query(
            nav_query,
            nav_params,
            timeout_ms=deadline.remaining_ms(),
        )

        if not nav_result.data:
            return None

        # Build result list matching PG format
        result = []
        for row in nav_result.data:
            sid = str(row["trace_session_id"])
            started_at = row.get("started_at")
            ended_at = row.get("ended_at")
            duration = 0
            if started_at and ended_at:
                duration = (ended_at - started_at).total_seconds()

            result.append(
                {
                    "total_cost": float(row.get("total_cost") or 0),
                    "total_tokens": int(row.get("total_tokens") or 0),
                    "duration": duration,
                    "total_traces_count": int(row.get("trace_count") or 0),
                    "start_time": started_at,
                    "end_time": ended_at,
                    "first_message": row.get("first_message", ""),
                    "last_message": row.get("last_message", ""),
                    "session_id": sid,
                    "created_at": started_at,
                    "user_id": None,
                }
            )

        # Apply filters and sorting
        if filters:
            filter_engine = FilterEngine(result)
            result = filter_engine.apply_filters(filters)

        if sort_params:
            for sort_param in reversed(sort_params):
                sort_key = sort_param.get("column_id")
                sort_direction = sort_param.get("direction", "asc")
                reverse = sort_direction == "desc"
                result.sort(
                    key=lambda x: (x.get(sort_key) is None, x.get(sort_key, 0)),
                    reverse=reverse,
                )

        # Find current session and return navigation
        current_index = None
        for i, item in enumerate(result):
            if item["session_id"] == str(current_session_id):
                current_index = i
                break

        next_session_id = None
        previous_session_id = None

        if current_index is not None:
            if current_index > 0:
                previous_session_id = result[current_index - 1]["session_id"]
            if current_index < len(result) - 1:
                next_session_id = result[current_index + 1]["session_id"]

        return next_session_id, previous_session_id

    except Exception:
        logger.exception(
            "ch_session_navigation_failed",
            project_id=str(project_id),
        )
        return None


def get_session_navigation(
    request,
    project_id,
    current_session_id,
    query_data=None,
    *,
    deadline=None,
):
    """
    Get previous and next session IDs based on the same ordering as list_sessions.

    Args:
        request: The request object
        project_id: The project ID
        current_session_id: The current session ID

    Returns ``(None, None)`` when ClickHouse is unavailable; callers
    render the page without prev/next arrows in that case.
    """
    if deadline is None:
        ch_result = _try_session_navigation_ch(
            request,
            project_id,
            current_session_id,
            query_data,
        )
    else:
        ch_result = _try_session_navigation_ch(
            request,
            project_id,
            current_session_id,
            query_data,
            deadline=deadline,
        )
    if ch_result is None:
        return None, None
    return ch_result
