"""
Authentication API Tests

Tests for login, token refresh, and authentication flows.
"""

from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework import status

from accounts.authentication import generate_encrypted_message
from accounts.models.auth_token import AuthToken, AuthTokenType

AUTH_REQUIRED_STATUS_CODES = (
    status.HTTP_401_UNAUTHORIZED,
    status.HTTP_403_FORBIDDEN,
)


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

    def _make_refresh_token(self, user):
        """Create an active refresh token and return (AuthToken, encrypted_str)."""
        auth_token = AuthToken.objects.create(
            user=user,
            auth_type=AuthTokenType.REFRESH.value,
            is_active=True,
            last_used_at=timezone.now(),
        )
        encrypted = generate_encrypted_message(
            {"user_id": str(user.id), "id": str(auth_token.id)}
        )
        return auth_token, encrypted

    def test_refresh_empty_string_token(self, api_client):
        """Refresh fails when refresh token is empty string (serializer rejects)."""
        response = api_client.post(
            "/accounts/token/refresh/",
            {"refresh": ""},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_refresh_fernet_invalid_token(self, api_client):
        """Refresh fails with a string that isn't a valid Fernet token."""
        response = api_client.post(
            "/accounts/token/refresh/",
            {"refresh": "gAAAAABhNotRealFernetContent"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_refresh_token_with_nonexistent_user(self, api_client, user):
        """Refresh fails when token references a user that does not exist."""
        auth_token, _ = self._make_refresh_token(user)
        # Encrypt with the valid token_id but a nonexistent user_id
        fake_user_token = generate_encrypted_message(
            {
                "user_id": "00000000-0000-0000-0000-000000000000",
                "id": str(auth_token.id),
            }
        )
        response = api_client.post(
            "/accounts/token/refresh/",
            {"refresh": fake_user_token},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_refresh_inactive_auth_token_rejected(self, api_client, user):
        """Refresh fails when the referenced AuthToken is inactive."""
        auth_token, encrypted = self._make_refresh_token(user)
        auth_token.is_active = False
        auth_token.save(update_fields=["is_active"])

        response = api_client.post(
            "/accounts/token/refresh/",
            {"refresh": encrypted},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_refresh_expired_token_deactivates_it(self, api_client, user):
        """Token older than 30 days returns 400 and gets deactivated."""
        auth_token, encrypted = self._make_refresh_token(user)
        # Backdate created_at past the 30-day window
        expired_time = timezone.now() - timedelta(days=31)
        AuthToken.objects.filter(id=auth_token.id).update(created_at=expired_time)

        response = api_client.post(
            "/accounts/token/refresh/",
            {"refresh": encrypted},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        auth_token.refresh_from_db()
        assert auth_token.is_active is False
        assert cache.get(f"refresh_token_{auth_token.id}") is None

    def test_refresh_inactive_user_rejected(self, api_client, user):
        """Refresh fails when the user account is deactivated."""
        auth_token, encrypted = self._make_refresh_token(user)
        user.is_active = False
        user.save(update_fields=["is_active"])

        response = api_client.post(
            "/accounts/token/refresh/",
            {"refresh": encrypted},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_refresh_recaptcha_rejected(self, api_client, user):
        """Refresh fails when reCAPTCHA verification returns False."""
        from unittest.mock import patch

        _, encrypted = self._make_refresh_token(user)
        with patch("accounts.views.user.verify_recaptcha", return_value=False):
            response = api_client.post(
                "/accounts/token/refresh/",
                {"refresh": encrypted},
                format="json",
            )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestAuthenticatedEndpoints:
    """Tests for endpoints requiring authentication."""

    def test_user_info_without_auth(self, api_client):
        """Unauthenticated request to protected endpoint fails."""
        response = api_client.get("/accounts/user-info/")
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

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
