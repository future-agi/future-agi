"""Transport routing for managed AI (Falcon / Turing / Protect).

The managed completion entry point must resolve its transport by deployment
mode: cloud reaches the co-located AgentCC gateway with the internal API key,
while self-hosted EE uses the activation-token exchange. A cloud deployment
carries no ``EE_LICENSE_KEY``, so routing it through the activation flow is
what broke Falcon on cloud.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from ee.licensing.activation_client import ManagedServiceError
from ee.licensing.managed_ai import chat_completion, stream_chat_completion


def _ok_response(payload=None):
    response = MagicMock(status_code=200)
    response.json.return_value = payload or {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    return response


class TestCloudTransport:
    def test_cloud_posts_to_internal_gateway_with_internal_key(self):
        response = _ok_response()
        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
            patch(
                "ee.usage.services.gateway_llm_client._get_setting",
                side_effect=lambda name, default="": {
                    "AGENTCC_INTERNAL_URL": "http://agentcc-gateway:8080",
                    "AGENTCC_INTERNAL_API_KEY": "internal-key",
                }.get(name, default),
            ),
            patch("httpx.post", return_value=response) as mock_post,
        ):
            result = chat_completion(
                {"model": "falcon_ai", "messages": [{"role": "user", "content": "hi"}]}
            )

        assert result["choices"][0]["message"]["content"] == "ok"
        url = (
            mock_post.call_args.args[0]
            if mock_post.call_args.args
            else (mock_post.call_args.kwargs["url"])
        )
        assert url == "http://agentcc-gateway:8080/v1/chat/completions"
        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer internal-key"

    def test_cloud_does_not_use_activation_flow(self):
        response = _ok_response()
        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
            patch(
                "ee.usage.services.gateway_llm_client._get_setting",
                side_effect=lambda name, default="": {
                    "AGENTCC_INTERNAL_URL": "http://agentcc-gateway:8080",
                    "AGENTCC_INTERNAL_API_KEY": "internal-key",
                }.get(name, default),
            ),
            patch("httpx.post", return_value=response),
            patch(
                "ee.licensing.activation_client.call_managed_service"
            ) as mock_activation,
        ):
            chat_completion({"model": "falcon_ai", "messages": []})

        mock_activation.assert_not_called()

    def test_cloud_missing_internal_key_raises_typed_error(self):
        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
            patch("ee.usage.services.gateway_llm_client._get_setting", return_value=""),
            patch("httpx.post") as mock_post,
        ):
            with pytest.raises(ManagedServiceError) as exc:
                chat_completion({"model": "falcon_ai", "messages": []})

        assert exc.value.code == "GATEWAY_UNCONFIGURED"
        mock_post.assert_not_called()

    @pytest.mark.parametrize(
        ("status_code", "expected_code"),
        [
            (401, "GATEWAY_UNAUTHORIZED"),
            (403, "FEATURE_DENIED"),
            (429, "RATE_LIMITED"),
            (500, "SERVICE_ERROR"),
        ],
    )
    def test_cloud_maps_gateway_status_to_typed_error(self, status_code, expected_code):
        response = MagicMock(status_code=status_code)
        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
            patch(
                "ee.usage.services.gateway_llm_client._get_setting",
                side_effect=lambda name, default="": {
                    "AGENTCC_INTERNAL_URL": "http://agentcc-gateway:8080",
                    "AGENTCC_INTERNAL_API_KEY": "internal-key",
                }.get(name, default),
            ),
            patch("httpx.post", return_value=response),
        ):
            with pytest.raises(ManagedServiceError) as exc:
                chat_completion({"model": "falcon_ai", "messages": []})

        assert exc.value.code == expected_code

    def test_cloud_unauthorized_does_not_touch_ee_token_cache(self):
        response = MagicMock(status_code=401)
        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
            patch(
                "ee.usage.services.gateway_llm_client._get_setting",
                side_effect=lambda name, default="": {
                    "AGENTCC_INTERNAL_URL": "http://agentcc-gateway:8080",
                    "AGENTCC_INTERNAL_API_KEY": "internal-key",
                }.get(name, default),
            ),
            patch("httpx.post", return_value=response),
            patch("ee.licensing.activation_client.invalidate_token") as invalidate,
        ):
            with pytest.raises(ManagedServiceError):
                chat_completion({"model": "falcon_ai", "messages": []})

        invalidate.assert_not_called()

    @pytest.mark.parametrize(
        ("error", "expected_code"),
        [
            (httpx.TimeoutException("timed out"), "GATEWAY_TIMEOUT"),
            (httpx.ConnectError("unreachable"), "GATEWAY_UNREACHABLE"),
        ],
    )
    def test_cloud_maps_transport_failure_to_typed_error(self, error, expected_code):
        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
            patch(
                "ee.usage.services.gateway_llm_client._get_setting",
                side_effect=lambda name, default="": {
                    "AGENTCC_INTERNAL_URL": "http://agentcc-gateway:8080",
                    "AGENTCC_INTERNAL_API_KEY": "internal-key",
                }.get(name, default),
            ),
            patch("httpx.post", side_effect=error),
        ):
            with pytest.raises(ManagedServiceError) as exc:
                chat_completion({"model": "falcon_ai", "messages": []})

        assert exc.value.code == expected_code


class TestCloudStreamTransport:
    @pytest.mark.asyncio
    async def test_cloud_stream_uses_internal_key_without_activation(self):
        chunks = [{"choices": [{"delta": {"content": "falcon"}}]}]

        async def dispatch(**kwargs):
            assert kwargs["url"] == ("http://agentcc-gateway:8080/v1/chat/completions")
            assert kwargs["api_key"] == "internal-key"
            for chunk in chunks:
                yield chunk

        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
            patch(
                "ee.usage.services.gateway_llm_client._get_setting",
                side_effect=_internal_setting,
            ),
            patch(
                "ee.licensing.managed_ai.dispatch_managed_stream",
                new=dispatch,
            ),
            patch(
                "ee.licensing.managed_ai.stream_managed_service"
            ) as activation_stream,
        ):
            result = [
                chunk
                async for chunk in stream_chat_completion(
                    {"model": "falcon_ai", "messages": [], "stream": True}
                )
            ]

        assert result == chunks
        activation_stream.assert_not_called()

    @pytest.mark.asyncio
    async def test_self_hosted_stream_uses_activation_transport(self):
        chunks = [{"choices": [{"delta": {"content": "ee"}}]}]

        async def activation_stream(*, json_body):
            assert json_body["model"] == "falcon_ai"
            for chunk in chunks:
                yield chunk

        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=False),
            patch(
                "ee.licensing.managed_ai.stream_managed_service",
                new=activation_stream,
            ),
            patch(
                "ee.usage.services.gateway_llm_client._get_setting"
            ) as internal_setting,
        ):
            result = [
                chunk
                async for chunk in stream_chat_completion(
                    {"model": "falcon_ai", "messages": [], "stream": True}
                )
            ]

        assert result == chunks
        internal_setting.assert_not_called()


class TestSelfHostedTransport:
    def test_ee_uses_activation_flow(self):
        expected = {"choices": [{"message": {"content": "ee"}}]}
        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=False),
            patch(
                "ee.licensing.managed_ai.call_managed_service", return_value=expected
            ) as mock_activation,
            patch("httpx.post") as mock_post,
        ):
            result = chat_completion({"model": "falcon_ai", "messages": []})

        assert result == expected
        mock_activation.assert_called_once()
        assert mock_activation.call_args.kwargs["path"] == "/v1/chat/completions"
        mock_post.assert_not_called()


class TestModelGuard:
    def test_rejects_non_managed_model(self):
        with pytest.raises(ValueError):
            chat_completion({"model": "gpt-4o", "messages": []})


# ── Regression guards ────────────────────────────────────────────────────────
#
# The bug: managed AI on cloud went through the self-hosted activation-token
# exchange (get_service_token / _activate), which needs an EE_LICENSE_KEY that
# cloud does not carry, so every managed call failed. These guards fail loudly
# if that branch is removed, if a managed model is added that skips it, or if a
# client entry point stops routing through chat_completion.

_INTERNAL = {
    "AGENTCC_INTERNAL_URL": "http://agentcc-gateway:8080",
    "AGENTCC_INTERNAL_API_KEY": "internal-key",
}

# Every managed-model shape is_managed_model() recognises. If someone adds a
# new managed prefix, add it here so the cloud guard covers it too.
_MANAGED_MODELS = [
    "falcon_ai",
    "turing_small",
    "turing_large",
    "protect",
    "protect_flash",
]


def _internal_setting(name, default=""):
    return _INTERNAL.get(name, default)


def _posted_url(mock_post):
    if mock_post.call_args.args:
        return mock_post.call_args.args[0]
    return mock_post.call_args.kwargs["url"]


def _posted_auth(mock_post):
    return mock_post.call_args.kwargs["headers"]["Authorization"]


class TestCloudActivationGuard:
    """On cloud, no managed model may reach the self-hosted activation flow."""

    @pytest.mark.parametrize("model", _MANAGED_MODELS)
    def test_cloud_never_calls_activation_token_exchange(self, model):
        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
            patch(
                "ee.usage.services.gateway_llm_client._get_setting",
                side_effect=_internal_setting,
            ),
            patch("httpx.post", return_value=_ok_response()) as mock_post,
            patch("ee.licensing.activation_client.get_service_token") as mock_get_token,
            patch("ee.licensing.activation_client._activate") as mock_activate,
        ):
            chat_completion(
                {"model": model, "messages": [{"role": "user", "content": "x"}]}
            )

        # Routed to the internal gateway with the internal key…
        assert (
            _posted_url(mock_post) == "http://agentcc-gateway:8080/v1/chat/completions"
        )
        assert _posted_auth(mock_post) == "Bearer internal-key"
        # …and the activation exchange was never touched.
        mock_get_token.assert_not_called()
        mock_activate.assert_not_called()


class TestEeActivationGuard:
    """Symmetric guard: EE must keep using activation, not the internal key."""

    @pytest.mark.parametrize("model", _MANAGED_MODELS)
    def test_ee_never_reads_internal_gateway_settings(self, model):
        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=False),
            patch(
                "ee.licensing.managed_ai.call_managed_service",
                return_value={"choices": [{"message": {"content": "ee"}}]},
            ) as mock_activation,
            patch("ee.usage.services.gateway_llm_client._get_setting") as mock_setting,
            patch("httpx.post") as mock_post,
        ):
            chat_completion({"model": model, "messages": []})

        mock_activation.assert_called_once()
        # Cloud-only settings resolution must not run on EE.
        mock_setting.assert_not_called()
        mock_post.assert_not_called()


class TestCloudCheckNotBypassed:
    """chat_completion must consult DeploymentMode.is_cloud — so the branch
    cannot be silently dropped without a test noticing."""

    def test_deployment_mode_is_consulted(self):
        is_cloud = MagicMock(return_value=True)
        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", is_cloud),
            patch(
                "ee.usage.services.gateway_llm_client._get_setting",
                side_effect=_internal_setting,
            ),
            patch("httpx.post", return_value=_ok_response()),
        ):
            chat_completion({"model": "falcon_ai", "messages": []})

        is_cloud.assert_called()


class TestManagedClientsCloudEndToEnd:
    """The bug surfaced through the real clients, not chat_completion directly.
    Drive them on cloud and prove they hit the internal gateway with no
    activation — the true regression guard."""

    @pytest.mark.asyncio
    async def test_falcon_client_on_cloud_uses_internal_gateway(self):
        from ee.falcon_ai.llm_client import FalconLLMClient

        dispatch_calls = []

        async def dispatch(**kwargs):
            dispatch_calls.append(kwargs)
            yield {
                "choices": [
                    {
                        "delta": {"content": "falcon"},
                        "finish_reason": None,
                    }
                ]
            }
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
            patch(
                "ee.usage.services.gateway_llm_client._get_setting",
                side_effect=_internal_setting,
            ),
            patch(
                "ee.licensing.managed_ai.dispatch_managed_stream",
                new=dispatch,
            ),
            patch("ee.licensing.activation_client.get_service_token") as mock_get_token,
        ):
            client = FalconLLMClient()
            assert client.use_managed_gateway is True
            chunks = [
                chunk
                async for chunk in client.stream_completion(
                    [{"role": "user", "content": "analyze"}], tools=None
                )
            ]

        assert any(c["choices"][0]["delta"].get("content") == "falcon" for c in chunks)
        assert dispatch_calls[0]["url"] == (
            "http://agentcc-gateway:8080/v1/chat/completions"
        )
        assert dispatch_calls[0]["api_key"] == "internal-key"
        mock_get_token.assert_not_called()

    def test_turing_client_on_cloud_uses_internal_gateway(self):
        from ee.turing.client import TuringClient

        with (
            patch("ee.usage.deployment.DeploymentMode.is_cloud", return_value=True),
            patch(
                "ee.usage.services.gateway_llm_client._get_setting",
                side_effect=_internal_setting,
            ),
            patch(
                "httpx.post",
                return_value=_ok_response(
                    {
                        "choices": [{"message": {"content": "turing"}}],
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    }
                ),
            ) as mock_post,
            patch("ee.licensing.activation_client.get_service_token") as mock_get_token,
        ):
            result = TuringClient().chat_completion(
                model="turing_small",
                messages=[{"role": "user", "content": "hi"}],
            )

        assert result == "turing"
        assert (
            _posted_url(mock_post) == "http://agentcc-gateway:8080/v1/chat/completions"
        )
        assert _posted_auth(mock_post) == "Bearer internal-key"
        mock_get_token.assert_not_called()
