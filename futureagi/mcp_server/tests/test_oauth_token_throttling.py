"""Tests for MCP OAuth token endpoint throttling."""

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Condition
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
def test_invalid_attempt_lockout_is_shared_across_grant_types(
    api_client, user, workspace, oauth_client_app
):
    # Authorization-code and refresh-token requests share the same
    # client-and-address attempt counter.
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
    assert (
        excinfo.value.detail["error_description"]
        == "Too many concurrent token requests"
    )
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
    throttled = []
    all_throttled = Condition()
    verified = []

    real_allow = throttles.MCPOAuthTokenLockoutThrottle.allow_request

    def counting_allow(self, request, view):
        try:
            return real_allow(self, request, view)
        finally:
            with all_throttled:
                throttled.append(1)
                all_throttled.notify_all()

    def slow_lookup(client_id, protocol="management"):
        # Hold every admitted request at client verification until the whole
        # burst has passed (or been refused by) the lockout throttle.
        with all_throttled:
            all_throttled.wait_for(lambda: len(throttled) >= burst, timeout=10)
        return None, None

    monkeypatch.setattr(
        throttles.MCPOAuthTokenLockoutThrottle, "allow_request", counting_allow
    )
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
    # Exactly the limit: fewer would mean legitimate requests are over-refused.
    assert len(verified) == 2
    assert statuses.count(401) == len(verified)
    assert all(status in (401, 429) for status in statuses)
    assert all(
        r.data["error"] == "slow_down" for r in responses if r.status_code == 429
    )


class _Clock:
    """Wall clock the test can move; drives locmem expiry and reservation ages."""

    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _reservation_request(client_id, remote_addr):
    raw_request = APIRequestFactory().post(
        TOKEN_URL,
        _bad_secret_payload(client_id, "refresh_token"),
        format="json",
        REMOTE_ADDR=remote_addr,
    )
    return MCPOAuthTokenView().initialize_request(raw_request)


_RESERVATION_SETTINGS = {
    "MCP_OAUTH_TOKEN_IP_THROTTLE_RATE": "100/min",
    "MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE": "100/min",
    "MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT": 2,
    "MCP_OAUTH_TOKEN_INVALID_ATTEMPT_WINDOW_SECONDS": 300,
    "MCP_OAUTH_TOKEN_INVALID_LOCKOUT_SECONDS": 60,
}


def _assert_lost_slots_expire_under_refused_traffic(clock, client_id, addr):
    start = clock.now
    # Two admitted requests whose worker died before releasing its slot.
    for _ in range(2):
        assert throttles.reserve_token_attempt(_reservation_request(client_id, addr))

    # The same client keeps sending requests. Each is refused while the lost
    # slots are younger than the reservation timeout, and must not keep them
    # alive.
    for offset in range(5, 60, 10):
        clock.now = start + offset
        retry = _reservation_request(client_id, addr)
        assert throttles.reserve_token_attempt(retry) is False

    clock.now = start + throttles._RESERVATION_TIMEOUT_SECONDS + 1
    retry = _reservation_request(client_id, addr)
    assert throttles.reserve_token_attempt(retry) is True
    throttles.release_token_attempt_reservation(retry)


@override_settings(**_RESERVATION_SETTINGS)
def test_lost_reservations_expire_while_refused_traffic_continues(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(time, "time", clock)
    monkeypatch.setattr(rate_limiter, "_redis_script", lambda lua: None)

    _assert_lost_slots_expire_under_refused_traffic(
        clock, "lost-slot-client", "203.0.113.70"
    )


@override_settings(**_RESERVATION_SETTINGS)
def test_lost_reservation_expires_while_admitted_traffic_continues(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(time, "time", clock)
    monkeypatch.setattr(rate_limiter, "_redis_script", lambda lua: None)
    start = clock.now

    lost = _reservation_request("lost-slot-client", "203.0.113.71")
    assert throttles.reserve_token_attempt(lost)

    # Authorized-user control: steady sequential traffic is admitted while
    # the lost slot still counts.
    for offset in range(5, 60, 10):
        clock.now = start + offset
        request = _reservation_request("lost-slot-client", "203.0.113.71")
        assert throttles.reserve_token_attempt(request) is True
        throttles.release_token_attempt_reservation(request)

    # Once the lost slot is older than the timeout, the full limit of
    # concurrent requests is available again.
    clock.now = start + throttles._RESERVATION_TIMEOUT_SECONDS + 1
    concurrent = [
        _reservation_request("lost-slot-client", "203.0.113.71") for _ in range(2)
    ]
    assert [throttles.reserve_token_attempt(r) for r in concurrent] == [True, True]
    for request in concurrent:
        throttles.release_token_attempt_reservation(request)


class _CounterExpiresMidReservation:
    """Let the in-flight counter expire between add() and incr().

    When the first incr() on the in-flight key fails because the counter just
    expired, another request reserves before the caller rewrites the counter.
    This guards against the add()/incr() counter coming back; implementations
    that never take that path run the same scenario sequentially, and
    test_fallback_concurrent_reservations_admit_exactly_the_limit covers
    their atomicity.
    """

    def __init__(self, inner, clock, expire_at, concurrent_reserve) -> None:
        self._inner = inner
        self._clock = clock
        self._expire_at = expire_at
        self._concurrent_reserve = concurrent_reserve
        self.injected = False
        self._armed = False

    def incr(self, key, *args, **kwargs):
        if "in_flight" in key and not self.injected:
            self._clock.now = self._expire_at
            try:
                return self._inner.incr(key, *args, **kwargs)
            except ValueError:
                self._armed = True
                raise
        return self._inner.incr(key, *args, **kwargs)

    def set(self, key, *args, **kwargs):
        if self._armed:
            self._armed = False
            self.injected = True
            self._concurrent_reserve()
        return self._inner.set(key, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@override_settings(**_RESERVATION_SETTINGS)
def test_reservation_racing_a_slot_expiry_is_still_counted(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(time, "time", clock)
    monkeypatch.setattr(rate_limiter, "_redis_script", lambda lua: None)
    start = clock.now
    addr, client_id = "203.0.113.72", "race-client"

    lost = _reservation_request(client_id, addr)
    assert throttles.reserve_token_attempt(lost)

    expire_at = start + throttles._RESERVATION_TIMEOUT_SECONDS + 0.5
    second = _reservation_request(client_id, addr)
    admitted = {}
    racing_cache = _CounterExpiresMidReservation(
        cache,
        clock,
        expire_at,
        lambda: admitted.setdefault("second", throttles.reserve_token_attempt(second)),
    )
    monkeypatch.setattr(throttles, "cache", racing_cache)

    clock.now = start + throttles._RESERVATION_TIMEOUT_SECONDS - 0.5
    first = _reservation_request(client_id, addr)
    assert throttles.reserve_token_attempt(first) is True
    if not racing_cache.injected:
        clock.now = expire_at
        admitted["second"] = throttles.reserve_token_attempt(second)
    assert admitted["second"] is True

    # first and second are both in flight; the limit is 2.
    third = _reservation_request(client_id, addr)
    assert throttles.reserve_token_attempt(third) is False


@pytest.fixture
def redis_cache(monkeypatch):
    import redis

    url = os.environ.get("REDIS_URL")
    if not url:
        pytest.skip("needs Redis")
    try:
        redis.Redis.from_url(url, socket_connect_timeout=1).ping()
    except redis.exceptions.RedisError:
        pytest.skip("needs a reachable Redis")
    # Scripts registered here are bound to this Redis; drop them afterwards
    # so later tests keep using their own cache backend.
    monkeypatch.setattr(rate_limiter, "_scripts", {})
    with override_settings(
        CACHES={
            "default": {
                "BACKEND": "django_redis.cache.RedisCache",
                "LOCATION": os.environ["REDIS_URL"],
            }
        },
        MCP_RATE_LIMIT_ALLOW_LOCAL_FALLBACK=False,
    ):
        yield cache


@override_settings(**_RESERVATION_SETTINGS)
def test_redis_lost_reservations_expire_while_refused_traffic_continues(
    redis_cache, monkeypatch
):
    clock = _Clock(time.time())
    monkeypatch.setattr(time, "time", clock)
    client_id = f"redis-lost-slot-{uuid.uuid4().hex}"
    addr = "203.0.113.73"
    key = throttles._in_flight_key(_reservation_request(client_id, addr))
    try:
        _assert_lost_slots_expire_under_refused_traffic(clock, client_id, addr)
    finally:
        redis_cache.delete(_invalid_attempt_key(_reservation_request(client_id, addr)))
        from django_redis import get_redis_connection

        get_redis_connection("default").delete(key)


@override_settings(**_RESERVATION_SETTINGS)
def test_redis_concurrent_reservations_admit_exactly_the_limit(redis_cache):
    client_id = f"redis-burst-{uuid.uuid4().hex}"
    callers = 8
    barrier = Barrier(callers)
    requests = [_reservation_request(client_id, "203.0.113.74") for _ in range(callers)]

    def reserve(request):
        barrier.wait(timeout=5)
        return throttles.reserve_token_attempt(request)

    try:
        with ThreadPoolExecutor(max_workers=callers) as workers:
            admitted = list(workers.map(reserve, requests))
        assert sum(admitted) == 2
    finally:
        for request in requests:
            throttles.release_token_attempt_reservation(request)

    from django_redis import get_redis_connection

    assert (
        get_redis_connection("default").zcard(throttles._in_flight_key(requests[0]))
        == 0
    )


@override_settings(**_RESERVATION_SETTINGS)
def test_fallback_concurrent_reservations_admit_exactly_the_limit(monkeypatch):
    monkeypatch.setattr(rate_limiter, "_redis_script", lambda lua: None)
    callers = 8
    barrier = Barrier(callers)
    requests = [
        _reservation_request("fallback-burst", "203.0.113.75") for _ in range(callers)
    ]

    def reserve(request):
        barrier.wait(timeout=5)
        return throttles.reserve_token_attempt(request)

    with ThreadPoolExecutor(max_workers=callers) as workers:
        admitted = list(workers.map(reserve, requests))
    assert sum(admitted) == 2

    for request in requests:
        throttles.release_token_attempt_reservation(request)
    assert cache.get(throttles._in_flight_key(requests[0])) is None


def _finish_with_invalid_attempt(request):
    # The view's order for a wrong secret: record the attempt, then release
    # the slot once the response has been built.
    record_invalid_token_attempt(request)
    throttles.release_token_attempt_reservation(request)


def _finish_after_next_reservation(monkeypatch, finish):
    """Run finish() as soon as the next reservation step returns.

    That is the point between a request taking its reservation and the
    admission decision, where another request can finish and free its slot.
    Returns a list that holds True once finish() has run.
    """
    real_add = throttles._add_reservation
    fired = []

    def add_then_finish(*args, **kwargs):
        result = real_add(*args, **kwargs)
        if not fired:
            fired.append(True)
            finish()
        return result

    monkeypatch.setattr(throttles, "_add_reservation", add_then_finish)
    return fired


def _clear_redis_attempts(redis_cache, client_id, addr):
    from django_redis import get_redis_connection

    probe = _reservation_request(client_id, addr)
    redis_cache.delete(_invalid_attempt_key(probe))
    redis_cache.delete(throttles._invalid_lockout_key(probe))
    get_redis_connection("default").delete(throttles._in_flight_key(probe))


def _assert_attempt_recorded_before_release_counts_once(client_id, addr):
    first = _reservation_request(client_id, addr)
    second = _reservation_request(client_id, addr)
    third = _reservation_request(client_id, addr)
    try:
        assert throttles.reserve_token_attempt(first) is True
        # first has failed its check and recorded it; its slot is released
        # only after its response is built.
        record_invalid_token_attempt(first)

        # One recorded attempt and nothing else being verified: one more
        # check fits within the limit of 2, and no more.
        assert throttles.reserve_token_attempt(second) is True
        assert throttles.reserve_token_attempt(third) is False
    finally:
        for request in (first, second, third):
            throttles.release_token_attempt_reservation(request)


def _assert_request_finishing_during_admission_counts_once(
    monkeypatch, client_id, addr
):
    earlier = _reservation_request(client_id, addr)
    request = _reservation_request(client_id, addr)
    over = _reservation_request(client_id, addr)
    try:
        assert throttles.reserve_token_attempt(earlier) is True
        fired = _finish_after_next_reservation(
            monkeypatch, lambda: _finish_with_invalid_attempt(earlier)
        )

        # earlier finishes with an invalid attempt right after request takes
        # its reservation. One recorded attempt plus request is the limit.
        assert throttles.reserve_token_attempt(request) is True
        assert fired == [True]
        assert throttles.reserve_token_attempt(over) is False
    finally:
        for pending in (earlier, request, over):
            throttles.release_token_attempt_reservation(pending)


@override_settings(**_RESERVATION_SETTINGS)
def test_attempt_recorded_before_release_counts_once(monkeypatch):
    monkeypatch.setattr(rate_limiter, "_redis_script", lambda lua: None)
    _assert_attempt_recorded_before_release_counts_once(
        "recorded-client", "203.0.113.76"
    )


@override_settings(**_RESERVATION_SETTINGS)
def test_request_finishing_during_admission_counts_once(monkeypatch):
    monkeypatch.setattr(rate_limiter, "_redis_script", lambda lua: None)
    _assert_request_finishing_during_admission_counts_once(
        monkeypatch, "finishing-client", "203.0.113.77"
    )


@override_settings(**_RESERVATION_SETTINGS)
def test_refused_reservation_does_not_take_a_slot_from_the_next_request(
    monkeypatch,
):
    monkeypatch.setattr(rate_limiter, "_redis_script", lambda lua: None)
    client_id, addr = "refused-client", "203.0.113.78"
    held = [_reservation_request(client_id, addr) for _ in range(2)]
    refused = _reservation_request(client_id, addr)
    following = _reservation_request(client_id, addr)
    outcome = {}

    def finish_and_follow():
        # One held request succeeds and frees its slot, and another request
        # arrives while the refused one is still being turned away.
        throttles.release_token_attempt_reservation(held[0])
        outcome["following"] = throttles.reserve_token_attempt(following)

    try:
        for request in held:
            assert throttles.reserve_token_attempt(request) is True
        fired = _finish_after_next_reservation(monkeypatch, finish_and_follow)

        assert throttles.reserve_token_attempt(refused) is False
        assert fired == [True]
        # held[1] and following are in flight: exactly the limit.
        assert outcome["following"] is True
    finally:
        for request in (*held, refused, following):
            throttles.release_token_attempt_reservation(request)


@override_settings(
    **{**_RESERVATION_SETTINGS, "MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT": 5}
)
def test_valid_exchange_is_admitted_when_an_earlier_request_finishes_meanwhile(
    api_client, user, workspace, oauth_client_app, monkeypatch
):
    addr = "203.0.113.79"
    held = [_reservation_request(oauth_client_app.client_id, addr) for _ in range(4)]
    auth_code = _create_auth_code(user, workspace, oauth_client_app)
    try:
        for request in held:
            assert throttles.reserve_token_attempt(request) is True
        # Four requests are being verified. The first finishes with an
        # invalid attempt just after the valid exchange takes its slot: one
        # recorded attempt plus four in flight is exactly the limit of 5.
        fired = _finish_after_next_reservation(
            monkeypatch, lambda: _finish_with_invalid_attempt(held[0])
        )
        response = _post_token(
            api_client,
            {
                "grant_type": "authorization_code",
                "code": auth_code.code,
                "client_id": oauth_client_app.client_id,
                "client_secret": CLIENT_SECRET,
                "redirect_uri": auth_code.redirect_uri,
            },
            remote_addr=addr,
        )
    finally:
        for request in held:
            throttles.release_token_attempt_reservation(request)

    assert fired == [True]
    assert response.status_code == 200, response.data
    assert response.data["access_token"]


@override_settings(**_RESERVATION_SETTINGS)
def test_redis_attempt_recorded_before_release_counts_once(redis_cache):
    client_id = f"redis-recorded-{uuid.uuid4().hex}"
    addr = "203.0.113.81"
    try:
        _assert_attempt_recorded_before_release_counts_once(client_id, addr)
    finally:
        _clear_redis_attempts(redis_cache, client_id, addr)


@override_settings(**_RESERVATION_SETTINGS)
def test_redis_request_finishing_during_admission_counts_once(redis_cache, monkeypatch):
    client_id = f"redis-finishing-{uuid.uuid4().hex}"
    addr = "203.0.113.82"
    try:
        _assert_request_finishing_during_admission_counts_once(
            monkeypatch, client_id, addr
        )
    finally:
        _clear_redis_attempts(redis_cache, client_id, addr)


@override_settings(
    **{**_RESERVATION_SETTINGS, "MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT": 5}
)
def test_redis_recorded_attempts_and_reservations_share_the_limit(redis_cache):
    client_id = f"redis-shared-{uuid.uuid4().hex}"
    addr = "203.0.113.83"
    probe = _reservation_request(client_id, addr)
    callers = 8
    barrier = Barrier(callers)
    requests = [_reservation_request(client_id, addr) for _ in range(callers)]

    def reserve(request):
        barrier.wait(timeout=5)
        return throttles.reserve_token_attempt(request)

    try:
        for _ in range(2):
            assert record_invalid_token_attempt(probe) == 0
        # The counter is the one the Django cache API reads and writes.
        assert redis_cache.get(_invalid_attempt_key(probe)) == 2

        # Two recorded attempts leave room for three concurrent checks.
        with ThreadPoolExecutor(max_workers=callers) as workers:
            admitted = list(workers.map(reserve, requests))
        assert sum(admitted) == 3
        for request in requests:
            throttles.release_token_attempt_reservation(request)

        # Reaching the limit still sets the lockout and resets the counter.
        assert record_invalid_token_attempt(probe) == 0
        assert record_invalid_token_attempt(probe) == 0
        assert record_invalid_token_attempt(probe) == 60
        assert get_invalid_attempt_lockout_wait(probe) > 0
        assert redis_cache.get(_invalid_attempt_key(probe)) is None
    finally:
        for request in requests:
            throttles.release_token_attempt_reservation(request)
        _clear_redis_attempts(redis_cache, client_id, addr)


def _client_ident_for(remote_addr, forwarded_for=None):
    extra = {"HTTP_X_FORWARDED_FOR": forwarded_for} if forwarded_for else {}
    raw_request = APIRequestFactory().post(
        TOKEN_URL,
        {"grant_type": "refresh_token", "client_id": "same-client"},
        format="json",
        REMOTE_ADDR=remote_addr,
        **extra,
    )
    return throttles.client_ident(MCPOAuthTokenView().initialize_request(raw_request))


@override_settings(MCP_OAUTH_TRUSTED_PROXIES=["10.0.0.0/8", "2001:db8:ffff::/48"])
@pytest.mark.parametrize(
    "remote_addr, forwarded_for, expected",
    [
        # The nearest address that is not a trusted proxy.
        ("10.0.0.1", "198.51.100.10", "198.51.100.10"),
        ("10.0.0.1", "192.0.2.7, 198.51.100.10", "198.51.100.10"),
        ("10.0.0.1", "192.0.2.7, 198.51.100.10, 10.0.0.9", "198.51.100.10"),
        ("2001:db8:ffff::1", "2001:db8:1::5", "2001:db8:1::5"),
        # Every hop is a trusted proxy: the first one.
        ("10.0.0.1", "10.0.0.8, 10.0.0.9", "10.0.0.8"),
        # A peer that is not a trusted proxy keeps its own address.
        ("203.0.113.5", "198.51.100.10", "203.0.113.5"),
        ("10.0.0.1", None, "10.0.0.1"),
        ("10.0.0.1", " , ", "10.0.0.1"),
    ],
)
def test_trusted_proxy_forwarded_address_is_used(remote_addr, forwarded_for, expected):
    assert _client_ident_for(remote_addr, forwarded_for) == expected


# The Helm chart's ingress.trustedProxies and gatewayApi.trustedProxies
# default, which it sets with MCP_OAUTH_TRUSTED_PROXY_HOPS=1.
CHART_ROUTE_PROXIES = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "100.64.0.0/10",
    "fc00::/7",
]


@override_settings(
    MCP_OAUTH_TRUSTED_PROXIES=CHART_ROUTE_PROXIES, MCP_OAUTH_TRUSTED_PROXY_HOPS=1
)
@pytest.mark.parametrize(
    "remote_addr, forwarded_for, expected",
    [
        ("10.42.0.7", "198.51.100.10", "198.51.100.10"),
        ("10.42.0.7", "192.0.2.7, 198.51.100.10", "198.51.100.10"),
        # A client on a private network is counted by the address the
        # ingress saw, whatever it sends before it.
        ("10.42.0.7", "192.0.2.7, 10.1.2.3", "10.1.2.3"),
        ("10.42.0.7", "10.9.9.9, 192.168.5.20", "192.168.5.20"),
        ("fd00::7", "2001:db8:1::5", "2001:db8:1::5"),
        ("fd00::7", "2001:db8:1::5, fd00::20", "fd00::20"),
        ("10.42.0.7", None, "10.42.0.7"),
        ("10.42.0.7", " , ", "10.42.0.7"),
        ("203.0.113.5", "198.51.100.10", "203.0.113.5"),
    ],
)
def test_one_trusted_hop_uses_the_address_the_proxy_saw(
    remote_addr, forwarded_for, expected
):
    assert _client_ident_for(remote_addr, forwarded_for) == expected


@override_settings(
    MCP_OAUTH_TRUSTED_PROXIES=["10.0.0.0/8"], MCP_OAUTH_TRUSTED_PROXY_HOPS=2
)
@pytest.mark.parametrize(
    "remote_addr, forwarded_for, expected",
    [
        ("10.0.0.1", "192.0.2.7, 198.51.100.10, 10.0.0.9", "198.51.100.10"),
        ("10.0.0.1", "192.0.2.7, 198.51.100.10", "198.51.100.10"),
        # The second proxy's own entry is as far as the count reaches.
        ("10.0.0.1", "192.0.2.7, 10.0.0.8, 10.0.0.9", "10.0.0.8"),
        ("10.0.0.1", "10.0.0.9", "10.0.0.9"),
    ],
)
def test_trusted_hop_count_limits_how_far_forwarded_entries_are_read(
    remote_addr, forwarded_for, expected
):
    assert _client_ident_for(remote_addr, forwarded_for) == expected


@override_settings(
    MCP_OAUTH_TRUSTED_PROXIES=["10.0.0.0/8", "2001:db8:ffff::/48"],
    MCP_OAUTH_TRUSTED_PROXY_HOPS=2,
)
@pytest.mark.parametrize(
    "forwarded_for, expected",
    [
        # Controllers that write the client's source port: the address alone.
        ("198.51.100.10:51234", "198.51.100.10"),
        ("192.0.2.7, 198.51.100.10:443", "198.51.100.10"),
        ("[2001:db8:1::5]:443", "2001:db8:1::5"),
        ("[2001:db8:1::5]", "2001:db8:1::5"),
        ("2001:db8:1::5", "2001:db8:1::5"),
        # A trusted proxy's entry with a port is still the trusted proxy.
        ("198.51.100.10, 10.0.0.9:8080", "198.51.100.10"),
    ],
)
def test_forwarded_entry_port_is_not_part_of_the_client_address(
    forwarded_for, expected
):
    assert _client_ident_for("10.0.0.1", forwarded_for) == expected


@override_settings(MCP_OAUTH_TRUSTED_PROXIES=["10.0.0.0/8"])
@pytest.mark.parametrize(
    "forwarded_for",
    [
        "unknown",
        "client.example",
        "198.51.100.10:port",
        "198.51.100.10:51234:1",
        "[2001:db8:1::5",
        "192.0.2.7, not-an-address",
    ],
)
def test_unparseable_forwarded_entry_counts_by_the_peer_address(forwarded_for):
    assert _client_ident_for("10.0.0.1", forwarded_for) == "10.0.0.1"


def test_source_ports_of_one_client_share_a_window():
    with override_settings(MCP_OAUTH_TRUSTED_PROXIES=["10.0.0.0/8"]):
        first = _client_ident_for("10.0.0.1", "198.51.100.10:50001")
        second = _client_ident_for("10.0.0.1", "198.51.100.10:50002")
    assert first == second == "198.51.100.10"


# examples/cloud/gke.yaml: GKE's load balancer connects from its proxy ranges
# and appends "<client>, <load balancer address>" to X-Forwarded-For.
GKE_GATEWAY_PROXIES = "35.191.0.0/16,130.211.0.0/22,203.0.113.10/32"


@override_settings(
    MCP_OAUTH_TRUSTED_PROXIES=GKE_GATEWAY_PROXIES, MCP_OAUTH_TRUSTED_PROXY_HOPS="2"
)
@pytest.mark.parametrize(
    "remote_addr, forwarded_for, expected",
    [
        ("35.191.3.4", "198.51.100.10, 203.0.113.10", "198.51.100.10"),
        ("130.211.1.2", "198.51.100.11, 203.0.113.10", "198.51.100.11"),
        # Whatever the client sends before the load balancer's two entries.
        ("35.191.3.4", "192.0.2.7, 198.51.100.10, 203.0.113.10", "198.51.100.10"),
        # A peer outside the load balancer's ranges keeps its own address.
        ("192.0.2.50", "198.51.100.10, 203.0.113.10", "192.0.2.50"),
    ],
)
def test_gke_load_balancer_entries_give_the_client_address(
    remote_addr, forwarded_for, expected
):
    assert _client_ident_for(remote_addr, forwarded_for) == expected


@pytest.mark.parametrize(
    "trusted, remote_addr",
    [
        (["::ffff:10.0.0.1"], "::ffff:10.0.0.1"),
        (["::ffff:10.0.0.0/104"], "::ffff:10.0.0.1"),
        (["10.0.0.0/8"], "::ffff:10.0.0.1"),
        (["::ffff:10.0.0.1"], "10.0.0.1"),
    ],
)
def test_ipv4_mapped_trusted_proxy_matches_either_form(trusted, remote_addr):
    with override_settings(MCP_OAUTH_TRUSTED_PROXIES=trusted):
        assert _client_ident_for(remote_addr, "198.51.100.10") == "198.51.100.10"


@override_settings(MCP_OAUTH_TRUSTED_PROXIES=[])
def test_forwarded_address_is_ignored_without_trusted_proxies():
    assert _client_ident_for("10.0.0.1", "198.51.100.10") == "10.0.0.1"


@override_settings(MCP_OAUTH_TRUSTED_PROXIES=["not-an-address", "10.0.0.0/8"])
def test_unparseable_trusted_proxy_entry_is_skipped():
    assert _client_ident_for("10.0.0.1", "198.51.100.10") == "198.51.100.10"


@override_settings(
    MCP_OAUTH_TRUSTED_PROXIES=["10.0.0.0/8"],
    REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": 1},
)
def test_configured_proxy_count_takes_precedence_over_trusted_proxies():
    assert _client_ident_for("10.0.0.1", "192.0.2.7, 10.0.0.9") == "10.0.0.9"


@override_settings(
    MCP_OAUTH_TRUSTED_PROXIES=["10.0.0.0/8"],
    MCP_OAUTH_TOKEN_IP_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_CLIENT_THROTTLE_RATE="100/min",
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_LIMIT=2,
    MCP_OAUTH_TOKEN_INVALID_ATTEMPT_WINDOW_SECONDS=300,
    MCP_OAUTH_TOKEN_INVALID_LOCKOUT_SECONDS=60,
)
def test_clients_behind_a_trusted_proxy_get_separate_lockouts(
    api_client, oauth_client_app
):
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": "bad-refresh-token",
        "client_id": oauth_client_app.client_id,
        "client_secret": CLIENT_SECRET,
    }

    def post(forwarded_for):
        return _post_token(
            api_client,
            payload,
            remote_addr="10.0.0.1",
            HTTP_X_FORWARDED_FOR=forwarded_for,
        ).status_code

    assert [post("198.51.100.10") for _ in range(3)] == [400, 400, 429]
    # The proxy appends the address it saw, so an earlier entry changes nothing.
    assert post("192.0.2.1, 198.51.100.10") == 429
    # Another client behind the same proxy has its own counter.
    assert post("198.51.100.11") == 400
