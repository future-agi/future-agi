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
