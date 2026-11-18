"""Pure projections used by Observe evaluation tiles.

These tests deliberately do not require Django or a ClickHouse fixture.  The
list projection counts every completed attempt; the detail projection selects
one completed attempt per span, so their totals are intentionally different.
"""

from tracer.services.eval_tiles import (
    build_eval_rollup,
    count_column_metadata,
    count_mode_cell,
    normalize_verdict,
    select_latest_completed,
    union_choice_labels,
)


def _row(**overrides):
    row = {
        "span_id": "span-1",
        "eval_config_id": "quality",
        "created_at": "2026-10-03T12:00:00Z",
        "log_id": "log-1",
        "status": "completed",
        "error": False,
        "output_bool": True,
        "eval_explanation": "selected explanation",
    }
    row.update(overrides)
    return row


def test_bool_count_cell_counts_every_completed_attempt_and_keeps_zero():
    rows = [
        _row(log_id="1", output_bool=True),
        _row(log_id="2", output_bool=True),
        _row(log_id="3", output_bool=True),
        _row(log_id="4", output_bool=False),
        _row(log_id="5", status="pending", output_bool=False),
    ]

    assert count_mode_cell(rows, "Pass/Fail", [], {}) == {"pass": 3, "fail": 1}
    assert count_mode_cell([_row(output_bool=True)], "Pass/Fail", [], {}) == {
        "pass": 1,
        "fail": 0,
    }


def test_choices_are_union_zero_filled_and_counted_once_per_result():
    rows = [
        _row(
            output_str_list=["safe", "safe", "unexpected"],
            output_bool=None,
        ),
        _row(
            log_id="2",
            output_str_list='["safe", "unexpected", "unexpected"]',
            output_bool=None,
        ),
    ]

    assert union_choice_labels(["safe", "other"], ["unexpected", "safe"]) == [
        "safe",
        "other",
        "unexpected",
    ]
    assert count_mode_cell(rows, "choices", ["safe", "other"], {}) == {
        "safe": 2,
        "other": 0,
        "unexpected": 2,
    }


def test_numeric_count_cell_keeps_zero_drops_non_finite_and_scales():
    rows = [
        _row(output_float=0.0, output_bool=None),
        _row(log_id="2", output_float=0.4, output_bool=None),
        _row(log_id="3", output_float=float("nan"), output_bool=None),
        _row(log_id="4", output_float=float("inf"), output_bool=None),
        _row(log_id="5", output_float=None, output_bool=None),
    ]

    assert count_mode_cell(rows, "score", [], {}) == 20.0
    assert (
        count_mode_cell([_row(output_float=0.0, output_bool=None)], "score", [], {})
        == 0.0
    )


def test_markers_win_when_a_config_has_no_completed_attempt():
    assert count_mode_cell([_row(error=True)], "Pass/Fail", [], {}) == {"error": True}
    assert count_mode_cell(
        [_row(status="skipped", skipped_reason="budget")], "Pass/Fail", [], {}
    ) == {
        "status": "skipped",
        "skipped_reason": "budget",
    }


def test_select_latest_completed_uses_created_at_then_stable_log_id():
    selected = select_latest_completed(
        [
            _row(created_at="2026-10-03T11:00:00Z", log_id="1", output_bool=False),
            _row(created_at="2026-10-03T12:00:00Z", log_id="1", output_bool=True),
            _row(
                created_at="2026-10-03T13:00:00Z",
                log_id="9",
                error=True,
                eval_explanation="later failure",
            ),
        ]
    )
    assert selected["output_bool"] is True
    assert selected["eval_explanation"] == "selected explanation"

    tied = select_latest_completed(
        [
            _row(created_at="2026-10-03T12:00:00Z", log_id="a", output_bool=False),
            _row(created_at="2026-10-03T12:00:00Z", log_id="b", output_bool=True),
        ]
    )
    assert tied["log_id"] == "b"
    assert select_latest_completed([_row(status=None)]) is not None
    assert select_latest_completed([_row(status="")]) is not None
    assert select_latest_completed([_row(status="running")]) is None


def test_detail_rollup_uses_latest_per_span_while_list_counts_all_attempts():
    rows = [
        _row(span_id="root", log_id="1", output_bool=False),
        _row(
            span_id="root",
            log_id="2",
            created_at="2026-10-03T13:00:00Z",
            output_bool=True,
            eval_explanation="root success",
        ),
        _row(
            span_id="child",
            log_id="3",
            created_at="2026-10-03T14:00:00Z",
            output_bool=False,
            eval_explanation="child failure",
        ),
    ]
    configs = {
        "quality": {
            "name": "Quality",
            "output_type": "Pass/Fail",
            "template_type": None,
            "target_type": "span",
            "choices_map": {},
        }
    }

    assert count_mode_cell(rows, "Pass/Fail", [], {}) == {"pass": 1, "fail": 2}

    rollups = build_eval_rollup(rows, configs, "root")
    root_eval = rollups["root"]["evals"][0]
    assert root_eval["aggregate"] == {"pass": 1, "fail": 1}
    assert [(row["span_id"], row["value"]) for row in root_eval["spans"]] == [
        ("root", "pass"),
        ("child", "fail"),
    ]
    assert root_eval["spans"][0]["explanation"] == "root success"
    child_eval = rollups["child"]["evals"][0]
    assert child_eval["aggregate"] == {"pass": 0, "fail": 1}
    assert [row["span_id"] for row in child_eval["spans"]] == ["child"]


def test_rollup_preserves_unknown_choice_with_neutral_tone_and_error_only_row():
    configs = {
        "labels": {
            "name": "Labels",
            "output_type": "choices",
            "target_type": "trace",
            "choices": ["safe", "other"],
            "choices_map": {"safe": {"tone": "success"}},
        }
    }
    rollups = build_eval_rollup(
        [
            _row(
                eval_config_id="labels",
                output_bool=None,
                output_str_list=["safe", "unexpected"],
            ),
            _row(
                span_id="child",
                eval_config_id="labels",
                log_id="2",
                error=True,
                eval_explanation="execution failed",
            ),
        ],
        configs,
        "root",
    )

    root_eval = rollups["root"]["evals"][0]
    assert root_eval["aggregate"] == {"safe": 1, "other": 0, "unexpected": 1}
    assert root_eval["choices_map"]["unexpected"] == "neutral"
    child_row = rollups["child"]["evals"][0]["spans"][0]
    assert child_row["value"] is None
    assert child_row["error"] is True
    assert child_row["explanation"] == "execution failed"


def test_rollup_reads_choices_under_the_detail_normalized_output_type():
    # Trace detail labels configs with derive_output_type(), which names a
    # choices template "deterministic" (and Pass/Fail "pass_fail").
    configs = {
        "tone": {
            "name": "Tone",
            "output_type": "deterministic",
            "choices": ["neutral"],
        },
        "helpful": {"name": "Helpful", "output_type": "pass_fail"},
    }
    rollups = build_eval_rollup(
        [
            _row(
                eval_config_id="tone",
                output_bool=None,
                output_str_list=["apologetic"],
            ),
            _row(eval_config_id="helpful", log_id="2", output_bool=False),
        ],
        configs,
        "root",
    )

    tone, helpful = rollups["root"]["evals"]
    assert tone["aggregate"] == {"neutral": 0, "apologetic": 1}
    assert tone["spans"][0]["value"] == ["apologetic"]
    assert helpful["aggregate"] == {"pass": 0, "fail": 1}


def test_normalize_verdict_maps_clickhouse_integer_booleans():
    assert normalize_verdict(" Pass ") == "pass"
    assert normalize_verdict("fail") == "fail"
    assert normalize_verdict(True) == "pass"
    assert normalize_verdict(False) == "fail"
    assert normalize_verdict(1) == "pass"
    assert normalize_verdict(0) == "fail"
    assert normalize_verdict(0.75) is None


def test_count_column_metadata_reads_target_types_and_observed_choices():
    rows = [
        {"eval_config_id": "tone", "target_type": "span"},
        {"eval_config_id": "gap", "target_type": "trace"},
        {"eval_config_id": "helpful", "target_type": "session"},
    ]
    eval_map = {
        "trace-1": {
            "tone": {"neutral": 1, "apologetic": 0},
            "helpful": {"pass": 1, "fail": 0},
        },
        "trace-2": {"tone": {"neutral": 0, "joy": 1}, "gap": {"error": True}},
    }

    target_types, observed = count_column_metadata(
        rows,
        eval_map,
        {"tone": "choices", "gap": "choices", "helpful": "Pass/Fail"},
    )

    assert target_types == {"tone": "span", "gap": "trace"}
    assert observed == {"tone": ["neutral", "apologetic", "joy"]}

    mixed_targets, _ = count_column_metadata(
        [
            {"eval_config_id": "tone", "target_type": "span"},
            {"eval_config_id": "tone", "target_type": "trace"},
        ],
        {},
        {"tone": "choices"},
    )
    assert mixed_targets == {}
