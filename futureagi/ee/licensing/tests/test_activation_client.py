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
    dispatch_managed_stream,
    get_service_token,
    invalidate_token,
    stream_managed_service,
)


@pytest.fixture(autouse=True)
def reset_token_cache():
    import ee.licensing.activation_client as mod

    mod._cached_token = None
    yield
    mod._cached_token = None


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


class _StreamResponse:
    def __init__(self, lines, status_code=200):
        self.lines = lines
        self.status_code = status_code

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
