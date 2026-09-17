"""Direct-write session analytics routing and latest-row contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest import mock

import pytest

from tracer.services.clickhouse.v2.query_builders.session_analytics import (
    SessionAnalyticsQueryBuilderV2,
)

pytestmark = pytest.mark.unit

PROJECT_ID = "11111111-1111-1111-1111-111111111111"
SESSION_ID = "22222222-2222-2222-2222-222222222222"
USER_ID = "33333333-3333-3333-3333-333333333333"


def _assert_latest_direct_sql(query: str) -> None:
    assert "FROM spans" in query
    assert "argMax(is_deleted, _version)" in query
    assert "latest_is_deleted = 0" in query
    assert "_peerdb_" not in query
    assert "tracer_observation_span" not in query


def test_session_metrics_replays_latest_rows_and_remaps_session_id():
    query, params = SessionAnalyticsQueryBuilderV2(
        project_id=PROJECT_ID
    ).build_session_metrics_query([SESSION_ID])

    _assert_latest_direct_sql(query)
    assert "trace_session_id_remap" in query
    assert "uniqExact(trace_id) AS trace_count" in query
    assert params["session_ids"] == (SESSION_ID,)


def test_user_stats_replays_latest_rows_and_both_identity_remaps():
    query, params = SessionAnalyticsQueryBuilderV2(
        project_id=PROJECT_ID
    ).build_user_stats_query(USER_ID)

    _assert_latest_direct_sql(query)
    assert "end_user_id_remap" in query
    assert "trace_session_id_remap" in query
    assert params["user_id"] == USER_ID


def test_navigation_is_time_bounded_and_returns_messages_in_same_query():
    filters = [
        {
            "column_id": "created_at",
            "filter_config": {
                "filter_type": "datetime",
                "filter_op": "between",
                "filter_value": [
                    "2026-07-01T00:00:00Z",
                    "2026-08-01T00:00:00Z",
                ],
            },
        }
    ]
    query, params = SessionAnalyticsQueryBuilderV2(
        project_id=PROJECT_ID,
        filters=filters,
        end_user_ids=[USER_ID],
    ).build_session_navigation_query()

    _assert_latest_direct_sql(query)
    assert "argMinIf(input, start_time" in query
    assert "argMaxIf(input, start_time" in query
    assert "IN %(end_user_ids)s" in query
    assert params["end_user_ids"] == (USER_ID,)
    assert params["window_start"] == datetime(2026, 7, 1)
    assert params["window_end"] == datetime(2026, 8, 1)


def test_navigation_helper_uses_one_v2_query_and_no_legacy_builder():
    from tracer.utils.session import _try_session_navigation_ch

    current = SESSION_ID
    adjacent = "44444444-4444-4444-4444-444444444444"
    now = datetime.now(UTC)
    rows = [
        {
            "trace_session_id": current,
            "started_at": now,
            "ended_at": now,
            "trace_count": 1,
            "total_tokens": 2,
            "total_cost": 0.1,
            "first_message": "first",
            "last_message": "last",
        },
        {
            "trace_session_id": adjacent,
            "started_at": now,
            "ended_at": now,
            "trace_count": 1,
            "total_tokens": 2,
            "total_cost": 0.1,
            "first_message": "next-first",
            "last_message": "next-last",
        },
    ]
    request = SimpleNamespace(query_params={})
    service = mock.MagicMock()
    service.execute_ch_query.return_value = SimpleNamespace(data=rows)

    with mock.patch(
        "tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService",
        return_value=service,
    ):
        result = _try_session_navigation_ch(
            request,
            PROJECT_ID,
            current,
            query_data={"filters": [], "sort_params": []},
        )

    assert result == (adjacent, None)
    service.execute_ch_query.assert_called_once()


WORKSPACE_ID = "55555555-5555-5555-5555-555555555555"
OTHER_PROJECT_ID = "66666666-6666-6666-6666-666666666666"


def _navigation_context(**changes):
    return {
        "project_id": None,
        "workspace_id": WORKSPACE_ID,
        "cursor_mode": True,
        "filters": [{"column_id": "created_at", "filter_config": {
            "filter_type": "datetime", "filter_op": "between",
            "filter_value": ["2026-07-01T00:00:00Z", "2026-08-01T00:00:00Z"],
        }}],
        "sort_params": [],
        **changes,
    }


@pytest.fixture
def navigation_context_call():
    from tracer.utils.session import get_session_navigation

    org = SimpleNamespace(id=PROJECT_ID, pk=PROJECT_ID)
    request = SimpleNamespace(query_params={}, organization=org,
        workspace=SimpleNamespace(pk=WORKSPACE_ID), user=SimpleNamespace(organization=org))
    service = mock.MagicMock()
    service.execute_ch_query.return_value = SimpleNamespace(data=[
        {"session_id": session_id, "project_id": PROJECT_ID,
         "start_time": datetime(2026, 7, 3 - index), "session_start": datetime(2026, 7, 3 - index),
         "max_project_count": 1, "project_count": 1,
         "total_count": 2, "remaining_count": 2}
        for index, session_id in enumerate((SESSION_ID, OTHER_PROJECT_ID))
    ])
    projects = mock.MagicMock()
    projects.filter.return_value = projects
    projects.exclude.return_value = projects
    projects.values_list.return_value = [PROJECT_ID, OTHER_PROJECT_ID]
    with mock.patch("tracer.services.clickhouse.v2.query_service.V2AnalyticsQueryService", return_value=service), \
         mock.patch("tracer.views.trace_session._project_queryset_for_request", return_value=projects), \
         mock.patch("tracer.views.trace_session._bounded_session_list_postgres_reads"),          mock.patch("tracer.views.trace_session.EndUser.objects.filter") as display_users, \
         mock.patch("tracer.services.clickhouse.v2.end_user_dict_reader.resolve_end_user_ids_by_user_id", return_value=[USER_ID]) as users, \
         mock.patch.object(SessionAnalyticsQueryBuilderV2, "build_session_navigation_query", side_effect=AssertionError("context cannot use legacy navigation")) as legacy:
        display_users.return_value.filter.return_value = display_users.return_value
        display_users.return_value.values.return_value.first.return_value = None
        def call(context, current=SESSION_ID):
            result = get_session_navigation(request, PROJECT_ID, current,
                query_data={"navigation_context": context})
            legacy.assert_not_called()
            return result
        yield call, service, projects, users


def test_context_navigation_preserves_cross_project_user_scope(navigation_context_call):
    call, service, projects, users = navigation_context_call
    assert call(_navigation_context(user_id="public-user")) == (OTHER_PROJECT_ID, None)
    query, params = service.execute_ch_query.call_args.args
    assert set(params["project_ids"]) == {PROJECT_ID, OTHER_PROJECT_ID}
    assert "FROM sessions" in query and "LIMIT %(limit)s" in query
    assert "ORDER BY session_start DESC, session_id DESC" in query
    assert "trace_session_id_remap" in query and "end_user_id_remap" in query
    assert "toStartOfHour(start_time)" in query and "_version" in query
    assert service.execute_ch_query.call_args_list[0].args[1]["ids"] == (SESSION_ID,)
    assert params["start_date"] == datetime(2026, 7, 1)
    assert params["end_date"] == datetime(2026, 8, 1)
    assert users.call_args.kwargs["project_id"] is None
    assert users.call_args.kwargs["organization_id"] == PROJECT_ID
    assert not any("id" in item.kwargs for item in projects.filter.call_args_list)


@pytest.mark.parametrize("changes", [
    {"workspace_id": OTHER_PROJECT_ID}, {"workspace_id": None},
    {"filters": []}, {"filters": "bad"}, {"extra": True},
    {"project_id": USER_ID}, {"sort_params": [{"column_id": "unknown", "direction": "asc"}]},
])
def test_invalid_context_never_falls_back_or_queries(navigation_context_call, changes):
    call, service, _, _ = navigation_context_call
    assert call(_navigation_context(**changes)) == (None, None)
    service.execute_ch_query.assert_not_called()


@pytest.mark.parametrize("context", [None, {}, "not-json"])
def test_present_invalid_context_is_not_legacy(navigation_context_call, context):
    call, service, _, _ = navigation_context_call
    assert call(context) == (None, None)
    service.execute_ch_query.assert_not_called()


def test_context_project_and_custom_sort(navigation_context_call):
    call, service, projects, _ = navigation_context_call
    projects.values_list.return_value = [PROJECT_ID]
    assert call(_navigation_context(project_id=PROJECT_ID, cursor_mode=False,
        sort_params=[{"column_id": "total_tokens", "direction": "asc"}])) == (OTHER_PROJECT_ID, None)
    query, params = service.execute_ch_query.call_args.args
    assert params["project_id"] == PROJECT_ID
    assert "ORDER BY total_tokens ASC, session_id ASC" in query
    assert "sum(total_tokens) AS total_tokens" in query


def test_context_cannot_pin_a_different_authorized_project(navigation_context_call):
    call, service, projects, _ = navigation_context_call
    projects.values_list.return_value = [OTHER_PROJECT_ID]
    assert call(_navigation_context(project_id=OTHER_PROJECT_ID)) == (None, None)
    service.execute_ch_query.assert_not_called()


def test_context_scalar_uses_all_span_classifier_and_bounded_uuid_string_order(navigation_context_call):
    call, service, _, _ = navigation_context_call
    context = _navigation_context()
    context["filters"].append({"column_id": "company_id", "filter_config": {
        "col_type": "SPAN_ATTRIBUTE", "filter_type": "text",
        "filter_op": "not_in", "filter_value": ["blocked"],
    }})
    assert call(context) == (OTHER_PROJECT_ID, None)
    query, _ = next(call.args for call in service.execute_ch_query.call_args_list
                    if "resolved_candidate_scalar_spans" in call.args[0])
    assert "resolved_candidate_scalar_spans" in query
    assert "GROUP BY project_id, session_id" in query
    assert "session_start AS start_time" in query
    assert "ORDER BY start_time DESC, toString(session_id) DESC" in query


@pytest.mark.parametrize("rows", [[], [{"max_project_count": 2,
    "next_session_id": OTHER_PROJECT_ID, "previous_session_id": None}]])
def test_context_missing_current_or_project_collision_has_no_neighbors(navigation_context_call, rows):
    call, service, _, _ = navigation_context_call
    service.execute_ch_query.return_value = SimpleNamespace(data=rows)
    assert call(_navigation_context()) == (None, None)


def test_retrieve_serializer_accepts_json_context_without_stripping_filter_provenance():
    import json

    from django.http import QueryDict

    from tracer.serializers.trace_session import TraceSessionRetrieveQuerySerializer
    context = _navigation_context()
    data = QueryDict(mutable=True)
    data["navigation_context"] = json.dumps(context)
    serializer = TraceSessionRetrieveQuerySerializer(data=data)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["navigation_context"] == context


def test_context_user_leaves_remain_and_and_custom_user_id_is_not_resolved(navigation_context_call):
    call, service, _, users = navigation_context_call
    context = _navigation_context(user_id="public-user")
    for value, op in [("second-user", "in"), ("excluded-user", "not_in")]:
        context["filters"].append({"column_id": "user_id", "filter_config": {
            "filter_type": "text", "filter_op": op, "filter_value": [value],
        }})
    context["filters"].append({"column_id": "user_id", "filter_config": {
        "col_type": "SPAN_ATTRIBUTE", "filter_type": "number",
        "filter_op": "greater_than", "filter_value": 2,
    }})
    users.side_effect = lambda value, **_: {
        "public-user": [USER_ID], "second-user": [OTHER_PROJECT_ID], "excluded-user": [],
    }[value]
    assert call(context) == (OTHER_PROJECT_ID, None)
    query, params = service.execute_ch_query.call_args.args
    assert users.call_count == 3
    assert "matching_scalar_sessions" in query
    assert "user_id" in params.values()
    assert (USER_ID,) in params.values() and (OTHER_PROJECT_ID,) in params.values()


def test_context_read_errors_do_not_become_neighbors(navigation_context_call):
    call, service, _, _ = navigation_context_call
    service.execute_ch_query.side_effect = TimeoutError("bounded read")
    assert call(_navigation_context()) == (None, None)


@pytest.mark.parametrize("case,expected", [
    ("default", (107, 100)), ("numeric", (102, 100)),
    ("cross_span", (102, None)), ("negative", (107, None)),
    ("false", (None, None)), ("latest_outside", (None, None)),
    ("sort", (109, 102)),
])
def test_inline_navigation_literal_neighbors_reuse_existing_adversarial_fixture(
    navigation_context_call, case, expected,
):
    # Local inline VALUES only: no database/DDL, not a production-planner proof.
    from datetime import timedelta
    from uuid import UUID

    from tracer.tests.test_session_exact_latest_cursor import (
        _adversarial_rows,
        _inline_execute,
    )
    chdb = pytest.importorskip("chdb")
    call, service, projects, _ = navigation_context_call
    projects.values_list.return_value = [PROJECT_ID]
    start = datetime(2026, 7, 1, 10, 30)
    end = start + timedelta(days=7)
    rows, remaps = _adversarial_rows(start, end)
    rows = [(PROJECT_ID, *row[1:]) for row in rows]
    context = _navigation_context(project_id=PROJECT_ID)
    context["filters"][0]["filter_config"]["filter_value"] = [start.isoformat(), end.isoformat()]
    conditions = {
        "numeric": [("amount", "number", "greater_than", 1)],
        "cross_span": [("amount", "number", "equals", 2), ("amount", "number", "equals", 4)],
        "negative": [("amount", "number", "not_equals", 5)],
        "false": [("ok", "boolean", "equals", False)],
    }
    for key, kind, op, value in conditions.get(case, []):
        context["filters"].append({"column_id": key, "filter_config": {
            "col_type": "SPAN_ATTRIBUTE", "filter_type": kind,
            "filter_op": op, "filter_value": value,
        }})
    if case == "sort":
        context.update(cursor_mode=False, sort_params=[{"column_id": "total_cost", "direction": "asc"}])
    service.execute_ch_query.side_effect = lambda sql, params, **_: SimpleNamespace(
        data=_inline_execute(chdb, sql, params, rows, remaps))
    # The requested new alias 300 must select canonical survivor 200.
    current = 103 if case == "latest_outside" else 300
    assert call(context, str(UUID(int=current))) == tuple(
        str(UUID(int=value)) if value else None for value in expected
    )


def test_decorated_typed_picker_survives_public_validation_and_navigation(navigation_context_call):
    from tracer.serializers.trace_session import SessionNavigationContextSerializer
    call, service, _, _ = navigation_context_call
    context = _navigation_context()
    picker = {"column_id": "company_id", "property_id": "custom_attribute:company_id",
        "source": "traces", "filter_config": {
            "col_type": "SPAN_ATTRIBUTE", "filter_type": "text", "filter_op": "in",
            "filter_value": [2, False, "10"], "attribute_value_types": ["number", "boolean", "string"],
        }}
    context["filters"].append(picker)
    serializer = SessionNavigationContextSerializer(data=context)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["filters"][-1] == picker
    assert call(context) == (OTHER_PROJECT_ID, None)
    query, params = service.execute_ch_query.call_args.args
    assert all(column in query for column in ("attrs_number", "attrs_bool", "attrs_string"))
    assert "matching_scalar_sessions" in query
    assert "company_id" in params.values()
    picker["property_id"] = "custom_attribute:different-key"
    service.reset_mock()
    assert call(context) == (None, None)
    service.execute_ch_query.assert_not_called()


@pytest.mark.parametrize("cross_project", [False, True])
def test_inline_navigation_uses_only_authorized_workspace_projects(navigation_context_call, cross_project):
    from datetime import timedelta
    from uuid import UUID

    from tracer.tests.test_session_exact_latest_cursor import (
        _adversarial_rows,
        _inline_execute,
    )
    chdb = pytest.importorskip("chdb")
    call, service, projects, _ = navigation_context_call
    projects.values_list.return_value = [PROJECT_ID, OTHER_PROJECT_ID] if cross_project else [PROJECT_ID]
    start = datetime(2026, 7, 1, 10, 30)
    end = start + timedelta(days=7)
    rows, remaps = _adversarial_rows(start, end)
    rows = [(PROJECT_ID, *row[1:]) for row in rows]
    foreign = list(rows[-1])
    foreign[0], foreign[3], foreign[7] = OTHER_PROJECT_ID, (start + timedelta(minutes=20)).isoformat(), str(UUID(int=600))
    rows.append(tuple(foreign))
    context = _navigation_context()
    context["filters"][0]["filter_config"]["filter_value"] = [start.isoformat(), end.isoformat()]
    service.execute_ch_query.side_effect = lambda sql, params, **_: SimpleNamespace(
        data=_inline_execute(chdb, sql, params, rows, remaps))
    assert call(context, str(UUID(int=300))) == (
        str(UUID(int=107)), str(UUID(int=600 if cross_project else 100)),
    )
