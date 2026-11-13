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
from django.conf import settings
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

LOCKOUT_SETTINGS = {
    "MCP_OAUTH_TOKEN_IP_THROTTLE_RATE": "100/min",
    "MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE": "100/min",
    "MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT": 2,
    "MCP_OAUTH_TOKEN_INVALID_ATTEMPT_WINDOW_SECONDS": 300,
    "MCP_OAUTH_TOKEN_INVALID_LOCKOUT_SECONDS": 60,
    "MCP_OAUTH_REGISTER_IP_THROTTLE_RATE": "100/hour",
}


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


def _count_client_lookups(monkeypatch):
    """Count the SDK's client lookups; each one precedes a client_secret check."""
    real_get_client = FutureAGIOAuthProvider.get_client
    lookups = []

    async def counting_get_client(self, cid):
        lookups.append(cid)
        return await real_get_client(self, cid)

    monkeypatch.setattr(FutureAGIOAuthProvider, "get_client", counting_get_client)
    return lookups


def _assert_invalid_request(response):
    assert response.status_code == 400, response.text
    assert response.json()["error"] == "invalid_request"
    assert response.headers["Cache-Control"] == "no-store"


@override_settings(**LOCKOUT_SETTINGS)
@pytest.mark.parametrize("path", ["/token", "/revoke"])
async def test_sdk_duplicate_client_id_returns_invalid_request(monkeypatch, path):
    # Repeated client_id fields return 400 before SDK dispatch.
    registered_id, registered_secret = await _register()
    lookups = _count_client_lookups(monkeypatch)
    addr = "203.0.113.120"
    for i in range(6):
        form = {
            **_code_form(registered_id, "wrong-secret"),
            "client_id": [f"alternate-{i}", registered_id],
            "token": "t",
        }
        _assert_invalid_request(await _post(path, form, addr))
    # No request reached the SDK's client authentication.
    assert lookups == []

    # A subsequent valid flow for the registered client succeeds from the
    # same address.
    monkeypatch.undo()
    await _valid_flow_succeeds(registered_id, registered_secret, addr)


@override_settings(
    **{**LOCKOUT_SETTINGS, "MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE": "2/min"}
)
@pytest.mark.parametrize("path", ["/token", "/revoke"])
async def test_sdk_multipart_body_returns_invalid_request(monkeypatch, path):
    # /token and /revoke accept URL-encoded form bodies; multipart requests
    # return 400 before SDK dispatch.
    client_id, _secret = await _register()
    lookups = _count_client_lookups(monkeypatch)
    fields = {**_code_form(client_id, "wrong-secret"), "token": "t"}
    for i in range(6):
        async with _client(f"203.0.113.{130 + i}") as http:
            response = await http.post(
                path, files={k: (None, v) for k, v in fields.items()}
            )
        _assert_invalid_request(response)
    assert lookups == []


@override_settings(
    **{**LOCKOUT_SETTINGS, "MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE": "2/min"}
)
async def test_sdk_mixed_case_urlencoded_body_counts_in_the_per_client_window(
    monkeypatch,
):
    # Mixed-case URL-encoded media types without parameters use the same
    # per-client request window.
    client_id, _secret = await _register()
    lookups = _count_client_lookups(monkeypatch)
    body = "&".join(
        f"{key}={value}" for key, value in _code_form(client_id, "wrong").items()
    )
    statuses = []
    for i in range(3):
        async with _client(f"203.0.113.{150 + i}") as http:
            response = await http.post(
                "/token",
                content=body,
                headers={"Content-Type": "Application/X-WWW-Form-Urlencoded"},
            )
        statuses.append(response.status_code)
    assert statuses == [401, 401, 429]
    _assert_throttled(response, "Too many token requests")
    assert lookups == [client_id, client_id]


@override_settings(**LOCKOUT_SETTINGS)
@pytest.mark.parametrize(
    "content_type",
    [
        # Starlette lowercases the media type only when it has no
        # parameters, so it reads this body as an empty form.
        "Application/X-WWW-Form-Urlencoded; charset=UTF-8",
        "application/json",
        "text/plain",
        None,
    ],
)
async def test_sdk_token_refuses_bodies_that_are_not_urlencoded_forms(
    monkeypatch, content_type
):
    client_id, _secret = await _register()
    lookups = _count_client_lookups(monkeypatch)
    headers = {"Content-Type": content_type} if content_type else {}
    async with _client("203.0.113.140") as http:
        response = await http.post(
            "/token",
            content=f"grant_type=authorization_code&client_id={client_id}"
            "&client_secret=wrong-secret&code=c&code_verifier=" + "v" * 43,
            headers=headers,
        )
    _assert_invalid_request(response)
    assert lookups == []


@override_settings(**LOCKOUT_SETTINGS)
async def test_sdk_token_accepts_urlencoded_with_charset_parameter():
    client_id, secret = await _register()
    code, verifier = _seed_code(client_id)
    async with _client("203.0.113.141") as http:
        response = await http.post(
            "/token",
            data=_code_form(client_id, secret, code, verifier),
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"
            },
        )
    assert response.status_code == 200, response.text
    assert response.json()["access_token"]


@override_settings(**{**LOCKOUT_SETTINGS, "MCP_RATE_LIMIT_ALLOW_LOCAL_FALLBACK": False})
async def test_sdk_register_outage_message_names_registration(monkeypatch):
    def failing_script(**kwargs):
        raise RedisError("redis unavailable")

    monkeypatch.setattr(rate_limiter, "_redis_script", lambda lua: failing_script)
    async with _client("203.0.113.142") as http:
        response = await http.post(
            "/register",
            json={"redirect_uris": [REDIRECT_URI], "client_name": "Synthetic"},
        )
    _assert_throttled(
        response, "Client registration throttling temporarily unavailable"
    )


@override_settings(**{**LOCKOUT_SETTINGS, "MCP_RATE_LIMIT_ALLOW_LOCAL_FALLBACK": False})
async def test_sdk_token_outage_releases_reservation_off_the_event_loop(monkeypatch):
    from mcp_server import oauth_sdk_throttle

    def failing_script(**kwargs):
        raise RedisError("redis unavailable")

    calls = []
    real_release = oauth_sdk_throttle.release_token_attempt_reservation

    def recording_release(request):
        try:
            asyncio.get_running_loop()
            calls.append("event-loop")
        except RuntimeError:
            calls.append("worker-thread")
        return real_release(request)

    monkeypatch.setattr(rate_limiter, "_redis_script", lambda lua: failing_script)
    monkeypatch.setattr(
        oauth_sdk_throttle, "release_token_attempt_reservation", recording_release
    )
    response = await _post("/token", _code_form("any-client", "x"), "203.0.113.143")
    _assert_throttled(response, "Token throttling temporarily unavailable")
    assert calls and "event-loop" not in calls


@override_settings(
    **LOCKOUT_SETTINGS, REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}
)
async def test_sdk_token_lockout_uses_the_proxy_added_forwarded_line(monkeypatch):
    # A proxy that adds its own X-Forwarded-For line leaves the client's line
    # first. Like Django's ASGIRequest, the throttle must join all the lines,
    # so the trusted (last) address keys the lockout, not one the client sends.
    client_id, _secret = await _register()
    lookups = _count_client_lookups(monkeypatch)
    statuses = []
    for i in range(4):
        async with _client("10.0.0.1") as http:
            response = await http.post(
                "/token",
                data=_code_form(client_id, "wrong-secret"),
                headers=[
                    ("X-Forwarded-For", f"192.0.2.{10 + i}"),
                    ("X-Forwarded-For", "198.51.100.50"),
                ],
            )
        statuses.append(response.status_code)
    assert statuses == [401, 401, 429, 429]
    assert len(lookups) == 2


@override_settings(**{**LOCKOUT_SETTINGS, "MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT": 5})
async def test_sdk_valid_exchange_is_admitted_when_an_earlier_request_finishes_meanwhile(
    monkeypatch,
):
    from mcp_server import oauth_sdk_throttle, throttles

    client_id, secret = await _register()
    addr = "203.0.113.144"
    scope = {"type": "http", "client": (addr, 40000), "headers": []}
    held = [
        oauth_sdk_throttle._ThrottleRequest(scope, {"client_id": client_id})
        for _ in range(4)
    ]
    for request in held:
        assert throttles.reserve_token_attempt(request) is True

    real_add = throttles._add_reservation
    fired = []

    def add_then_finish(*args, **kwargs):
        # The first held request finishes with an invalid attempt just after
        # the valid exchange takes its slot: one recorded attempt plus four
        # in flight is exactly the limit of 5.
        result = real_add(*args, **kwargs)
        if not fired:
            fired.append(True)
            throttles.record_invalid_token_attempt(held[0])
            throttles.release_token_attempt_reservation(held[0])
        return result

    monkeypatch.setattr(throttles, "_add_reservation", add_then_finish)
    code, verifier = _seed_code(client_id)
    try:
        response = await _post(
            "/token", _code_form(client_id, secret, code, verifier), addr
        )
    finally:
        for request in held:
            throttles.release_token_attempt_reservation(request)

    assert fired == [True]
    assert response.status_code == 200, response.text
    assert response.json()["access_token"]


TRUSTED_PROXY = "10.0.0.1"


def _forwarded_client(forwarded_for, peer=TRUSTED_PROXY):
    transport = httpx.ASGITransport(app=_oauth_app(), client=(peer, 40000))
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={"X-Forwarded-For": forwarded_for},
    )


async def _register_status(http):
    response = await http.post(
        "/register",
        json={"redirect_uris": [REDIRECT_URI], "client_name": "Synthetic"},
    )
    return response


@override_settings(
    **{**LOCKOUT_SETTINGS, "MCP_OAUTH_REGISTER_IP_THROTTLE_RATE": "2/hour"},
    MCP_OAUTH_TRUSTED_PROXIES=["10.0.0.0/8"],
)
async def test_sdk_register_window_is_per_forwarded_client_behind_a_trusted_proxy():
    # Three clients reach the API through the same ingress address.
    for i in range(3):
        async with _forwarded_client(f"198.51.100.{i + 1}") as http:
            response = await _register_status(http)
        assert response.status_code == 201, response.text

    # Each client still has its own window, whatever earlier entries it sends.
    async with _forwarded_client("198.51.100.1") as http:
        assert (await _register_status(http)).status_code == 201
    async with _forwarded_client("192.0.2.9, 198.51.100.1") as http:
        _assert_throttled(await _register_status(http), "Too many client registrations")


@override_settings(
    **{**LOCKOUT_SETTINGS, "MCP_OAUTH_TOKEN_IP_THROTTLE_RATE": "2/min"},
    MCP_OAUTH_TRUSTED_PROXIES=["10.0.0.0/8"],
)
async def test_sdk_token_window_is_per_forwarded_client_behind_a_trusted_proxy():
    statuses = []
    for i in range(3):
        async with _forwarded_client(f"198.51.100.{20 + i}") as http:
            response = await http.post("/token", data=_code_form(f"unknown-{i}", "x"))
        statuses.append(response.status_code)
    assert statuses == [401, 401, 401]

    async with _forwarded_client("198.51.100.20") as http:
        second = await http.post("/revoke", {"client_id": "unknown-9", "token": "t"})
        third = await http.post("/token", data=_code_form("unknown-10", "x"))
    assert second.status_code == 401
    _assert_throttled(third, "Too many token requests")


@override_settings(
    **{**LOCKOUT_SETTINGS, "MCP_OAUTH_REGISTER_IP_THROTTLE_RATE": "2/hour"},
    MCP_OAUTH_TRUSTED_PROXIES=["10.0.0.0/8"],
)
async def test_sdk_forwarded_address_from_an_untrusted_peer_is_ignored():
    statuses = []
    for i in range(3):
        async with _forwarded_client(
            f"198.51.100.{40 + i}", peer="203.0.113.9"
        ) as http:
            statuses.append((await _register_status(http)).status_code)
    assert statuses == [201, 201, 429]


ORIGIN = "http://localhost:3031"


def _assert_readable_cross_origin(response):
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    exposed = response.headers["Access-Control-Expose-Headers"].lower()
    assert "retry-after" in [name.strip() for name in exposed.split(",")]


@override_settings(
    **{
        **LOCKOUT_SETTINGS,
        "MCP_OAUTH_TOKEN_IP_THROTTLE_RATE": "1/min",
        "MCP_OAUTH_REGISTER_IP_THROTTLE_RATE": "1/hour",
    }
)
@pytest.mark.parametrize("path", ["/token", "/revoke", "/register"])
async def test_sdk_rate_limited_response_carries_cors_headers(path):
    addr = "203.0.113.160"
    async with _client(addr) as http:
        if path == "/register":
            first = await _register_status(http)
            refused = await http.post(
                path,
                json={"redirect_uris": [REDIRECT_URI], "client_name": "Synthetic"},
                headers={"Origin": ORIGIN},
            )
        else:
            form = {**_code_form("unknown", "x"), "token": "t"}
            first = await http.post(path, data=form)
            refused = await http.post(path, data=form, headers={"Origin": ORIGIN})
    assert first.status_code in (201, 401), first.text
    assert refused.status_code == 429
    assert refused.json()["error"] == "slow_down"
    assert int(refused.headers["Retry-After"]) > 0
    _assert_readable_cross_origin(refused)


@override_settings(**LOCKOUT_SETTINGS)
async def test_sdk_invalid_request_response_carries_cors_headers():
    async with _client("203.0.113.161") as http:
        response = await http.post(
            "/token",
            json={"client_id": "x"},
            headers={"Origin": ORIGIN},
        )
    _assert_invalid_request(response)
    assert response.headers["Access-Control-Allow-Origin"] == "*"


@override_settings(**LOCKOUT_SETTINGS)
async def test_sdk_responses_without_origin_have_no_cors_headers():
    async with _client("203.0.113.162") as http:
        response = await http.post("/token", json={"client_id": "x"})
    _assert_invalid_request(response)
    assert "Access-Control-Allow-Origin" not in response.headers


@override_settings(**LOCKOUT_SETTINGS)
async def test_sdk_handled_response_keeps_its_cors_headers():
    # The SDK's own reply (wrong credentials) uses the same CORS policy.
    async with _client("203.0.113.163") as http:
        response = await http.post(
            "/token", data=_code_form("unknown", "x"), headers={"Origin": ORIGIN}
        )
    assert response.status_code == 401
    assert response.headers["Access-Control-Allow-Origin"] == "*"
