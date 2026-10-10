import pytest
from agentic_eval.core_evals.fi_evals.function.functions import calculate_numeric_similarity


def test_identical_positive_numbers():
    result = calculate_numeric_similarity("100", "100")
    assert result["result"] == 1.0


def test_identical_negative_numbers():
    result = calculate_numeric_similarity("-50", "-50")
    assert result["result"] == 1.0


def test_both_zero():
    result = calculate_numeric_similarity("0", "0")
    assert result["result"] == 1.0


def test_negative_numbers_normalization():
    result = calculate_numeric_similarity("-5", "-10")
    # diff = 5, max_abs = 10 -> similarity = 0.5
    assert result["result"] == 0.5
    assert 0.0 <= result["result"] <= 1.0


def test_divergent_numbers_clamped_to_zero():
    result = calculate_numeric_similarity("100", "-50")
    # diff = 150, max_abs = 100 -> clamped to 0.0
    assert result["result"] == 0.0


def test_fractional_numbers():
    result = calculate_numeric_similarity("0.5", "1.0")
    # diff = 0.5, max_abs = 1.0 -> similarity = 0.5
    assert result["result"] == 0.5


def test_numbers_in_text():
    result = calculate_numeric_similarity("Estimated 42.0 units", "Target is 42.0 units")
    assert result["result"] == 1.0


def test_invalid_non_numeric_input():
    result = calculate_numeric_similarity("no numbers here", "still nothing")
    assert result["result"] == 0.0
    assert "Cannot calculate numeric similarity" in result["reason"]
