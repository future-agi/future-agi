import pytest
from completion_timing import completion_timing


@pytest.mark.parametrize("target", [60, 75])
@pytest.mark.parametrize("elapsed", [30, 90, 180])
def test_correct_completion_does_not_disguise_a_missed_latency_target(target, elapsed):
    result = completion_timing(elapsed, latency_target_seconds=target)
    assert result["completion_seconds"] == elapsed
    assert result["latency_target_met"] is (elapsed <= target)
    assert result["latency_target_seconds"] == target
    assert result["correctness_deadline_seconds"] == 180


@pytest.mark.parametrize("elapsed", [-1, 180.01, float("inf"), float("nan")])
def test_no_unbounded_or_invalid_completion_wait(elapsed):
    with pytest.raises(RuntimeError, match="correctness wait"):
        completion_timing(elapsed, latency_target_seconds=75)
