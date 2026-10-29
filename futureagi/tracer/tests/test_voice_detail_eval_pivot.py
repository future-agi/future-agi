"""The voice-call drawer reads each span's newest completed eval result."""

import pytest

from tracer.views.trace import _pivot_voice_detail_eval_rows


def _row(log_id, minutes, **values):
    return {
        "span_id": "root",
        "eval_config_id": "helpful",
        "status": "completed",
        "error": 0,
        "created_at": f"2026-10-01T12:{minutes:02d}:00",
        "log_id": log_id,
        **values,
    }


def test_a_rerun_replaces_the_earlier_result_for_its_span():
    pivoted = _pivot_voice_detail_eval_rows(
        [
            _row("a", 1, output_bool=True),
            _row("b", 2, output_bool=False),
            _row("c", 3, error=1, output_str="ERROR"),
        ]
    )

    assert pivoted["helpful"]["pass_rate"] == 0.0
    assert pivoted["helpful"]["count"] == 1


def test_each_span_still_counts_once():
    pivoted = _pivot_voice_detail_eval_rows(
        [
            _row("a", 1, output_bool=True),
            _row("b", 2, output_bool=False, span_id="turn-1"),
            {**_row("c", 1, output_str_list='["clear"]'), "eval_config_id": "tone"},
            {**_row("d", 2, output_str_list='["unclear"]'), "eval_config_id": "tone"},
        ]
    )

    assert pivoted["helpful"]["pass_rate"] == 50.0
    assert pivoted["tone"]["per_choice"] == {"unclear": 100.0}


@pytest.mark.parametrize("verdict,label", [(1, "Pass"), (0, "Fail")])
def test_bool_result_is_not_reinterpreted_as_a_choice(verdict, label):
    pivoted = _pivot_voice_detail_eval_rows(
        [_row("a", 1, output_bool=verdict, output_str=label, output_str_list="[]")],
        output_types={"helpful": "pass_fail"},
    )
    assert pivoted["helpful"]["pass_rate"] == verdict * 100.0
    assert "per_choice" not in pivoted["helpful"]


def test_scored_choice_stays_numeric_when_template_output_is_percentage():
    pivoted = _pivot_voice_detail_eval_rows(
        [
            _row(
                "a",
                1,
                output_float=0.7,
                output_bool=None,
                output_str='{"score": 0.7, "choice": "safe"}',
                output_str_list="[]",
            )
        ],
        output_types={"helpful": "percentage"},
    )
    assert pivoted["helpful"]["avg_score"] == 70.0
    assert "per_choice" not in pivoted["helpful"]


@pytest.mark.parametrize("value", ["safe", '{"choice": "safe"}', "{'choice': 'safe'}"])
def test_declared_choice_template_accepts_legacy_scalar_results(value):
    pivoted = _pivot_voice_detail_eval_rows(
        [_row("a", 1, output_str=value, output_str_list="[]")],
        output_types={"helpful": "deterministic"},
    )
    assert pivoted["helpful"]["per_choice"] == {"safe": 100.0}


def test_unrecognized_legacy_choice_object_does_not_create_a_none_label():
    pivoted = _pivot_voice_detail_eval_rows(
        [_row("a", 1, output_str='{"score": 0.7}', output_str_list="[]")],
        output_types={"helpful": "deterministic"},
    )
    assert "per_choice" not in pivoted["helpful"]
