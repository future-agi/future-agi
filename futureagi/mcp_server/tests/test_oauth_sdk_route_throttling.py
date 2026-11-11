"""Throttling of the MCP SDK OAuth routes mounted at the server root.

The requests go through tfc.asgi's OAuth app, the same object the ASGI router
sends /token, /revoke and /register to.
"""

import asyncio
import base64
import hashlib
import secrets
import time
import uuid

import httpx
import pytest
from django.core.cache import cache
from django.test import override_settings
from redis.exceptions import RedisError

from mcp_server import rate_limiter
from mcp_server.oauth_provider import (
    CODE_PREFIX,
    FutureAGIAuthorizationCode,
    FutureAGIOAuthProvider,
)

REDIRECT_URI = "http://127.0.0.1/callback"

LOCKOUT_SETTINGS = dict(
    MCP_OAUTH_TOKEN_IP_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT=2,
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_WINDOW_SECONDS=300,
    MCP_OAUTH_TOKEN_INVALID_LOCKOUT_SECONDS=60,
    MCP_OAUTH_REGISTER_IP_THROTTLE_RATE="100/hour",
)


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


def _oauth_app():
    import tfc.asgi

    return tfc.asgi._get_oauth_app()


def _client(addr="203.0.113.80"):
    transport = httpx.ASGITransport(app=_oauth_app(), client=(addr, 40000))
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


async def _register(addr="203.0.113.90"):
    async with _client(addr) as http:
        response = await http.post(
            "/register",
            json={
                "redirect_uris": [REDIRECT_URI],
                "token_endpoint_auth_method": "client_secret_post",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "scope": "context",
                "client_name": "Synthetic client",
            },
        )
    assert response.status_code == 201, response.text
    data = response.json()
    return data["client_id"], data["client_secret"]


def _seed_code(client_id):
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    code = secrets.token_urlsafe(32)
    cache.set(
        f"{CODE_PREFIX}{code}",
        FutureAGIAuthorizationCode(
            code=code,
            scopes=["context"],
            expires_at=time.time() + 600,
            client_id=client_id,
            code_challenge=challenge,
            redirect_uri=REDIRECT_URI,
            redirect_uri_provided_explicitly=True,
            user_id=str(uuid.uuid4()),
            organization_id=str(uuid.uuid4()),
            workspace_id=None,
        ).model_dump(mode="json"),
        600,
    )
    return code, verifier


def _code_form(client_id, secret, code="bad-code", verifier="v" * 43):
    return {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": secret,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    }


async def _post(path, form, addr="203.0.113.80"):
    async with _client(addr) as http:
        return await http.post(path, data=form)


def _assert_throttled(response, detail):
    assert response.status_code == 429, response.text
    assert response.json() == {"error": "slow_down", "error_description": detail}
    assert int(response.headers["Retry-After"]) > 0


async def _valid_flow_succeeds(client_id, secret, addr):
    """Authorized-user control: code exchange, refresh and revoke all work."""
    code, verifier = _seed_code(client_id)
    exchanged = await _post(
        "/token", _code_form(client_id, secret, code, verifier), addr
    )
    assert exchanged.status_code == 200, exchanged.text
    tokens = exchanged.json()
    refreshed = await _post(
        "/token",
        {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": secret,
            "refresh_token": tokens["refresh_token"],
        },
        addr,
    )
    assert refreshed.status_code == 200, refreshed.text
    revoked = await _post(
        "/revoke",
        {
            "client_id": client_id,
            "client_secret": secret,
            "token": refreshed.json()["access_token"],
        },
        addr,
    )
    assert revoked.status_code == 200, revoked.text


@override_settings(**LOCKOUT_SETTINGS)
async def test_sdk_token_wrong_client_secret_triggers_lockout():
    client_id, secret = await _register()
    addr = "203.0.113.81"
    for _ in range(2):
        response = await _post("/token", _code_form(client_id, "wrong-secret"), addr)
        assert response.status_code == 401
        assert response.json()["error"] == "unauthorized_client"

    _assert_throttled(
        await _post("/token", _code_form(client_id, "wrong-secret"), addr),
        "Too many invalid token attempts",
    )
    # The lockout also stops the right secret from this address.
    code, verifier = _seed_code(client_id)
    _assert_throttled(
        await _post("/token", _code_form(client_id, secret, code, verifier), addr),
        "Too many invalid token attempts",
    )

    # Another registered client from the same address is unaffected.
    other_id, other_secret = await _register()
    await _valid_flow_succeeds(other_id, other_secret, addr)


@override_settings(**LOCKOUT_SETTINGS)
async def test_sdk_token_invalid_codes_and_refresh_tokens_trigger_lockout():
    client_id, secret = await _register()
    addr = "203.0.113.82"
    bad_code = await _post("/token", _code_form(client_id, secret), addr)
    assert bad_code.status_code == 400
    assert bad_code.json()["error"] == "invalid_grant"
    bad_refresh = await _post(
        "/token",
        {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": secret,
            "refresh_token": "not-a-refresh-token",
        },
        addr,
    )
    assert bad_refresh.status_code == 400
    assert bad_refresh.json()["error"] == "invalid_grant"

    _assert_throttled(
        await _post("/token", _code_form(client_id, secret), addr),
        "Too many invalid token attempts",
    )


@override_settings(**LOCKOUT_SETTINGS)
async def test_sdk_revoke_wrong_client_secret_triggers_lockout():
    client_id, _secret = await _register()
    addr = "203.0.113.83"
    form = {"client_id": client_id, "client_secret": "wrong", "token": "t"}
    for _ in range(2):
        response = await _post("/revoke", form, addr)
        assert response.status_code == 401
    _assert_throttled(
        await _post("/revoke", form, addr), "Too many invalid token attempts"
    )


@override_settings(**{**LOCKOUT_SETTINGS, "MCP_OAUTH_TOKEN_IP_THROTTLE_RATE": "2/min"})
async def test_sdk_token_and_revoke_share_the_per_address_window():
    addr = "203.0.113.84"
    first = await _post("/token", _code_form("unknown-1", "x"), addr)
    second = await _post("/revoke", {"client_id": "unknown-2", "token": "t"}, addr)
    assert first.status_code == 401
    assert second.status_code == 401
    _assert_throttled(
        await _post("/token", _code_form("unknown-3", "x"), addr),
        "Too many token requests",
    )


@override_settings(
    **{**LOCKOUT_SETTINGS, "MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE": "2/min"}
)
async def test_sdk_token_per_client_window_spans_addresses():
    client_id, secret = await _register()
    for i in range(2):
        response = await _post(
            "/token", _code_form(client_id, secret), f"203.0.113.{100 + i}"
        )
        assert response.status_code == 400
    _assert_throttled(
        await _post("/token", _code_form(client_id, secret), "203.0.113.110"),
        "Too many token requests",
    )


@override_settings(
    **{**LOCKOUT_SETTINGS, "MCP_OAUTH_REGISTER_IP_THROTTLE_RATE": "2/hour"}
)
async def test_sdk_register_is_limited_per_address():
    await _register("203.0.113.85")
    await _register("203.0.113.85")
    async with _client("203.0.113.85") as http:
        refused = await http.post(
            "/register",
            json={"redirect_uris": [REDIRECT_URI], "client_name": "Synthetic"},
        )
    _assert_throttled(refused, "Too many client registrations")
    # Another address can still register.
    await _register("203.0.113.86")


@override_settings(**LOCKOUT_SETTINGS)
async def test_sdk_token_concurrent_burst_gets_at_most_the_limit(monkeypatch):
    client_id, _secret = await _register()
    real_get_client = FutureAGIOAuthProvider.get_client

    async def slow_get_client(self, cid):
        # Hold every admitted request until the whole burst has arrived.
        await asyncio.sleep(0.5)
        return await real_get_client(self, cid)

    monkeypatch.setattr(FutureAGIOAuthProvider, "get_client", slow_get_client)
    responses = await asyncio.gather(
        *[
            _post("/token", _code_form(client_id, "wrong-secret"), "203.0.113.87")
            for _ in range(6)
        ]
    )
    statuses = sorted(r.status_code for r in responses)
    assert statuses == [401, 401, 429, 429, 429, 429]
    assert all(
        r.json()["error"] == "slow_down" for r in responses if r.status_code == 429
    )


@override_settings(**{**LOCKOUT_SETTINGS, "MCP_RATE_LIMIT_ALLOW_LOCAL_FALLBACK": False})
async def test_sdk_token_fails_closed_when_redis_is_unavailable(monkeypatch):
    def failing_script(**kwargs):
        raise RedisError("redis unavailable")

    monkeypatch.setattr(rate_limiter, "_redis_script", lambda lua: failing_script)
    response = await _post("/token", _code_form("any-client", "x"), "203.0.113.88")
    _assert_throttled(response, "Token throttling temporarily unavailable")
    assert response.headers["Retry-After"] == "60"


@override_settings(**LOCKOUT_SETTINGS)
async def test_sdk_valid_flow_and_metadata_are_unchanged():
    client_id, secret = await _register()
    await _valid_flow_succeeds(client_id, secret, "203.0.113.89")
    async with _client() as http:
        metadata = await http.get("/.well-known/oauth-authorization-server")
    assert metadata.status_code == 200
    assert metadata.json()["token_endpoint"].endswith("/token")


@override_settings(**LOCKOUT_SETTINGS)
async def test_sdk_token_rejects_oversized_body():
    async with _client() as http:
        response = await http.post(
            "/token",
            content=b"client_id=x&pad=" + b"a" * (70 * 1024),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    assert response.status_code == 413
    assert response.json()["error"] == "invalid_request"
