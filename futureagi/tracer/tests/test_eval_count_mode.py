"""Unit tests for count-mode eval aggregation shared by the trace / voice /
span list endpoints (list_traces_of_session, list_voice_calls,
list_spans_observe).

``pivot_eval_results(count_mode=True)`` returns raw appearance counts instead
of averages:
  * Pass/Fail  -> score cell carries exact ``pass_count`` / ``fail_count``
  * Choices    -> ``{"choice_counts": {label: n}}`` (one column, chip-style)

``count_mode=False`` (the default for every other caller) must keep the
historical avg / pass_rate / per_choice output unchanged. ``eval_count_cell``
turns a count-mode cell into the chip value the views render.
"""

from types import SimpleNamespace

from tracer.services.clickhouse.query_builders.span_list import (
    SpanListQueryBuilder,
)
from tracer.services.clickhouse.query_builders.trace_list import (
    TraceListQueryBuilder,
)
from tracer.utils.helper import eval_count_cell


def _cfg(output, choices=None):
    """Minimal CustomEvalConfig stand-in for eval_count_cell."""
    return SimpleNamespace(
        eval_template=SimpleNamespace(config={"output": output}, choices=choices or [])
    )


def _passfail_row(trace_id="t1", cfg="c1", pass_count=5, fail_count=4):
    success = pass_count + fail_count
    return {
        "trace_id": trace_id,
        "eval_config_id": cfg,
        "avg_score": None,
        "pass_rate": (100.0 * pass_count / success) if success else None,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "success_count": success,
        "error_count": 0,
        "eval_count": success,
        "str_lists": "[]",  # non-CHOICES rows come back as the literal '[]'
    }


def _choices_row(
    trace_id="t2", cfg="c2", labels=("Accurate", "Accurate", "Inaccurate")
):
    return {
        "trace_id": trace_id,
        "eval_config_id": cfg,
        "avg_score": None,
        "pass_rate": None,
        "pass_count": 0,
        "fail_count": 0,
        "success_count": len(labels),
        "error_count": 0,
        "eval_count": len(labels),
        "str_lists": [f'["{x}"]' for x in labels],
    }


def test_count_mode_passfail_returns_exact_counts():
    row = _passfail_row(pass_count=5, fail_count=4)
    out = TraceListQueryBuilder.pivot_eval_results(
        [row], list(row.keys()), count_mode=True
    )
    assert out["t1"]["c1"]["pass_count"] == 5
    assert out["t1"]["c1"]["fail_count"] == 4


def test_count_mode_choices_returns_label_counts():
    row = _choices_row(labels=("Accurate", "Accurate", "Inaccurate"))
    out = TraceListQueryBuilder.pivot_eval_results(
        [row], list(row.keys()), count_mode=True
    )
    assert out["t2"]["c2"] == {"choice_counts": {"Accurate": 2, "Inaccurate": 1}}


def test_default_mode_unchanged_passfail():
    """Without count_mode, Pass/Fail keeps pass_rate and exposes no counts."""
    row = _passfail_row(pass_count=5, fail_count=4)
    out = TraceListQueryBuilder.pivot_eval_results([row], list(row.keys()))
    cell = out["t1"]["c1"]
    assert "pass_count" not in cell
    assert cell["pass_rate"] == round(100.0 * 5 / 9, 2)


def test_default_mode_unchanged_choices_percentages():
    """Without count_mode, Choices keeps per-choice percentages."""
    row = _choices_row(labels=("Accurate", "Accurate", "Inaccurate"))
    out = TraceListQueryBuilder.pivot_eval_results([row], list(row.keys()))
    assert "per_choice" in out["t2"]["c2"]
    assert "choice_counts" not in out["t2"]["c2"]
    pc = out["t2"]["c2"]["per_choice"]
    assert pc["Accurate"] == 66.67 and pc["Inaccurate"] == 33.33


def test_count_mode_all_errored_still_marks_error():
    """An all-errored (trace, config) pair stays an explicit error marker."""
    row = {
        "trace_id": "t3",
        "eval_config_id": "c3",
        "avg_score": None,
        "pass_rate": None,
        "pass_count": 0,
        "fail_count": 0,
        "success_count": 0,
        "error_count": 3,
        "eval_count": 3,
        "str_lists": "[]",
    }
    out = TraceListQueryBuilder.pivot_eval_results(
        [row], list(row.keys()), count_mode=True
    )
    assert out["t3"]["c3"] == {"error": True}


# ---------------------------------------------------------------------------
# SpanListQueryBuilder.pivot_eval_results — same count-mode contract, dict rows.
# ---------------------------------------------------------------------------


def _span_passfail_row(span="s1", cfg="c1", pass_count=5, fail_count=4):
    success = pass_count + fail_count
    return {
        "observation_span_id": span,
        "eval_config_id": cfg,
        "avg_score": None,
        "pass_rate": (100.0 * pass_count / success) if success else None,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "success_count": success,
        "error_count": 0,
        "eval_count": success,
        "str_lists": "[]",
    }


def _span_choices_row(
    span="s2", cfg="c2", labels=("Accurate", "Accurate", "Inaccurate")
):
    return {
        "observation_span_id": span,
        "eval_config_id": cfg,
        "avg_score": None,
        "pass_rate": None,
        "pass_count": 0,
        "fail_count": 0,
        "success_count": len(labels),
        "error_count": 0,
        "eval_count": len(labels),
        "str_lists": [f'["{x}"]' for x in labels],
    }


def test_span_count_mode_passfail_counts():
    out = SpanListQueryBuilder.pivot_eval_results(
        [_span_passfail_row(pass_count=5, fail_count=4)], count_mode=True
    )
    assert out["s1"]["c1"]["pass_count"] == 5
    assert out["s1"]["c1"]["fail_count"] == 4


def test_span_count_mode_choice_counts():
    out = SpanListQueryBuilder.pivot_eval_results(
        [_span_choices_row()], count_mode=True
    )
    assert out["s2"]["c2"] == {"choice_counts": {"Accurate": 2, "Inaccurate": 1}}


def test_span_default_mode_unchanged():
    """Default span pivot stays a plain number (PF) and a per-choice dict."""
    out = SpanListQueryBuilder.pivot_eval_results(
        [_span_passfail_row(), _span_choices_row()]
    )
    assert isinstance(out["s1"]["c1"], (int, float))
    assert "choice_counts" not in out["s2"]["c2"]  # per-choice percentages


# ---------------------------------------------------------------------------
# eval_count_cell — shared chip-value mapper used by all three views.
# ---------------------------------------------------------------------------


def test_cell_passfail_returns_pass_fail_object():
    cell = {"avg_score": None, "pass_count": 5, "fail_count": 4}
    assert eval_count_cell(cell, _cfg("Pass/Fail")) == {"pass": 5, "fail": 4}


def test_cell_choices_zero_fills_all_labels():
    cell = {"choice_counts": {"Accurate": 8, "Inaccurate": 3}}
    cfg = _cfg("choices", choices=["Accurate", "Inaccurate", "Unknown"])
    assert eval_count_cell(cell, cfg) == {"Accurate": 8, "Inaccurate": 3, "Unknown": 0}


def test_cell_score_returns_average():
    cell = {"avg_score": 80.0, "pass_count": 0, "fail_count": 0}
    assert eval_count_cell(cell, _cfg("score")) == 80.0


def test_cell_non_dict_passthrough():
    assert eval_count_cell(None, _cfg("score")) is None
    assert eval_count_cell(42, _cfg("score")) == 42
