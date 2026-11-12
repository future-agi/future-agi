"""Revoked OAuth access tokens must stop working on Streamable HTTP ``/mcp``.

An MCP OAuth access token is a self-contained encrypted payload, so decrypting
it proves only that this server minted it and that it has not expired. Every
``/mcp`` request must also confirm that nothing has revoked it since: the SDK
``/revoke`` endpoint, deletion or deactivation of the client it was issued to,
or deactivation of the user. A revoked token must get the documented 401
before any session bookkeeping, rate limiting, usage record or API dispatch.
"""

import base64
import hashlib
import secrets
import time
from contextlib import asynccontextmanager

import httpx
import pytest
from asgiref.sync import sync_to_async
from django.core.cache import cache
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mcp_server import mcp_app
from mcp_server.models.oauth_client import MCPOAuthClient
from mcp_server.models.session import MCPSession
from mcp_server.models.usage import MCPUsageRecord
from mcp_server.oauth_provider import (
    ACCESS_PREFIX,
    CLIENT_PREFIX,
    CODE_PREFIX,
    FutureAGIAuthorizationCode,
)
from mcp_server.oauth_utils import generate_oauth_token, hash_client_secret

pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

REDIRECT_URI = "http://127.0.0.1:33418/callback"


# ── Helpers ──────────────────────────────────────────────────────────


def _initialize_body():
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "revocation-test", "version": "1.0"},
        },
    }


async def _post_initialize(token):
    """POST a raw initialize so the 401 and its challenge can be observed."""
    mcp_app._streamable_app = None
    mcp_app._session_manager = None
    mcp_app.get_mcp_streamable_app()
    async with (
        mcp_app._session_manager.run(),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_app.mcp_streamable_with_auth),
            base_url="http://localhost",
        ) as http_client,
    ):
        return await http_client.post(
            "/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {token}",
            },
            json=_initialize_body(),
        )


@asynccontextmanager
async def _mcp_session(token, monkeypatch):
    monkeypatch.setattr(mcp_app, "_streamable_app", None)
    monkeypatch.setattr(mcp_app, "_session_manager", None)
    mcp_app.get_mcp_streamable_app()
    async with (
        mcp_app._session_manager.run(),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp_app.mcp_streamable_with_auth),
            base_url="http://localhost",
            headers={"Authorization": f"Bearer {token}"},
        ) as http_client,
        streamable_http_client("http://localhost/mcp", http_client=http_client) as (
            read,
            write,
            _,
        ),
    ):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _assert_invalid_token(response):
    assert response.status_code == 401, response.text
    assert response.json()["error"] == "invalid_token"
    assert "oauth-protected-resource" in response.headers["WWW-Authenticate"]


def _bookkeeping(user):
    return {
        "sessions": MCPSession.objects.filter(user=user).count(),
        "usage": MCPUsageRecord.objects.filter(session__user=user).count(),
    }


class _Spy:
    def __init__(self, wrapped):
        self.calls = 0
        self._wrapped = wrapped

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self._wrapped(*args, **kwargs)


class _AsyncSpy(_Spy):
    async def __call__(self, *args, **kwargs):
        self.calls += 1
        return await self._wrapped(*args, **kwargs)


@pytest.fixture
def side_effect_spies(monkeypatch):
    """Count dispatches and rate-limit checks made on behalf of a request."""
    from mcp_server import rate_limiter

    executor = _AsyncSpy(mcp_app.executor.execute)
    monkeypatch.setattr(mcp_app.executor, "execute", executor)
    limiter = _Spy(rate_limiter.check_rate_limit)
    monkeypatch.setattr(rate_limiter, "check_rate_limit", limiter)
    return {"executor": executor, "rate_limit": limiter}


# ── Django-registered clients (``/mcp/oauth/token/``) ────────────────


@pytest.fixture
def django_client(db):
    return MCPOAuthClient.objects.create(
        client_id="revocation-django-client",
        client_secret_hash=hash_client_secret("synthetic-secret"),
        name="Revocation Django Client",
        redirect_uris=[REDIRECT_URI],
    )


@pytest.fixture
def django_token(user, workspace, django_client):
    token, _ = generate_oauth_token(
        user.id,
        user.organization_id,
        workspace.id,
        django_client.client_id,
        ["context", "datasets"],
    )
    return token


async def test_django_client_token_is_accepted(django_token):
    """Positive control: an active client's token for an active user works."""
    response = await _post_initialize(django_token)

    assert response.status_code == 200, response.text


async def test_token_of_a_deactivated_django_client_is_rejected(
    django_client, django_token
):
    assert (await _post_initialize(django_token)).status_code == 200

    django_client.is_active = False
    await sync_to_async(django_client.save)(update_fields=["is_active"])

    _assert_invalid_token(await _post_initialize(django_token))


async def test_token_of_a_deleted_django_client_is_rejected(
    django_client, django_token
):
    assert (await _post_initialize(django_token)).status_code == 200

    await sync_to_async(django_client.delete)()

    _assert_invalid_token(await _post_initialize(django_token))


async def test_token_naming_an_unregistered_client_is_rejected(user, workspace):
    token, _ = await sync_to_async(generate_oauth_token)(
        user.id, user.organization_id, workspace.id, "never-registered", ["context"]
    )

    _assert_invalid_token(await _post_initialize(token))


async def test_token_of_a_deactivated_user_is_rejected(user, django_token):
    assert (await _post_initialize(django_token)).status_code == 200

    user.is_active = False
    await sync_to_async(user.save)(update_fields=["is_active"])

    _assert_invalid_token(await _post_initialize(django_token))


async def test_revocation_mid_session_stops_before_any_side_effect(
    user, django_client, django_token, side_effect_spies, monkeypatch
):
    """A live session must not get one more call once its client is revoked."""
    seen = {}
    with pytest.raises(BaseException) as refused:
        async with _mcp_session(django_token, monkeypatch) as session:
            allowed = await session.call_tool("whoami", {})
            seen["allowed"] = allowed.isError is False
            seen["executor"] = side_effect_spies["executor"].calls
            seen["rate_limit"] = side_effect_spies["rate_limit"].calls
            seen["bookkeeping"] = await sync_to_async(_bookkeeping)(user)

            django_client.is_active = False
            await sync_to_async(django_client.save)(update_fields=["is_active"])

            await session.call_tool("whoami", {})

    assert seen["allowed"] is True
    assert seen["executor"] == 1 and seen["rate_limit"] == 1
    statuses = [
        error.response.status_code
        for error in _leaf_exceptions(refused.value)
        if isinstance(error, httpx.HTTPStatusError)
    ]
    assert statuses == [401], repr(refused.value)
    assert side_effect_spies["executor"].calls == seen["executor"]
    assert side_effect_spies["rate_limit"].calls == seen["rate_limit"]
    assert await sync_to_async(_bookkeeping)(user) == seen["bookkeeping"]


def _leaf_exceptions(error):
    if isinstance(error, BaseExceptionGroup):
        for inner in error.exceptions:
            yield from _leaf_exceptions(inner)
    else:
        yield error


# ── MCP SDK clients (root ``/register``, ``/token``, ``/revoke``) ────


@asynccontextmanager
async def _oauth_http():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mcp_app.get_mcp_oauth_app()),
        base_url="http://localhost",
    ) as client:
        yield client


async def _sdk_register(http):
    response = await http.post(
        "/register",
        json={
            "redirect_uris": [REDIRECT_URI],
            "token_endpoint_auth_method": "client_secret_post",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "client_name": "Revocation SDK Client",
            "scope": "context datasets",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _sdk_issue_tokens(http, client, user, workspace):
    """Exchange a consented authorization code at the SDK ``/token`` route.

    The code is stored exactly as the consent step stores it, so the token
    comes out of the SDK handler and provider unmodified.
    """
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    code = secrets.token_urlsafe(32)
    auth_code = FutureAGIAuthorizationCode(
        code=code,
        scopes=["context", "datasets"],
        expires_at=time.time() + 600,
        client_id=client["client_id"],
        code_challenge=challenge,
        redirect_uri=REDIRECT_URI,
        redirect_uri_provided_explicitly=True,
        user_id=str(user.id),
        organization_id=str(user.organization_id),
        workspace_id=str(workspace.id),
    )
    await sync_to_async(cache.set)(
        f"{CODE_PREFIX}{code}", auth_code.model_dump(mode="json"), 600
    )
    response = await http.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_sdk_token_is_accepted(user, workspace):
    """Positive control: an SDK-issued token works until something revokes it."""
    async with _oauth_http() as http:
        client = await _sdk_register(http)
        tokens = await _sdk_issue_tokens(http, client, user, workspace)

    assert (await _post_initialize(tokens["access_token"])).status_code == 200


async def test_sdk_refreshed_token_is_accepted(user, workspace):
    """Positive control: a token from the SDK refresh grant works too."""
    async with _oauth_http() as http:
        client = await _sdk_register(http)
        tokens = await _sdk_issue_tokens(http, client, user, workspace)
        refreshed = await http.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
            },
        )
    assert refreshed.status_code == 200, refreshed.text

    assert (
        await _post_initialize(refreshed.json()["access_token"])
    ).status_code == 200


async def test_sdk_token_revoked_at_the_revoke_endpoint_is_rejected(
    user, workspace
):
    async with _oauth_http() as http:
        client = await _sdk_register(http)
        tokens = await _sdk_issue_tokens(http, client, user, workspace)
        assert (await _post_initialize(tokens["access_token"])).status_code == 200

        revoked = await http.post(
            "/revoke",
            data={
                "token": tokens["access_token"],
                "token_type_hint": "access_token",
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
            },
        )
    assert revoked.status_code == 200, revoked.text
    assert await sync_to_async(cache.get)(
        f"{ACCESS_PREFIX}{tokens['access_token']}"
    ) is None

    _assert_invalid_token(await _post_initialize(tokens["access_token"]))


async def test_token_of_a_deleted_sdk_client_is_rejected(user, workspace):
    async with _oauth_http() as http:
        client = await _sdk_register(http)
        tokens = await _sdk_issue_tokens(http, client, user, workspace)
    assert (await _post_initialize(tokens["access_token"])).status_code == 200

    await sync_to_async(cache.delete)(f"{CLIENT_PREFIX}{client['client_id']}")

    _assert_invalid_token(await _post_initialize(tokens["access_token"]))


async def test_sdk_token_of_a_deactivated_user_is_rejected(user, workspace):
    async with _oauth_http() as http:
        client = await _sdk_register(http)
        tokens = await _sdk_issue_tokens(http, client, user, workspace)
    assert (await _post_initialize(tokens["access_token"])).status_code == 200

    user.is_active = False
    await sync_to_async(user.save)(update_fields=["is_active"])

    _assert_invalid_token(await _post_initialize(tokens["access_token"]))


async def _sdk_revoke(http, client, token, hint):
    response = await http.post(
        "/revoke",
        data={
            "token": token,
            "token_type_hint": hint,
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
        },
    )
    assert response.status_code == 200, response.text


async def test_revoking_the_sdk_refresh_token_also_revokes_its_access_token(
    user, workspace
):
    """RFC 7009 2.1: revoking a refresh token ends its grant's access token."""
    async with _oauth_http() as http:
        client = await _sdk_register(http)
        revoked = await _sdk_issue_tokens(http, client, user, workspace)
        other = await _sdk_issue_tokens(http, client, user, workspace)
        assert (await _post_initialize(revoked["access_token"])).status_code == 200

        await _sdk_revoke(http, client, revoked["refresh_token"], "refresh_token")

    _assert_invalid_token(await _post_initialize(revoked["access_token"]))
    # Another grant of the same client is untouched.
    assert (await _post_initialize(other["access_token"])).status_code == 200


async def test_revoking_the_sdk_access_token_also_revokes_its_refresh_token(
    user, workspace
):
    async with _oauth_http() as http:
        client = await _sdk_register(http)
        tokens = await _sdk_issue_tokens(http, client, user, workspace)

        await _sdk_revoke(http, client, tokens["access_token"], "access_token")
        refreshed = await http.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": tokens["refresh_token"],
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
            },
        )

    assert refreshed.status_code == 400, refreshed.text
    assert refreshed.json()["error"] == "invalid_grant"
