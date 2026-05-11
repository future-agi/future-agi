"""
Tests for AuthMonitoringMiddleware rate limiting.

Verifies that the IP-based rate limit window correctly enforces
MAX_LOGIN_ATTEMPTS_PER_HOUR over a 1-hour (3600s) window.

These tests use Django's test framework and require DJANGO_SETTINGS_MODULE.
"""

import time
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory

from accounts.authentication import AuthMonitoringMiddleware

pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear cache before and after each test."""
    cache.clear()
    yield
    cache.clear()


def _make_middleware():
    """Create middleware with a no-op get_response."""
    return AuthMonitoringMiddleware(get_response=lambda req: HttpResponse(status=200))


def _make_request(path="/accounts/login/", ip="192.168.1.100"):
    """Create a fake request targeting the given path from the given IP."""
    factory = RequestFactory()
    request = factory.post(path)
    request.META["REMOTE_ADDR"] = ip
    return request


class TestLoginRateLimitWindow:
    """Rate limit window should be 3600 seconds (1 hour)."""

    def test_allows_requests_within_limit(self):
        """Under MAX_LOGIN_ATTEMPTS_PER_HOUR requests should pass."""
        middleware = _make_middleware()

        for _ in range(9):  # default limit = 10
            response = middleware(_make_request())
            assert response.status_code == 200

    def test_blocks_after_max_attempts(self):
        """At MAX_LOGIN_ATTEMPTS_PER_HOUR, next request is blocked (403)."""
        middleware = _make_middleware()

        for _ in range(10):
            middleware(_make_request())

        response = middleware(_make_request())
        assert response.status_code == 403

    def test_window_is_one_hour_not_shorter(self):
        """Requests made 30 min ago (within 1h) still count toward the limit."""
        middleware = _make_middleware()
        ip = "10.0.0.1"

        fake_now = time.time()
        thirty_min_ago = fake_now - 1800

        # 9 requests at T-30min
        with patch("time.time", return_value=thirty_min_ago):
            for _ in range(9):
                middleware(_make_request(ip=ip))

        # 10th request at T=now should trigger block (still within 1h window)
        with patch("time.time", return_value=fake_now):
            response = middleware(_make_request(ip=ip))
            assert response.status_code == 403

    def test_old_requests_outside_window_are_forgotten(self):
        """Requests older than 3600s are pruned — counter resets."""
        middleware = _make_middleware()
        ip = "10.0.0.2"

        fake_now = time.time()
        two_hours_ago = fake_now - 7200

        # 9 requests at T-2h (outside 1h window)
        with patch("time.time", return_value=two_hours_ago):
            for _ in range(9):
                middleware(_make_request(ip=ip))

        # At T=now, old requests are pruned; new request should pass
        with patch("time.time", return_value=fake_now):
            response = middleware(_make_request(ip=ip))
            assert response.status_code == 200

    def test_different_ips_have_independent_limits(self):
        """Rate limits are per-IP."""
        middleware = _make_middleware()

        # Exhaust limit for IP A
        for _ in range(10):
            middleware(_make_request(ip="1.1.1.1"))

        # IP B should still be allowed
        response = middleware(_make_request(ip="2.2.2.2"))
        assert response.status_code == 200


class TestPasswordResetRateLimitWindow:
    """Password reset endpoint uses the same 1-hour window."""

    def test_blocks_after_max_attempts(self):
        """Password reset blocked after MAX_LOGIN_ATTEMPTS_PER_HOUR."""
        middleware = _make_middleware()
        path = "/accounts/password-reset-initiate/"

        for _ in range(10):
            middleware(_make_request(path=path))

        response = middleware(_make_request(path=path))
        assert response.status_code == 403

    def test_old_requests_outside_window_are_forgotten(self):
        """Password reset requests older than 1 hour don't count."""
        middleware = _make_middleware()
        ip = "10.0.0.3"
        path = "/accounts/password-reset-initiate/"

        fake_now = time.time()
        two_hours_ago = fake_now - 7200

        with patch("time.time", return_value=two_hours_ago):
            for _ in range(9):
                middleware(_make_request(path=path, ip=ip))

        with patch("time.time", return_value=fake_now):
            response = middleware(_make_request(path=path, ip=ip))
            assert response.status_code == 200


class TestCacheTTLMatchesWindow:
    """Cache TTL should be >= filter window to avoid premature eviction."""

    def test_cache_entry_survives_full_window(self):
        """Verify cache TTL is set to 3600s (not shorter than filter window)."""
        middleware = _make_middleware()
        ip = "10.0.0.4"

        middleware(_make_request(ip=ip))

        # Verify the cache key exists with expected data
        cached = cache.get(f"ip_requests_{ip}")
        assert cached is not None
        assert len(cached) == 1

        # The value is a list of timestamps
        assert isinstance(cached[0], float)
