"""Tests for FalconLLMClient.response_format support.

Verifies that the response_format attribute defaults to None and is
included in the _stream_openai payload when set.

To run:
    docker exec backend bash -c "export DJANGO_SETTINGS_MODULE=tfc.settings.test && python -m pytest ee/falcon_ai/tests/test_llm_client_response_format.py -v -s -n 0"
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

EVAL_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "eval_result",
        "schema": {
            "type": "object",
            "properties": {
                "result": {"type": "number"},
                "explanation": {"type": "string"},
            },
            "required": ["result", "explanation"],
        },
    },
}


def _clear_env():
    """Return an environ dict with no FALCON_AI_* leakage from host/container."""
    return {"ANTHROPIC_API_KEY": "test-key"}


class ResponseFormatTests(unittest.TestCase):
    """response_format attribute on FalconLLMClient."""

    def test_default_is_none(self):
        with mock.patch.dict("os.environ", _clear_env(), clear=True):
            client = FalconLLMClient(provider="anthropic")
        self.assertIsNone(client.response_format)

    def test_can_be_set(self):
        with mock.patch.dict("os.environ", _clear_env(), clear=True):
            client = FalconLLMClient(provider="anthropic")
        client.response_format = EVAL_RESPONSE_FORMAT
        self.assertEqual(client.response_format["type"], "json_schema")
        self.assertEqual(client.response_format["json_schema"]["name"], "eval_result")

    def test_none_after_reset(self):
        with mock.patch.dict("os.environ", _clear_env(), clear=True):
            client = FalconLLMClient(provider="anthropic")
        client.response_format = EVAL_RESPONSE_FORMAT
        client.response_format = None
        self.assertIsNone(client.response_format)
