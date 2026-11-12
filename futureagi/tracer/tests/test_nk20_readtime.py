"""NK-20 RT-01/02/06/07/08/09 regressions; cloud execution only."""

from datetime import datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from tracer.utils.eval import score_categorical, score_evals
from tracer.utils.graphs import GraphEngine

CONFIG = SimpleNamespace(id=UUID(int=1), name="quality")


def row(**kwargs):
    values = {
        "custom_eval_config": CONFIG,
        "trace": SimpleNamespace(id=UUID(int=2)),
        "status": "completed",
        "error": False,
        "skipped_reason": None,
        "output_float": None,
        "output_bool": None,
        "output_str": None,
        "output_str_list": [],
        "interval": datetime(2026, 10, 1),
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


def engine(prop="average"):
    obj = GraphEngine.__new__(GraphEngine)
    obj.property = prop
    obj.timestamps = [{"start": datetime(2026, 10, 1), "end": datetime(2026, 10, 2)}]
    obj.primary_traffic = {}
    return obj


BAD = [
    pytest.param(
        {"error": True, "output_str": "ERROR"}, "error_count", id="fresh-error"
    ),
    pytest.param({"error": True, "output_float": 0}, "error_count", id="kept-zero"),
    pytest.param({"error": True, "output_bool": False}, "error_count", id="kept-false"),
    pytest.param({"error": True, "output_bool": True}, "error_count", id="kept-true"),
    pytest.param(
        {"error": True, "output_str_list": ["yes"]}, "error_count", id="kept-choice"
    ),
    pytest.param(
        {"output_str": "ERROR", "output_float": 1}, "error_count", id="sentinel"
    ),
    pytest.param({"status": "errored", "output_float": 1}, "error_count", id="errored"),
    pytest.param(
        {"status": "rate_limited", "output_float": 1}, "error_count", id="rate-limited"
    ),
    pytest.param(
        {"status": "pending", "output_float": 1}, "pending_count", id="pending"
    ),
    pytest.param(
        {"status": "running", "output_float": 1}, "running_count", id="running"
    ),
    pytest.param(
        {"status": "skipped", "output_float": 1}, "skipped_count", id="skipped"
    ),
    pytest.param(
        {"skipped_reason": "missing input", "output_float": 1},
        "skipped_count",
        id="skip-reason",
    ),
    pytest.param({"output_str": "not numeric"}, "unscored_count", id="non-numeric"),
]


@pytest.mark.parametrize("status", [None, "", "not_started"])
def test_unknown_state_kept_output_is_unscored(status):
    entry = row(
        status=status, output_float=1, output_bool=True, output_str_list=["yes"]
    )
    assert score_evals([entry])["avg_score"] is None
    assert engine()._score_evals_property([entry])["value"] is None
    assert score_categorical([entry], "yes") is None


@pytest.mark.parametrize("bad,count", BAD)
def test_rt01_quality_population(bad, count):
    bad_row = row(**bad)
    result = score_evals([row(output_float=1), bad_row, row(output_float=0)])
    assert result["avg_score"] == 50
    assert result[count] == 1
    assert result["scored_count"] == 2
    config = result["eval_response_data"][str(CONFIG.id)]
    assert config["count"] == 3  # UUID lookup must not reset a string-keyed counter.
    assert config["passed_count"] == config["failed_count"] == 1
    assert config["failed_traces_count"] == 1
    assert score_evals([bad_row])["avg_score"] is None
    assert (
        score_evals([bad_row])["eval_response_data"][str(CONFIG.id)]["failed_count"]
        == 0
    )


@pytest.mark.parametrize(
    "prop,expected",
    [
        ("average", 50),
        ("p50", 50),
        ("p75", 75),
        ("p95", 95),
        ("standard_deviation", 50),
    ],
)
@pytest.mark.parametrize("bad,count", BAD)
def test_rt02_valid_statistics(bad, count, prop, expected):
    graph = engine(prop)
    result = graph._score_evals_property(
        [row(output_float=0), row(output_float=1), row(**bad)]
    )
    assert result["value"] == expected
    assert result[count] == 1
    assert graph._score_evals_property([row(**bad)])["value"] is None


@pytest.mark.parametrize("bad,count", BAD)
def test_rt06_categorical_population(bad, count):
    assert (
        score_categorical(
            [row(output_str_list=["yes"]), row(output_str_list=["no"]), row(**bad)],
            "yes",
        )
        == 50
    )
    assert score_categorical([row(**bad)], "yes") is None


@pytest.mark.parametrize(
    "kind,method,good",
    [
        ("bool", "process_boolean_output", {"output_bool": True}),
        ("str_list", "process_str_list_output", {"output_str_list": ["yes"]}),
        ("float", "process_float_output", {"output_float": 1}),
    ],
)
@pytest.mark.parametrize("bad,count", BAD)
def test_rt07_rt08_rt09_status_before_output(kind, method, good, bad, count):
    graph = engine()
    config = {
        "type": "EVAL",
        "output_type": kind,
        "value": True if kind == "bool" else "yes",
    }
    fn = getattr(graph, method)
    assert fn([row(**good), row(**bad)], config) == 100
    assert fn([row(**bad)], config) is None
    points = graph.process_config([row(**bad)], None, config)
    assert points[0]["value"] is None
    assert points[0][count] == 1


def test_successful_zero_false_and_empty_buckets():
    assert score_evals([row(output_float=0)])["avg_score"] == 0
    assert score_evals([row(output_bool=False)])["avg_score"] == 0
    assert engine()._score_evals_property([row(output_float=0)])["value"] == 0
    assert score_categorical([row(output_str_list=["no"])], "yes") == 0
    assert (
        engine().process_boolean_output(
            [row(output_bool=False)], {"type": "EVAL", "value": True}
        )
        == 0
    )
    assert (
        engine().process_str_list_output(
            [row(output_str_list=["no"])], {"type": "EVAL", "value": "yes"}
        )
        == 0
    )
    assert engine().process_float_output([row(output_float=0)], {"type": "EVAL"}) == 0
    assert score_evals([])["avg_score"] is None
    assert score_categorical([], "yes") is None
    assert engine()._score_evals_property([])["value"] is None
    assert (
        engine().process_config([], None, {"type": "EVAL", "output_type": "float"})[0][
            "value"
        ]
        is None
    )


def test_nonfinite_output_and_session_failure_do_not_crash():
    assert score_evals([row(output_float=float("nan"))])["avg_score"] is None
    result = score_evals([row(output_float=0, trace=None)])
    assert result["avg_score"] == 0
    assert result["eval_response_data"][str(CONFIG.id)]["failed_traces_count"] == 0
