import pytest
from structlog.testing import capture_logs

from agentic_eval.core.utils.score import clamp_unit_score


@pytest.mark.parametrize(
    "raw,expected",
    [
        # in-range pass-through
        (0.0, 0.0),
        (0.5, 0.5),
        (1.0, 1.0),
        # clamp upper bound
        (1.5, 1.0),
        (float("inf"), 1.0),
        # clamp lower bound
        (-0.5, 0.0),
        (float("-inf"), 0.0),
        # integer coerced and clamped
        (7, 1.0),
        # string-numeric parsed and clamped
        ("3.5", 1.0),
    ],
)
def test_clamp_numeric_inputs(raw, expected):
    assert clamp_unit_score(raw) == expected


def test_unparseable_string_passes_through():
    assert clamp_unit_score("abc") == "abc"


def test_none_returns_none():
    assert clamp_unit_score(None) is None


def test_non_numeric_object_passes_through():
    obj = {"k": "v"}
    assert clamp_unit_score(obj) is obj


def test_out_of_range_emits_warning_event():
    with capture_logs() as captured:
        clamp_unit_score(3.5)
    events = [e["event"] for e in captured]
    assert "eval_score_out_of_range_clamped" in events


def test_in_range_does_not_emit_warning():
    with capture_logs() as captured:
        clamp_unit_score(0.5)
    events = [e["event"] for e in captured]
    assert "eval_score_out_of_range_clamped" not in events
