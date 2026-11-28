"""Tests for the managed AI activation client."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from ee.licensing.activation_client import (
    ManagedServiceError,
    ServiceToken,
    call_managed_service,
    dispatch_managed_request,
    dispatch_managed_stream,
    get_service_token,
    invalidate_token,
    stream_managed_service,
)


@pytest.fixture(autouse=True)
def reset_token_cache():
    import ee.licensing.activation_client as mod

    mod._cached_token = None
    mod._last_failure = None
    yield
    mod._cached_token = None
    mod._last_failure = None


class TestServiceToken:
    def test_not_expired_when_fresh(self):
        token = ServiceToken(
            access_token="tok_123",
            gateway_url="https://gateway.futureagi.com",
            expires_at=time.time() + 3600,
            allowed_services=["turing", "falcon"],
            allowed_models=["turing_large"],
            scope="enterprise",
        )
        assert token.is_expired is False

    def test_expired_within_refresh_margin(self):
        token = ServiceToken(
            access_token="tok_123",
            gateway_url="https://gateway.futureagi.com",
            expires_at=time.time() + 200,  # Less than 300s margin
            allowed_services=[],
            allowed_models=[],
            scope="enterprise",
        )
        assert token.is_expired is True

    def test_expired_past_expiry(self):
        token = ServiceToken(
            access_token="tok_123",
            gateway_url="https://gateway.futureagi.com",
            expires_at=time.time() - 100,
            allowed_services=[],
            allowed_models=[],
            scope="enterprise",
        )
        assert token.is_expired is True


class TestGetServiceToken:
    def test_returns_cached_token_if_valid(self):
        import ee.licensing.activation_client as mod

        mod._cached_token = ServiceToken(
            access_token="cached_tok",
            gateway_url="https://gw.test",
            expires_at=time.time() + 3600,
            allowed_services=["turing"],
            allowed_models=["turing_large"],
            scope="enterprise",
        )
        result = get_service_token()
        assert result.access_token == "cached_tok"

    @patch("ee.licensing.activation_client._activate")
    def test_activates_on_first_call(self, mock_activate):
        mock_activate.return_value = ServiceToken(
            access_token="new_tok",
            gateway_url="https://gw.test",
            expires_at=time.time() + 3600,
            allowed_services=[],
            allowed_models=[],
            scope="enterprise",
        )
        result = get_service_token()
        assert result.access_token == "new_tok"
        mock_activate.assert_called_once()

    @patch("ee.licensing.activation_client._activate")
    def test_returns_none_on_activation_failure(self, mock_activate):
        mock_activate.return_value = None
        result = get_service_token()
        assert result is None


class TestInvalidateToken:
    def test_clears_cached_token(self):
        import ee.licensing.activation_client as mod

        mod._cached_token = ServiceToken(
            access_token="old",
            gateway_url="",
            expires_at=time.time() + 3600,
            allowed_services=[],
            allowed_models=[],
            scope="enterprise",
        )
        invalidate_token()
        assert mod._cached_token is None


class TestActivate:
    def test_reports_the_version_deployment_telemetry_reports(self, monkeypatch):
        """``latest`` is a placeholder tag: telemetry falls back to
        SERVICE_VERSION, and activation must send the same version."""
        from ee.licensing.activation_client import _activate

        monkeypatch.setenv("FUTURE_AGI_VERSION", "latest")
        monkeypatch.setenv("SERVICE_VERSION", "1.8.0")
        monkeypatch.delenv("GIT_SHA", raising=False)
        state = MagicMock(instance_id="00000000-0000-0000-0000-000000000001")
        with (
            patch(
                "tfc.deployment_telemetry.state.get_or_create_telemetry_state",
                return_value=state,
            ),
            patch("httpx.post", return_value=MagicMock(status_code=503)) as post,
        ):
            assert _activate() is None

        assert post.call_args.kwargs["json"]["version"] == "1.8.0"


@pytest.fixture
def activation(settings):
    settings.EE_LICENSE_KEY = "fixture-license"
    with (
        patch("tfc.deployment_telemetry.state.get_or_create_telemetry_state"),
        patch("ee.licensing.activation_client.time", wraps=time) as clock,
        patch("httpx.post") as post,
    ):
        clock.monotonic.return_value = 100.0
        yield post, clock


class TestActivationBackoff:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status", "reason", "message"),
        [
            (401, "invalid_license", "not recognized"),
            (403, "license_inactive", "inactive"),
            (403, "license_expired", "expired"),
            (403, "instance_limit", "instance limit"),
        ],
    )
    async def test_shared_refusal(self, activation, status, reason, message):
        post, _ = activation
        post.return_value = httpx.Response(
            status, json={"error": reason, "detail": "untrusted response text"}
        )
        with patch("ee.licensing.activation_client.logger") as log:
            with pytest.raises(ManagedServiceError) as sync:
                call_managed_service(json_body={})
            with pytest.raises(ManagedServiceError) as stream:
                await anext(stream_managed_service(json_body={}))

        post.assert_called_once()
        assert sync.value.code == stream.value.code == "ACTIVATION_FAILED"
        assert sync.value.detail == stream.value.detail
        assert message in sync.value.detail
        assert "untrusted response text" not in sync.value.detail
        log.error.assert_called_once_with(
            "activation_failed",
            status_code=status,
            detail=sync.value.detail,
            retry_after=60,
        )

    def test_backoff_doubles_to_one_hour_and_resets_after_success(self, activation):
        post, clock = activation
        refusal = httpx.Response(401, json={"error": "invalid_license"})
        success = httpx.Response(200, json={"access_token": "service-token"})
        post.return_value = refusal
        for attempt, delay in enumerate([60, 120, 240, 480, 960, 1920, 3600, 3600], 1):
            assert get_service_token() is None
            assert post.call_count == attempt
            clock.monotonic.return_value += delay - 1
            assert get_service_token() is None
            assert post.call_count == attempt
            clock.monotonic.return_value += 1

        post.return_value = success
        assert get_service_token() is not None
        invalidate_token()
        post.return_value = refusal
        assert get_service_token() is None
        clock.monotonic.return_value += 60
        post.return_value = success
        assert get_service_token() is not None

    def test_failed_refresh_uses_old_token_only_until_expiry(self, activation):
        post, clock = activation
        clock.time.return_value = 1000
        post.return_value = httpx.Response(
            200, json={"access_token": "old-token", "expires_in": 10}
        )
        token = get_service_token()
        assert token is not None
        post.return_value = httpx.Response(503)
        assert get_service_token() is token
        clock.time.return_value += 10
        clock.monotonic.return_value += 10
        assert get_service_token() is None
        assert post.call_count == 2

    @pytest.mark.parametrize(("hint", "delay"), [(5, 5), (7200, 7200), ("bad", 60)])
    def test_rate_limit_defers_until_retry_after(self, activation, hint, delay):
        post, clock = activation
        post.side_effect = [
            httpx.Response(429, headers={"Retry-After": str(hint)}),
            httpx.Response(200, json={"access_token": "service-token"}),
        ]
        assert get_service_token() is None
        clock.monotonic.return_value += delay - 1
        assert get_service_token() is None
        post.assert_called_once()
        clock.monotonic.return_value += 1
        token = get_service_token()
        assert token is not None
        assert get_service_token() is token
        assert post.call_count == 2

    @pytest.mark.parametrize("change", ["key", "endpoint", "keyless"])
    def test_config_change_retries(self, activation, settings, monkeypatch, change):
        post, _ = activation
        post.return_value = httpx.Response(401, json={"error": "invalid_license"})
        assert get_service_token() is None
        assert get_service_token() is None
        post.assert_called_once()

        if change == "endpoint":
            monkeypatch.setenv("FUTURE_AGI_LICENSE_URL", "https://new.example.invalid")
        else:
            settings.EE_LICENSE_KEY = "" if change == "keyless" else "corrected-key"
        if change == "keyless":
            monkeypatch.delenv("EE_LICENSE_KEY", raising=False)
            post.return_value = httpx.Response(
                200, json={"access_token": None, "scope": "oss"}
            )
            with pytest.raises(ManagedServiceError) as exc:
                call_managed_service(json_body={})
            assert exc.value.detail == "Could not obtain service token"
            assert "license_key_hash" not in post.call_args.kwargs["json"]
        else:
            post.return_value = httpx.Response(200, json={"access_token": "token"})
            assert get_service_token() is not None
        assert post.call_count == 2


class _StreamResponse:
    def __init__(self, lines, status_code=200, headers=None):
        self.lines = lines
        self.status_code = status_code
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def aiter_lines(self):
        for line in self.lines:
            yield line

    def raise_for_status(self):
        return None


class _StreamClient:
    def __init__(self, response):
        self.response = response
        self.call = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def stream(self, method, url, **kwargs):
        self.call = (method, url, kwargs)
        return self.response


class TestManagedStreamDispatch:
    @pytest.mark.asyncio
    async def test_yields_data_events_and_stops_at_done(self):
        response = _StreamResponse(
            [
                "",
                ": keepalive",
                "event: message",
                'data: {"choices":[{"delta":{"content":"hi"}}]}',
                "data: [DONE]",
                'data: {"ignored":true}',
            ]
        )
        client = _StreamClient(response)

        with patch("httpx.AsyncClient", return_value=client):
            chunks = [
                chunk
                async for chunk in dispatch_managed_stream(
                    url="https://gw.test/v1/chat/completions",
                    api_key="token",
                    json_body={"model": "falcon_ai", "stream": True},
                    timeout=300.0,
                    on_unauthorized=lambda: None,
                )
            ]

        assert chunks == [{"choices": [{"delta": {"content": "hi"}}]}]
        assert client.call[0:2] == (
            "POST",
            "https://gw.test/v1/chat/completions",
        )
        headers = client.call[2]["headers"]
        assert headers["Authorization"] == "Bearer token"
        assert headers["x-agentcc-include-metadata"] == "true"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status_code", "expected_code"),
        [
            (403, "FEATURE_DENIED"),
            (429, "RATE_LIMITED"),
            (500, "SERVICE_ERROR"),
        ],
    )
    async def test_maps_gateway_status(self, status_code, expected_code):
        client = _StreamClient(_StreamResponse([], status_code=status_code))

        with patch("httpx.AsyncClient", return_value=client):
            with pytest.raises(ManagedServiceError) as exc:
                async for _ in dispatch_managed_stream(
                    url="https://gw.test/v1/chat/completions",
                    api_key="token",
                    json_body={},
                    timeout=300.0,
                    on_unauthorized=lambda: None,
                ):
                    pass

        assert exc.value.code == expected_code

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("error", "expected_code"),
        [
            (httpx.TimeoutException("timed out"), "GATEWAY_TIMEOUT"),
            (httpx.ConnectError("unreachable"), "GATEWAY_UNREACHABLE"),
        ],
    )
    async def test_maps_transport_failure(self, error, expected_code):
        client = _StreamClient(_StreamResponse([]))
        client.stream = MagicMock(side_effect=error)

        with patch("httpx.AsyncClient", return_value=client):
            with pytest.raises(ManagedServiceError) as exc:
                async for _ in dispatch_managed_stream(
                    url="https://gw.test/v1/chat/completions",
                    api_key="token",
                    json_body={},
                    timeout=300.0,
                    on_unauthorized=lambda: None,
                ):
                    pass

        assert exc.value.code == expected_code


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("2", 2),
        ("0", 0),
        ("-1", 0),
        # Kept as sent: the retry policy, not the parser, decides whether a
        # wait is one to sit out or one to fail on.
        ("3600", 3600),
        ("Tue, 14 Nov 2023 22:13:22 GMT", 2),
        ("Tue, 14 Nov 2023 22:14:20 GMT", 60),
        ("Tue, 14 Nov 2023 23:13:20 GMT", 3600),
        ("Tue, 14 Nov 2023 22:13:00 GMT", 0),
        (None, None),
        ("", None),
        ("not a delay", None),
        ("NaN", None),
        ("inf", None),
    ],
)
@pytest.mark.asyncio
async def test_retry_after_survives_managed_status_mapping(
    monkeypatch, streaming, header, expected
):
    monkeypatch.setattr("ee.licensing.activation_client.time.time", lambda: 1700000000)
    headers = {} if header is None else {"Retry-After": header}
    kwargs = {
        "url": "https://judge.example.invalid/v1/chat/completions",
        "api_key": "synthetic-fixture-key",
        "json_body": {},
        "timeout": 300,
        "on_unauthorized": lambda: None,
    }
    with pytest.raises(ManagedServiceError) as exc:
        if streaming:
            client = _StreamClient(_StreamResponse([], 429, headers=headers))
            with patch("httpx.AsyncClient", return_value=client):
                async for _ in dispatch_managed_stream(**kwargs):
                    pass
        else:
            with patch("httpx.post", return_value=httpx.Response(429, headers=headers)):
                dispatch_managed_request(**kwargs)
    assert exc.value.code == "RATE_LIMITED"
    assert exc.value.retry_after == expected


class TestStreamManagedService:
    @pytest.mark.asyncio
    async def test_unauthorized_invalidates_cached_token(self):
        token = ServiceToken(
            access_token="service-token",
            gateway_url="https://gw.test",
            expires_at=time.time() + 3600,
            allowed_services=["falcon"],
            allowed_models=["falcon_ai"],
            scope="enterprise",
        )

        async def dispatch(**kwargs):
            kwargs["on_unauthorized"]()
            if False:
                yield {}

        with (
            patch(
                "ee.licensing.activation_client.get_service_token",
                return_value=token,
            ),
            patch(
                "ee.licensing.activation_client.dispatch_managed_stream",
                new=dispatch,
            ),
            patch("ee.licensing.activation_client.invalidate_token") as invalidate,
        ):
            with pytest.raises(ManagedServiceError) as exc:
                async for _ in stream_managed_service(json_body={}):
                    pass

        assert exc.value.code == "TOKEN_EXPIRED"
        invalidate.assert_called_once_with()


class TestCallManagedService:
    def test_raises_on_no_token(self):
        with patch(
            "ee.licensing.activation_client.get_service_token", return_value=None
        ):
            with pytest.raises(ManagedServiceError) as exc:
                call_managed_service(
                    json_body={"model": "turing_large", "messages": []}
                )
            assert exc.value.code == "ACTIVATION_FAILED"

    def test_raises_on_oss_scope_without_token(self):
        oss_token = ServiceToken(
            access_token="",
            gateway_url="https://gw.test",
            expires_at=time.time() + 3600,
            allowed_services=[],
            allowed_models=[],
            scope="oss",
        )
        with patch(
            "ee.licensing.activation_client.get_service_token", return_value=oss_token
        ):
            with pytest.raises(ManagedServiceError) as exc:
                call_managed_service(
                    json_body={"model": "turing_large", "messages": []}
                )
            assert exc.value.code == "NO_ENTERPRISE_LICENSE"

    @pytest.mark.parametrize(
        ("status_code", "expected_code"),
        [
            (403, "FEATURE_DENIED"),
            (429, "RATE_LIMITED"),
            (500, "SERVICE_ERROR"),
        ],
    )
    def test_raises_typed_error_for_gateway_response(
        self,
        status_code,
        expected_code,
    ):
        token = self._enterprise_token()
        response = MagicMock(status_code=status_code)
        with (
            patch(
                "ee.licensing.activation_client.get_service_token",
                return_value=token,
            ),
            patch("httpx.post", return_value=response),
        ):
            with pytest.raises(ManagedServiceError) as exc:
                call_managed_service(
                    json_body={"model": "turing_large", "messages": []}
                )

        assert exc.value.code == expected_code

    def test_unauthorized_response_invalidates_cached_token(self):
        token = self._enterprise_token()
        response = MagicMock(status_code=401)
        with (
            patch(
                "ee.licensing.activation_client.get_service_token",
                return_value=token,
            ),
            patch("httpx.post", return_value=response),
            patch("ee.licensing.activation_client.invalidate_token") as invalidate,
        ):
            with pytest.raises(ManagedServiceError) as exc:
                call_managed_service(
                    json_body={"model": "turing_large", "messages": []}
                )

        assert exc.value.code == "TOKEN_EXPIRED"
        invalidate.assert_called_once_with()

    @pytest.mark.parametrize(
        ("error", "expected_code"),
        [
            (httpx.TimeoutException("timed out"), "GATEWAY_TIMEOUT"),
            (httpx.ConnectError("unreachable"), "GATEWAY_UNREACHABLE"),
        ],
    )
    def test_raises_typed_error_for_transport_failure(self, error, expected_code):
        token = self._enterprise_token()
        with (
            patch(
                "ee.licensing.activation_client.get_service_token",
                return_value=token,
            ),
            patch("httpx.post", side_effect=error),
        ):
            with pytest.raises(ManagedServiceError) as exc:
                call_managed_service(
                    json_body={"model": "turing_large", "messages": []}
                )

        assert exc.value.code == expected_code

    @staticmethod
    def _enterprise_token():
        return ServiceToken(
            access_token="service-token",
            gateway_url="https://gw.test",
            expires_at=time.time() + 3600,
            allowed_services=["turing"],
            allowed_models=["turing_large"],
            scope="enterprise",
        )
