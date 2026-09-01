"""
Phase 2: Metering (check_usage) Tests

Tests the pre-check function that gates billable actions.
Unit tests mock Redis. Integration tests need real Redis.
"""

import os
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings as _settings
from ee.usage.schemas.events import CheckResult
from ee.usage.services.config import BillingConfig

# billing.yaml ships only with the private cloud overlay; these tests
# resolve real call types/plans from it. Missing-file behavior is covered
# by test_billing_config_missing.py.
pytestmark = pytest.mark.skipif(
    not os.path.exists(
        getattr(
            _settings,
            "BILLING_CONFIG_PATH",
            os.path.join(_settings.BASE_DIR, "billing.yaml"),
        )
    ),
    reason="billing.yaml ships only with the private cloud overlay",
)

# ---------------------------------------------------------------------------
# Unit tests (no Redis needed — mocked)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckUsageUnit:
    """Unit tests for check_usage logic. Redis is mocked."""

    @pytest.fixture(autouse=True)
    def _active_billing_status(self):
        with patch(
            "ee.usage.services.metering._get_cached_billing_status",
            return_value="active",
        ):
            yield

    def test_returns_check_result(self):
        """check_usage always returns a CheckResult."""
        with (
            patch("ee.usage.services.metering.get_redis") as mock_redis,
            patch("ee.usage.services.metering._get_cached_plan", return_value="payg"),
        ):
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.get.return_value = None  # no pause flag

            from ee.usage.services.metering import check_usage

            result = check_usage("org-1", "turing_large_evaluator")
            assert isinstance(result, CheckResult)

    def test_paid_plan_always_allowed(self):
        """PAYG+ plans always pass (soft cap)."""
        with (
            patch("ee.usage.services.metering.get_redis") as mock_redis,
            patch("ee.usage.services.metering._get_cached_plan", return_value="boost"),
        ):
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.get.return_value = None  # no pause

            from ee.usage.services.metering import check_usage

            result = check_usage("org-1", "turing_large_evaluator")
            assert result.allowed is True

    def test_free_plan_under_limit_allowed(self):
        """Free plan under the limit → allowed."""
        with (
            patch("ee.usage.services.metering.get_redis") as mock_redis,
            patch("ee.usage.services.metering._get_cached_plan", return_value="free"),
            patch("ee.usage.services.metering._get_lua_sha", return_value="fake_sha"),
        ):
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.evalsha.return_value = 500  # current usage = 500
            mock_r.get.return_value = None  # no pause

            from ee.usage.services.metering import check_usage

            result = check_usage("org-1", "turing_large_evaluator")
            assert result.allowed is True

    def test_free_plan_over_limit_denied(self):
        """Free plan over limit → denied with FREE_TIER_LIMIT."""
        with (
            patch("ee.usage.services.metering.get_redis") as mock_redis,
            patch("ee.usage.services.metering._get_cached_plan", return_value="free"),
            patch("ee.usage.services.metering._get_lua_sha", return_value="fake_sha"),
        ):
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.evalsha.return_value = -1  # over limit
            mock_r.get.side_effect = lambda k: (
                b"2500" if k.startswith("usage:") else None
            )

            from ee.usage.services.metering import check_usage

            result = check_usage("org-1", "turing_large_evaluator")
            assert result.allowed is False
            assert result.error_code == "FREE_TIER_LIMIT"

    def test_denied_has_upgrade_cta(self):
        """Denied result includes upgrade CTA."""
        with (
            patch("ee.usage.services.metering.get_redis") as mock_redis,
            patch("ee.usage.services.metering._get_cached_plan", return_value="free"),
            patch("ee.usage.services.metering._get_lua_sha", return_value="fake_sha"),
        ):
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.evalsha.return_value = -1
            mock_r.get.side_effect = lambda k: (
                b"2500" if k.startswith("usage:") else None
            )

            from ee.usage.services.metering import check_usage

            result = check_usage("org-1", "turing_large_evaluator")
            assert result.upgrade_cta is not None
            assert result.upgrade_cta.plan == "payg"

    def test_denied_has_dimension(self):
        """Denied result includes the dimension that was checked."""
        with (
            patch("ee.usage.services.metering.get_redis") as mock_redis,
            patch("ee.usage.services.metering._get_cached_plan", return_value="free"),
            patch("ee.usage.services.metering._get_lua_sha", return_value="fake_sha"),
        ):
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.evalsha.return_value = -1
            mock_r.get.side_effect = lambda k: (
                b"2500" if k.startswith("usage:") else None
            )

            from ee.usage.services.metering import check_usage

            result = check_usage("org-1", "turing_large_evaluator")
            assert result.dimension == "ai_credits"

    def test_budget_pause_blocks_usage(self):
        """Budget pause flag → BUDGET_PAUSED."""
        with (
            patch("ee.usage.services.metering.get_redis") as mock_redis,
            patch("ee.usage.services.metering._get_cached_plan", return_value="payg"),
        ):
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            # pause flag is set for ai_credits
            mock_r.get.side_effect = lambda k: (
                "1" if "pause:" in k and "ai_credits" in k else None
            )

            from ee.usage.services.metering import check_usage

            result = check_usage("org-1", "turing_large_evaluator")
            assert result.allowed is False
            assert result.error_code == "BUDGET_PAUSED"

    def test_budget_pause_only_affects_paused_dimension(self):
        """Pause on ai_credits doesn't block gateway_requests."""
        with (
            patch("ee.usage.services.metering.get_redis") as mock_redis,
            patch("ee.usage.services.metering._get_cached_plan", return_value="payg"),
        ):
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            # Only ai_credits is paused, not gateway_requests
            mock_r.get.side_effect = lambda k: (
                "1" if "pause:" in k and "ai_credits" in k else None
            )

            from ee.usage.services.metering import check_usage

            result = check_usage("org-1", "gateway_request")
            assert result.allowed is True

    def test_resolves_turing_large_to_ai_credits(self):
        """Event type resolution from billing.yaml."""
        with (
            patch("ee.usage.services.metering.get_redis") as mock_redis,
            patch("ee.usage.services.metering._get_cached_plan", return_value="payg"),
        ):
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.get.return_value = None

            from ee.usage.services.metering import check_usage

            result = check_usage("org-1", "turing_large_evaluator")
            assert result.dimension == "ai_credits"

    def test_resolves_observe_add_to_storage(self):
        with (
            patch("ee.usage.services.metering.get_redis") as mock_redis,
            patch("ee.usage.services.metering._get_cached_plan", return_value="payg"),
        ):
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.get.return_value = None

            from ee.usage.services.metering import check_usage

            result = check_usage("org-1", "observe_add", amount=1024)
            assert result.dimension == "storage"

    def test_unknown_event_type_allows(self):
        """Unknown event types are allowed (logged for investigation)."""
        with (
            patch("ee.usage.services.metering.get_redis") as mock_redis,
            patch("ee.usage.services.metering._get_cached_plan", return_value="payg"),
        ):
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.get.return_value = None

            from ee.usage.services.metering import check_usage

            result = check_usage("org-1", "nonexistent_type_xyz")
            assert result.allowed is True


@pytest.mark.unit
class TestBillingStatusGate:
    """Unpaid (Stripe retries exhausted) blocks billable usage; past_due stays soft."""

    def test_unpaid_status_blocks_usage(self):
        with (
            patch("ee.usage.services.metering.get_redis") as mock_redis,
            patch(
                "ee.usage.services.metering._get_cached_billing_status",
                return_value="unpaid",
            ),
        ):
            mock_redis.return_value = MagicMock()

            from ee.usage.services.metering import check_usage

            result = check_usage("org-1", "turing_large_evaluator")
            assert result.allowed is False
            assert result.error_code == "PAYMENT_REQUIRED"

    def test_past_due_status_does_not_block(self):
        with (
            patch("ee.usage.services.metering.get_redis") as mock_redis,
            patch(
                "ee.usage.services.metering._get_cached_billing_status",
                return_value="past_due",
            ),
            patch("ee.usage.services.metering._get_cached_plan", return_value="payg"),
        ):
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.get.return_value = None

            from ee.usage.services.metering import check_usage

            result = check_usage("org-1", "turing_large_evaluator")
            assert result.allowed is True
