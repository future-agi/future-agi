"""A mapping that names a bare model path ("traces" of a session, "spans" of
a trace) gives the evaluator JSON of the fields it could have named one by
one, not a Python object: Jev and every other evaluator failed with "Object
of type Trace is not JSON serializable"."""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from tracer.models.trace import Trace
from tracer.utils.eval import _mapped_json

pytestmark = pytest.mark.unit


def test_a_sessions_traces_become_their_public_fields():
    traces = [
        Trace(id=uuid.uuid4(), name="turn 1", input={"q": "refund?"}, output="Done."),
        Trace(id=uuid.uuid4(), name="turn 2", input="thanks", output="Bye."),
    ]
    with pytest.raises(TypeError):
        json.dumps(traces)
    decoded = json.loads(_mapped_json(traces))
    assert [t["name"] for t in decoded] == ["turn 1", "turn 2"]
    assert decoded[0]["input"] == {"q": "refund?"}
    assert decoded[1]["output"] == "Bye."
    assert decoded[0]["id"] == str(traces[0].id)
    assert "_state" not in decoded[0]


def test_spans_keep_span_fields_and_plain_values_pass_through():
    span = SimpleNamespace(
        id="s1",
        observation_type="llm",
        name="chat",
        input="hi",
        output="hello",
        cost=Decimal("0.0012"),
        start_time=datetime(2026, 10, 27, tzinfo=UTC),
    )
    decoded = json.loads(_mapped_json([span]))
    assert decoded[0]["observation_type"] == "llm"
    assert decoded[0]["cost"] == pytest.approx(0.0012)
    assert _mapped_json("already text") == "already text"
    assert json.loads(_mapped_json({"n": 3})) == {"n": 3}


def test_an_unknown_object_is_still_refused():
    with pytest.raises(TypeError):
        _mapped_json([object()])
