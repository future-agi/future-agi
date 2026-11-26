"""Regressions for explicit zero image usage."""

from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost


def test_zero_generated_images_remains_zero():
    cost = calculate_total_cost("dall-e-3", {"images_generated": 0})
    assert cost["total_cost"] == 0
    assert cost["pricing_source"] == "available_models"
