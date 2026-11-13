"""MCP OAuth refresh tokens expire, rotate on every use, and a replayed
refresh token revokes its family. Covers both token routes:
``/mcp/oauth/token/`` (DRF view) and ``/token`` (MCP SDK provider)."""

import base64
import contextlib
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from mcp_server.models.oauth_client import MCPOAuthClient
from mcp_server.models.oauth_code import MCPOAuthCode
from mcp_server.oauth_utils import (
    decrypt_oauth_token,
    generate_refresh_token,
    hash_client_secret,
)
from tfc.middleware.workspace_context import set_workspace_context

REDIRECT = "https://example.com/callback"
SECRET = "synthetic-secret"


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _relaxed_throttles(settings):
    # These tests exercise token lifecycle, not throttling.
    settings.MCP_OAUTH_TOKEN_IP_THROTTLE_RATE = "1000/min"
    settings.MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE = "1000/min"
    settings.MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT = 1000


# ── DRF route: /mcp/oauth/token/ ─────────────────────────────────────────


def _client(client_id):
    return MCPOAuthClient.objects.create(
        client_id=client_id,
        client_secret_hash=hash_client_secret(SECRET),
        name=client_id,
        redirect_uris=[REDIRECT],
    )


def _code(user, workspace, client, value):
    set_workspace_context(
        workspace=workspace, organization=user.organization, user=user
    )
    MCPOAuthCode.objects.create(
        code=value,
        client=client,
        user=user,
        organization=user.organization,
        workspace=workspace,
        redirect_uri=REDIRECT,
        scope=["evaluations"],
    )


def _post(payload, remote_addr="198.51.100.30"):
    from rest_framework.test import APIClient

    return APIClient().post(
        "/mcp/oauth/token/", payload, format="json", REMOTE_ADDR=remote_addr
    )


def _authorize(user, workspace, client_id="rt-client", code="rt-code"):
    client = MCPOAuthClient.objects.filter(client_id=client_id).first() or _client(
        client_id
    )
    _code(user, workspace, client, code)
    response = _post(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "client_secret": SECRET,
            "redirect_uri": REDIRECT,
        }
    )
    assert response.status_code == 200, response.data
    return response.data


def _refresh(token, client_id="rt-client", remote_addr="198.51.100.30"):
    return _post(
        {
            "grant_type": "refresh_token",
            "refresh_token": token,
            "client_id": client_id,
            "client_secret": SECRET,
        },
        remote_addr=remote_addr,
    )


@pytest.mark.django_db
class TestDRFRefreshRotation:
    def test_refresh_returns_a_new_refresh_token_that_works(self, user, workspace):
        r0 = _authorize(user, workspace)["refresh_token"]

        first = _refresh(r0)
        assert first.status_code == 200, first.data
        r1 = first.data.get("refresh_token")
        assert r1 and r1 != r0
        assert first.data["access_token"]

        second = _refresh(r1)
        assert second.status_code == 200, second.data
        assert second.data["refresh_token"] not in (r0, r1)

    def test_used_refresh_token_is_refused_and_revokes_its_family(
        self, user, workspace
    ):
        r0 = _authorize(user, workspace)["refresh_token"]
        r1 = _refresh(r0).data["refresh_token"]

        replay = _refresh(r0)
        assert replay.status_code == 400
        assert replay.data["error"] == "invalid_grant"
        assert "access_token" not in replay.data

        # The successor issued before the replay is now revoked too.
        after = _refresh(r1)
        assert after.status_code == 400
        assert after.data["error"] == "invalid_grant"

    def test_refresh_token_expires(self, user, workspace):
        with override_settings(MCP_OAUTH_REFRESH_TOKEN_TTL_SECONDS=1):
            r0 = _authorize(user, workspace)["refresh_token"]
        time.sleep(1.5)

        response = _refresh(r0)

        assert response.status_code == 400
        assert response.data["error"] == "invalid_grant"
        assert "access_token" not in response.data

    @override_settings(MCP_OAUTH_REFRESH_TOKEN_TTL_SECONDS=7200)
    def test_lifetime_is_configurable_and_restarts_on_rotation(self, user, workspace):
        from mcp_server.models.oauth_refresh_token import MCPOAuthRefreshToken
        from mcp_server.oauth_utils import hash_refresh_token

        r0 = _authorize(user, workspace)["refresh_token"]
        record = MCPOAuthRefreshToken.objects.get(token_hash=hash_refresh_token(r0))
        assert abs(
            (record.expires_at - timezone.now()) - timedelta(seconds=7200)
        ) < timedelta(seconds=60)
        assert decrypt_oauth_token(r0)["expires_at"]

        r1 = _refresh(r0).data["refresh_token"]
        successor = MCPOAuthRefreshToken.objects.get(token_hash=hash_refresh_token(r1))
        assert successor.family_id == record.family_id
        assert successor.expires_at >= record.expires_at

    def test_default_lifetime_is_thirty_days(self, user, workspace):
        from mcp_server.models.oauth_refresh_token import MCPOAuthRefreshToken
        from mcp_server.oauth_utils import hash_refresh_token

        r0 = _authorize(user, workspace)["refresh_token"]
        record = MCPOAuthRefreshToken.objects.get(token_hash=hash_refresh_token(r0))
        assert abs(
            (record.expires_at - timezone.now()) - timedelta(days=30)
        ) < timedelta(minutes=5)

    def test_refresh_token_of_another_client_is_refused(self, user, workspace):
        r0 = _authorize(user, workspace)["refresh_token"]
        _client("rt-other")

        response = _refresh(r0, client_id="rt-other")

        assert response.status_code == 400
        assert response.data["error"] == "invalid_grant"
        # The owner can still use it.
        assert _refresh(r0).status_code == 200

    def test_new_authorization_supersedes_the_clients_previous_refresh_token(
        self, user, workspace
    ):
        old = _authorize(user, workspace, code="rt-code-a")["refresh_token"]
        new = _authorize(user, workspace, code="rt-code-b")["refresh_token"]

        assert _refresh(old).status_code == 400
        assert _refresh(new).status_code == 200

    def test_refresh_token_issued_before_rotation_keeps_working_once_then_rotates(
        self, user, workspace
    ):
        """A token from before this change (no expiry, no server record) that
        is still the connection's current token is honoured, given a lifetime,
        and rotated; replaying it afterwards is refused."""
        from mcp_server.models.connection import MCPConnection

        _client("rt-client")
        set_workspace_context(
            workspace=workspace, organization=user.organization, user=user
        )
        legacy = generate_refresh_token(
            user_id=user.id, org_id=user.organization.id, client_id="rt-client"
        )
        MCPConnection.no_workspace_objects.create(
            user=user,
            organization=user.organization,
            workspace=workspace,
            oauth_refresh_token_encrypted=legacy,
        )

        first = _refresh(legacy)
        assert first.status_code == 200, first.data
        assert first.data["refresh_token"] != legacy
        assert _refresh(legacy).status_code == 400

    def test_migration_gives_existing_refresh_tokens_an_expiry(self, user, workspace):
        import importlib

        from django.apps import apps
        from django.db import connection as db_connection

        from mcp_server.models.connection import MCPConnection
        from mcp_server.models.oauth_refresh_token import MCPOAuthRefreshToken
        from mcp_server.oauth_utils import hash_refresh_token

        set_workspace_context(
            workspace=workspace, organization=user.organization, user=user
        )
        legacy = generate_refresh_token(
            user_id=user.id, org_id=user.organization.id, client_id="rt-client"
        )
        MCPConnection.no_workspace_objects.create(
            user=user,
            organization=user.organization,
            workspace=workspace,
            oauth_refresh_token_encrypted=legacy,
        )
        migration = importlib.import_module(
            "mcp_server.migrations.0005_oauth_refresh_token_families"
        )

        class _SchemaEditor:
            connection = db_connection

        migration.give_existing_refresh_tokens_an_expiry(apps, _SchemaEditor())

        record = MCPOAuthRefreshToken.objects.get(token_hash=hash_refresh_token(legacy))
        assert record.used_at is None
        assert record.family.client_id == "rt-client"
        assert abs(
            (record.expires_at - timezone.now()) - timedelta(days=30)
        ) < timedelta(minutes=5)


@pytest.mark.django_db(transaction=True)
def test_drf_concurrent_refresh_with_one_token_yields_one_success(user, workspace):
    """Two threads with separate PostgreSQL connections both decode the same
    refresh token before either rotates it. Exactly one may succeed."""
    from django.db import connection

    from mcp_server.views import oauth as oauth_views

    r0 = _authorize(user, workspace, code="rt-race-code")["refresh_token"]
    both_decoded = threading.Barrier(2)
    real_decrypt = oauth_views.decrypt_oauth_token

    def decrypt_then_wait(token):
        payload = real_decrypt(token)
        both_decoded.wait(timeout=10)
        return payload

    def refresh(index):
        try:
            return _refresh(r0, remote_addr=f"198.51.100.{40 + index}")
        finally:
            connection.close()

    try:
        with patch.object(oauth_views, "decrypt_oauth_token", decrypt_then_wait):
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(refresh, range(2)))
    finally:
        cache.clear()

    statuses = sorted(r.status_code for r in responses)
    assert statuses == [200, 400], [r.data for r in responses]
    loser = next(r for r in responses if r.status_code == 400)
    assert loser.data["error"] == "invalid_grant"
    assert "access_token" not in loser.data


# ── MCP SDK route: /token ────────────────────────────────────────────────

SDK_REDIRECT = "https://client.example.com/callback"
VERIFIER = "w" * 64


def _challenge():
    digest = hashlib.sha256(VERIFIER.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _seed_sdk_code(client_id, code_value):
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
        code_challenge=_challenge(),
        redirect_uri=AnyUrl(SDK_REDIRECT),
        redirect_uri_provided_explicitly=True,
        user_id="00000000-0000-0000-0000-000000000001",
        organization_id="00000000-0000-0000-0000-000000000002",
        workspace_id=None,
    )
    cache.set(f"{CODE_PREFIX}{code_value}", code.model_dump(mode="json"), CODE_TTL)


def _sdk_app(monkeypatch, provider_cls=None):
    from mcp_server import mcp_app, oauth_provider

    monkeypatch.setattr(mcp_app, "_oauth_app", None)
    if provider_cls is not None:
        monkeypatch.setattr(oauth_provider, "FutureAGIOAuthProvider", provider_cls)
    return mcp_app.get_mcp_oauth_app()


class _SDK:
    def __init__(self, http):
        self.http = http
        self.client = None

    async def register(self):
        response = await self.http.post(
            "/register",
            json={
                "redirect_uris": [SDK_REDIRECT],
                "token_endpoint_auth_method": "client_secret_post",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "client_name": "Synthetic MCP client",
            },
        )
        assert response.status_code == 201, response.text
        self.client = response.json()

    async def authorize(self, code_value):
        _seed_sdk_code(self.client["client_id"], code_value)
        response = await self.http.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code_value,
                "redirect_uri": SDK_REDIRECT,
                "client_id": self.client["client_id"],
                "client_secret": self.client["client_secret"],
                "code_verifier": VERIFIER,
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    def refresh(self, token):
        return self.http.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": token,
                "client_id": self.client["client_id"],
                "client_secret": self.client["client_secret"],
            },
        )


@contextlib.asynccontextmanager
async def _sdk_session(monkeypatch, provider_cls=None):
    import httpx

    app = _sdk_app(monkeypatch, provider_cls)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        sdk = _SDK(http)
        await sdk.register()
        yield sdk


class TestSDKRefreshRotation:
    async def test_rotation_and_reuse_revokes_family(self, monkeypatch):
        async with _sdk_session(monkeypatch) as sdk:
            r0 = (await sdk.authorize("sdk-rt-1"))["refresh_token"]
            first = await sdk.refresh(r0)
            assert first.status_code == 200, first.text
            r1 = first.json()["refresh_token"]
            assert r1 != r0

            replay = await sdk.refresh(r0)
            assert replay.status_code == 400
            assert replay.json()["error"] == "invalid_grant"

            after = await sdk.refresh(r1)
            assert after.status_code == 400, after.text
            assert after.json()["error"] == "invalid_grant"

    async def test_rotation_chain_keeps_working(self, monkeypatch):
        async with _sdk_session(monkeypatch) as sdk:
            token = (await sdk.authorize("sdk-rt-2"))["refresh_token"]
            for _ in range(3):
                response = await sdk.refresh(token)
                assert response.status_code == 200, response.text
                token = response.json()["refresh_token"]

    async def test_refresh_token_expires(self, monkeypatch, settings):
        settings.MCP_OAUTH_REFRESH_TOKEN_TTL_SECONDS = 1
        async with _sdk_session(monkeypatch) as sdk:
            r0 = (await sdk.authorize("sdk-rt-3"))["refresh_token"]
            time.sleep(1.5)
            response = await sdk.refresh(r0)

        assert response.status_code == 400
        assert response.json()["error"] == "invalid_grant"

    async def test_concurrent_refresh_with_one_token_yields_one_success(
        self, monkeypatch
    ):
        import asyncio

        from mcp_server import oauth_provider

        both_loaded = asyncio.Barrier(2)

        class BothLoadFirst(oauth_provider.FutureAGIOAuthProvider):
            async def load_refresh_token(self, client, refresh_token):
                token = await super().load_refresh_token(client, refresh_token)
                await asyncio.wait_for(both_loaded.wait(), timeout=10)
                return token

        async with _sdk_session(monkeypatch, BothLoadFirst) as sdk:
            r0 = (await sdk.authorize("sdk-rt-4"))["refresh_token"]
            responses = await asyncio.gather(sdk.refresh(r0), sdk.refresh(r0))

        statuses = sorted(r.status_code for r in responses)
        assert statuses == [200, 400], [r.text for r in responses]
        loser = next(r for r in responses if r.status_code == 400)
        assert loser.json()["error"] == "invalid_grant"


@pytest.mark.django_db
def test_late_duplicate_of_an_adopted_legacy_token_is_seen_as_reuse(user, workspace):
    """A legacy token that another request already adopted and rotated (so the
    connection no longer holds it) must resolve to its used record, not to
    'not found', so the replay revokes the family."""
    from mcp_server.models.connection import MCPConnection
    from mcp_server.oauth_utils import hash_refresh_token
    from mcp_server.refresh_tokens import _adopt_legacy_token, rotate_refresh_token

    _client("rt-client")
    set_workspace_context(
        workspace=workspace, organization=user.organization, user=user
    )
    legacy = generate_refresh_token(
        user_id=user.id, org_id=user.organization.id, client_id="rt-client"
    )
    connection = MCPConnection.no_workspace_objects.create(
        user=user,
        organization=user.organization,
        workspace=workspace,
        oauth_refresh_token_encrypted=legacy,
    )
    rotation = rotate_refresh_token(
        legacy,
        client_id="rt-client",
        user_id=str(user.id),
        org_id=str(user.organization.id),
    )
    assert rotation.ok
    connection.oauth_refresh_token_encrypted = rotation.refresh_token
    connection.save(update_fields=["oauth_refresh_token_encrypted"])

    record = _adopt_legacy_token(
        legacy, hash_refresh_token(legacy), client_id="rt-client", user_id=str(user.id)
    )

    assert record is not None and record.used_at is not None


async def test_sdk_concurrent_refresh_of_a_legacy_token_revokes_the_winner(monkeypatch):
    """Two concurrent refreshes of a refresh token cached before families
    existed: one gets 200, and its successor is revoked like any other
    concurrent duplicate."""
    import asyncio

    from mcp_server import oauth_provider

    both_loaded = asyncio.Barrier(2)
    racing = {"on": True}

    class BothLoadFirst(oauth_provider.FutureAGIOAuthProvider):
        async def load_refresh_token(self, client, refresh_token):
            token = await super().load_refresh_token(client, refresh_token)
            if racing["on"]:
                await asyncio.wait_for(both_loaded.wait(), timeout=10)
            return token

    async with _sdk_session(monkeypatch, BothLoadFirst) as sdk:
        legacy = "legacy-refresh-token-without-family"
        cache.set(
            f"{oauth_provider.REFRESH_PREFIX}{legacy}",
            {
                "token": legacy,
                "client_id": sdk.client["client_id"],
                "scopes": ["evaluations"],
                "expires_at": None,
                "user_id": "00000000-0000-0000-0000-000000000001",
                "organization_id": "00000000-0000-0000-0000-000000000002",
                "workspace_id": None,
            },
            3600,
        )
        responses = await asyncio.gather(sdk.refresh(legacy), sdk.refresh(legacy))
        statuses = sorted(r.status_code for r in responses)
        assert statuses == [200, 400], [r.text for r in responses]
        winner = next(r for r in responses if r.status_code == 200)
        racing["on"] = False
        successor = await sdk.refresh(winner.json()["refresh_token"])

    assert successor.status_code == 400, successor.text
    assert successor.json()["error"] == "invalid_grant"
