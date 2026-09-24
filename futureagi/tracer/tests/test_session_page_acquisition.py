"""Shared Session admission and public hydration wiring; no native-SQL claim.

Real serializers, preflight, selectors, builders and hydration execute. Unit
scope lookup and storage are finite mocks; native public auth is gated separately.
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest import mock
from uuid import UUID

import pytest
from rest_framework.test import APIRequestFactory, force_authenticate

pytestmark = pytest.mark.unit
START = datetime(2026, 8, 1, 12, 15, tzinfo=UTC)
END = START + timedelta(minutes=30)


def uid(n):
    return str(UUID(int=n))


ORG, WORKSPACE, P = map(uid, (10, 20, 1))


def leaf(key="amount", kind="number", op="greater_than", value=1):
    return {
        "column_id": key,
        "property_id": "custom_attribute:" + key,
        "source": "traces",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": kind,
            "filter_op": op,
            "filter_value": value,
        },
    }


def context(*leaves, **changes):
    return {
        "project_id": P,
        "workspace_id": WORKSPACE,
        "cursor_mode": True,
        "sort_params": [],
        "filters": [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [START.isoformat(), END.isoformat()],
                },
            },
            *leaves,
        ],
        **changes,
    }


@pytest.fixture
def route(monkeypatch):
    from tracer.services.clickhouse.v2 import query_service
    from tracer.utils import session as utils
    from tracer.views import trace_session as view

    organization = NS(id=ORG, pk=ORG)
    workspace = NS(
        id=WORKSPACE, pk=WORKSPACE, organization=organization, is_default=False
    )
    user = NS(
        id=uid(11),
        pk=uid(11),
        organization=organization,
        is_authenticated=True,
        is_active=True,
    )
    project = NS(
        id=P,
        organization=organization,
        workspace=workspace,
        source="SDK",
        deleted=False,
        trace_type="observe",
        session_config=[],
    )
    projects = mock.MagicMock()
    projects.get.return_value = project
    projects.filter.return_value = projects.exclude.return_value = projects
    projects.values_list.return_value = [P]
    monkeypatch.setattr(view, "_project_queryset_for_request", lambda request: projects)
    monkeypatch.setattr(view.AnnotationsLabels.objects, "filter", lambda *a, **k: [])
    monkeypatch.setattr(
        view.TraceSessionView, "_fetch_session_names", lambda *a, **k: {}
    )
    monkeypatch.setattr(
        view.TraceSessionView, "_fetch_end_user_info", lambda *a, **k: {}
    )
    calls = []

    def execute(sql, params, **kwargs):
        calls.append((sql, params))
        return NS(data=[])

    service = NS(execute_ch_query=execute)
    monkeypatch.setattr(view, "V2AnalyticsQueryService", lambda: service)
    monkeypatch.setattr(query_service, "V2AnalyticsQueryService", lambda: service)
    request = NS(
        organization=organization, workspace=workspace, user=user, query_params={}
    )

    def public(query, method):
        factory = APIRequestFactory()
        req = (
            factory.get(
                "/api/trace-session/list_sessions/",
                query | {"filters": json.dumps(query["filters"])},
            )
            if method == "get"
            else factory.post("/api/trace-session/list_sessions/", query, format="json")
        )
        req.organization, req.workspace = organization, workspace
        force_authenticate(req, user=user)
        return view.TraceSessionView.as_view({method: "list_sessions"})(req)

    return NS(
        view=view,
        utils=utils,
        request=request,
        project=project,
        service=service,
        calls=calls,
        public=public,
    )


def boundary(view, filtered):
    from tracer.selectors.trace_filter_reads import MAX_NUMBERED_PAGE_WORK_ROWS

    size = 30

    def rejected(number):
        return view.numbered_page_depth_exceeded(
            page_number=number, page_size=size
        ) or (
            filtered
            and view.bounded_numbered_page_depth_exceeded(
                page_number=number,
                page_size=size,
                max_candidates=view.SESSION_LIST_FILTER_MAX_CANDIDATES,
                classify_batch_size=view.SessionListQueryBuilderV2.recommended_filter_classify_batch_size(),
                seed_batch_size=view.SessionListQueryBuilderV2.recommended_filter_seed_batch_size(),
                max_seed_attempts=view.SESSION_LIST_FILTER_MAX_SEED_ATTEMPTS,
                max_query_count=view.SESSION_LIST_FILTER_MAX_QUERIES,
            )
        )

    return next(
        number
        for number in range(MAX_NUMBERED_PAGE_WORK_ROWS // size + 1)
        if rejected(number)
    ), size


@pytest.mark.parametrize(
    "filtered", [False, True], ids=["general-limit", "filtered-limit"]
)
@pytest.mark.parametrize("method", ["get", "post"])
def test_shared_selection_rejects_same_depth_as_actual_public_list_before_io(
    route, filtered, method
):
    number, size = boundary(route.view, filtered)
    query = {
        "project_id": P,
        "page_number": number,
        "page_size": size,
        "cursor_mode": False,
        "sort_params": [],
        "filters": context(*([leaf()] if filtered else []))["filters"],
    }
    response = route.public(query, method)
    assert response.status_code == 422 and not route.calls
    selected = route.view.TraceSessionView()._select_session_page(
        route.request, P, route.project, route.service, query
    )
    assert getattr(selected, "status_code", None) == 422, {
        "selected": type(selected).__name__,
        "dispatched_offsets": [p.get("offset") for _, p in route.calls],
    }
    assert (
        selected.data["code"]
        == response.data["code"]
        == route.view.PAGE_DEPTH_EXCEEDED_CODE
    )
    assert not route.calls


@pytest.mark.parametrize("field", ["page_request", "previous_page_request"])
def test_navigation_nested_depth_rejected_before_alias_or_candidate_io(route, field):
    number, size = boundary(route.view, False)
    ctx = context(cursor_mode=False)
    ctx[field] = {
        "project_id": P,
        "page_number": number,
        "page_size": size,
        "cursor_mode": False,
        "filters": ctx["filters"],
        "sort_params": [],
    }
    result = route.utils.get_session_navigation(
        route.request, P, uid(300), query_data={"navigation_context": ctx}
    )
    assert result == (None, None)
    assert not route.calls, {
        "dispatched_offsets": [p.get("offset") for _, p in route.calls]
    }


def test_numbered_continuation_rechecks_existing_depth_contract(route):
    number, size = boundary(route.view, False)

    def execute(sql, params, **kwargs):
        route.calls.append((sql, params))
        if "offset" not in params:
            return NS(data=[])
        if params["offset"] >= number * size:
            raise AssertionError("Out-of-contract numbered continuation dispatched")
        return NS(
            data=[
                {
                    "session_id": uid(100),
                    "session_start": START,
                    "total_count": (number + 1) * size,
                    "project_id": P,
                }
            ]
        )

    route.service.execute_ch_query = execute
    ctx = context(cursor_mode=False)
    ctx["page_request"] = {
        "project_id": P,
        "page_number": number - 1,
        "page_size": size,
        "cursor_mode": False,
        "filters": ctx["filters"],
        "sort_params": [],
    }
    assert route.utils.get_session_navigation(
        route.request, P, uid(300), query_data={"navigation_context": ctx}
    ) == (None, None)
    offsets = [p["offset"] for _, p in route.calls if "offset" in p]
    assert (number - 1) * size in offsets, (
        "Last legal numbered origin must still dispatch"
    )
    assert all(offset < number * size for offset in offsets), offsets


@pytest.fixture
def public_list(route):
    def call(method, empty):
        calls = []

        def execute(sql, params, *, timeout_ms, settings):
            if "count() OVER() AS total_count" in sql:
                phase = "candidate"
                rows = (
                    []
                    if empty
                    else [
                        {
                            "session_id": uid(100),
                            "session_start": START,
                            "total_count": 1,
                            "project_id": P,
                        }
                    ]
                )
            elif "SELECT count() AS total" in sql:
                phase, rows = "count", [{"total": 1}]
            elif "sum(total_tokens) AS total_tokens" in sql:
                phase = "metrics"
                rows = [
                    {
                        "session_id": uid(100),
                        "session_start": START,
                        "session_end": END,
                        "duration": 30.0,
                        "total_cost": 12.0,
                        "total_tokens": 40,
                        "traces_count": 3,
                    }
                ]
            elif "AS first_message" in sql:
                phase = "content"
                rows = [
                    {
                        "session_id": uid(100),
                        "first_message": "first",
                        "last_message": "last",
                    }
                ]
            elif "latest_attrs_number AS attrs_number" in sql:
                phase = "attributes"
                rows = [
                    {
                        "session_id": uid(100),
                        "attrs_number": {"company_id": 2},
                        "attrs_string": {},
                        "attrs_bool": {"failed": False},
                    }
                ]
            else:
                raise AssertionError("Unexpected real-builder SQL: " + sql[:300])
            calls.append(
                {
                    "phase": phase,
                    "sql": sql,
                    "params": params,
                    "timeout_ms": timeout_ms,
                    "settings": settings,
                }
            )
            return NS(data=rows)

        route.service.execute_ch_query = execute
        params = {
            "project_id": P,
            "filters": context()["filters"],
            "page_size": 1,
            "page_number": 1 if empty else 0,
        }
        return route.public(params, method), calls, route.view

    return call


@pytest.mark.parametrize("method", ["get", "post"])
@pytest.mark.parametrize(
    "empty", [False, True], ids=["nonempty-hydration", "empty-numbered-count"]
)
def test_public_list_hydration_and_exact_empty_page_count(public_list, method, empty):
    response, calls, view = public_list(method, empty)
    assert response.status_code == 200, response.data
    data = response.data["result"]
    assert data["metadata"]["total_rows"] == 1
    phases = {call["phase"] for call in calls}
    assert phases == (
        {"candidate", "count"}
        if empty
        else {"candidate", "metrics", "content", "attributes"}
    )
    assert len(calls) == len(phases)
    for call in calls:
        maximum = (
            2
            if call["phase"] == "candidate"
            else view.SESSION_LIST_ATTRIBUTE_RESULT_ROWS
            if call["phase"] == "attributes"
            else 1
        )
        assert call["settings"] == view._session_read_settings(max_result_rows=maximum)
        assert call["timeout_ms"] > 0
        assert call["params"]["project_id"] == P
    if empty:
        assert data["table"] == []
    else:
        assert len(data["table"]) == 1
        row = data["table"][0]
        assert row["session_id"] == uid(100) and row["project_id"] == P
        assert row["total_cost"] == 12.0 and row["total_traces_count"] == 3
        assert row["first_message"] == "first" and row["last_message"] == "last"
        assert row["company_id"] == 2 and row["failed"] is False
