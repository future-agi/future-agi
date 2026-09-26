"""Tests for FalconLLMClient default settings.

Regression coverage for TH-4501: the default ``max_tokens`` must be large
enough that tool-call arguments carrying long user prompts (for example
``create_agent_definition(description=<10K-character prompt>)``) are not
truncated mid-string because the model ran out of output budget.
"""

import unittest
from unittest import mock

from ee.falcon_ai.llm_client import FalconLLMClient

FALCON_ENV_KEYS = [
    "FALCON_AI_MAX_TOKENS",
    "FALCON_AI_TEMPERATURE",
    "FALCON_AI_PROVIDER",
    "FALCON_AI_MODEL",
    "FALCON_AI_EXTENDED_THINKING",
]


def _clear_env():
    """Return an environ dict with no FALCON_AI_* leakage from host/container."""
    return {"ANTHROPIC_API_KEY": "test-key"}


class ManagedTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_uses_managed_gateway_for_default_client(self):
        response = {
            "choices": [{"message": {"content": "summary"}}],
        }
        with (
            mock.patch.dict("os.environ", _clear_env(), clear=True),
            mock.patch(
                "ee.licensing.managed_ai.chat_completion",
                return_value=response,
            ) as managed_call,
            mock.patch("ee.falcon_ai.llm_client.httpx.AsyncClient") as direct_client,
        ):
            client = FalconLLMClient()
            result = await client.generate_summary("Summarize", "conversation")

        self.assertEqual(result, "summary")
        self.assertEqual(managed_call.call_args.args[0]["model"], "falcon_ai")
        direct_client.assert_not_called()


class MaxTokensDefaultTests(unittest.TestCase):
    """TH-4501: default max_tokens must fit long tool-call arguments."""

    def test_default_is_at_least_16k(self):
        with mock.patch.dict("os.environ", _clear_env(), clear=True):
            client = FalconLLMClient(provider="anthropic")
        self.assertGreaterEqual(
            client.max_tokens,
            16384,
            "max_tokens default would truncate long tool arguments "
            "(e.g. create_agent_definition with a long prompt). "
            "TH-4501 requires >= 16384.",
        )

    def test_env_override_wins_when_higher(self):
        env = _clear_env() | {"FALCON_AI_MAX_TOKENS": "32768"}
        with mock.patch.dict("os.environ", env, clear=True):
            client = FalconLLMClient(provider="anthropic")
        self.assertEqual(client.max_tokens, 32768)

    def test_env_override_wins_when_lower(self):
        # Deployment-level override still respected even if lower — callers
        # explicitly set this. Kept for backwards-compat with existing envs.
        env = _clear_env() | {"FALCON_AI_MAX_TOKENS": "2048"}
        with mock.patch.dict("os.environ", env, clear=True):
            client = FalconLLMClient(provider="anthropic")
        self.assertEqual(client.max_tokens, 2048)

    def test_constructor_arg_wins_over_env(self):
        env = _clear_env() | {"FALCON_AI_MAX_TOKENS": "8192"}
        with mock.patch.dict("os.environ", env, clear=True):
            client = FalconLLMClient(provider="anthropic", max_tokens=40000)
        self.assertEqual(client.max_tokens, 40000)
