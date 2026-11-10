"""Configuration gates before any durable or paid grouping work."""

import uuid
from unittest.mock import patch

import pytest
from django.test import override_settings

from tracer.services.grouping.control import _eligible_project


@pytest.mark.parametrize("invalid_cap", ["0", "not-a-number"])
@override_settings(
    ERROR_FEED_GROUPING_ENABLED=True,
    ERROR_FEED_GROUPING_ALL_PROJECTS=True,
    ERROR_FEED_GROUPING_BUDGET_ENFORCED=True,
    ERROR_FEED_GROUPING_WORK_BUDGET_USD="10",
    ERROR_FEED_GROUPING_TENANT_BUDGET_USD="10",
)
def test_enforced_budget_requires_valid_positive_caps(invalid_cap):
    with patch("tracer.services.grouping.control.is_oss", return_value=False):
        with override_settings(ERROR_FEED_GROUPING_PROJECT_BUDGET_USD=invalid_cap):
            assert not _eligible_project(uuid.uuid4())
        with override_settings(ERROR_FEED_GROUPING_PROJECT_BUDGET_USD="10"):
            assert _eligible_project(uuid.uuid4())


@override_settings(
    ERROR_FEED_GROUPING_ENABLED=True,
    ERROR_FEED_GROUPING_ALL_PROJECTS=True,
    ERROR_FEED_GROUPING_BUDGET_ENFORCED=False,
)
def test_oss_never_eligible_even_with_budget_enforcement_disabled():
    with patch("tracer.services.grouping.control.is_oss", return_value=True):
        assert not _eligible_project(uuid.uuid4())
