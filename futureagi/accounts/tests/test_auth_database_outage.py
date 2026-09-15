"""A PostgreSQL outage must not be reported as an authentication failure.

Every authenticated request runs ``decode_token``, which touches PostgreSQL on
every branch — the cache-hit branch alone issues an ``AuthToken …
last_used_at`` UPDATE per request. Before the fix, a blanket ``except
Exception`` turned the resulting ``OperationalError`` into
``AuthenticationFailed`` and the client saw HTTP 401 ``authentication_failed``
carrying raw driver text; the natural client reaction to a 401 is to log the
user out. These tests pin the honest shape: HTTP 503 ``service_unavailable``,
no driver text, and no ``WWW-Authenticate`` challenge.

No database is used: the outage is injected at the ORM call sites, so the
modules under test run exactly as they do in production while PostgreSQL is
unreachable.
"""

from unittest import mock

import pytest
from django.db import InterfaceError, OperationalError
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from accounts.authentication import (
    APIKeyAuthentication,
    decode_token,
    generate_encrypted_message,
)
from tfc.utils.api_errors import DatabaseUnavailable

# The exact psycopg wording a standby/recovering PostgreSQL returns. It must
# never reach a client.
RECOVERY_ERROR = OperationalError(
    "connection failed: FATAL:  the database system is in recovery mode"
)


@pytest.fixture
def access_token():
    """A well-formed access token — the encryption layer needs no database."""
    return generate_encrypted_message(
        {"user_id": "00000000-0000-0000-0000-000000000001", "id": "token-id"}
    )


class _ProbeView(APIView):
    """Smallest possible authenticated endpoint, routed by the factory."""

    authentication_classes = [APIKeyAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):  # pragma: no cover - never reached while PG is down
        return Response({"status": True})


class TestDecodeTokenDatabaseOutage:
    def test_cached_token_last_used_write_failure_is_not_an_auth_failure(
        self, access_token
    ):
        """The per-request ``last_used_at`` UPDATE is the reported mechanism."""
        cached = {"user": mock.Mock(), "token": mock.Mock()}
        cached["user"]._state.fields_cache = {"organization": None}

        with (
            mock.patch("accounts.authentication.cache") as cache_mock,
            mock.patch("accounts.authentication.AuthToken") as auth_token_mock,
        ):
            cache_mock.get.return_value = cached
            auth_token_mock.objects.filter.return_value.update.side_effect = (
                RECOVERY_ERROR
            )

            with pytest.raises(DatabaseUnavailable):
                decode_token(access_token)

    def test_uncached_token_lookup_failure_is_not_an_auth_failure(self, access_token):
        """The cache-miss branch reads PostgreSQL too."""
        with (
            mock.patch("accounts.authentication.cache") as cache_mock,
            mock.patch("accounts.authentication.User") as user_mock,
        ):
            cache_mock.get.return_value = None
            user_mock.objects.select_related.return_value.get.side_effect = (
                RECOVERY_ERROR
            )

            with pytest.raises(DatabaseUnavailable):
                decode_token(access_token)

    def test_dropped_connection_is_not_an_auth_failure(self, access_token):
        """A recycled/severed connection raises InterfaceError, not Operational."""
        with (
            mock.patch("accounts.authentication.cache") as cache_mock,
            mock.patch("accounts.authentication.User") as user_mock,
        ):
            cache_mock.get.return_value = None
            user_mock.objects.select_related.return_value.get.side_effect = (
                InterfaceError("connection already closed")
            )

            with pytest.raises(DatabaseUnavailable):
                decode_token(access_token)

    def test_unreadable_token_is_still_an_auth_failure(self):
        """Regression guard: a genuinely bad credential stays a 401."""
        with pytest.raises(AuthenticationFailed):
            decode_token("not-a-real-token")


class TestAuthenticateDatabaseOutage:
    def test_outage_is_not_flattened_into_401_by_the_authenticator(self, access_token):
        """``authenticate`` has its own blanket except around ``decode_token``."""
        request = APIRequestFactory().get(
            "/probe/", HTTP_AUTHORIZATION=f"Bearer {access_token}"
        )

        with mock.patch(
            "accounts.authentication.decode_token", side_effect=RECOVERY_ERROR
        ):
            with pytest.raises(DatabaseUnavailable):
                APIKeyAuthentication().authenticate(request)

    def test_workspace_context_outage_is_not_flattened_into_401(self, access_token):
        """The org/workspace resolution after ``decode_token`` also reads PG."""
        request = APIRequestFactory().get(
            "/probe/", HTTP_AUTHORIZATION=f"Bearer {access_token}"
        )
        user = mock.Mock(is_active=True)

        with (
            mock.patch(
                "accounts.authentication.decode_token",
                return_value=(user, "token"),
            ),
            mock.patch.object(
                APIKeyAuthentication,
                "_set_workspace_context",
                side_effect=RECOVERY_ERROR,
            ),
        ):
            with pytest.raises(DatabaseUnavailable):
                APIKeyAuthentication().authenticate(request)


class TestDatabaseOutageResponseShape:
    @staticmethod
    def _response(access_token):
        request = APIRequestFactory().get(
            "/probe/", HTTP_AUTHORIZATION=f"Bearer {access_token}"
        )
        with mock.patch(
            "accounts.authentication.decode_token", side_effect=RECOVERY_ERROR
        ):
            return _ProbeView.as_view()(request)

    def test_client_sees_503_service_unavailable(self, access_token):
        response = self._response(access_token)

        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert response.data["type"] == "service_unavailable"
        assert response.data["code"] == "service_unavailable"
        assert response.data["status"] is False

    def test_driver_text_is_not_leaked_to_the_client(self, access_token):
        response = self._response(access_token)

        assert "recovery mode" not in str(response.data)
        assert "Invalid Token" not in str(response.data)

    def test_no_authentication_challenge_is_sent(self, access_token):
        """A 401 carries ``WWW-Authenticate``; an outage must not."""
        response = self._response(access_token)

        assert not response.has_header("WWW-Authenticate")
