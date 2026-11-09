"""RT-11b backend port: unscored intervals differ from successful zero.

Ports the dsx round-1 backend cases without importing its lane-only test file.
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tfc.utils.functions import calculate_eval_average
from tracer.utils.graphs import GraphEngine


class _Logs(list):
    def filter(self, **kwargs):
        def matches(row):
            for key, value in kwargs.items():
                field, op = key.split("__", 1)
                if op == "gte" and not row[field] >= value:
                    return False
                if op == "lt" and not row[field] < value:
                    return False
                if op == "in" and row[field] not in value:
                    return False
            return True

        return _Logs(row for row in self if matches(row))

    def count(self):
        return len(self)


def _metric_engine(rows):
    start = datetime(2026, 11, 1, tzinfo=UTC)
    engine = GraphEngine.__new__(GraphEngine)
    engine.timestamps = [{"start": start, "end": start + timedelta(days=1)}]
    engine.objects = _Logs(
        {
            "interval": start,
            "status": status,
            "config": json.dumps({"output": {"output": value}}),
        }
        for status, value in rows
    )
    return engine, start


@pytest.mark.parametrize("output_type", ["score", "Pass/Fail", "choices", "reason"])
@pytest.mark.parametrize(
    "rows,error_count",
    [
        pytest.param([], 0, id="empty"),
        pytest.param(
            [("error", 0), ("insufficient_credits", False), ("rate_limited", "Failed")],
            3,
            id="all-errors",
        ),
    ],
)
def test_unscored_intervals_are_null(output_type, rows, error_count):
    engine, start = _metric_engine(rows)
    template = SimpleNamespace(config={"output": output_type}, multi_choice=False)

    result = engine.get_eval_metric_data(template)

    assert result["avg_graph_data"] == [{"timestamp": start, "value": None}]
    assert result["count_graph_data"] == [{"timestamp": start, "value": len(rows)}]
    assert engine.get_error_graph_data(template) == [
        {"timestamp": start, "value": error_count}
    ]


@pytest.mark.parametrize(
    "output_type,value,choices_map",
    [
        pytest.param("score", 0, None, id="numeric-zero"),
        pytest.param("score", False, None, id="boolean-false"),
        pytest.param("Pass/Fail", "Failed", None, id="failed-quality"),
        pytest.param("choices", ["bad"], {"bad": "fail"}, id="failed-choice"),
    ],
)
def test_successful_zero_is_not_unscored(output_type, value, choices_map):
    engine, start = _metric_engine([("success", value)])
    template = SimpleNamespace(
        config={"output": output_type, "choices_map": choices_map}, multi_choice=False
    )

    result = engine.get_eval_metric_data(template)

    assert result["avg_graph_data"] == [{"timestamp": start, "value": 0}]
    assert result["count_graph_data"] == [{"timestamp": start, "value": 1}]
    assert engine.get_error_graph_data(template) == [{"timestamp": start, "value": 0}]


def test_successful_unscored_logs_do_not_create_a_zero_score():
    engine, start = _metric_engine(
        [("success", None), ("success", "not a score"), ("processing", 0)]
    )
    template = SimpleNamespace(config={"output": "score"}, multi_choice=False)

    result = engine.get_eval_metric_data(template)

    assert result["avg_graph_data"] == [{"timestamp": start, "value": None}]
    assert result["count_graph_data"] == [{"timestamp": start, "value": 3}]
    assert engine.get_error_graph_data(template) == [{"timestamp": start, "value": 0}]


def test_mixed_interval_averages_only_successful_valid_scores():
    engine, start = _metric_engine(
        [
            ("success", 0.2),
            ("success", 0.8),
            ("success", None),
            ("success", "invalid"),
            ("error", 1),
            ("rate_limited", 0),
            ("processing", 1),
        ]
    )
    template = SimpleNamespace(config={"output": "score"}, multi_choice=False)

    result = engine.get_eval_metric_data(template)

    assert result["avg_graph_data"] == [{"timestamp": start, "value": 50}]
    assert result["count_graph_data"] == [{"timestamp": start, "value": 7}]
    assert engine.get_error_graph_data(template) == [{"timestamp": start, "value": 2}]


def test_adjacent_empty_error_and_zero_intervals_stay_distinct():
    engine, start = _metric_engine([("error", 1)])
    day = timedelta(days=1)
    engine.timestamps = [
        {"start": start + i * day, "end": start + (i + 1) * day} for i in range(3)
    ]
    engine.objects.append(
        {
            "interval": start + 2 * day,
            "status": "success",
            "config": json.dumps({"output": {"output": 0}}),
        }
    )
    template = SimpleNamespace(config={"output": "score"}, multi_choice=False)

    result = engine.get_eval_metric_data(template)

    assert result["avg_graph_data"] == [
        {"timestamp": start, "value": None},
        {"timestamp": start + day, "value": None},
        {"timestamp": start + 2 * day, "value": 0},
    ]
    assert [point["value"] for point in result["count_graph_data"]] == [1, 0, 1]
    assert [point["value"] for point in engine.get_error_graph_data(template)] == [
        1,
        0,
        0,
    ]


@pytest.mark.parametrize("rows", [[], [("error", 1)], [("success", "invalid")]])
def test_legacy_average_callers_keep_numeric_empty_default(rows):
    engine, _ = _metric_engine(rows)
    template = SimpleNamespace(config={"output": "score"}, multi_choice=False)

    assert calculate_eval_average(template, engine.objects) == 0


@pytest.mark.parametrize(
    "output_type,value,average",
    [("score", 0.8, 80), ("Pass/Fail", "Passed", 100)],
)
def test_eval_metric_excludes_error_outputs_but_keeps_attempts(
    output_type, value, average
):
    engine, start = _metric_engine(
        [
            ("success", value),
            ("error", value),
            ("insufficient_credits", value),
            ("rate_limited", value),
            ("processing", value),
            ("not_started", value),
        ]
    )
    template = SimpleNamespace(config={"output": output_type}, multi_choice=False)

    result = engine.get_eval_metric_data(template)

    assert result["count_graph_data"] == [{"timestamp": start, "value": 6}]
    assert result["avg_graph_data"] == [{"timestamp": start, "value": average}]
    assert engine.get_error_graph_data(template) == [{"timestamp": start, "value": 3}]
