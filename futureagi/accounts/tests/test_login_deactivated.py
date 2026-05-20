"""
Tests for TH-3463: Login should distinguish deactivated accounts from
invalid credentials, and the catch-all handler should not mask real errors.
"""

import pytest
from rest_framework import status


@pytest.mark.integration
@pytest.mark.api
class TestDeactivatedUserLogin:
    """Login returns 'Account deactivated' for inactive users."""

    def test_deactivated_user_gets_deactivated_error(self, api_client, user):
        """Deactivated user sees 'Account deactivated', not 'Invalid credentials'."""
        user.is_active = False
        user.save(update_fields=["is_active"])

        response = api_client.post(
            "/accounts/token/",
            {"email": user.email, "password": "testpassword123"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["result"]["error"] == "Account deactivated"
        assert "deactivated" in data["result"]["message"].lower()
        assert data["result"]["error_code"] == "LOGIN_ACCOUNT_DEACTIVATED"

    def test_deactivated_user_does_not_increment_failed_attempts(
        self, api_client, user
    ):
        """Deactivated-user response should not count as a failed login attempt."""
        user.is_active = False
        user.save(update_fields=["is_active"])

        from django.core.cache import cache

        attempts_key = f"login_attempts_{user.email}"
        cache.delete(attempts_key)

        api_client.post(
            "/accounts/token/",
            {"email": user.email, "password": "testpassword123"},
            format="json",
        )

        assert cache.get(attempts_key, 0) == 0


@pytest.mark.integration
@pytest.mark.api
class TestActiveUserLogin:
    """Active user login still works normally after the refactor."""

    def test_active_user_valid_password(self, api_client, user):
        """Active user with correct password can still login."""
        response = api_client.post(
            "/accounts/token/",
            {"email": user.email, "password": "testpassword123"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert "access" in data
        assert "refresh" in data

    def test_active_user_wrong_password(self, api_client, user):
        """Active user with wrong password gets 'Invalid credentials'."""
        response = api_client.post(
            "/accounts/token/",
            {"email": user.email, "password": "wrongpassword"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["result"]["error"] == "Invalid credentials"
        assert data["result"]["error_code"] == "LOGIN_INVALID_CREDENTIALS"

    def test_nonexistent_email(self, api_client, db):
        """Non-existent email gets 'Invalid credentials'."""
        response = api_client.post(
            "/accounts/token/",
            {"email": "nobody@futureagi.com", "password": "anypass"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["result"]["error"] == "Invalid credentials"
        assert data["result"]["error_code"] == "LOGIN_INVALID_CREDENTIALS"


@pytest.mark.integration
@pytest.mark.api
class TestCatchAllHandler:
    """The catch-all returns 'Login failed' instead of 'Invalid credentials'."""

    def test_unexpected_error_returns_login_failed(self, api_client, user):
        """When an unexpected exception occurs, the error is 'Login failed'."""
        from unittest.mock import patch

        with patch(
            "accounts.views.user.check_password",
            side_effect=RuntimeError("boom"),
        ):
            response = api_client.post(
                "/accounts/token/",
                {"email": user.email, "password": "testpassword123"},
                format="json",
            )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["result"]["error"] == "Login failed"
        assert data["result"]["error_code"] == "LOGIN_UNEXPECTED_ERROR"
        assert "remaining_attempts" in data["result"]
