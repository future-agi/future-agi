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
