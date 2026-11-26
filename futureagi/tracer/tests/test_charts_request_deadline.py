from __future__ import annotations

import json
from contextlib import contextmanager
from inspect import unwrap
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

import pytest


def _view_and_request(serializer_class):
    from tracer.views import charts

    view = charts.ChartsView()
    view.serializer_class = serializer_class
    view._gm = SimpleNamespace(
        bad_request=lambda detail: ("bad_request", detail),
        success_response=lambda result: ("success", result),
        custom_error_response=lambda status_code, detail, code: (
            "error",
            status_code,
            detail,
            code,
        ),
    )
    request = SimpleNamespace(
        method="GET",
        data={},
        query_params={},
        workspace=SimpleNamespace(id=uuid4()),
    )
    return charts, view, request


@pytest.mark.unit
def test_charts_graph_deadline_starts_before_validation_and_bounds_eval_scope():
    from tracer.services.clickhouse import graph_action_deadline as deadline_helpers

    events = []
    project_id = str(uuid4())
    organization_id = uuid4()
    workspace_id = uuid4()
    deadline = object()
    validated_query = {
        "req_data_config": {"type": "EVAL", "id": str(uuid4())},
        "interval": "day",
        "filters": [],
        "property": "average",
        "project_id": project_id,
        "allow_sampled": False,
        "refresh": False,
    }

    class Serializer:
        def __init__(self, *, data):
            assert data == {}
            events.append("serializer_init")
            self.validated_data = validated_query

        def is_valid(self):
            events.append("validated")
            return True

    charts, view, request = _view_and_request(Serializer)
    request.workspace.id = workspace_id
    project = SimpleNamespace(
        id=project_id,
        organization_id=organization_id,
    )

    @contextmanager
    def budget(received_deadline):
        assert received_deadline is deadline
        events.append("pg_budget_enter")
        yield
        events.append("pg_budget_exit")

    metric_data = {"query_status": "complete"}

    def validate_public_query(serializer, *, raise_exception=False):
        del raise_exception
        events.append("validated")
        serializer._validated_data = validated_query
        serializer._errors = {}
        return True

    with (
        mock.patch.object(
            deadline_helpers,
            "start_graph_action_deadline",
            side_effect=lambda: events.append("deadline") or deadline,
        ),
        mock.patch.object(
            charts,
            "graph_action_postgres_budget",
            side_effect=budget,
        ),
        mock.patch.object(
            charts.FetchGraphSerializer,
            "is_valid",
            new=validate_public_query,
        ),
        mock.patch.object(
            charts,
            "get_request_organization",
            return_value=SimpleNamespace(id=organization_id),
        ),
        mock.patch.object(
            charts,
            "bind_request_my_annotations_principal",
            return_value=[],
        ),
        mock.patch.object(
            charts.Project.objects,
            "get",
            side_effect=lambda **_kwargs: events.append("project") or project,
        ),
        mock.patch.object(
            charts,
            "get_eval_graph_data",
            side_effect=lambda **_kwargs: events.append("graph") or metric_data,
        ),
        mock.patch.object(
            charts,
            "graph_payload_is_publishable",
            return_value=True,
        ),
        mock.patch.object(
            deadline_helpers,
            "finish_graph_action_response",
            side_effect=lambda received, response: (
                events.append("finish") or response
                if received is deadline
                else pytest.fail("wrong deadline")
            ),
        ),
    ):
        response = charts.ChartsView.fetch_graph(view, request)

    assert response == ("success", metric_data)
    assert events == [
        "deadline",
        "validated",
        "pg_budget_enter",
        "project",
        "pg_budget_exit",
        "pg_budget_enter",
        "graph",
        "pg_budget_exit",
        "finish",
    ]


@pytest.mark.unit
def test_charts_invalid_validation_finishes_without_database_work():
    from tracer.services.clickhouse import graph_action_deadline as deadline_helpers

    events = []
    deadline = object()

    class Serializer:
        errors = {"project_id": ["required"]}

        def __init__(self, *, data):
            assert data == {}

        def is_valid(self):
            events.append("validated")
            return False

    charts, view, request = _view_and_request(Serializer)

    def reject_public_query(serializer, *, raise_exception=False):
        del raise_exception
        events.append("validated")
        serializer._validated_data = {}
        serializer._errors = Serializer.errors
        return False

    with (
        mock.patch.object(
            deadline_helpers,
            "start_graph_action_deadline",
            side_effect=lambda: events.append("deadline") or deadline,
        ),
        mock.patch.object(
            charts,
            "graph_action_postgres_budget",
            side_effect=AssertionError("validation must not open PostgreSQL"),
        ),
        mock.patch.object(
            charts.FetchGraphSerializer,
            "is_valid",
            new=reject_public_query,
        ),
        mock.patch.object(
            charts.Project.objects,
            "get",
            side_effect=AssertionError("validation must not query projects"),
        ),
        mock.patch.object(
            deadline_helpers,
            "finish_graph_action_response",
            side_effect=lambda received, response: (
                events.append("finish") or response
                if received is deadline
                else pytest.fail("wrong deadline")
            ),
        ),
    ):
        response = charts.ChartsView.fetch_graph(view, request)

    assert response.status_code == 400
    assert response.data["details"] == Serializer.errors
    assert events == ["deadline", "validated", "finish"]


@pytest.mark.unit
def test_charts_deadline_failure_is_sanitized_503():
    from tracer.services.clickhouse.graph_action_deadline import (
        GraphActionUnavailable,
    )

    class Serializer:
        def __init__(self, *, data):
            self.validated_data = {
                "req_data_config": {"type": "SYSTEM_METRIC", "id": "latency"},
                "interval": "day",
                "filters": [],
                "property": "average",
                "project_id": str(uuid4()),
                "allow_sampled": False,
                "refresh": False,
            }

        def is_valid(self):
            return True

    charts, view, request = _view_and_request(Serializer)

    @contextmanager
    def expired(_deadline):
        raise GraphActionUnavailable("secret timeout detail")
        yield

    with (
        mock.patch.object(charts, "start_graph_action_deadline", return_value=object()),
        mock.patch.object(charts, "graph_action_postgres_budget", side_effect=expired),
        mock.patch.object(
            charts,
            "bind_request_my_annotations_principal",
            return_value=[],
        ),
    ):
        response = unwrap(charts.ChartsView.fetch_graph)(view, request)

    assert response[0] == "error"
    assert response[1] == 503
    assert response[3] == "service_unavailable"
    assert "secret" not in response[2]


@pytest.mark.unit
def test_charts_action_metadata_is_preserved():
    from tracer.views.charts import ChartsView

    assert ChartsView.pagination_class is None
    assert ChartsView.fetch_graph.mapping == {"get": "fetch_graph"}
    assert ChartsView.fetch_graph.detail is False
    assert ChartsView.fetch_graph.url_path == "fetch_graph"
    schema = ChartsView.fetch_graph._swagger_auto_schema["get"]
    assert schema["query_serializer"].__name__ == "FetchGraphSerializer"
    assert schema["responses"][200].__name__ == "FetchGraphResponseSerializer"
    assert set(schema["responses"]) == {200, 400, 500, 503}


@pytest.mark.unit
def test_checked_swagger_publishes_exact_fetch_graph_contract():
    swagger_path = Path(__file__).parents[3] / "api_contracts/openapi/swagger.json"
    operation = json.loads(swagger_path.read_text())["paths"][
        "/tracer/charts/fetch_graph/"
    ]["get"]

    assert [parameter["name"] for parameter in operation["parameters"]] == [
        "interval",
        "filters",
        "property",
        "req_data_config",
        "project_id",
        "allow_sampled",
        "refresh",
    ]
    assert operation["responses"]["200"]["schema"]["$ref"] == (
        "#/definitions/FetchGraphResponse"
    )
    assert {
        code: response["schema"]["$ref"]
        for code, response in operation["responses"].items()
        if code in {"400", "500", "503"}
    } == {
        "400": "#/definitions/ApiErrorResponse",
        "500": "#/definitions/ApiErrorResponse",
        "503": "#/definitions/ApiErrorResponse",
    }
