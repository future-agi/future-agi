"""
Login Structured Error Codes — Tests

Verifies that every login failure path returns a machine-readable
`error_code` field so the frontend can switch on codes instead of
matching raw strings.

Covers:
  - All 7 bad_request() paths in CustomTokenObtainPairView
  - All 4 HttpResponseForbidden → JSON-403 paths in AuthMonitoringMiddleware
  - Response shape (status/result envelope)
  - Backward-compat: existing `error` strings are unchanged
  - No error_code leaking into successful responses
"""

import json
import time
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory
from rest_framework import status
from rest_framework.test import APIClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def inactive_user(organization, db):
    from accounts.models import User
    from tfc.constants.roles import OrganizationRoles

    return User.objects.create_user(
        email="inactive_ec@futureagi.com",
        password="testpassword123",
        name="Inactive EC User",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
        is_active=False,
    )


def _login(api_client, email, password="testpassword123"):
    return api_client.post(
        "/accounts/token/",
        {"email": email, "password": password},
        format="json",
    )


def _result(response):
    """Extract the `result` dict from a response, handling both shapes."""
    data = response.json()
    return data.get("result", data)


# ---------------------------------------------------------------------------
# 1. View — LOGIN_INVALID_CREDENTIALS
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestInvalidCredentialsErrorCode:
    """Wrong password and non-existent email both return LOGIN_INVALID_CREDENTIALS."""

    def test_wrong_password_error_code(self, api_client, user):
        resp = _login(api_client, user.email, "absolutelywrong")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        result = _result(resp)
        assert result["error_code"] == "LOGIN_INVALID_CREDENTIALS"

    def test_wrong_password_preserves_legacy_error_string(self, api_client, user):
        """Backward compat: `error` field still says 'Invalid credentials'."""
        resp = _login(api_client, user.email, "absolutelywrong")
        result = _result(resp)
        assert result["error"] == "Invalid credentials"

    def test_wrong_password_includes_remaining_attempts(self, api_client, user):
        resp = _login(api_client, user.email, "absolutelywrong")
        result = _result(resp)
        assert "remaining_attempts" in result
        assert isinstance(result["remaining_attempts"], int)

    def test_nonexistent_email_error_code(self, api_client, db):
        resp = _login(api_client, "nobody@futureagi.com", "anypass")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        result = _result(resp)
        assert result["error_code"] == "LOGIN_INVALID_CREDENTIALS"

    def test_nonexistent_email_preserves_legacy_error_string(self, api_client, db):
        resp = _login(api_client, "nobody@futureagi.com", "anypass")
        result = _result(resp)
        assert result["error"] == "Invalid credentials"


# ---------------------------------------------------------------------------
# 2. View — LOGIN_ACCOUNT_DEACTIVATED
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestAccountDeactivatedErrorCode:
    """Deactivated user sees LOGIN_ACCOUNT_DEACTIVATED."""

    def test_deactivated_user_error_code(self, api_client, inactive_user):
        resp = _login(api_client, inactive_user.email)
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        result = _result(resp)
        assert result["error_code"] == "LOGIN_ACCOUNT_DEACTIVATED"

    def test_deactivated_user_preserves_legacy_error_string(
        self, api_client, inactive_user
    ):
        resp = _login(api_client, inactive_user.email)
        result = _result(resp)
        assert result["error"] == "Account deactivated"

    def test_deactivated_user_includes_message(self, api_client, inactive_user):
        resp = _login(api_client, inactive_user.email)
        result = _result(resp)
        assert "deactivated" in result.get("message", "").lower()

    def test_deactivated_user_no_remaining_attempts(self, api_client, inactive_user):
        """Deactivated response must NOT expose remaining_attempts."""
        resp = _login(api_client, inactive_user.email)
        result = _result(resp)
        assert "remaining_attempts" not in result

    def test_deactivated_user_after_active_login(self, api_client, user):
        """User deactivated post-creation sees error_code correctly."""
        user.is_active = False
        user.save(update_fields=["is_active"])
        resp = _login(api_client, user.email)
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        result = _result(resp)
        assert result["error_code"] == "LOGIN_ACCOUNT_DEACTIVATED"


# ---------------------------------------------------------------------------
# 3. View — LOGIN_RECAPTCHA_FAILED
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestRecaptchaFailedErrorCode:
    """Failed reCAPTCHA returns LOGIN_RECAPTCHA_FAILED."""

    def test_recaptcha_failed_error_code(self, api_client, user):
        with patch("accounts.views.user.verify_recaptcha", return_value=False):
            # Use a non-localhost, non-special-email address to trigger recaptcha
            from accounts.models import User
            from tfc.constants.roles import OrganizationRoles

            external_user = User.objects.create_user(
                email="external@example.com",
                password="testpassword123",
                name="External User",
                organization=user.organization,
                organization_role=OrganizationRoles.MEMBER,
                is_active=True,
            )
            resp = api_client.post(
                "/accounts/token/",
                {
                    "email": external_user.email,
                    "password": "testpassword123",
                    "recaptcha-response": "bad-token",
                },
                format="json",
                SERVER_NAME="example.com",
            )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        result = _result(resp)
        assert result["error_code"] == "LOGIN_RECAPTCHA_FAILED"

    def test_recaptcha_failed_preserves_legacy_error_string(self, api_client, user):
        with patch("accounts.views.user.verify_recaptcha", return_value=False):
            from accounts.models import User
            from tfc.constants.roles import OrganizationRoles

            external_user = User.objects.create_user(
                email="external2@example.com",
                password="testpassword123",
                name="External User 2",
                organization=user.organization,
                organization_role=OrganizationRoles.MEMBER,
                is_active=True,
            )
            resp = api_client.post(
                "/accounts/token/",
                {
                    "email": external_user.email,
                    "password": "testpassword123",
                    "recaptcha-response": "bad-token",
                },
                format="json",
                SERVER_NAME="example.com",
            )
        result = _result(resp)
        assert result["error"] == "reCAPTCHA verification failed"


# ---------------------------------------------------------------------------
# 4. View — LOGIN_ACCOUNT_BLOCKED
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestAccountBlockedErrorCode:
    """Account blocked after too many failures returns LOGIN_ACCOUNT_BLOCKED."""

    def _seed_block(self, email: str) -> None:
        """Pre-seed the block cache entry exactly as the view writes it."""
        block_key = f"user_blocked_{email}"
        cache.set(
            block_key,
            {"blocked": True, "expiry": time.time() + 3600},
            3600,
        )

    def test_account_blocked_error_code(self, api_client, user):
        """Seed the cache as if the account is already blocked, then login."""
        self._seed_block(user.email)
        resp = _login(api_client, user.email)
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        result = _result(resp)
        assert result["error_code"] == "LOGIN_ACCOUNT_BLOCKED"

    def test_account_blocked_includes_block_time_remaining(self, api_client, user):
        self._seed_block(user.email)
        resp = _login(api_client, user.email)
        result = _result(resp)
        assert "block_time_remaining" in result
        assert isinstance(result["block_time_remaining"], int)
        assert result["block_time_remaining"] > 0

    def test_account_blocked_includes_blocked_flag(self, api_client, user):
        self._seed_block(user.email)
        resp = _login(api_client, user.email)
        result = _result(resp)
        assert result.get("blocked") is True

    def test_account_blocked_preserves_legacy_error_string(self, api_client, user):
        self._seed_block(user.email)
        resp = _login(api_client, user.email)
        result = _result(resp)
        assert "blocked" in result["error"].lower()


# ---------------------------------------------------------------------------
# 5. View — LOGIN_TOO_MANY_ATTEMPTS (transition moment)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestTooManyAttemptsErrorCode:
    """
    LOGIN_TOO_MANY_ATTEMPTS fires in the exception handler when an unexpected
    error occurs and the attempts counter hits MAX_LOGIN_ATTEMPTS.

    To trigger it reliably we:
      1. Pre-seed `login_attempts_{email}` to MAX - 1
      2. Patch check_password to raise an exception (triggers the except branch)
      The except branch increments attempts and, at threshold, returns TOO_MANY_ATTEMPTS.
    """

    def _seed_attempts(self, email: str, count: int) -> None:
        from django.conf import settings

        max_attempts = getattr(settings, "MAX_LOGIN_ATTEMPTS", 10)
        cache.set(f"login_attempts_{email}", count, 3600)

    def test_too_many_attempts_error_code(self, api_client, user):
        """Exception path at the attempt threshold returns LOGIN_TOO_MANY_ATTEMPTS."""
        from django.conf import settings

        max_attempts = getattr(settings, "MAX_LOGIN_ATTEMPTS", 10)
        self._seed_attempts(user.email, max_attempts - 1)

        with patch(
            "accounts.views.user.check_password",
            side_effect=RuntimeError("boom"),
        ):
            resp = _login(api_client, user.email)

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        result = _result(resp)
        assert result["error_code"] == "LOGIN_TOO_MANY_ATTEMPTS"

    def test_too_many_attempts_includes_blocked_flag(self, api_client, user):
        from django.conf import settings

        max_attempts = getattr(settings, "MAX_LOGIN_ATTEMPTS", 10)
        self._seed_attempts(user.email, max_attempts - 1)

        with patch(
            "accounts.views.user.check_password",
            side_effect=RuntimeError("boom"),
        ):
            resp = _login(api_client, user.email)

        result = _result(resp)
        assert result.get("blocked") is True

    def test_too_many_attempts_includes_block_time(self, api_client, user):
        from django.conf import settings

        max_attempts = getattr(settings, "MAX_LOGIN_ATTEMPTS", 10)
        self._seed_attempts(user.email, max_attempts - 1)

        with patch(
            "accounts.views.user.check_password",
            side_effect=RuntimeError("boom"),
        ):
            resp = _login(api_client, user.email)

        result = _result(resp)
        assert "block_time" in result


# ---------------------------------------------------------------------------
# 6. View — LOGIN_UNEXPECTED_ERROR
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestUnexpectedErrorCode:
    """Unhandled exception returns LOGIN_UNEXPECTED_ERROR."""

    def test_unexpected_error_code(self, api_client, user):
        with patch(
            "accounts.views.user.check_password",
            side_effect=RuntimeError("unexpected boom"),
        ):
            resp = _login(api_client, user.email)

        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        result = _result(resp)
        assert result["error_code"] == "LOGIN_UNEXPECTED_ERROR"

    def test_unexpected_error_preserves_legacy_error_string(self, api_client, user):
        with patch(
            "accounts.views.user.check_password",
            side_effect=RuntimeError("boom"),
        ):
            resp = _login(api_client, user.email)
        result = _result(resp)
        assert result["error"] == "Login failed"

    def test_unexpected_error_includes_remaining_attempts(self, api_client, user):
        with patch(
            "accounts.views.user.check_password",
            side_effect=RuntimeError("boom"),
        ):
            resp = _login(api_client, user.email)
        result = _result(resp)
        assert "remaining_attempts" in result


# ---------------------------------------------------------------------------
# 7. Middleware unit tests — _json_forbidden helper + per-path routing
#
# We test the middleware directly via RequestFactory rather than the full
# test client, so we control the IP precisely without needing DB access.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMiddlewareJsonForbidden:
    """AuthMonitoringMiddleware._json_forbidden returns structured JSON 403."""

    def _middleware(self):
        from accounts.authentication import AuthMonitoringMiddleware

        return AuthMonitoringMiddleware(lambda r: HttpResponse("OK", status=200))

    def test_json_forbidden_status_code(self):
        middleware = self._middleware()
        resp = middleware._json_forbidden("LOGIN_IP_BLOCKED", "blocked")
        assert resp.status_code == 403

    def test_json_forbidden_content_type(self):
        middleware = self._middleware()
        resp = middleware._json_forbidden("LOGIN_IP_BLOCKED", "blocked")
        assert "application/json" in resp.get("Content-Type", "")

    def test_json_forbidden_body_is_valid_json(self):
        middleware = self._middleware()
        resp = middleware._json_forbidden("LOGIN_IP_BLOCKED", "blocked message")
        body = json.loads(resp.content)
        assert isinstance(body, dict)

    def test_json_forbidden_envelope_shape(self):
        middleware = self._middleware()
        resp = middleware._json_forbidden("LOGIN_IP_BLOCKED", "blocked message")
        body = json.loads(resp.content)
        assert body["status"] is False
        assert "result" in body
        assert "error" in body["result"]
        assert "error_code" in body["result"]

    def test_json_forbidden_error_code_value(self):
        middleware = self._middleware()
        resp = middleware._json_forbidden("LOGIN_IP_BLOCKED", "msg")
        body = json.loads(resp.content)
        assert body["result"]["error_code"] == "LOGIN_IP_BLOCKED"

    def test_json_forbidden_error_message(self):
        middleware = self._middleware()
        resp = middleware._json_forbidden("LOGIN_IP_BLOCKED", "custom message")
        body = json.loads(resp.content)
        assert body["result"]["error"] == "custom message"

    def test_json_forbidden_extra_kwargs(self):
        middleware = self._middleware()
        resp = middleware._json_forbidden("LOGIN_IP_BLOCKED", "msg", blocked=True)
        body = json.loads(resp.content)
        assert body["result"]["blocked"] is True


@pytest.mark.unit
class TestMiddlewareIpBlockedRouting:
    """Middleware blocks /login/, /token/, /signup/ paths when IP is cached."""

    TEST_IP = "10.10.10.99"

    def _middleware(self):
        from accounts.authentication import AuthMonitoringMiddleware

        return AuthMonitoringMiddleware(lambda r: HttpResponse("OK", status=200))

    def _request(self, path: str):
        factory = RequestFactory()
        req = factory.post(path, content_type="application/json")
        req.META["REMOTE_ADDR"] = self.TEST_IP
        return req

    def test_ip_blocked_on_token_path(self):
        cache.set(f"blocked_ip_{self.TEST_IP}", True, 3600)
        middleware = self._middleware()
        resp = middleware(self._request("/api/accounts/token/"))
        assert resp.status_code == 403
        body = json.loads(resp.content)
        assert body["result"]["error_code"] == "LOGIN_IP_BLOCKED"
        assert body["result"]["blocked"] is True

    def test_ip_blocked_on_login_path(self):
        cache.set(f"blocked_ip_{self.TEST_IP}", True, 3600)
        middleware = self._middleware()
        resp = middleware(self._request("/api/accounts/login/"))
        assert resp.status_code == 403
        body = json.loads(resp.content)
        assert body["result"]["error_code"] == "LOGIN_IP_BLOCKED"

    def test_ip_blocked_on_signup_path(self):
        cache.set(f"blocked_ip_{self.TEST_IP}", True, 3600)
        middleware = self._middleware()
        resp = middleware(self._request("/api/accounts/signup/"))
        assert resp.status_code == 403
        body = json.loads(resp.content)
        assert body["result"]["error_code"] == "LOGIN_IP_BLOCKED"

    def test_ip_rate_limited_error_code(self):
        """When IP requests hit the threshold, middleware returns LOGIN_IP_RATE_LIMITED."""
        from django.conf import settings

        max_attempts = getattr(settings, "MAX_LOGIN_ATTEMPTS_PER_HOUR", 10)
        now = time.time()
        # The check is `len(requests) >= max_attempts`, so seed exactly max_attempts
        # recent requests so the condition is True on the next call.
        cache.set(
            f"ip_requests_{self.TEST_IP}",
            [now - i for i in range(max_attempts)],
            1200,
        )
        middleware = self._middleware()
        resp = middleware(self._request("/api/accounts/token/"))
        assert resp.status_code == 403
        body = json.loads(resp.content)
        assert body["result"]["error_code"] == "LOGIN_IP_RATE_LIMITED"
        assert body["result"]["blocked"] is True

    def test_non_blocked_ip_passes_through(self):
        """Unblocked IP is not intercepted — passes through to next handler."""
        middleware = self._middleware()
        resp = middleware(self._request("/api/accounts/token/"))
        # Passes through to the dummy lambda which returns 200
        assert resp.status_code == 200


@pytest.mark.unit
class TestMiddlewarePasswordResetRouting:
    """Middleware rate-limits /password-reset-initiate/ correctly."""

    TEST_IP = "10.10.20.50"

    def _middleware(self):
        from accounts.authentication import AuthMonitoringMiddleware

        return AuthMonitoringMiddleware(lambda r: HttpResponse("OK", status=200))

    def _request(self):
        factory = RequestFactory()
        req = factory.post(
            "/api/accounts/password-reset-initiate/",
            content_type="application/json",
        )
        req.META["REMOTE_ADDR"] = self.TEST_IP
        return req

    def test_blocked_ip_on_password_reset(self):
        cache.set(f"rate_limit_{self.TEST_IP}", True, 3600)
        middleware = self._middleware()
        resp = middleware(self._request())
        assert resp.status_code == 403
        body = json.loads(resp.content)
        assert body["result"]["error_code"] == "LOGIN_PASSWORD_RESET_RATE_LIMITED"

    def test_rate_limit_trigger_on_password_reset(self):
        from django.conf import settings

        max_attempts = getattr(settings, "MAX_LOGIN_ATTEMPTS_PER_HOUR", 10)
        now = time.time()
        # The check is `len(requests) >= max_attempts`, so seed exactly max_attempts
        # recent requests so the condition fires on the next call.
        cache.set(
            f"rate_limit_requests_{self.TEST_IP}",
            [now - i for i in range(max_attempts)],
            1200,
        )
        middleware = self._middleware()
        resp = middleware(self._request())
        assert resp.status_code == 403
        body = json.loads(resp.content)
        assert body["result"]["error_code"] == "LOGIN_PASSWORD_RESET_RATE_LIMITED"

    def test_password_reset_response_shape(self):
        cache.set(f"rate_limit_{self.TEST_IP}", True, 3600)
        middleware = self._middleware()
        resp = middleware(self._request())
        body = json.loads(resp.content)
        assert body["status"] is False
        assert "result" in body
        assert "error" in body["result"]
        assert "error_code" in body["result"]


# ---------------------------------------------------------------------------
# 9. Successful login — no error_code leaked
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestSuccessfulLoginHasNoErrorCode:
    """Successful login response must NOT include error_code."""

    def test_successful_login_has_no_error_code(self, api_client, user):
        resp = _login(api_client, user.email)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "error_code" not in data
        assert "error_code" not in data.get("result", {})

    def test_successful_login_tokens_present(self, api_client, user):
        resp = _login(api_client, user.email)
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "access" in data
        assert "refresh" in data


# ---------------------------------------------------------------------------
# 10. Response envelope shape (all error responses)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.api
class TestErrorResponseEnvelope:
    """Every error response wraps in {status: false, result: {...}}."""

    def test_invalid_credentials_envelope(self, api_client, user):
        resp = _login(api_client, user.email, "wrong")
        data = resp.json()
        assert data["status"] is False
        assert "result" in data
        assert "error" in data["result"]
        assert "error_code" in data["result"]

    def test_account_deactivated_envelope(self, api_client, inactive_user):
        resp = _login(api_client, inactive_user.email)
        data = resp.json()
        assert data["status"] is False
        assert "result" in data
        assert "error_code" in data["result"]

    def test_unexpected_error_envelope(self, api_client, user):
        with patch(
            "accounts.views.user.check_password", side_effect=RuntimeError("boom")
        ):
            resp = _login(api_client, user.email)
        data = resp.json()
        assert data["status"] is False
        assert "error_code" in data["result"]
