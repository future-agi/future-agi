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


def _list_context_navigation(
    request, current_project_id, current_session_id, context, service, deadline
):
    """Opt-in list adjacency; invalid/unsupported context never uses legacy rows."""
    from tracer.models.project import ProjectSourceChoices
    from tracer.serializers.trace_session import SessionNavigationContextSerializer
    from tracer.services.clickhouse.list_cursor import cursor_scope_for_request
    from tracer.services.clickhouse.v2.query_builders.session_list import (
        SessionListQueryBuilderV2,
    )
    from tracer.views.trace_session import (
        _bounded_session_list_postgres_reads,
        _project_queryset_for_request,
    )

    serializer = SessionNavigationContextSerializer(data=context)
    serializer.is_valid(raise_exception=True)
    context = serializer.validated_data
    scope = cursor_scope_for_request(request, project_ids=[])
    if (
        not scope["organization_id"]
        or str(context["workspace_id"]) != scope["workspace_id"]
    ):
        raise ValueError("Session navigation workspace changed")
    project_id = str(context["project_id"]) if context["project_id"] else None
    with _bounded_session_list_postgres_reads(deadline):
        projects = _project_queryset_for_request(request).exclude(
            source=ProjectSourceChoices.SIMULATOR.value
        )
        projects = (
            projects.filter(id=project_id)
            if project_id
            else projects.filter(trace_type__in=("observe", "experiment"))
        )
        project_ids = [str(value) for value in projects.values_list("id", flat=True)]
    if str(current_project_id) not in project_ids or (
        project_id and project_id != str(current_project_id)
    ):
        return None

    builder = SessionListQueryBuilderV2(
        project_id=project_id,
        project_ids=None if project_id else project_ids,
        filters=context["filters"],
        sort_params=context["sort_params"],
    )
    if not any(
        (
            item.get("column_id") in {"created_at", "start_time"}
            and item.get("filter_config", {}).get("filter_op") == "between"
        )
        for item in builder._native_session_filters()
    ):
        raise ValueError("Session navigation requires the originating date range")
    # Validate the originating sort before even the canonical-ID lookup.
    builder._candidate_order_clause()
    return _relational_list_context_navigation(
        request, current_session_id, context, service, deadline, project_ids
    )


def _relational_list_context_navigation(
    request,
    current_session_id,
    context,
    service,
    deadline,
    project_ids,
):
    import json

    from tracer.serializers.trace_session import TraceSessionListQuerySerializer
    from tracer.services.clickhouse.list_cursor import (
        list_cursor_boundary_fingerprint,
        normalize_filter_conjunction,
    )
    from tracer.services.filter_principal_context import (
        bind_request_my_annotations_principal,
    )
    from tracer.views.trace_session import (
        SessionPageSelection,
        TraceSessionView,
        _bounded_session_list_postgres_reads,
        _resolve_session_ids_to_canonical,
        _session_page_depth_exceeded,
        _session_read_settings,
    )

    project_id = str(context["project_id"]) if context["project_id"] else None
    public_query = {
        "filters": json.dumps(context["filters"]),
        "sort_params": json.dumps(context["sort_params"]),
        "cursor_mode": context["cursor_mode"],
    }
    if project_id:
        public_query["project_id"] = project_id
    if context.get("user_id"):
        public_query["user_id"] = context["user_id"]

    def validate_query(raw):
        serializer = TraceSessionListQuerySerializer(data=raw)
        serializer.is_valid(raise_exception=True)
        query = serializer.validated_data
        # Position is a hint, never a second independent filter/scope payload.
        if (
            str(query.get("project_id") or "") != str(project_id or "")
            or query.get("user_id") != context.get("user_id")
            or query["cursor_mode"] != context["cursor_mode"]
            or query["sort_params"] != context["sort_params"]
            or normalize_filter_conjunction(query["filters"])
            != normalize_filter_conjunction(context["filters"])
            or "bookmarked" in query
        ):
            raise ValueError("Session navigation page context changed")
        query["filters"] = bind_request_my_annotations_principal(
            request, query["filters"]
        )
        if _session_page_depth_exceeded(query):
            raise ValueError("Session navigation page depth exceeded")
        return query

    origin = validate_query(context.get("page_request", public_query))
    previous = context.get("previous_page_request")
    previous = validate_query(previous) if previous is not None else None
    view = TraceSessionView()
    current_session_id = _resolve_session_ids_to_canonical(
        service,
        [str(current_session_id)],
        deadline=deadline,
        settings=_session_read_settings(max_result_rows=2),
    ).get(str(current_session_id), str(current_session_id))

    def read(query):
        deadline.remaining_ms()
        with _bounded_session_list_postgres_reads(deadline):
            page = view._select_session_page(
                request,
                project_id,
                None,
                service,
                query,
                org_project_ids=None if project_id else project_ids,
                read_deadline=deadline,
            )
        if not isinstance(page, SessionPageSelection):
            raise ValueError("Session navigation page unavailable")
        if page.bounded_page is not None and not page.bounded_page.complete:
            raise ValueError("Session navigation page is incomplete")
        return page

    # All actual order, scope, project collisions, latest/remap membership and
    # finite relational history are enforced by the same list reader.
    page = read(origin)
    newer = None
    if origin.get("cursor"):
        if previous is not None:
            prior = read(previous)
            prior_cursor = prior.cursor(prior.candidate_total_count)[1]
            if not prior_cursor or list_cursor_boundary_fingerprint(
                prior_cursor
            ) != list_cursor_boundary_fingerprint(origin["cursor"]):
                raise ValueError("Session navigation previous page changed")
            if not prior.page_candidates:
                raise ValueError("Session navigation predecessor lacks a row boundary")
            newer = str(prior.page_candidates[-1]["session_id"])
        else:
            # Never claim previous=None merely because a client omitted the
            # predecessor. Reconstruct it through the normal exact forward path.
            origin = {k: v for k, v in origin.items() if k != "cursor"}
            origin["page_number"] = 0
            page = read(origin)
    elif origin["page_number"]:
        prior = read(dict(origin, page_number=origin["page_number"] - 1))
        newer = (
            str(prior.page_candidates[-1]["session_id"])
            if prior.page_candidates
            else None
        )

    query, found, seen_cursors = origin, False, set()
    while True:
        for row in page.page_candidates:
            identity = str(row["session_id"])
            if found:
                return identity, newer
            if identity == str(current_session_id):
                found = True
            else:
                newer = identity
        _, token, has_more = page.cursor(page.candidate_total_count)
        if not page.cursor_enabled:
            has_more = (
                page.bounded_page.has_more
                if page.bounded_page is not None
                else (page.builder.page_number + 1) * page.builder.page_size
                < (page.candidate_total_count or 0)
            )
        if not has_more:
            if found:
                return None, newer
            # The target may now lie before the originating page (including
            # repeated Previous clicks). Re-read from the head once, not a
            # guessed reverse max-root seed ordering.
            if origin.get("cursor") or origin["page_number"]:
                origin = {k: v for k, v in origin.items() if k != "cursor"}
                origin["page_number"] = 0
                query, page, newer = origin, read(origin), None
                seen_cursors.clear()
                continue
            return None
        if token:
            boundary = list_cursor_boundary_fingerprint(token)
            if boundary in seen_cursors:
                raise ValueError("Session navigation cursor did not progress")
            seen_cursors.add(boundary)
            query = dict(query, cursor=token, page_number=0)
        else:
            query = dict(query, page_number=query["page_number"] + 1)
        page = read(query)


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
        if "navigation_context" in query_data:
            context = query_data["navigation_context"]
            if query_data.get("user_id") and (
                not isinstance(context, dict)
                or context.get("user_id") != query_data["user_id"]
            ):
                raise ValueError("Session navigation user context changed")
            return _list_context_navigation(
                request, project_id, current_session_id, context, service, deadline
            )
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
