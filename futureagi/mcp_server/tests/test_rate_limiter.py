"""Tests for MCP Server rate limiter."""

import time
from unittest.mock import MagicMock, patch

import pytest

from mcp_server.exceptions import RateLimitExceededError
from mcp_server.constants import RATE_LIMITS
from mcp_server.rate_limiter import check_rate_limit, get_rate_limit_tier


class TestGetRateLimitTier:
    """Tests for get_rate_limit_tier()."""

    @patch("ee.usage.models.usage.OrganizationSubscription")
    def test_returns_free_when_no_subscription(self, mock_cls):
        """Returns 'free' when no OrganizationSubscription exists."""

        class _DoesNotExist(Exception):
            pass

        mock_cls.DoesNotExist = _DoesNotExist
        mock_cls.objects.select_related.return_value.get.side_effect = _DoesNotExist()
        org = MagicMock()
        assert get_rate_limit_tier(org) == "free"

    @patch("ee.usage.models.usage.OrganizationSubscription")
    def test_returns_pro_for_basic_tier(self, _mock_cls):
        """Maps 'basic' subscription tier to 'pro' rate limit tier."""
        try:
            from ee.usage.models.usage import OrganizationSubscription
        except ImportError:
            OrganizationSubscription = None

        mock_sub = MagicMock()
        mock_sub.subscription_tier.name = "basic"
        OrganizationSubscription.objects.select_related.return_value.get.return_value = (
            mock_sub
        )
        org = MagicMock()
        assert get_rate_limit_tier(org) == "pro"

    @patch("ee.usage.models.usage.OrganizationSubscription")
    def test_returns_pro_for_basic_yearly_tier(self, _mock_cls):
        """Maps 'basic_yearly' subscription tier to 'pro' rate limit tier."""
        try:
            from ee.usage.models.usage import OrganizationSubscription
        except ImportError:
            OrganizationSubscription = None

        mock_sub = MagicMock()
        mock_sub.subscription_tier.name = "basic_yearly"
        OrganizationSubscription.objects.select_related.return_value.get.return_value = (
            mock_sub
        )
        org = MagicMock()
        assert get_rate_limit_tier(org) == "pro"

    @patch("ee.usage.models.usage.OrganizationSubscription")
    def test_returns_enterprise_for_custom_tier(self, _mock_cls):
        """Maps 'custom' subscription tier to 'enterprise' rate limit tier."""
        try:
            from ee.usage.models.usage import OrganizationSubscription
        except ImportError:
            OrganizationSubscription = None

        mock_sub = MagicMock()
        mock_sub.subscription_tier.name = "custom"
        OrganizationSubscription.objects.select_related.return_value.get.return_value = (
            mock_sub
        )
        org = MagicMock()
        assert get_rate_limit_tier(org) == "enterprise"

    @patch("ee.usage.models.usage.OrganizationSubscription")
    def test_returns_free_for_unknown_tier(self, _mock_cls):
        """Falls back to 'free' for unknown tier names."""
        try:
            from ee.usage.models.usage import OrganizationSubscription
        except ImportError:
            OrganizationSubscription = None

        mock_sub = MagicMock()
        mock_sub.subscription_tier.name = "unknown_tier"
        OrganizationSubscription.objects.select_related.return_value.get.return_value = (
            mock_sub
        )
        org = MagicMock()
        assert get_rate_limit_tier(org) == "free"


class TestCheckRateLimit:
    """Tests for check_rate_limit()."""

    @patch("mcp_server.rate_limiter.cache")
    def test_allows_calls_within_limit(self, mock_cache):
        """Calls within rate limit should succeed without raising."""
        mock_cache.get.return_value = None  # No existing window / count
        # Should not raise
        check_rate_limit("org-123", "free")

    @patch("mcp_server.rate_limiter.cache")
    def test_allows_calls_up_to_limit_minus_one(self, mock_cache):
        """Calls right below the per-minute limit should succeed."""
        now = time.time()
        limit = RATE_LIMITS["free"]["per_minute"]
        timestamps = [now - i * 0.1 for i in range(limit - 1)]

        def side_effect(key, default=None):
            if "min" in key:
                return timestamps
            return 0  # day count

        mock_cache.get.side_effect = side_effect
        # Should not raise (19 < 20)
        check_rate_limit("org-123", "free")

    @patch("mcp_server.rate_limiter.cache")
    def test_raises_when_per_minute_exceeded(self, mock_cache):
        """Exceeding per-minute limit raises RateLimitExceededError."""
        now = time.time()
        limit = RATE_LIMITS["free"]["per_minute"]
        timestamps = [now - i * 0.1 for i in range(limit)]

        def side_effect(key, default=None):
            if "min" in key:
                return timestamps
            return 0  # day count

        mock_cache.get.side_effect = side_effect

        with pytest.raises(RateLimitExceededError) as exc_info:
            check_rate_limit("org-123", "free")

        assert f"{limit} calls/minute" in str(exc_info.value)
        assert exc_info.value.retry_after >= 1

    @patch("mcp_server.rate_limiter.cache")
    def test_raises_when_per_day_exceeded(self, mock_cache):
        """Exceeding per-day limit raises RateLimitExceededError."""

        def side_effect(key, default=None):
            if "min" in key:
                return []  # empty minute window
            return RATE_LIMITS["free"]["per_day"]

        mock_cache.get.side_effect = side_effect

        with pytest.raises(RateLimitExceededError) as exc_info:
            check_rate_limit("org-123", "free")

        assert f"{RATE_LIMITS['free']['per_day']} calls/day" in str(exc_info.value)
        assert exc_info.value.retry_after > 0

    @patch("mcp_server.rate_limiter.cache")
    def test_retry_after_is_reasonable_per_minute(self, mock_cache):
        """retry_after for per-minute limit should be between 1 and 61 seconds."""
        now = time.time()
        timestamps = [now - i * 0.1 for i in range(RATE_LIMITS["free"]["per_minute"])]

        def side_effect(key, default=None):
            if "min" in key:
                return timestamps
            return 0

        mock_cache.get.side_effect = side_effect

        with pytest.raises(RateLimitExceededError) as exc_info:
            check_rate_limit("org-123", "free")

        assert 1 <= exc_info.value.retry_after <= 61

    @patch("mcp_server.rate_limiter.cache")
    def test_records_call_in_cache(self, mock_cache):
        """Successful calls should update both minute window and day counter."""
        mock_cache.get.return_value = None

        check_rate_limit("org-123", "free")

        # Should have called cache.set twice (minute window + day counter)
        assert mock_cache.set.call_count == 2
        # First call: minute window with 120s timeout
        minute_call = mock_cache.set.call_args_list[0]
        assert "min" in minute_call[0][0]
        assert minute_call[1]["timeout"] == 120 or minute_call[0][2] == 120
        # Second call: day counter with 86400s timeout
        day_call = mock_cache.set.call_args_list[1]
        assert "day" in day_call[0][0]

    @patch("mcp_server.rate_limiter.cache")
    def test_pro_tier_has_higher_limits(self, mock_cache):
        """Pro tier should allow 100 calls/minute (not just 20)."""
        now = time.time()
        # 50 timestamps (above free limit but below pro limit)
        timestamps = [now - i * 0.5 for i in range(50)]

        def side_effect(key, default=None):
            if "min" in key:
                return timestamps
            return 0

        mock_cache.get.side_effect = side_effect

        # Should not raise for pro tier (50 < 100)
        check_rate_limit("org-123", "pro")

    @patch("mcp_server.rate_limiter.cache")
    def test_expired_timestamps_are_pruned(self, mock_cache):
        """Timestamps older than 60 seconds should be pruned from the window."""
        now = time.time()
        # Mix of recent and old timestamps
        timestamps = [now - 10, now - 20, now - 70, now - 80]  # 2 recent, 2 expired

        def side_effect(key, default=None):
            if "min" in key:
                return timestamps
            return 0

        mock_cache.get.side_effect = side_effect

        # Should not raise (only 2 valid timestamps, well under limit)
        check_rate_limit("org-123", "free")

    @patch("mcp_server.rate_limiter.cache")
    def test_falls_back_to_free_for_unknown_tier(self, mock_cache):
        """Unknown tier falls back to free tier limits."""
        now = time.time()
        timestamps = [now - i * 0.1 for i in range(RATE_LIMITS["free"]["per_minute"])]

        def side_effect(key, default=None):
            if "min" in key:
                return timestamps
            return 0

        mock_cache.get.side_effect = side_effect

        # "unknown" tier falls back to free, so a full free window should raise
        with pytest.raises(RateLimitExceededError):
            check_rate_limit("org-123", "unknown")
