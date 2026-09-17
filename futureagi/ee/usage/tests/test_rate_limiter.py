"""
Rate limiter tests — Redis sliding window, plan-aware.
"""

from unittest.mock import MagicMock, patch

import pytest

from ee.usage.services.rate_limiter import RateLimiter, RateLimitResult


@pytest.mark.unit
class TestRateLimiter:

    def test_unlimited_always_allowed(self):
        with patch("ee.usage.services.rate_limiter.Entitlements") as mock_ent:
            mock_ent.get_limit.return_value = -1
            result = RateLimiter.check("org-1", "api")
            assert result.allowed is True

    def test_zero_limit_always_allowed(self):
        with patch("ee.usage.services.rate_limiter.Entitlements") as mock_ent:
            mock_ent.get_limit.return_value = 0
            result = RateLimiter.check("org-1", "api")
            assert result.allowed is True

    def test_under_limit_allowed(self):
        with (
            patch("ee.usage.services.rate_limiter.Entitlements") as mock_ent,
            patch("ee.usage.services.rate_limiter.get_redis") as mock_redis,
        ):
            mock_ent.get_limit.return_value = 100
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.incr.return_value = 50
            result = RateLimiter.check("org-1", "api")
            assert result.allowed is True
            assert result.current == 50

    def test_over_limit_denied(self):
        with (
            patch("ee.usage.services.rate_limiter.Entitlements") as mock_ent,
            patch("ee.usage.services.rate_limiter.get_redis") as mock_redis,
        ):
            mock_ent.get_limit.return_value = 100
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.incr.return_value = 101
            result = RateLimiter.check("org-1", "api")
            assert result.allowed is False
            assert result.current == 101
            assert result.limit == 100
            assert "Rate limit exceeded" in result.reason

    def test_denied_has_retry_after(self):
        with (
            patch("ee.usage.services.rate_limiter.Entitlements") as mock_ent,
            patch("ee.usage.services.rate_limiter.get_redis") as mock_redis,
        ):
            mock_ent.get_limit.return_value = 100
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.incr.return_value = 150
            result = RateLimiter.check("org-1", "api")
            assert result.retry_after > 0
            assert result.retry_after <= 60

    def test_unknown_limit_type_allowed(self):
        result = RateLimiter.check("org-1", "unknown_type")
        assert result.allowed is True

    def test_redis_failure_fails_open(self):
        with (
            patch("ee.usage.services.rate_limiter.Entitlements") as mock_ent,
            patch("ee.usage.services.rate_limiter.get_redis") as mock_redis,
        ):
            mock_ent.get_limit.return_value = 100
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.incr.side_effect = ConnectionError("Redis down")
            result = RateLimiter.check("org-1", "api")
            assert result.allowed is True  # fail open

    def test_ingestion_rate_limit(self):
        with (
            patch("ee.usage.services.rate_limiter.Entitlements") as mock_ent,
            patch("ee.usage.services.rate_limiter.get_redis") as mock_redis,
        ):
            mock_ent.get_limit.return_value = 5000
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.incr.return_value = 5001
            result = RateLimiter.check("org-1", "ingestion")
            assert result.allowed is False
            assert result.limit == 5000

    def test_sets_ttl_on_first_request(self):
        with (
            patch("ee.usage.services.rate_limiter.Entitlements") as mock_ent,
            patch("ee.usage.services.rate_limiter.get_redis") as mock_redis,
        ):
            mock_ent.get_limit.return_value = 100
            mock_r = MagicMock()
            mock_redis.return_value = mock_r
            mock_r.incr.return_value = 1  # first request
            RateLimiter.check("org-1", "api")
            mock_r.expire.assert_called_once()
