"""Tests for MCP OAuth utilities and models."""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from mcp_server.oauth_utils import (
    decrypt_oauth_token,
    generate_authorization_code,
    generate_oauth_token,
    generate_refresh_token,
    hash_client_secret,
    verify_client_secret,
)


class TestGenerateAuthorizationCode:
    def test_returns_string(self):
        code = generate_authorization_code()
        assert isinstance(code, str)
        assert len(code) > 20

    def test_unique_codes(self):
        codes = {generate_authorization_code() for _ in range(50)}
        assert len(codes) == 50


class TestOAuthTokenRoundTrip:
    def test_access_token_round_trip(self):
        token, expires_at = generate_oauth_token(
            user_id="user-123",
            org_id="org-456",
            workspace_id="ws-789",
            client_id="client-abc",
            scope=["evaluations", "datasets"],
        )
        assert isinstance(token, str)
        assert isinstance(expires_at, datetime)

        payload = decrypt_oauth_token(token)
        assert payload is not None
        assert payload["type"] == "mcp_oauth"
        assert payload["user_id"] == "user-123"
        assert payload["org_id"] == "org-456"
        assert payload["workspace_id"] == "ws-789"
        assert payload["client_id"] == "client-abc"
        assert payload["scope"] == ["evaluations", "datasets"]

    def test_access_token_with_null_workspace(self):
        token, _ = generate_oauth_token(
            user_id="u1",
            org_id="o1",
            workspace_id=None,
            client_id="c1",
            scope=[],
        )
        payload = decrypt_oauth_token(token)
        assert payload is not None
        assert payload["workspace_id"] is None

    def test_refresh_token_round_trip(self):
        token = generate_refresh_token(
            user_id="user-123",
            org_id="org-456",
            client_id="client-abc",
        )
        assert isinstance(token, str)

        payload = decrypt_oauth_token(token)
        assert payload is not None
        assert payload["type"] == "mcp_refresh"
        assert payload["user_id"] == "user-123"
        assert payload["org_id"] == "org-456"
        assert payload["client_id"] == "client-abc"


class TestExpiredToken:
    def test_expired_access_token_returns_none(self):
        # Generate a token that expired 1 second ago
        with patch("mcp_server.oauth_utils.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2020, 1, 1, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            token, _ = generate_oauth_token(
                user_id="u1",
                org_id="o1",
                workspace_id=None,
                client_id="c1",
                scope=[],
                expires_in=1,
            )

        # Token was created with expires_at = 2020-01-01 00:00:01 UTC
        # Current time is now >> that, so it should be expired
        payload = decrypt_oauth_token(token)
        assert payload is None

    def test_refresh_token_does_not_expire(self):
        with patch("mcp_server.oauth_utils.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2020, 1, 1, tzinfo=timezone.utc)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            token = generate_refresh_token(
                user_id="u1",
                org_id="o1",
                client_id="c1",
            )

        # Refresh tokens have no expiry check
        payload = decrypt_oauth_token(token)
        assert payload is not None
        assert payload["type"] == "mcp_refresh"


class TestInvalidToken:
    def test_garbage_string_returns_none(self):
        assert decrypt_oauth_token("not-a-real-token") is None

    def test_empty_string_returns_none(self):
        assert decrypt_oauth_token("") is None


class TestClientSecretHashing:
    def test_hash_and_verify(self):
        secret = "my-super-secret-value"
        hashed = hash_client_secret(secret)
        assert isinstance(hashed, str)
        assert hashed != secret
        assert verify_client_secret(secret, hashed)

    def test_wrong_secret_fails(self):
        hashed = hash_client_secret("correct-secret")
        assert not verify_client_secret("wrong-secret", hashed)

    def test_deterministic_hash(self):
        h1 = hash_client_secret("same")
        h2 = hash_client_secret("same")
        assert h1 == h2


@pytest.mark.django_db
class TestMCPOAuthCodeModel:
    def test_is_expired_false_for_fresh_code(self, user, workspace):
        from mcp_server.models.oauth_client import MCPOAuthClient
        from mcp_server.models.oauth_code import MCPOAuthCode
        from mcp_server.oauth_utils import hash_client_secret
        from tfc.middleware.workspace_context import set_workspace_context

        set_workspace_context(
            workspace=workspace, organization=user.organization, user=user
        )

        client = MCPOAuthClient.objects.create(
            client_id="test-client",
            client_secret_hash=hash_client_secret("secret"),
            name="Test Client",
            redirect_uris=["https://example.com/callback"],
        )
        code = MCPOAuthCode.objects.create(
            code="test-code-123",
            client=client,
            user=user,
            organization=user.organization,
            workspace=workspace,
            redirect_uri="https://example.com/callback",
            scope=["evaluations"],
        )
        assert not code.is_expired
        assert not code.used

    def test_is_expired_true_after_ttl(self, user, workspace):
        from django.utils import timezone as dj_timezone

        from mcp_server.models.oauth_client import MCPOAuthClient
        from mcp_server.models.oauth_code import MCPOAuthCode
        from mcp_server.oauth_utils import hash_client_secret
        from tfc.middleware.workspace_context import set_workspace_context

        set_workspace_context(
            workspace=workspace, organization=user.organization, user=user
        )

        client = MCPOAuthClient.objects.create(
            client_id="test-client-2",
            client_secret_hash=hash_client_secret("secret"),
            name="Test Client 2",
            redirect_uris=["https://example.com/callback"],
        )
        code = MCPOAuthCode.objects.create(
            code="test-code-expired",
            client=client,
            user=user,
            organization=user.organization,
            workspace=workspace,
            redirect_uri="https://example.com/callback",
            scope=[],
        )
        # Manually set created_at to 11 minutes ago
        MCPOAuthCode.objects.filter(pk=code.pk).update(
            created_at=dj_timezone.now() - timedelta(minutes=11)
        )
        code.refresh_from_db()
        assert code.is_expired


@pytest.mark.django_db
class TestAuthorizationCodeConsumption:
    """Authorization codes are single-use bearer grants (see issue #1134)."""

    def _setup_code(self, user, workspace, *, client_id, code_value):
        from mcp_server.models.oauth_client import MCPOAuthClient
        from mcp_server.models.oauth_code import MCPOAuthCode
        from mcp_server.oauth_utils import hash_client_secret
        from tfc.middleware.workspace_context import set_workspace_context

        set_workspace_context(
            workspace=workspace, organization=user.organization, user=user
        )
        client = MCPOAuthClient.objects.create(
            client_id=client_id,
            client_secret_hash=hash_client_secret("test-secret"),
            name="Test Client",
            redirect_uris=["https://example.com/callback"],
        )
        MCPOAuthCode.objects.create(
            code=code_value,
            client=client,
            user=user,
            organization=user.organization,
            workspace=workspace,
            redirect_uri="https://example.com/callback",
            scope=["evaluations"],
        )
        return client

    def _exchange(self, code_value, client_id):
        from rest_framework.test import APIClient

        return APIClient().post(
            "/mcp/oauth/token/",
            {
                "grant_type": "authorization_code",
                "code": code_value,
                "client_id": client_id,
                "client_secret": "test-secret",
                "redirect_uri": "https://example.com/callback",
            },
            format="json",
        )

    def test_valid_code_exchange_succeeds(self, user, workspace):
        """Regression guard: the happy path still mints tokens."""
        self._setup_code(user, workspace, client_id="cc-1", code_value="code-ok")

        response = self._exchange("code-ok", "cc-1")

        assert response.status_code == 200
        assert response.data["access_token"]
        assert response.data["refresh_token"]
        assert response.data["token_type"] == "Bearer"

    def test_reused_code_is_rejected(self, user, workspace):
        """Replaying a consumed code must fail with invalid_grant."""
        self._setup_code(user, workspace, client_id="cc-2", code_value="code-replay")

        first = self._exchange("code-replay", "cc-2")
        assert first.status_code == 200

        second = self._exchange("code-replay", "cc-2")
        assert second.status_code == 400
        assert second.data["error"] == "invalid_grant"

    def test_concurrent_claim_yields_single_winner(self, user, workspace):
        """Simulate two simultaneous exchanges: the atomic compare-and-swap
        lets exactly one flip used False->True. The second claim affects 0 rows,
        which is what drives the invalid_grant response in the view."""
        from mcp_server.models.oauth_code import MCPOAuthCode

        self._setup_code(user, workspace, client_id="cc-3", code_value="code-race")
        code = MCPOAuthCode.objects.get(code="code-race")

        first_claim = MCPOAuthCode.objects.filter(pk=code.pk, used=False).update(
            used=True
        )
        second_claim = MCPOAuthCode.objects.filter(pk=code.pk, used=False).update(
            used=True
        )

        assert first_claim == 1
        assert second_claim == 0


@pytest.mark.unit
class TestConsentRedirectBase:
    """The consent page lives on the UI that every other link names,
    settings.FRONTEND_BASE_URL (FRONTEND_URL, else APP_BASE_URL)."""

    def test_uses_the_frontend_base_url(self, monkeypatch, settings):
        from mcp_server.oauth_provider import FutureAGIOAuthProvider

        monkeypatch.delenv("FRONTEND_URL", raising=False)
        settings.APP_BASE_URL = "https://app.example.com"
        settings.FRONTEND_BASE_URL = "https://ui.example.com"

        assert FutureAGIOAuthProvider().frontend_url == "https://ui.example.com"

    def test_without_one_the_dev_ui(self, settings):
        from mcp_server.oauth_provider import FutureAGIOAuthProvider

        settings.FRONTEND_BASE_URL = ""

        assert FutureAGIOAuthProvider().frontend_url == "http://localhost:3031"

    @pytest.mark.parametrize(
        "frontend_base_url, consent_base",
        [
            ("https://app.example.com", "https://app.example.com"),
            ("https://ui.example.com", "https://ui.example.com"),
        ],
    )
    def test_the_oauth_app_gives_its_provider_the_consent_base(
        self, monkeypatch, settings, frontend_base_url, consent_base
    ):
        from mcp_server import mcp_app, oauth_provider

        monkeypatch.delenv("FRONTEND_URL", raising=False)
        settings.APP_BASE_URL = "https://app.example.com"
        settings.FRONTEND_BASE_URL = frontend_base_url
        monkeypatch.setattr(mcp_app, "_oauth_app", None)
        providers = []

        class RecordingProvider(oauth_provider.FutureAGIOAuthProvider):
            def __init__(self, frontend_url=None):
                super().__init__(frontend_url=frontend_url)
                providers.append(self)

        monkeypatch.setattr(oauth_provider, "FutureAGIOAuthProvider", RecordingProvider)

        app = mcp_app.get_mcp_oauth_app()

        [provider] = providers
        assert provider.frontend_url == consent_base
        paths = {route.path for route in app.routes}
        assert {"/authorize", "/token", "/register"} <= paths
        # Built once per process.
        assert mcp_app.get_mcp_oauth_app() is app and len(providers) == 1
