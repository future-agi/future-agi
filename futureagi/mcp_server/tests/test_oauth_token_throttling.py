"""Tests for MCP OAuth token endpoint throttling."""

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, BrokenBarrierError
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from redis.exceptions import RedisError
from rest_framework.test import APIRequestFactory

import mcp_server.throttles as throttles
from mcp_server import rate_limiter
from mcp_server.models.oauth_client import MCPOAuthClient
from mcp_server.models.oauth_code import MCPOAuthCode
from mcp_server.oauth_utils import hash_client_secret
from mcp_server.throttles import (
    MCPOAuthTokenIPThrottle,
    _invalid_attempt_key,
    get_invalid_attempt_lockout_wait,
    record_invalid_token_attempt,
)
from mcp_server.views.oauth import MCPOAuthTokenView
from tfc.middleware.workspace_context import set_workspace_context

TOKEN_URL = "/mcp/oauth/token/"
CLIENT_SECRET = "correct-secret"

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def oauth_client_app():
    return MCPOAuthClient.objects.create(
        client_id="test-oauth-client",
        client_secret_hash=hash_client_secret(CLIENT_SECRET),
        name="Test OAuth Client",
        redirect_uris=["https://example.com/callback"],
    )


def _post_token(api_client, payload, *, remote_addr="203.0.113.10", **extra):
    return api_client.post(
        TOKEN_URL,
        payload,
        format="json",
        REMOTE_ADDR=remote_addr,
        **extra,
    )


def _create_auth_code(user, workspace, client):
    set_workspace_context(
        workspace=workspace,
        organization=user.organization,
        user=user,
    )
    return MCPOAuthCode.objects.create(
        code="valid-code",
        client=client,
        user=user,
        organization=user.organization,
        workspace=workspace,
        redirect_uri="https://example.com/callback",
        scope=["evaluations"],
    )


class _SlowCache:
    """Pause concurrent reads so locmem races fail without locking."""

    def __init__(self, inner, barrier: Barrier) -> None:
        self._inner = inner
        self._barrier = barrier

    def get(self, *args, **kwargs):
        value = self._inner.get(*args, **kwargs)
        self._barrier.wait(timeout=5)
        return value

    def set(self, *args, **kwargs):
        return self._inner.set(*args, **kwargs)


class _SynchronizedCache:
    def __init__(self, inner, barrier: Barrier, synchronized_key: str) -> None:
        self._inner = inner
        self._barrier = barrier
        self._synchronized_key = synchronized_key

    def add(self, key, *args, **kwargs):
        if key == self._synchronized_key:
            self._barrier.wait(timeout=5)
        return self._inner.add(key, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@override_settings(
    MCP_OAUTH_TOKEN_IP_THROTTLE_RATE="2/min",
    MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE="100/min",
)
def test_authorization_code_attempts_are_throttled_by_ip(api_client, oauth_client_app):
    payload = {
        "grant_type": "authorization_code",
        "code": "bad-code",
        "client_id": oauth_client_app.client_id,
        "client_secret": "wrong-secret",
        "redirect_uri": "https://example.com/callback",
    }

    first = _post_token(api_client, payload, remote_addr="203.0.113.10")
    second = _post_token(api_client, payload, remote_addr="203.0.113.10")
    third = _post_token(api_client, payload, remote_addr="203.0.113.10")

    assert first.status_code == 401
    assert second.status_code == 401
    assert third.status_code == 429
    assert third.data["error"] == "slow_down"
    assert int(third["Retry-After"]) > 0


@override_settings(
    MCP_OAUTH_TOKEN_IP_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE="2/min",
)
def test_refresh_token_attempts_are_throttled_by_client_id_across_ips(
    api_client, oauth_client_app
):
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": "bad-refresh-token",
        "client_id": oauth_client_app.client_id,
    }

    first = _post_token(
        api_client,
        {**payload, "client_secret": "wrong-secret-1"},
        remote_addr="203.0.113.21",
    )
    second = _post_token(
        api_client,
        {**payload, "client_secret": "wrong-secret-2"},
        remote_addr="203.0.113.22",
    )
    third = _post_token(
        api_client,
        {**payload, "client_secret": "wrong-secret-3"},
        remote_addr="203.0.113.23",
    )

    assert first.status_code == 401
    assert second.status_code == 401
    assert third.status_code == 429
    assert third.data["error"] == "slow_down"
    assert int(third["Retry-After"]) > 0


@override_settings(
    MCP_OAUTH_TOKEN_IP_THROTTLE_RATE="2/min",
    MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE="100/min",
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
)
def test_ip_throttle_counts_concurrent_requests_atomically(monkeypatch):
    factory = APIRequestFactory()
    callers = 8
    barrier = Barrier(callers)
    monkeypatch.setattr(rate_limiter, "_redis_script", lambda lua: None)

    def run_race(remote_addr: str, cache_wrapper=None) -> int:
        requests = [
            factory.post(
                TOKEN_URL,
                {"grant_type": "refresh_token", "client_id": "same-client"},
                format="json",
                REMOTE_ADDR=remote_addr,
            )
            for _ in range(callers)
        ]

        def allow(request):
            barrier.wait()
            return MCPOAuthTokenIPThrottle().allow_request(request, None)

        if cache_wrapper is None:
            with ThreadPoolExecutor(max_workers=callers) as workers:
                return sum(workers.map(allow, requests))
        with patch.object(rate_limiter, "cache", cache_wrapper):
            with ThreadPoolExecutor(max_workers=callers) as workers:
                return sum(workers.map(allow, requests))

    assert run_race("203.0.113.25") == 2

    class _NoLock:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    with patch.object(rate_limiter, "_fallback_lock", _NoLock()):
        assert run_race("203.0.113.26", _SlowCache(cache, Barrier(callers))) > 2


@pytest.mark.skipif(not os.environ.get("REDIS_URL"), reason="needs Redis")
def test_redis_throttle_script_counts_concurrent_requests_atomically():
    import redis

    client = redis.Redis.from_url(os.environ["REDIS_URL"])
    client.ping()
    script = client.register_script(rate_limiter._SINGLE_WINDOW_LUA)
    key = f"mcp_oauth_test:{uuid.uuid4().hex}"
    callers = 8
    barrier = Barrier(callers)

    def allow(_):
        barrier.wait()
        allowed, _oldest = script(
            keys=[key],
            args=[time.time(), 2, 60, uuid.uuid4().hex],
        )
        return int(allowed) == 1

    try:
        with ThreadPoolExecutor(max_workers=callers) as workers:
            assert sum(workers.map(allow, range(callers))) == 2
    finally:
        client.delete(key)


@override_settings(
    MCP_OAUTH_TOKEN_IP_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT=2,
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_WINDOW_SECONDS=300,
    MCP_OAUTH_TOKEN_INVALID_LOCKOUT_SECONDS=60,
)
def test_repeated_invalid_refresh_grants_trigger_lockout(api_client, oauth_client_app):
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": "bad-refresh-token",
        "client_id": oauth_client_app.client_id,
        "client_secret": CLIENT_SECRET,
    }

    first = _post_token(api_client, payload, remote_addr="203.0.113.40")
    second = _post_token(api_client, payload, remote_addr="203.0.113.40")
    third = _post_token(api_client, payload, remote_addr="203.0.113.40")

    assert first.status_code == 400
    assert first.data["error"] == "invalid_grant"
    assert second.status_code == 400
    assert second.data["error"] == "invalid_grant"
    assert third.status_code == 429
    assert third.data["error"] == "slow_down"
    assert third.data["error_description"] == "Too many invalid token attempts"
    assert int(third["Retry-After"]) > 0


@override_settings(
    MCP_OAUTH_TOKEN_IP_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT=2,
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_WINDOW_SECONDS=300,
    MCP_OAUTH_TOKEN_INVALID_LOCKOUT_SECONDS=60,
)
def test_unknown_client_id_attempts_trigger_lockout(api_client):
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": "bad-refresh-token",
        "client_id": "nonexistent-client-id",
        "client_secret": "guessed-secret",
    }

    first = _post_token(api_client, payload, remote_addr="203.0.113.42")
    second = _post_token(api_client, payload, remote_addr="203.0.113.42")
    third = _post_token(api_client, payload, remote_addr="203.0.113.42")

    assert first.status_code == 401
    assert first.data["error"] == "invalid_client"
    assert second.status_code == 401
    assert second.data["error"] == "invalid_client"
    assert third.status_code == 429
    assert third.data["error"] == "slow_down"


@override_settings(
    MCP_OAUTH_TOKEN_IP_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT=2,
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_WINDOW_SECONDS=300,
    MCP_OAUTH_TOKEN_INVALID_LOCKOUT_SECONDS=1,
)
def test_lockout_expiry_resets_attempt_counter(
    api_client, oauth_client_app, monkeypatch
):
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": "bad-refresh-token",
        "client_id": oauth_client_app.client_id,
        "client_secret": CLIENT_SECRET,
    }
    now = 1_700_000_000.0
    monkeypatch.setattr(throttles.time, "time", lambda: now)

    assert (
        _post_token(api_client, payload, remote_addr="203.0.113.43").status_code == 400
    )
    assert (
        _post_token(api_client, payload, remote_addr="203.0.113.43").status_code == 400
    )
    assert (
        _post_token(api_client, payload, remote_addr="203.0.113.43").status_code == 429
    )

    # Advance past the 1s lockout while still inside the 300s attempt window.
    now += 2.0
    after_expiry = _post_token(api_client, payload, remote_addr="203.0.113.43")
    assert after_expiry.status_code == 400
    assert after_expiry.data["error"] == "invalid_grant"


@override_settings(
    MCP_OAUTH_TOKEN_IP_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT=2,
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_WINDOW_SECONDS=300,
    MCP_OAUTH_TOKEN_INVALID_LOCKOUT_SECONDS=60,
)
def test_forwarded_for_cannot_override_remote_address(api_client, oauth_client_app):
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": "bad-refresh-token",
        "client_id": oauth_client_app.client_id,
        "client_secret": CLIENT_SECRET,
    }

    assert (
        _post_token(
            api_client,
            payload,
            remote_addr="10.0.0.1",
            HTTP_X_FORWARDED_FOR="198.51.100.10",
        ).status_code
        == 400
    )
    assert (
        _post_token(
            api_client,
            payload,
            remote_addr="10.0.0.1",
            HTTP_X_FORWARDED_FOR="198.51.100.10",
        ).status_code
        == 400
    )
    assert (
        _post_token(
            api_client,
            payload,
            remote_addr="10.0.0.1",
            HTTP_X_FORWARDED_FOR="198.51.100.10",
        ).status_code
        == 429
    )

    # A different forwarded address cannot bypass the remote-address lockout.
    other_client = _post_token(
        api_client,
        payload,
        remote_addr="10.0.0.1",
        HTTP_X_FORWARDED_FOR="198.51.100.11",
    )
    assert other_client.status_code == 429

    # A different socket peer gets its own lockout bucket.
    different_client = _post_token(
        api_client,
        payload,
        remote_addr="10.0.0.2",
        HTTP_X_FORWARDED_FOR="198.51.100.11",
    )
    assert different_client.status_code == 400


@override_settings(REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": 1})
def test_configured_proxy_count_uses_forwarded_address():
    raw_request = APIRequestFactory().post(
        TOKEN_URL,
        {"grant_type": "refresh_token", "client_id": "same-client"},
        format="json",
        REMOTE_ADDR="10.0.0.1",
        HTTP_X_FORWARDED_FOR="198.51.100.10",
    )
    request = MCPOAuthTokenView().initialize_request(raw_request)

    assert throttles.client_ident(request) == "198.51.100.10"


def test_redis_script_failure_falls_back_to_locked_window(monkeypatch):
    def failing_script(**kwargs):
        raise RedisError("redis unavailable")

    key = f"redis-failure:{uuid.uuid4().hex}"
    monkeypatch.setattr(rate_limiter, "_redis_script", lambda lua: failing_script)

    with patch.object(rate_limiter.logger, "warning") as warning:
        assert rate_limiter.check_sliding_window(key, 1, 60) == (
            True,
            0.0,
        )
        allowed, oldest = rate_limiter.check_sliding_window(key, 1, 60)

    assert allowed is False
    assert oldest > 0
    warning.assert_called_with(
        "mcp_rate_limit_redis_error_falling_back_to_locked_window",
        reason="redis_script_execution_failed",
        exc_info=True,
    )


@override_settings(MCP_RATE_LIMIT_ALLOW_LOCAL_FALLBACK=False)
def test_redis_script_failure_fails_closed_in_production(monkeypatch):
    def failing_script(**kwargs):
        raise RedisError("redis unavailable")

    monkeypatch.setattr(rate_limiter, "_redis_script", lambda lua: failing_script)

    with pytest.raises(rate_limiter.RateLimitBackendUnavailableError):
        rate_limiter.check_sliding_window("redis-required", 1, 60)


@override_settings(
    MCP_RATE_LIMIT_ALLOW_LOCAL_FALLBACK=False,
    MCP_OAUTH_TOKEN_IP_THROTTLE_RATE="2/min",
    MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE="100/min",
)
def test_oauth_endpoint_fails_closed_when_redis_is_unavailable(api_client, monkeypatch):
    def failing_script(**kwargs):
        raise RedisError("redis unavailable")

    monkeypatch.setattr(rate_limiter, "_redis_script", lambda lua: failing_script)

    response = _post_token(
        api_client,
        {
            "grant_type": "refresh_token",
            "refresh_token": "untrusted-token",
            "client_id": "test-client",
            "client_secret": "untrusted-secret",
        },
    )

    assert response.status_code == 429
    assert response.data["error"] == "slow_down"
    assert response.data["error_description"] == (
        "Token throttling temporarily unavailable"
    )
    assert int(response["Retry-After"]) == 60


@override_settings(
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT=2,
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_WINDOW_SECONDS=300,
    MCP_OAUTH_TOKEN_INVALID_LOCKOUT_SECONDS=60,
)
def test_concurrent_invalid_attempts_set_lockout(oauth_client_app, monkeypatch):
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": "bad-refresh-token",
        "client_id": oauth_client_app.client_id,
        "client_secret": CLIENT_SECRET,
    }

    raw_request = APIRequestFactory().post(
        TOKEN_URL, payload, format="json", REMOTE_ADDR="203.0.113.41"
    )
    request = MCPOAuthTokenView().initialize_request(raw_request)
    assert request.data["client_id"] == oauth_client_app.client_id
    cache_barrier = Barrier(2)
    monkeypatch.setattr(
        throttles,
        "cache",
        _SynchronizedCache(cache, cache_barrier, _invalid_attempt_key(request)),
    )

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(
            workers.map(
                record_invalid_token_attempt,
                [request, request],
            )
        )

    assert sorted(result > 0 for result in results) == [False, True]
    assert get_invalid_attempt_lockout_wait(request) > 0


@override_settings(
    MCP_OAUTH_TOKEN_IP_THROTTLE_RATE="2/min",
    MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE="2/min",
)
def test_low_volume_authorization_code_exchange_is_allowed(
    api_client, user, workspace, oauth_client_app
):
    auth_code = _create_auth_code(user, workspace, oauth_client_app)

    response = _post_token(
        api_client,
        {
            "grant_type": "authorization_code",
            "code": auth_code.code,
            "client_id": oauth_client_app.client_id,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": auth_code.redirect_uri,
        },
        remote_addr="203.0.113.30",
    )

    assert response.status_code == 200
    assert response.data["token_type"] == "Bearer"
    assert response.data["access_token"]
    assert response.data["refresh_token"]


@override_settings(
    MCP_OAUTH_TOKEN_IP_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT=5,
)
def test_invalid_client_secret_returns_oauth_error_without_crashing(
    api_client, oauth_client_app
):
    response = _post_token(
        api_client,
        {
            "grant_type": "refresh_token",
            "refresh_token": "malformed-token",
            "client_id": oauth_client_app.client_id,
            "client_secret": "wrong-secret",
        },
        remote_addr="203.0.113.50",
    )

    assert response.status_code == 401
    assert response.data["error"] == "invalid_client"


@override_settings(
    MCP_OAUTH_TOKEN_IP_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT=5,
)
def test_invalid_refresh_token_returns_oauth_error_without_crashing(
    api_client, oauth_client_app
):
    response = _post_token(
        api_client,
        {
            "grant_type": "refresh_token",
            "refresh_token": "malformed-token",
            "client_id": oauth_client_app.client_id,
            "client_secret": CLIENT_SECRET,
        },
        remote_addr="203.0.113.51",
    )

    assert response.status_code == 400
    assert response.data["error"] == "invalid_grant"


def _bad_secret_payload(client_id, grant_type, secret="wrong-secret"):
    payload = {
        "grant_type": grant_type,
        "client_id": client_id,
        "client_secret": secret,
    }
    if grant_type == "authorization_code":
        payload["code"] = "bad-code"
    else:
        payload["refresh_token"] = "bad-refresh-token"
    return payload


@override_settings(
    MCP_OAUTH_TOKEN_IP_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT=2,
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_WINDOW_SECONDS=300,
    MCP_OAUTH_TOKEN_INVALID_LOCKOUT_SECONDS=60,
)
def test_invalid_secret_lockout_cannot_be_bypassed_by_switching_grant_type(
    api_client, user, workspace, oauth_client_app
):
    # Both grant types verify the same client_secret, so a lockout earned with
    # one grant must also stop secret guesses sent with the other grant.
    for _ in range(2):
        response = _post_token(
            api_client,
            _bad_secret_payload(oauth_client_app.client_id, "authorization_code"),
            remote_addr="203.0.113.60",
        )
        assert response.status_code == 401
        assert response.data["error"] == "invalid_client"

    switched = _post_token(
        api_client,
        _bad_secret_payload(oauth_client_app.client_id, "refresh_token"),
        remote_addr="203.0.113.60",
    )
    assert switched.status_code == 429
    assert switched.data["error"] == "slow_down"
    assert switched.data["error_description"] == "Too many invalid token attempts"
    assert int(switched["Retry-After"]) > 0

    # Authorized-user control: another registered client exchanging a valid
    # code from the same address is not caught by this client's lockout.
    other_client = MCPOAuthClient.objects.create(
        client_id="other-oauth-client",
        client_secret_hash=hash_client_secret("other-secret"),
        name="Other OAuth Client",
        redirect_uris=["https://example.com/callback"],
    )
    auth_code = _create_auth_code(user, workspace, other_client)
    allowed = _post_token(
        api_client,
        {
            "grant_type": "authorization_code",
            "code": auth_code.code,
            "client_id": other_client.client_id,
            "client_secret": "other-secret",
            "redirect_uri": auth_code.redirect_uri,
        },
        remote_addr="203.0.113.60",
    )
    assert allowed.status_code == 200
    assert allowed.data["access_token"]


@override_settings(
    MCP_OAUTH_TOKEN_IP_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE="2/min",
)
def test_client_throttle_counts_every_grant_type_for_the_same_client(
    api_client, oauth_client_app
):
    first = _post_token(
        api_client,
        _bad_secret_payload(oauth_client_app.client_id, "authorization_code"),
        remote_addr="203.0.113.61",
    )
    second = _post_token(
        api_client,
        _bad_secret_payload(oauth_client_app.client_id, "authorization_code"),
        remote_addr="203.0.113.62",
    )
    switched = _post_token(
        api_client,
        _bad_secret_payload(oauth_client_app.client_id, "refresh_token"),
        remote_addr="203.0.113.63",
    )

    assert first.status_code == 401
    assert second.status_code == 401
    assert switched.status_code == 429
    assert switched.data["error"] == "slow_down"
    assert switched.data["error_description"] == "Too many token requests"
    assert int(switched["Retry-After"]) > 0


@override_settings(
    MCP_OAUTH_TOKEN_IP_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT=2,
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_WINDOW_SECONDS=300,
    MCP_OAUTH_TOKEN_INVALID_LOCKOUT_SECONDS=60,
)
def test_in_flight_attempts_count_toward_invalid_attempt_limit():
    # Simulate a concurrent burst: every request has passed the throttles and
    # none has finished verifying its secret yet. The burst must not get more
    # secret checks than the invalid-attempt limit allows.
    view = MCPOAuthTokenView()

    def new_request():
        raw_request = APIRequestFactory().post(
            TOKEN_URL,
            _bad_secret_payload("burst-client", "refresh_token"),
            format="json",
            REMOTE_ADDR="203.0.113.64",
        )
        return view.initialize_request(raw_request)

    request = new_request()
    throttle = throttles.MCPOAuthTokenLockoutThrottle()

    in_flight = [new_request() for _ in range(2)]
    for pending in in_flight:
        assert throttle.allow_request(pending, view) is True

    with pytest.raises(throttles.MCPOAuthTokenThrottled) as excinfo:
        throttle.allow_request(request, view)
    assert excinfo.value.detail["error"] == "slow_down"
    assert int(excinfo.value.wait) > 0

    # Once the in-flight requests finish, the reservation is returned and a
    # legitimate retry is evaluated normally.
    for pending in in_flight:
        throttles.release_token_attempt_reservation(pending)
    retry = new_request()
    assert throttle.allow_request(retry, view) is True
    throttles.release_token_attempt_reservation(retry)


@override_settings(
    MCP_OAUTH_TOKEN_IP_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT=2,
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_WINDOW_SECONDS=300,
    MCP_OAUTH_TOKEN_INVALID_LOCKOUT_SECONDS=60,
)
def test_concurrent_burst_gets_at_most_the_invalid_attempt_limit(monkeypatch):
    from mcp_server.views import oauth as oauth_views

    burst = 6
    # With the limit at 2, only two requests may be verifying at once; the
    # barrier holds them together so the rest of the burst arrives mid-flight.
    started = Barrier(2)
    verified = []

    def slow_lookup(client_id, protocol="management"):
        # Hold admitted requests together so the rest of the burst arrives
        # while they are still verifying. A request left without a partner
        # (only possible without in-flight accounting) carries on after the
        # timeout, so the count assertion below reports the failure.
        try:
            started.wait(timeout=2)
        except BrokenBarrierError:
            pass
        return None, None

    real_record = throttles.record_invalid_token_attempt

    def counting_record(request):
        verified.append(1)
        return real_record(request)

    monkeypatch.setattr(oauth_views, "_get_active_oauth_client", slow_lookup)
    monkeypatch.setattr(oauth_views, "record_invalid_token_attempt", counting_record)

    def send(_):
        raw = APIRequestFactory().post(
            TOKEN_URL,
            _bad_secret_payload("burst-client", "authorization_code"),
            format="json",
            REMOTE_ADDR="203.0.113.65",
        )
        return MCPOAuthTokenView.as_view()(raw)

    with ThreadPoolExecutor(max_workers=burst) as workers:
        responses = list(workers.map(send, range(burst)))

    statuses = sorted(r.status_code for r in responses)
    assert len(verified) <= 2
    assert statuses.count(401) == len(verified)
    assert all(status in (401, 429) for status in statuses)
