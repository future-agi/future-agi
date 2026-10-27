from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from ee.usage.middleware.rate_limit import RATE_LIMIT_SKIP_PREFIXES, RateLimitMiddleware
from ee.usage.services.rate_limiter import RateLimitResult


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.fixture
def get_response():
    def _response(request):
        from django.http import HttpResponse

        return HttpResponse("OK", status=200)

    return _response


@pytest.fixture
def middleware(get_response):
    return RateLimitMiddleware(get_response)


@pytest.fixture
def mock_org():
    org = MagicMock()
    org.id = "org-123"
    return org


@pytest.fixture
def mock_user(mock_org):
    user = MagicMock()
    user.is_authenticated = True
    user.organization = mock_org
    return user


def _make_request(rf, path="/api/some-endpoint/", method="GET", user=None, org=None):
    request = getattr(rf, method.lower())(path)
    if user:
        request.user = user
    else:
        request.user = MagicMock(is_authenticated=False)
    if org:
        request.organization = org
    return request


class TestRateLimitMiddlewareBasic:
    def test_allows_request_under_limit(self, middleware, rf, mock_user, mock_org):
        request = _make_request(rf, user=mock_user, org=mock_org)
        with patch("ee.usage.services.rate_limiter.RateLimiter.check") as mock_check:
            mock_check.return_value = RateLimitResult(
                allowed=True, current=50, limit=100
            )
            response = middleware(request)
            assert response.status_code == 200
            mock_check.assert_called_once_with(str(mock_org.id), "api")

    def test_denies_request_over_limit(self, middleware, rf, mock_user, mock_org):
        request = _make_request(rf, user=mock_user, org=mock_org)
        with patch("ee.usage.services.rate_limiter.RateLimiter.check") as mock_check:
            mock_check.return_value = RateLimitResult(
                allowed=False,
                reason="Rate limit exceeded: 100 api requests per minute",
                current=101,
                limit=100,
                retry_after=30,
            )
            response = middleware(request)
            assert response.status_code == 429

    def test_429_response_format(self, middleware, rf, mock_user, mock_org):
        request = _make_request(rf, user=mock_user, org=mock_org)
        with patch("ee.usage.services.rate_limiter.RateLimiter.check") as mock_check:
            mock_check.return_value = RateLimitResult(
                allowed=False,
                reason="Rate limit exceeded: 100 api requests per minute",
                current=101,
                limit=100,
                retry_after=30,
            )
            response = middleware(request)
            import json

            body = json.loads(response.content)
            assert body["status"] is False
            assert body["result"]["code"] == "RATE_LIMITED"
            assert "100 api requests" in body["result"]["message"]
            assert body["result"]["retry_after"] == 30

    def test_429_has_retry_after_header(self, middleware, rf, mock_user, mock_org):
        request = _make_request(rf, user=mock_user, org=mock_org)
        with patch("ee.usage.services.rate_limiter.RateLimiter.check") as mock_check:
            mock_check.return_value = RateLimitResult(
                allowed=False, reason="limited", retry_after=45
            )
            response = middleware(request)
            assert response["Retry-After"] == "45"


class TestRateLimitMiddlewareSkipPaths:
    def test_skips_health_check(self, middleware, rf, mock_user, mock_org):
        request = _make_request(rf, path="/health/", user=mock_user, org=mock_org)
        with patch("ee.usage.services.rate_limiter.RateLimiter.check") as mock_check:
            response = middleware(request)
            assert response.status_code == 200
            mock_check.assert_not_called()

    def test_skips_stripe_webhooks(self, middleware, rf, mock_user, mock_org):
        request = _make_request(
            rf, path="/api/usage/webhooks/stripe/", user=mock_user, org=mock_org
        )
        with patch("ee.usage.services.rate_limiter.RateLimiter.check") as mock_check:
            response = middleware(request)
            assert response.status_code == 200
            mock_check.assert_not_called()

    def test_skips_admin(self, middleware, rf, mock_user, mock_org):
        request = _make_request(rf, path="/admin/login/", user=mock_user, org=mock_org)
        with patch("ee.usage.services.rate_limiter.RateLimiter.check") as mock_check:
            response = middleware(request)
            assert response.status_code == 200
            mock_check.assert_not_called()

    def test_skips_options_request(self, middleware, rf, mock_user, mock_org):
        request = _make_request(
            rf, path="/api/some/", method="OPTIONS", user=mock_user, org=mock_org
        )
        with patch("ee.usage.services.rate_limiter.RateLimiter.check") as mock_check:
            response = middleware(request)
            assert response.status_code == 200
            mock_check.assert_not_called()


class TestRateLimitMiddlewareAuth:
    def test_skips_unauthenticated_requests(self, middleware, rf):
        request = _make_request(rf)
        with patch("ee.usage.services.rate_limiter.RateLimiter.check") as mock_check:
            response = middleware(request)
            assert response.status_code == 200
            mock_check.assert_not_called()

    def test_skips_request_without_organization(self, middleware, rf):
        user = MagicMock()
        user.is_authenticated = True
        user.organization = None
        request = _make_request(rf, user=user)
        with patch("ee.usage.services.rate_limiter.RateLimiter.check") as mock_check:
            response = middleware(request)
            assert response.status_code == 200
            mock_check.assert_not_called()


class TestRateLimitMiddlewareFailure:
    def test_allows_when_import_fails(self, middleware, rf, mock_user, mock_org):
        request = _make_request(rf, user=mock_user, org=mock_org)
        with patch.dict("sys.modules", {"ee.usage.services.rate_limiter": None}):
            response = middleware(request)
            assert response.status_code == 200

    def test_org_from_user_fallback(self, middleware, rf, mock_user, mock_org):
        request = _make_request(rf, user=mock_user)
        with patch("ee.usage.services.rate_limiter.RateLimiter.check") as mock_check:
            mock_check.return_value = RateLimitResult(allowed=True)
            response = middleware(request)
            assert response.status_code == 200
            mock_check.assert_called_once_with(str(mock_org.id), "api")
