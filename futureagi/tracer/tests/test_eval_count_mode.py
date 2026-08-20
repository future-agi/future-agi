"""Unit tests for the count-mode eval cells (TH-7610).

Covers the ``count_mode`` flag on ``TraceListQueryBuilder.pivot_eval_results``
and ``SpanListQueryBuilder.pivot_eval_results`` (the Observe list endpoints)
plus the ``eval_count_cell`` mapper:

  * Pass/Fail cells surface exact ``{"pass": n, "fail": n}`` counts.
  * Choices cells surface ``{label: count}`` in ONE column (zero-filled across
    the template's declared labels by ``eval_count_cell``).
  * Score cells keep the numeric average — a real 0.0 survives.
  * ``{"error": True}`` and lifecycle ``{"status": ...}`` markers are emitted
    and passed through identically in both modes.
  * Default (non-count) mode stays byte-identical for every other caller.
"""

import pytest

from tracer.services.clickhouse.query_builders.span_list import SpanListQueryBuilder
from tracer.services.clickhouse.query_builders.trace_list import TraceListQueryBuilder
from tracer.utils.helper import eval_count_cell

# Column order of the trace-list Phase-2 eval query SELECT.
TRACE_EVAL_COLUMNS = [
    "trace_id",
    "eval_config_id",
    "avg_score",
    "pass_rate",
    "success_count",
    "error_count",
    "eval_count",
    "str_lists",
    "skipped_count",
    "running_count",
    "pending_count",
    "skipped_reason",
    "pass_count",
    "fail_count",
]


def _trace_row(**overrides):
    row = {
        "trace_id": "t1",
        "eval_config_id": "c1",
        "avg_score": None,
        "pass_rate": None,
        "success_count": 0,
        "error_count": 0,
        "eval_count": 0,
        "str_lists": [],
        "skipped_count": 0,
        "running_count": 0,
        "pending_count": 0,
        "skipped_reason": None,
        "pass_count": 0,
        "fail_count": 0,
    }
    row.update(overrides)
    return row


def _trace_pivot(rows, count_mode=False):
    return TraceListQueryBuilder.pivot_eval_results(
        [[row[c] for c in TRACE_EVAL_COLUMNS] for row in rows],
        TRACE_EVAL_COLUMNS,
        count_mode=count_mode,
    )


def _span_row(**overrides):
    row = {
        "observation_span_id": "s1",
        "eval_config_id": "c1",
        "avg_score": None,
        "pass_rate": None,
        "success_count": 0,
        "error_count": 0,
        "skipped_count": 0,
        "running_count": 0,
        "pending_count": 0,
        "skipped_reason": None,
        "eval_count": 0,
        "str_lists": [],
        "pass_count": 0,
        "fail_count": 0,
    }
    row.update(overrides)
    return row


class TestTracePivotCountMode:
    def test_count_mode_passfail_returns_exact_counts(self):
        result = _trace_pivot(
            [
                _trace_row(
                    pass_rate=55.56,
                    success_count=9,
                    eval_count=9,
                    pass_count=5,
                    fail_count=4,
                )
            ],
            count_mode=True,
        )
        cell = result["t1"]["c1"]
        assert cell["pass_count"] == 5
        assert cell["fail_count"] == 4
        assert cell["pass_rate"] == 55.56
        assert cell["count"] == 9

    def test_count_mode_choices_returns_label_counts(self):
        result = _trace_pivot(
            [
                _trace_row(
                    success_count=3,
                    eval_count=3,
                    str_lists=[["Accurate"], ["Accurate"], ["Inaccurate"]],
                )
            ],
            count_mode=True,
        )
        assert result["t1"]["c1"] == {"choice_counts": {"Accurate": 2, "Inaccurate": 1}}

    def test_count_mode_zero_avg_score_survives(self):
        """A real 0.0 average must not be blanked by a truthiness guard."""
        result = _trace_pivot(
            [_trace_row(avg_score=0.0, success_count=2, eval_count=2)],
            count_mode=True,
        )
        assert result["t1"]["c1"]["avg_score"] == 0.0

    def test_count_mode_all_errored_still_marks_error(self):
        result = _trace_pivot(
            [_trace_row(success_count=0, error_count=3, eval_count=3)],
            count_mode=True,
        )
        assert result["t1"]["c1"] == {"error": True}

    def test_count_mode_lifecycle_marker_preserved(self):
        """A (trace, config) with only pending/running rows keeps its marker."""
        result = _trace_pivot(
            [_trace_row(running_count=2, eval_count=2)],
            count_mode=True,
        )
        assert result["t1"]["c1"] == {"status": "running"}

    def test_count_mode_skipped_marker_carries_reason(self):
        result = _trace_pivot(
            [_trace_row(skipped_count=1, skipped_reason="filters", eval_count=1)],
            count_mode=True,
        )
        assert result["t1"]["c1"] == {
            "status": "skipped",
            "skipped_reason": "filters",
        }

    def test_default_mode_unchanged_passfail(self):
        """Backwards-compat: non-count callers still get pass_rate shapes."""
        result = _trace_pivot(
            [
                _trace_row(
                    pass_rate=75.0,
                    success_count=4,
                    eval_count=4,
                    pass_count=3,
                    fail_count=1,
                )
            ]
        )
        assert result["t1"]["c1"] == {
            "avg_score": None,
            "pass_rate": 75.0,
            "count": 4,
        }

    def test_default_mode_unchanged_choices_percentages(self):
        result = _trace_pivot(
            [
                _trace_row(
                    success_count=4,
                    eval_count=4,
                    str_lists=[["A"], ["A"], ["A"], ["B"]],
                )
            ]
        )
        assert result["t1"]["c1"] == {"per_choice": {"A": 75.0, "B": 25.0}}


class TestSpanPivotCountMode:
    def test_span_count_mode_passfail_counts(self):
        result = SpanListQueryBuilder.pivot_eval_results(
            [
                _span_row(
                    pass_rate=100.0,
                    success_count=1,
                    eval_count=1,
                    pass_count=1,
                )
            ],
            count_mode=True,
        )
        cell = result["s1"]["c1"]
        assert cell["pass_count"] == 1
        assert cell["fail_count"] == 0
        # The span cell is the lean shape — no trace-only keys.
        assert "pass_rate" not in cell
        assert "count" not in cell

    def test_span_count_mode_choice_counts(self):
        result = SpanListQueryBuilder.pivot_eval_results(
            [
                _span_row(
                    success_count=2,
                    eval_count=2,
                    str_lists=[["Good"], ["Bad"]],
                )
            ],
            count_mode=True,
        )
        assert result["s1"]["c1"] == {"choice_counts": {"Good": 1, "Bad": 1}}

    def test_span_count_mode_zero_avg_score_survives(self):
        """The legacy scalar path drops a 0.0 avg; count mode must not."""
        result = SpanListQueryBuilder.pivot_eval_results(
            [_span_row(avg_score=0.0, success_count=1, eval_count=1)],
            count_mode=True,
        )
        assert result["s1"]["c1"]["avg_score"] == 0.0

    def test_span_count_mode_error_and_marker_preserved(self):
        errored = _span_row(success_count=0, error_count=2, eval_count=2)
        pending = _span_row(observation_span_id="s2", pending_count=1, eval_count=1)
        result = SpanListQueryBuilder.pivot_eval_results(
            [errored, pending], count_mode=True
        )
        assert result["s1"]["c1"] == {"error": True}
        assert result["s2"]["c1"] == {"status": "pending"}

    def test_span_default_mode_unchanged(self):
        """Backwards-compat: scalar / per-choice-pct shapes for other callers."""
        result = SpanListQueryBuilder.pivot_eval_results(
            [
                _span_row(avg_score=0.8, success_count=1, eval_count=1),
                _span_row(
                    observation_span_id="s2",
                    success_count=2,
                    eval_count=2,
                    str_lists=[["A"], ["B"]],
                ),
            ]
        )
        assert result["s1"]["c1"] == 80.0
        assert result["s2"]["c1"] == {"A": 50.0, "B": 50.0}


class TestBuildEvalQueryCountColumns:
    """The count columns ride the same completed-row predicate as the
    aggregates, so counts can never disagree with the markers."""

    def test_trace_eval_query_selects_pass_fail_counts(self):
        builder = TraceListQueryBuilder(
            project_id="11111111-1111-1111-1111-111111111111",
            filters=[],
            page_number=0,
            page_size=10,
            eval_config_ids=["22222222-2222-2222-2222-222222222222"],
        )
        query, _params = builder.build_eval_query(["t1"])
        assert "AS pass_count" in query
        assert "AS fail_count" in query
        assert (
            "output_bool = 1 AND error = 0 AND ifNull(output_str, '') != 'ERROR'"
            in query
        )

    def test_span_eval_query_selects_pass_fail_counts(self):
        builder = SpanListQueryBuilder(
            project_id="11111111-1111-1111-1111-111111111111",
            filters=[],
            page_number=0,
            page_size=10,
            eval_config_ids=["22222222-2222-2222-2222-222222222222"],
        )
        query, _params = builder.build_eval_query(["s1"])
        assert "AS pass_count" in query
        assert "AS fail_count" in query


class _Template:
    def __init__(self, output=None, choices=None):
        self.config = {"output": output} if output else {}
        self.choices = choices or []


class _Config:
    def __init__(self, output=None, choices=None):
        self.eval_template = _Template(output, choices)


class TestEvalCountCell:
    def test_cell_passfail_returns_pass_fail_object(self):
        value = eval_count_cell(
            {"avg_score": None, "pass_count": 5, "fail_count": 2},
            _Config(output="Pass/Fail"),
        )
        assert value == {"pass": 5, "fail": 2}

    def test_cell_choices_zero_fills_all_labels(self):
        value = eval_count_cell(
            {"choice_counts": {"Accurate": 8}},
            _Config(output="choices", choices=["Accurate", "Inaccurate", "Unknown"]),
        )
        assert value == {"Accurate": 8, "Inaccurate": 0, "Unknown": 0}

    def test_cell_score_returns_average(self):
        value = eval_count_cell(
            {"avg_score": 87.5, "pass_count": 0, "fail_count": 0},
            _Config(output="score"),
        )
        assert value == 87.5

    def test_cell_score_zero_average_survives(self):
        value = eval_count_cell(
            {"avg_score": 0.0, "pass_count": 0, "fail_count": 0},
            _Config(output="score"),
        )
        assert value == 0.0

    def test_cell_error_marker_passes_through(self):
        assert eval_count_cell({"error": True}, _Config(output="Pass/Fail")) == {
            "error": True
        }

    def test_cell_lifecycle_marker_passes_through(self):
        """A running/pending/skipped marker must NOT become {"pass":0,"fail":0}."""
        marker = {"status": "running"}
        assert eval_count_cell(marker, _Config(output="Pass/Fail")) == marker
        skipped = {"status": "skipped", "skipped_reason": "filters"}
        assert eval_count_cell(skipped, _Config(output="choices")) == skipped

    def test_cell_non_dict_passthrough(self):
        assert eval_count_cell(42.0, _Config(output="score")) == 42.0
        assert eval_count_cell(None, _Config(output="score")) is None


@pytest.mark.parametrize(
    "output,choices,cell,expected",
    [
        ("Pass/Fail", [], {"pass_count": 0, "fail_count": 0}, {"pass": 0, "fail": 0}),
        ("choices", [], {"choice_counts": {"X": 3}}, {"X": 3}),
        (None, [], {"avg_score": 12.5}, 12.5),
    ],
)
def test_eval_count_cell_shapes(output, choices, cell, expected):
    assert eval_count_cell(cell, _Config(output=output, choices=choices)) == expected
