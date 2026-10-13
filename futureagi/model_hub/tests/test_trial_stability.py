"""Repeated trials reveal dispersion that pooled averages hide."""

import json
from dataclasses import asdict
from sys import float_info

import pytest

from model_hub.services.experiment_analysis import RunItemRecord
from model_hub.services.trial_stability import summarize_trial_stability


def _trials(example_id, values):
    return [
        RunItemRecord(example_id, trial_number=index, score=value)
        for index, value in enumerate(values, 1)
    ]


def test_equal_means_preserve_per_example_dispersion_and_paired_deltas():
    result = summarize_trial_stability(
        _trials("x", [0, 1]) + _trials("y", [10, 10]),
        _trials("x", [0.5, 0.5]) + _trials("y", [10, 10]),
        expected_identities=[("y", 2), ("x", 2), ("y", 1), ("x", 1)],
    )
    assert [row.example_id for row in result] == ["x", "y"]
    first, second = result
    assert first.expected_trial_count == 2
    assert first.baseline.score_average == first.candidate.score_average == 0.5
    assert first.baseline.score_stddev == 0.5
    assert first.candidate.score_stddev == 0
    assert first.baseline.score_min == 0
    assert first.baseline.score_max == 1
    assert first.paired.paired_trial_count == 2
    assert first.paired.delta_average == 0
    assert first.paired.delta_stddev == 0.5
    assert second.baseline.score_stddev == second.candidate.score_stddev == 0


def test_missing_both_and_one_sided_trials_remain_in_the_denominators():
    result = summarize_trial_stability(
        _trials("x", [0.8]),
        [],
        expected_identities=[("x", 1), ("x", 2), ("absent", 1)],
    )
    absent, partial = result
    assert absent.expected_trial_count == 1
    assert absent.baseline.recorded_trial_count == 0
    assert absent.baseline.missing_trial_count == 1
    assert absent.baseline.score_average is None
    assert absent.baseline.score_stddev is None
    assert absent.baseline == absent.candidate
    assert partial.expected_trial_count == 2
    assert partial.baseline.recorded_trial_count == 1
    assert partial.baseline.missing_trial_count == 1
    assert partial.baseline.assessed_trial_count == 1
    assert partial.baseline.score_average == 0.8
    assert partial.baseline.score_stddev is None
    assert partial.candidate.missing_trial_count == 2
    assert partial.paired.paired_trial_count == 0
    assert partial.paired.delta_average is partial.paired.delta_stddev is None


@pytest.mark.parametrize(
    "item",
    [
        RunItemRecord("x", status="failed", score=0.9),
        RunItemRecord("x", status="running", score=0.9),
        RunItemRecord("x", score=float("nan")),
        RunItemRecord("x", score=True),
        RunItemRecord("x", scores={"score": {"status": "unknown", "value": 0.9}}),
        RunItemRecord("x", scores={"score": {"status": "unsupported", "value": 0.9}}),
        RunItemRecord("x", scores={"score": {"status": "not_assessed", "value": 0.9}}),
        RunItemRecord("x", scores={"score": {"status": "error", "value": 0.9}}),
    ],
)
def test_ineligible_scores_do_not_invent_repeatability_or_paired_deltas(item):
    result = summarize_trial_stability(
        _trials("x", [0.1, 0.1]),
        [item, RunItemRecord("x", trial_number=2, score=0.7)],
        expected_identities=[("x", 1), ("x", 2)],
    )[0]
    assert result.candidate.recorded_trial_count == 2
    assert result.candidate.missing_trial_count == 0
    assert result.candidate.assessed_trial_count == 1
    assert result.candidate.score_stddev is None
    assert result.paired.paired_trial_count == 1
    assert result.paired.delta_average == pytest.approx(0.6)
    assert result.paired.delta_stddev is None


def test_duplicate_trial_is_recorded_but_never_assessed_or_paired():
    result = summarize_trial_stability(
        [RunItemRecord("x", score=0.2), RunItemRecord("x", score=0.8)],
        [RunItemRecord("x", score=0.9)],
        expected_identities=[("x", 1)],
    )[0]
    assert result.baseline.recorded_trial_count == 1
    assert result.baseline.assessed_trial_count == 0
    assert result.baseline.score_average is None
    assert result.paired.paired_trial_count == 0


def test_extreme_finite_scores_do_not_overflow_stddev_or_json():
    result = summarize_trial_stability(
        _trials("x", [-float_info.max, float_info.max]),
        _trials("x", [float_info.max, -float_info.max]),
        expected_identities=[("x", 1), ("x", 2)],
    )[0]
    assert result.baseline.score_average == 0
    assert result.baseline.score_stddev == float_info.max
    assert result.paired.paired_trial_count == 0
    assert result.paired.delta_average is None
    json.dumps(asdict(result), allow_nan=False)


@pytest.mark.parametrize("values", [[0, 0], [0.5, 0.5], [1e-300, 3e-300]])
def test_zero_and_very_small_spreads_remain_finite(values):
    result = summarize_trial_stability(
        _trials("x", values),
        _trials("x", values),
        expected_identities=[("x", 1), ("x", 2)],
    )[0]
    assert result.baseline.score_stddev == (values[1] - values[0]) / 2
    assert result.paired.delta_stddev == 0


def test_selected_named_metric_and_raw_delta_direction_are_preserved():
    result = summarize_trial_stability(
        [RunItemRecord("x", score=100, scores={"cost": 10})],
        [RunItemRecord("x", score=0, scores={"cost": 5})],
        expected_identities=[("x", 1)],
        metric="cost",
    )[0]
    assert result.baseline.score_average == 10
    assert result.candidate.score_average == 5
    assert result.paired.delta_average == -5


def test_empty_plan_and_invalid_plan_evidence():
    assert summarize_trial_stability([], [], expected_identities=[]) == ()
    with pytest.raises(ValueError, match="unique"):
        summarize_trial_stability([], [], expected_identities=[("x", 1), ("x", 1)])
    with pytest.raises(ValueError, match="outside"):
        summarize_trial_stability(_trials("x", [1]), [], expected_identities=[])
    with pytest.raises(ValueError, match="trial_number"):
        summarize_trial_stability([], [], expected_identities=[("x", 0)])
