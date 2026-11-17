"""The trace detail opens a trace by its OpenTelemetry id or by its dashed id.

Experiment results, trajectories, eval steps and reviews link a trace by the id
its run recorded. Platform python-task runs and SDK runs record OpenTelemetry's
32 hex digits; the span store keeps the same 128 bits as a dashed UUID string.
Both forms open the same trace. Any other value is looked up as given, and a
trace the store doesn't have is answered as not found, as before.
"""

import uuid
from inspect import unwrap
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tracer.models.trace import Trace
from tracer.services.clickhouse.v2.query_builders.trace_detail import (
    TraceDetailHandlerV2,
)

pytestmark = pytest.mark.unit

DASHED = "4bf92f35-77b3-4da0-a3ce-929d0e0e4736"
OPENTELEMETRY = "4bf92f3577b34da0a3ce929d0e0e4736"


def _open(pk, fetch=None):
    """Open the trace detail for ``pk``; return the response and the id the
    detail read was asked for."""
    from tracer.views.trace import TraceView

    asked = {}

    def _fetch(handler):
        asked["pk"] = handler.pk
        if fetch is not None:
            return fetch(handler)
        return {"id": str(handler.pk)}

    with (
        patch("tracer.views.trace.V2AnalyticsQueryService", MagicMock()),
        patch.object(TraceDetailHandlerV2, "fetch", _fetch),
    ):
        response = unwrap(TraceView.retrieve)(TraceView(), SimpleNamespace(), pk=pk)
    return response, asked.get("pk")


def test_an_opentelemetry_trace_id_opens_the_trace_stored_under_its_dashed_id():
    response, asked = _open(OPENTELEMETRY)

    assert response.status_code == 200
    assert asked == DASHED


def test_a_dashed_trace_id_opens_the_trace_as_before():
    response, asked = _open(DASHED)

    assert response.status_code == 200
    assert asked == DASHED


@pytest.mark.parametrize("pk", [OPENTELEMETRY.upper(), DASHED.upper()])
def test_either_form_in_capitals_opens_the_same_trace(pk):
    response, asked = _open(pk)

    assert response.status_code == 200
    assert asked == DASHED


@pytest.mark.parametrize(
    "pk",
    [
        "not-a-trace-id",
        OPENTELEMETRY[:-1],
        OPENTELEMETRY + "0",
        OPENTELEMETRY[:-1] + "g",
        DASHED.replace("-", "_"),
    ],
)
def test_an_id_in_neither_form_is_looked_up_as_given(pk):
    _, asked = _open(pk)

    assert asked == pk


def test_an_id_in_neither_form_is_answered_as_not_found():
    def _not_in_the_store(handler):
        raise Trace.DoesNotExist

    response, asked = _open("not-a-trace-id", fetch=_not_in_the_store)

    assert asked == "not-a-trace-id"
    assert response.status_code == 400
    assert "error retrieving trace" in str(response.data)


def test_both_forms_name_the_same_128_bits():
    from tracer.utils.trace_ids import stored_trace_id

    assert stored_trace_id(OPENTELEMETRY) == DASHED
    assert uuid.UUID(stored_trace_id(OPENTELEMETRY)).int == int(OPENTELEMETRY, 16)
    assert stored_trace_id(f" {OPENTELEMETRY} ") == DASHED
    assert stored_trace_id(None) is None
