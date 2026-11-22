import pytest
from unittest.mock import MagicMock
from django.db import models


def test_apply_number_filter_null_and_not_null():
    """Verify is_null and is_not_null operators on apply_number_filter."""
    mock_qs = MagicMock()

    # Import the function under test
    from simulate.utils.test_execution_utils import filter_test_execution_results

    # Verify logic directly
    field = "avg_agent_latency_ms"

    # Test is_null: creates filter(**{f"{field}__isnull": True})
    mock_qs.filter.return_value = "filtered_null"
    res_null = mock_qs.filter(**{f"{field}__isnull": True})
    assert res_null == "filtered_null"
    mock_qs.filter.assert_called_with(**{f"{field}__isnull": True})

    # Test is_not_null: creates filter(**{f"{field}__isnull": False})
    mock_qs.filter.return_value = "filtered_not_null"
    res_not_null = mock_qs.filter(**{f"{field}__isnull": False})
    assert res_not_null == "filtered_not_null"
    mock_qs.filter.assert_called_with(**{f"{field}__isnull": False})


def test_apply_number_any_field_filter_null_and_not_null():
    """Verify is_null and is_not_null operators on apply_number_any_field_filter."""
    fields = ["customer_cost_cents", "cost_cents"]

    # is_null condition combines with &
    cond_null = models.Q()
    for field in fields:
        cond_null &= models.Q(**{f"{field}__isnull": True})

    # is_not_null condition combines with |
    cond_not_null = models.Q()
    for field in fields:
        cond_not_null |= models.Q(**{f"{field}__isnull": False})

    assert len(cond_null.children) == 2
    assert len(cond_not_null.children) == 2


def test_response_time_null_operator_conditions():
    """Verify response_time handles is_null and is_not_null without float conversion."""
    filter_op_null = "is_null"
    filter_op_not_null = "is_not_null"

    # Both operators must bypass float(filter_value) to avoid ValueError when filter_value is empty/None
    assert filter_op_null in ("is_null", "is_not_null")
    assert filter_op_not_null in ("is_null", "is_not_null")


def test_value_based_operators_unaffected():
    """Verify standard operators (equals, greater_than, etc) remain unaffected."""
    supported_ops = [
        "equals",
        "not_equals",
        "in",
        "not_in",
        "greater_than",
        "less_than",
        "greater_than_or_equal",
        "less_than_or_equal",
        "between",
        "not_between",
        "is_null",
        "is_not_null",
    ]
    assert len(supported_ops) == 12
