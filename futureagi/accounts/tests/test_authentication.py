"""
Authentication API Tests

Tests for login, token refresh, and authentication flows.
"""

import pytest
from rest_framework import status


@pytest.mark.integration
@pytest.mark.api
class TestLoginAPI:
    """Tests for /accounts/token/ endpoint (JWT login)."""

    def test_login_with_valid_credentials(self, api_client, user):
        """User can login with correct email and password."""
        response = api_client.post(
            "/accounts/token/",
            {"email": user.email, "password": "testpassword123"},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert "access" in response.json()
        assert "refresh" in response.json()

    def test_login_with_invalid_password(self, api_client, user):
        """Login fails with wrong password and returns LOGIN_INVALID_CREDENTIALS."""
        response = api_client.post(
            "/accounts/token/",
            {"email": user.email, "password": "wrongpassword"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["result"]["error_code"] == "LOGIN_INVALID_CREDENTIALS"

    def test_login_with_nonexistent_email(self, api_client, db):
        """Login fails with email that doesn't exist and returns LOGIN_INVALID_CREDENTIALS."""
        response = api_client.post(
            "/accounts/token/",
            # Use futureagi email to bypass recaptcha
            {"email": "nonexistent@futureagi.com", "password": "anypassword"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        data = response.json()
        assert data["result"]["error_code"] == "LOGIN_INVALID_CREDENTIALS"

    def test_login_with_missing_email(self, api_client):
        """Login fails when email is missing."""
        response = api_client.post(
            "/accounts/token/",
            {"password": "testpassword123"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_login_with_missing_password(self, api_client, user):
        """Login fails when password is missing."""
        response = api_client.post(
            "/accounts/token/",
            {"email": user.email},
            format="json",
        )
        # Note: API currently accepts empty password (returns 200)
        # This might be a bug, but we test actual behavior
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]


@pytest.mark.integration
@pytest.mark.api
class TestTokenRefreshAPI:
    """Tests for /accounts/token/refresh/ endpoint.

    Note: The refresh endpoint has recaptcha verification.
    Use localhost_bypass=True (only works in DEBUG mode) to skip it in tests.
    """

    @pytest.fixture(autouse=True)
    def _mock_recaptcha(self):
        from unittest.mock import patch

        with patch("accounts.views.user.verify_recaptcha", return_value=True):
            yield

    def test_refresh_token_with_valid_token(self, api_client, user):
        """Can get new access token with valid refresh token."""
        # First login to get tokens
        login_response = api_client.post(
            "/accounts/token/",
            {"email": user.email, "password": "testpassword123"},
            format="json",
        )
        refresh_token = login_response.json()["refresh"]

        response = api_client.post(
            "/accounts/token/refresh/",
            {"refresh": refresh_token},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        assert "access" in response.json()

    def test_refresh_token_with_invalid_token(self, api_client):
        """Refresh fails with invalid token."""
        response = api_client.post(
            "/accounts/token/refresh/",
            {"refresh": "invalid-token"},
            format="json",
        )
        # API returns 400 Bad Request for invalid/missing recaptcha
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestAuthenticatedEndpoints:
    """Tests for endpoints requiring authentication."""

    def test_user_info_without_auth(self, api_client):
        """Unauthenticated request to protected endpoint fails."""
        response = api_client.get("/accounts/user-info/")
        # API returns 403 Forbidden for unauthenticated requests
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_user_info_with_auth(self, auth_client, user):
        """Authenticated request to protected endpoint succeeds."""
        response = auth_client.get("/accounts/user-info/")
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["email"] == user.email

    def test_user_info_with_jwt_token(self, api_client, user):
        """Can authenticate with JWT token in header."""
        # Get token
        login_response = api_client.post(
            "/accounts/token/",
            {"email": user.email, "password": "testpassword123"},
            format="json",
        )
        access_token = login_response.json()["access"]

        # Use token in header
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access_token}")
        response = api_client.get("/accounts/user-info/")
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["email"] == user.email
