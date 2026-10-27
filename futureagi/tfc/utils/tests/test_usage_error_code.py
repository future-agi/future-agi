"""
Unit tests for get_usage_error_code.

get_usage_error_code stamps a billing/usage error_code onto per-cell eval
errors so the frontend upgrade banner (ErrorCellRenderer USAGE_LIMIT_CTA) can
render an actionable "Upgrade" affordance instead of a bare "Error".

Run with: futureagi/bin/test tfc/utils/tests/test_usage_error_code.py -v
"""

import pytest

from tfc.utils.error_codes import get_usage_error_code


class TestGetUsageErrorCode:
    """Tests for get_usage_error_code text-matching detection."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "error",
        [
            Exception("rate_limited"),
            Exception("Rate limit reached. Please try again later."),
            Exception("Too many requests"),
            Exception("Request failed with status 429"),
            Exception("RATE LIMITED by upstream"),
        ],
    )
    def test_detects_rate_limited(self, error):
        assert get_usage_error_code(error) == "RATE_LIMITED"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "error",
        [
            Exception("insufficient_credits"),
            Exception("Insufficient credits. Please recharge your account."),
            Exception("INSUFFICIENT CREDITS"),
        ],
    )
    def test_detects_free_tier_limit(self, error):
        assert get_usage_error_code(error) == "FREE_TIER_LIMIT"

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "error",
        [
            Exception("Something unrelated went wrong"),
            Exception("Evaluation failed. Please contact Future AGI support."),
            ValueError("FAILED_TO_PROCESS_EVALUATION"),
            Exception(""),
        ],
    )
    def test_returns_none_for_non_usage_errors(self, error):
        assert get_usage_error_code(error) is None

    @pytest.mark.unit
    def test_reads_status_from_valueerror_second_arg(self):
        """ValueError(message, status) surfaces the status in the haystack."""
        error = ValueError("Upstream call failed", "rate_limited")
        assert get_usage_error_code(error) == "RATE_LIMITED"

    @pytest.mark.unit
    def test_valueerror_message_only_still_matches(self):
        error = ValueError("insufficient credits for this request")
        assert get_usage_error_code(error) == "FREE_TIER_LIMIT"
