import structlog
from django.db import models
from django.db.models import (
    Count,
    F,
    Max,
    Min,
    OuterRef,
    Subquery,
    Sum,
)
from django.db.models.functions import Coalesce

from tracer.models.observation_span import EndUser, ObservationSpan
from tracer.models.project import Project
from tracer.models.trace_session import TraceSession
from tracer.utils.filters import FilterEngine

logger = structlog.get_logger(__name__)


def _get_navigation_query_data(request, query_data=None):
    if query_data is not None:
        return query_data

    from tracer.serializers.trace_session import TraceSessionRetrieveQuerySerializer

    serializer = TraceSessionRetrieveQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _try_session_navigation_ch(request, project_id, current_session_id, query_data=None):
    """Attempt to compute session navigation using ClickHouse.

    Returns (next_session_id, previous_session_id) on success,
    or None on failure (caller should fall back to PG).
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

        query_data = _get_navigation_query_data(request, query_data)
        filters = query_data.get("filters", [])
        sort_params = query_data.get("sort_params", [])

        builder = SessionAnalyticsQueryBuilder(project_id=str(project_id))

        # Get session navigation data
        nav_query, nav_params = builder.build_session_navigation_query()

        user_id = query_data.get("user_id")
        if user_id:
            # Add user filter to the navigation query
            nav_params["user_id"] = user_id
            nav_query = nav_query.replace(
                "AND trace_session_id != ''",
                "AND trace_session_id != '' AND end_user_id = %(user_id)s",
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
        logger.warning(
            "ch_session_navigation_failed, falling back to postgres",
            project_id=str(project_id),
            exc_info=True,
        )
        return None


def get_session_navigation(request, project_id, current_session_id, query_data=None):
    """
    Get previous and next session IDs based on the same ordering as list_sessions.

    Args:
        request: The request object
        project_id: The project ID
        current_session_id: The current session ID

    Returns:
        tuple: (next_session_id, previous_session_id)
    """
    # Try ClickHouse first
    query_data = _get_navigation_query_data(request, query_data)

    ch_result = _try_session_navigation_ch(
        request, project_id, current_session_id, query_data
    )
    if ch_result is not None:
        return ch_result

    filters = query_data.get("filters", [])
    sort_params = query_data.get("sort_params", [])

    user_id = query_data.get("user_id")
    end_user_filter = {}
    if user_id:
        try:
            project = Project.objects.get(id=project_id)
            end_user = EndUser.objects.get(
                user_id=user_id,
                organization=getattr(request, "organization", None)
                or request.user.organization,
                deleted=False,
                project=project,
            )
            end_user_filter["end_user"] = end_user
        except (EndUser.DoesNotExist, Project.DoesNotExist):
            pass

    trace_sessions = (
        TraceSession.objects.filter(project_id=project_id)
        .values("id", "created_at")
        .order_by("-created_at")
    )

    session_ids = [session["id"] for session in trace_sessions]

    spans_data = (
        ObservationSpan.objects.filter(
            trace__session_id__in=session_ids, **end_user_filter
        )
        .values("trace__session_id")
        .annotate(
            start_time=Min("start_time"),
            end_time=Max("end_time"),
            # Get first message chronologically (earliest start_time)
            first_message=Subquery(
                ObservationSpan.objects.filter(
                    trace__session_id=OuterRef("trace__session_id"), **end_user_filter
                )
                .order_by("start_time")
                .values("input")[:1]
            ),
            # Get last message chronologically (latest start_time)
            last_message=Subquery(
                ObservationSpan.objects.filter(
                    trace__session_id=OuterRef("trace__session_id"), **end_user_filter
                )
                .order_by("-start_time")
                .values("input")[:1]
            ),
            total_cost=Coalesce(
                Sum(
                    F("prompt_tokens") * 0.00000015
                    + F("completion_tokens") * 0.0000006,
                    output_field=models.FloatField(),
                ),
                0.0,
            ),
            total_tokens=Coalesce(
                Sum(
                    F("total_tokens"),
                    output_field=models.IntegerField(),
                ),
                0,
            ),
            traces_count=Count("trace_id", distinct=True),
            user_id=Subquery(
                EndUser.objects.filter(
                    id=OuterRef("end_user_id"),
                    organization=getattr(request, "organization", None)
                    or request.user.organization,
                ).values("user_id")[:1]
            ),
        )
        .order_by("start_time")
    )

    session_data_map = {
        str(span["trace__session_id"]): {
            "start_time": span["start_time"],
            "end_time": span["end_time"],
            "first_message": span["first_message"],
            "last_message": span["last_message"],
            "total_cost": span["total_cost"],
            "traces_count": span["traces_count"],
            "user_id": span.get("user_id", None),
            "total_tokens": span["total_tokens"],
        }
        for span in spans_data
    }

    result = []
    for session in trace_sessions:
        session_id = str(session["id"])
        span_data = session_data_map.get(session_id, {})

        if not span_data:
            continue

        start_time = span_data["start_time"]
        end_time = span_data["end_time"]

        parsed_data = {
            "total_cost": span_data["total_cost"] or 0,
            "total_tokens": span_data["total_tokens"],
            "duration": (
                (end_time - start_time).total_seconds()
                if end_time and start_time
                else 0
            ),
            "total_traces_count": span_data["traces_count"],
            "start_time": start_time,
            "end_time": end_time,
            "first_message": span_data["first_message"],
            "last_message": span_data["last_message"],
            "session_id": session_id,
            "created_at": session["created_at"],
            "user_id": span_data.get("user_id", None),
        }
        result.append(parsed_data)

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
