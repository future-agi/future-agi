"""A valid trace absent from the caller's scope is a structured 404.

Exercise the real retrieve method with the same unwrapped-view boundary as
``test_trace_detail_opens_an_opentelemetry_trace_id``. Only analytics creation
and the handler's storage fetch are stubbed; response construction and ID
canonicalization remain real. These tests do not exercise or bypass production
request authorization, and make no promise that an absent trace will arrive.
"""

from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from uuid import UUID

import pytest

from tfc.utils.general_methods import GeneralMethods
from tracer.models.trace import Trace
from tracer.services.clickhouse.v2.query_builders.trace_detail import (
    TraceDetailHandlerV2,
)
from tracer.services.clickhouse.v2.trace_detail_reads import TraceDetailReadUnavailable

pytestmark = pytest.mark.unit

DASHED = "4bf92f35-77b3-4da0-a3ce-929d0e0e4736"
OPENTELEMETRY = "4bf92f3577b34da0a3ce929d0e0e4736"
PROJECT_ID = "6dc638f4-cc67-4ea7-9e98-364c5c3da63b"
TEST_EXECUTION_ID = UUID("29fe9ab1-543f-4ca4-a968-e7af00569a6e")
CALL_EXECUTION_ID = UUID("13f9a2c8-f4e9-4477-885f-bf4c162cced0")
VALID_IDS = [
    pytest.param(OPENTELEMETRY, id="otel"),
    pytest.param(DASHED, id="dashed"),
    pytest.param(OPENTELEMETRY.upper(), id="otel-uppercase"),
    pytest.param(DASHED.upper(), id="dashed-uppercase"),
    pytest.param(f" {OPENTELEMETRY} ", id="otel-whitespace"),
    pytest.param(f" {DASHED.upper()} ", id="dashed-uppercase-whitespace"),
]
SCOPES = [
    pytest.param({}, id="caller-projects"),
    pytest.param({"project_id": PROJECT_ID}, id="project"),
    pytest.param(
        {"project_id": PROJECT_ID, "test_execution_id": TEST_EXECUTION_ID},
        id="test-run",
    ),
    pytest.param(
        {"project_id": PROJECT_ID, "call_execution_id": CALL_EXECUTION_ID},
        id="call-run",
    ),
]


@pytest.fixture
def open_trace():
    from tracer.views.trace import TraceView

    # Reuse the view across sequential reads so the retry test cannot be
    # satisfied by a view-local negative cache or a fabricated empty success.
    view = TraceView()

    def _open(pk, *, fetch=None, query=None):
        request = SimpleNamespace(validated_query_data=query or {})
        asked = {}

        def _fetch(handler):
            asked.update(
                pk=handler.pk,
                project_id=handler.project_id,
                test_execution_id=handler.test_execution_id,
                call_execution_id=handler.call_execution_id,
            )
            assert handler.view is view
            assert handler.request is request
            if fetch is not None:
                return fetch(handler)
            return {"id": str(handler.pk)}

        with (
            patch("tracer.views.trace.V2AnalyticsQueryService", MagicMock()),
            patch.object(TraceDetailHandlerV2, "fetch", _fetch),
        ):
            response = unwrap(TraceView.retrieve)(view, request, pk=pk)
        return response, asked

    return _open


def _assert_lookup(asked, pk, query):
    assert asked == {
        "pk": pk,
        "project_id": query.get("project_id"),
        "test_execution_id": query.get("test_execution_id"),
        "call_execution_id": query.get("call_execution_id"),
    }


def _assert_not_found(response):
    assert response.status_code == 404
    assert response.data["status"] is False
    assert response.data["code"] == "not_found"
    assert response.data["type"] == "not_found"
    message = response.data["message"]
    assert isinstance(message, str)
    assert "not found" in message.casefold()
    assert DASHED not in message.casefold()
    assert OPENTELEMETRY not in message.casefold()
    assert "retry" not in message.casefold()
    assert "ingest" not in message.casefold()
    # Use the existing helper, rather than reimplementing the error envelope.
    assert response.data == GeneralMethods().not_found(message).data


@pytest.mark.parametrize("pk", VALID_IDS)
def test_valid_missing_trace_returns_structured_404(open_trace, pk):
    query = {"project_id": PROJECT_ID, "test_execution_id": TEST_EXECUTION_ID}
    fetch = Mock(side_effect=Trace.DoesNotExist())

    response, asked = open_trace(pk, fetch=fetch, query=query)

    fetch.assert_called_once()
    _assert_lookup(asked, DASHED, query)
    _assert_not_found(response)


@pytest.mark.parametrize("query", SCOPES)
def test_same_trace_absent_then_available_returns_404_then_200(open_trace, query):
    payload = {"id": DASHED, "spans": [{"span_id": "00f067aa0ba902b7"}]}
    fetch = Mock(side_effect=[Trace.DoesNotExist(), payload])

    missing, first_lookup = open_trace(OPENTELEMETRY, fetch=fetch, query=query)
    available, second_lookup = open_trace(OPENTELEMETRY, fetch=fetch, query=query)

    assert fetch.call_count == 2
    _assert_lookup(first_lookup, DASHED, query)
    _assert_lookup(second_lookup, DASHED, query)
    assert available.status_code == 200
    assert available.data == GeneralMethods().success_response(payload).data
    _assert_not_found(missing)


@pytest.mark.parametrize(
    "pk",
    [
        pytest.param("not-a-trace-id", id="non-id"),
        pytest.param(OPENTELEMETRY[:-1], id="too-short"),
        pytest.param(OPENTELEMETRY + "0", id="too-long"),
        pytest.param(OPENTELEMETRY[:-1] + "g", id="non-hex"),
        pytest.param(DASHED.replace("-", "_"), id="wrong-separator"),
        pytest.param("", id="empty"),
        pytest.param("   ", id="blank"),
        pytest.param(None, id="none"),
    ],
)
def test_malformed_missing_trace_stays_400_and_is_forwarded_unchanged(open_trace, pk):
    fetch = Mock(side_effect=Trace.DoesNotExist())

    response, asked = open_trace(pk, fetch=fetch)

    fetch.assert_called_once()
    _assert_lookup(asked, pk, {})
    assert response.status_code == 400
    assert response.data["code"] == "invalid"
    assert response.data["type"] == "validation_error"
    assert "error retrieving trace" in str(response.data)


@pytest.mark.parametrize("pk", VALID_IDS)
@pytest.mark.parametrize("query", SCOPES)
def test_existing_trace_stays_200_with_canonical_id_and_unchanged_scope(
    open_trace, pk, query
):
    payload = {"id": DASHED, "spans": [{"span_id": "00f067aa0ba902b7"}]}
    fetch = Mock(return_value=payload)

    response, asked = open_trace(pk, fetch=fetch, query=query)

    fetch.assert_called_once()
    _assert_lookup(asked, DASHED, query)
    assert response.status_code == 200
    assert response.data == GeneralMethods().success_response(payload).data


@pytest.mark.parametrize(
    "code", ["read_budget_exceeded", "deadline_exceeded", "clickhouse_query_failed"]
)
def test_temporary_read_failure_stays_503(open_trace, code):
    fetch = Mock(side_effect=TraceDetailReadUnavailable(code))

    response, asked = open_trace(OPENTELEMETRY, fetch=fetch)

    fetch.assert_called_once()
    _assert_lookup(asked, DASHED, {})
    assert response.status_code == 503
    assert response.data == GeneralMethods().custom_error_response(
        503,
        "Trace details are temporarily unavailable. Please retry.",
        code="service_unavailable",
    ).data


@pytest.mark.parametrize(
    "code", ["span_limit_exceeded", "eval_limit_exceeded", "annotation_limit_exceeded"]
)
def test_trace_too_large_stays_400_with_its_existing_message(open_trace, code):
    from tracer.views.trace import TraceView

    fetch = Mock(side_effect=TraceDetailReadUnavailable(code))

    response, asked = open_trace(DASHED, fetch=fetch)

    fetch.assert_called_once()
    _assert_lookup(asked, DASHED, {})
    assert response.status_code == 400
    assert response.data == GeneralMethods().custom_error_response(
        400, TraceView._TRACE_TOO_LARGE_MESSAGES[code], code="trace_too_large"
    ).data
