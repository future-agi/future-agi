"""An eval task's trace ids are saved in the span store's form.

An eval task created over the API may name its traces by the OpenTelemetry id
from the caller's own logs: 32 hex digits. The span store keeps the same 128
bits as a lowercase dashed UUID string, which is what the task's trace
selection matches. The filters keep that form; any other value is kept as
given, and span and session ids are untouched.
"""

import pytest

from tracer.serializers.filters import EvalTaskFiltersField

pytestmark = pytest.mark.unit

DASHED = "4bf92f35-77b3-4da0-a3ce-929d0e0e4736"
OPENTELEMETRY = "4bf92f3577b34da0a3ce929d0e0e4736"


def _saved(filters):
    return EvalTaskFiltersField().to_internal_value(filters)


@pytest.mark.parametrize(
    "given",
    [OPENTELEMETRY, OPENTELEMETRY.upper(), DASHED.upper(), DASHED],
)
def test_a_trace_id_is_saved_in_the_dashed_form(given):
    assert _saved({"trace_id": [given]})["trace_id"] == [DASHED]


def test_a_single_trace_id_is_saved_as_a_list_in_the_dashed_form():
    assert _saved({"trace_id": OPENTELEMETRY})["trace_id"] == [DASHED]


def test_an_id_in_neither_form_is_saved_as_given():
    assert _saved({"trace_id": ["trace-1", OPENTELEMETRY]})["trace_id"] == [
        "trace-1",
        DASHED,
    ]


def test_span_and_session_ids_are_untouched():
    saved = _saved({"span_id": [OPENTELEMETRY], "session_id": [OPENTELEMETRY]})

    assert saved["span_id"] == [OPENTELEMETRY]
    assert saved["session_id"] == [OPENTELEMETRY]
