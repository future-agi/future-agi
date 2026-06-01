import json

import structlog

from tracer.utils.filters import FilterEngine

logger = structlog.get_logger(__name__)


def _try_session_navigation_ch(request, project_id, current_session_id):
    """Compute session navigation via ClickHouse.

    Returns ``(next_session_id, previous_session_id)`` on success, or
    ``None`` if ClickHouse is disabled or the query failed.
    """
    from tracer.services.clickhouse.query_builders.session_analytics import (
        SessionAnalyticsQueryBuilder,
    )
    from tracer.services.clickhouse.query_service import (
        AnalyticsQueryService,
        QueryType,
    )

    try:
        service = AnalyticsQueryService()
        if not service.should_use_clickhouse(QueryType.SESSION_ANALYTICS):
            return None

        query_data = {
            "filters": request.query_params.get("filters", "[]"),
            "sort_params": request.query_params.get("sort_params", "[]")
            or request.query_params.get("sortParams", "[]"),
        }
        if query_data["filters"]:
            query_data["filters"] = json.loads(query_data["filters"])
        if query_data["sort_params"]:
            query_data["sort_params"] = json.loads(query_data["sort_params"])

        filters = query_data.get("filters", [])
        sort_params = query_data.get("sort_params", [])

        builder = SessionAnalyticsQueryBuilder(project_id=str(project_id))

        # Get session navigation data
        nav_query, nav_params = builder.build_session_navigation_query()

        user_id = request.query_params.get("user_id") or request.query_params.get(
            "userId"
        )
        if user_id:
            # Add user filter to the navigation query. Anchor must match the
            # exact line emitted by ``build_session_navigation_query``; keep
            # in sync if that builder is changed.
            nav_params["user_id"] = user_id
            nav_query = nav_query.replace(
                "AND trace_session_id IS NOT NULL",
                "AND trace_session_id IS NOT NULL AND end_user_id = %(user_id)s",
            )

        nav_result = service.execute_ch_query(nav_query, nav_params)

        if not nav_result.data:
            return None, None

        session_ids = [str(row["trace_session_id"]) for row in nav_result.data]

        # Get first/last messages for these sessions
        first_q, last_q, msg_params = builder.build_first_last_message_query(
            session_ids
        )
        if user_id:
            msg_params["user_id"] = user_id

        first_result = service.execute_ch_query(first_q, msg_params)
        last_result = service.execute_ch_query(last_q, msg_params)

        first_msg_map = {
            str(r["trace_session_id"]): r.get("input", "") for r in first_result.data
        }
        last_msg_map = {
            str(r["trace_session_id"]): r.get("input", "") for r in last_result.data
        }

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
                    "first_message": first_msg_map.get(sid, ""),
                    "last_message": last_msg_map.get(sid, ""),
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


def get_session_navigation(request, project_id, current_session_id):
    """Return ``(next_session_id, previous_session_id)`` for the detail UI.

    Returns ``(None, None)`` when ClickHouse is unavailable; callers
    render the page without prev/next arrows in that case.
    """
    ch_result = _try_session_navigation_ch(request, project_id, current_session_id)
    if ch_result is None:
        return None, None
    return ch_result
