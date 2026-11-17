"""The filter-value pickers' retryable 503 has one registered message.

It was a literal at 21 sites in ``tracer/views/dashboard.py``. Every site now
reads it from ``tfc/utils/error_codes.py`` (FILTER_VALUES_UNAVAILABLE) and
answers with the typed ``ApiErrorCode.SERVICE_UNAVAILABLE``. The wire shape is
unchanged: same status, same message text, same ``service_unavailable`` code.
"""

import ast
import inspect
from types import SimpleNamespace
from unittest.mock import Mock

from tfc.utils.error_codes import get_error_message
from tracer.services.clickhouse.read_budget import ReadDeadlineExceeded
from tracer.tests.test_filter_value_http_deadline import _ProjectScopeStub
from tracer.views import dashboard as dashboard_view

MESSAGE = "Filter values are temporarily unavailable. Please retry."


def test_message_is_registered_with_its_wire_text():
    assert get_error_message("FILTER_VALUES_UNAVAILABLE") == MESSAGE


def _unavailable_calls():
    tree = ast.parse(inspect.getsource(dashboard_view))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "custom_error_response"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Call)
            and getattr(node.args[1].func, "id", "") == "get_error_message"
            and node.args[1].args
            and getattr(node.args[1].args[0], "value", "")
            == "FILTER_VALUES_UNAVAILABLE"
        ):
            yield node


def test_every_picker_site_reads_the_registered_message_with_the_typed_code():
    source = inspect.getsource(dashboard_view)
    calls = list(_unavailable_calls())

    assert MESSAGE not in source
    assert len(calls) == 21
    for call in calls:
        assert ast.unparse(call.args[0]) == "status.HTTP_503_SERVICE_UNAVAILABLE"
        codes = [kw.value for kw in call.keywords if kw.arg == "code"]
        assert [ast.unparse(code) for code in codes] == [
            "ApiErrorCode.SERVICE_UNAVAILABLE"
        ]


def test_picker_deadline_answers_the_registered_message(monkeypatch):
    project_id = "00000000-0000-4000-8000-000000000001"
    request_deadline = Mock()
    request_deadline.remaining_ms.side_effect = ReadDeadlineExceeded("deadline")
    monkeypatch.setattr(
        dashboard_view,
        "ReadDeadline",
        SimpleNamespace(start=lambda _total_ms: request_deadline),
    )
    monkeypatch.setattr(
        dashboard_view,
        "project_queryset_for_request",
        lambda _request: _ProjectScopeStub([project_id]),
    )
    monkeypatch.setattr(
        dashboard_view, "_run_filter_value_pg_read", lambda _deadline, read: read()
    )
    monkeypatch.setattr(
        dashboard_view,
        "cursor_scope_for_request",
        lambda *_args, **_kwargs: {"principal": "unit"},
    )
    monkeypatch.setattr(dashboard_view, "V2AnalyticsQueryService", lambda: object())
    request = SimpleNamespace(
        validated_query_data={
            "metric_name": "ended_reason",
            "metric_type": "system_metric",
            "source": "traces",
            "project_ids": [project_id],
            "search": "customer",
            "page_size": 20,
        },
        workspace=SimpleNamespace(id="00000000-0000-4000-8000-000000000002"),
    )

    response = dashboard_view.DashboardViewSet.filter_values.__wrapped__(
        dashboard_view.DashboardViewSet(), request
    )

    assert response.status_code == 503
    assert response.data["code"] == "service_unavailable"
    assert response.data["message"] == MESSAGE
