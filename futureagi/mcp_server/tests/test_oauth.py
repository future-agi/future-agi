"""Tests for MCP OAuth utilities and models."""

import time
from datetime import UTC, datetime, timedelta, timezone
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


def _code_exchange_fixture(user, workspace, *, client_id, code_value, secret):
    from mcp_server.models.oauth_client import MCPOAuthClient
    from mcp_server.models.oauth_code import MCPOAuthCode
    from mcp_server.oauth_utils import hash_client_secret
    from tfc.middleware.workspace_context import set_workspace_context

    set_workspace_context(
        workspace=workspace, organization=user.organization, user=user
    )
    client = MCPOAuthClient.objects.create(
        client_id=client_id,
        client_secret_hash=hash_client_secret(secret),
        name=f"Client {client_id}",
        redirect_uris=["https://example.com/callback"],
    )
    if code_value is None:
        return client, None
    code = MCPOAuthCode.objects.create(
        code=code_value,
        client=client,
        user=user,
        organization=user.organization,
        workspace=workspace,
        redirect_uri="https://example.com/callback",
        scope=["evaluations"],
    )
    return client, code


def _post_code_exchange(code_value, client_id, secret, remote_addr="198.51.100.7"):
    from rest_framework.test import APIClient

    return APIClient().post(
        "/mcp/oauth/token/",
        {
            "grant_type": "authorization_code",
            "code": code_value,
            "client_id": client_id,
            "client_secret": secret,
            "redirect_uri": "https://example.com/callback",
        },
        format="json",
        REMOTE_ADDR=remote_addr,
    )


def _stored_access_tokens(user):
    from mcp_server.models.connection import MCPConnection

    return [
        c.oauth_token_encrypted
        for c in MCPConnection.no_workspace_objects.filter(user=user, deleted=False)
        if c.oauth_token_encrypted
    ]


@pytest.mark.django_db
class TestAuthorizationCodeExchangeRace:
    """The token view must mint tokens for an authorization code at most once,
    even when two exchanges both pass the ``used=False`` read."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from django.core.cache import cache

        cache.clear()
        yield
        cache.clear()

    def test_exchange_that_loses_the_claim_mints_no_token(self, user, workspace):
        """Another exchange consumes the code after this one read it: this one
        must answer invalid_grant and mint nothing."""
        from mcp_server.models.oauth_code import MCPOAuthCode

        _code_exchange_fixture(
            user, workspace, client_id="race-1", code_value="race-code-1", secret="s1"
        )

        def concurrent_winner_consumes(code_obj):
            MCPOAuthCode.objects.filter(pk=code_obj.pk).update(used=True)
            return False

        with (
            patch.object(
                MCPOAuthCode,
                "is_expired",
                property(concurrent_winner_consumes),
            ),
            patch("mcp_server.views.oauth.generate_oauth_token") as mint_access,
            patch("mcp_server.views.oauth.generate_refresh_token") as mint_refresh,
        ):
            mint_access.return_value = (
                "synthetic-access",
                datetime.now(UTC) + timedelta(hours=1),
            )
            mint_refresh.return_value = "synthetic-refresh"
            response = _post_code_exchange("race-code-1", "race-1", "s1")

        assert response.status_code == 400
        assert response.data["error"] == "invalid_grant"
        assert "access_token" not in response.data
        mint_access.assert_not_called()
        mint_refresh.assert_not_called()
        assert _stored_access_tokens(user) == []

    def test_expired_code_is_refused(self, user, workspace):
        from datetime import timedelta as td

        from django.utils import timezone as dj_tz

        from mcp_server.models.oauth_code import MCPOAuthCode

        _, code = _code_exchange_fixture(
            user, workspace, client_id="exp-1", code_value="exp-code-1", secret="s1"
        )
        MCPOAuthCode.objects.filter(pk=code.pk).update(
            created_at=dj_tz.now() - td(minutes=11)
        )

        response = _post_code_exchange("exp-code-1", "exp-1", "s1")

        assert response.status_code == 400
        assert response.data["error"] == "invalid_grant"
        assert "access_token" not in response.data
        assert _stored_access_tokens(user) == []

    def test_code_of_another_client_is_refused(self, user, workspace):
        """A different registered client, with its own valid secret, cannot
        redeem a code issued to someone else, and the code stays usable for
        the client it was issued to."""
        _, code = _code_exchange_fixture(
            user, workspace, client_id="owner-1", code_value="own-code-1", secret="s1"
        )
        _code_exchange_fixture(
            user, workspace, client_id="intruder-1", code_value=None, secret="s2"
        )

        foreign = _post_code_exchange("own-code-1", "intruder-1", "s2")

        assert foreign.status_code == 400
        assert foreign.data["error"] == "invalid_grant"
        assert "access_token" not in foreign.data
        code.refresh_from_db()
        assert code.used is False

        owner = _post_code_exchange(
            "own-code-1", "owner-1", "s1", remote_addr="198.51.100.8"
        )
        assert owner.status_code == 200
        assert owner.data["access_token"]


@pytest.mark.django_db(transaction=True)
def test_concurrent_code_exchanges_on_postgres_yield_one_token(user, workspace):
    """Two real threads, each with its own PostgreSQL connection, both read the
    unused code before either claims it. Exactly one may receive tokens."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from django.core.cache import cache
    from django.db import connection

    from mcp_server.models.oauth_code import MCPOAuthCode

    cache.clear()
    _code_exchange_fixture(
        user, workspace, client_id="pg-race", code_value="pg-race-code", secret="s1"
    )
    both_read = threading.Barrier(2)
    original_is_expired = MCPOAuthCode.is_expired

    def wait_for_both_reads(code_obj):
        both_read.wait(timeout=10)
        return original_is_expired.fget(code_obj)

    def exchange(index):
        try:
            return _post_code_exchange(
                "pg-race-code", "pg-race", "s1", remote_addr=f"198.51.100.{20 + index}"
            )
        finally:
            connection.close()

    try:
        with patch.object(MCPOAuthCode, "is_expired", property(wait_for_both_reads)):
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(exchange, range(2)))
    finally:
        cache.clear()

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 400], [r.data for r in responses]
    loser = next(r for r in responses if r.status_code == 400)
    assert loser.data["error"] == "invalid_grant"
    assert "access_token" not in loser.data
    assert MCPOAuthCode.objects.get(code="pg-race-code").used is True


class TestSDKTokenEndpointCodeReplay:
    """The MCP SDK ``/token`` route (FutureAGIOAuthProvider, cache-backed codes)
    must also redeem an authorization code at most once."""

    REDIRECT_URI = "https://client.example.com/callback"
    VERIFIER = "v" * 64

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        from django.core.cache import cache

        cache.clear()
        yield
        cache.clear()

    def _challenge(self):
        import base64
        import hashlib

        digest = hashlib.sha256(self.VERIFIER.encode()).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def _seed_code(self, client_id, code_value):
        from django.core.cache import cache
        from pydantic import AnyUrl

        from mcp_server.oauth_provider import (
            CODE_PREFIX,
            CODE_TTL,
            FutureAGIAuthorizationCode,
        )

        code = FutureAGIAuthorizationCode(
            code=code_value,
            scopes=["evaluations"],
            expires_at=time.time() + CODE_TTL,
            client_id=client_id,
            code_challenge=self._challenge(),
            redirect_uri=AnyUrl(self.REDIRECT_URI),
            redirect_uri_provided_explicitly=True,
            user_id="00000000-0000-0000-0000-000000000001",
            organization_id="00000000-0000-0000-0000-000000000002",
            workspace_id=None,
        )
        cache.set(f"{CODE_PREFIX}{code_value}", code.model_dump(mode="json"), CODE_TTL)

    def _app(self, monkeypatch, provider_cls=None):
        from mcp_server import mcp_app, oauth_provider

        monkeypatch.setattr(mcp_app, "_oauth_app", None)
        if provider_cls is not None:
            monkeypatch.setattr(oauth_provider, "FutureAGIOAuthProvider", provider_cls)
        return mcp_app.get_mcp_oauth_app()

    async def _register(self, http):
        response = await http.post(
            "/register",
            json={
                "redirect_uris": [self.REDIRECT_URI],
                "token_endpoint_auth_method": "client_secret_post",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "client_name": "Synthetic MCP client",
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    def _token_form(self, client, code_value):
        return {
            "grant_type": "authorization_code",
            "code": code_value,
            "redirect_uri": self.REDIRECT_URI,
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "code_verifier": self.VERIFIER,
        }

    async def test_concurrent_exchanges_of_one_code_yield_one_token(self, monkeypatch):
        import asyncio

        import httpx

        from mcp_server import oauth_provider

        both_loaded = asyncio.Barrier(2)

        class BothLoadFirstProvider(oauth_provider.FutureAGIOAuthProvider):
            async def load_authorization_code(self, client, authorization_code):
                code = await super().load_authorization_code(client, authorization_code)
                await asyncio.wait_for(both_loaded.wait(), timeout=10)
                return code

        app = self._app(monkeypatch, BothLoadFirstProvider)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as http:
            client = await self._register(http)
            self._seed_code(client["client_id"], "sdk-race-code")
            form = self._token_form(client, "sdk-race-code")
            responses = await asyncio.gather(
                http.post("/token", data=form), http.post("/token", data=form)
            )

        statuses = sorted(r.status_code for r in responses)
        assert statuses == [200, 400], [r.text for r in responses]
        loser = next(r for r in responses if r.status_code == 400)
        assert loser.json()["error"] == "invalid_grant"
        assert "access_token" not in loser.json()

    async def test_single_exchange_succeeds_and_replay_is_refused(self, monkeypatch):
        import httpx

        app = self._app(monkeypatch)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as http:
            client = await self._register(http)
            self._seed_code(client["client_id"], "sdk-code-ok")
            form = self._token_form(client, "sdk-code-ok")

            first = await http.post("/token", data=form)
            second = await http.post("/token", data=form)

        assert first.status_code == 200, first.text
        assert first.json()["access_token"]
        assert first.json()["refresh_token"]
        assert second.status_code == 400
        assert second.json()["error"] == "invalid_grant"

    async def test_code_of_another_client_is_refused(self, monkeypatch):
        import httpx

        app = self._app(monkeypatch)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as http:
            owner = await self._register(http)
            intruder = await self._register(http)
            self._seed_code(owner["client_id"], "sdk-owned-code")

            foreign = await http.post(
                "/token", data=self._token_form(intruder, "sdk-owned-code")
            )
            owner_exchange = await http.post(
                "/token", data=self._token_form(owner, "sdk-owned-code")
            )

        assert foreign.status_code == 400
        assert foreign.json()["error"] == "invalid_grant"
        assert owner_exchange.status_code == 200, owner_exchange.text
