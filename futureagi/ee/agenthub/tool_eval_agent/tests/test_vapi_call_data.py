"""Offline regressions for the legacy VAPI tool-evaluation data path.

Load the real agent module with only its external dependencies stubbed. This
also allows this pytest-discoverable file to run directly without Django,
a database, provider credentials, or an LLM client.
"""

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch


AGENT_PATH = Path(__file__).resolve().parents[1] / "tool_eval_agent.py"
VAPI_MODULE = "ee.voice.services.vapi_service"


def _module(name, **attributes):
    module = ModuleType(name)
    module.__dict__.update(attributes)
    return module


def _load_agent():
    spec = importlib.util.spec_from_file_location("_tool_eval_agent_test", AGENT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ToolEvalAgent


class TestVapiCallData(unittest.TestCase):
    def setUp(self):
        self.service = Mock(spec=["fetch_call"])
        self.service_factory = Mock(return_value=self.service)
        self.transcripts = Mock()
        self.transcripts.objects.filter.return_value.order_by.return_value = []
        model_config = SimpleNamespace(
            model_name="test-model", temperature=0, max_tokens=100, provider="test"
        )
        dependencies = {
            "ee.agenthub.tool_eval_agent.prompts": {
                "TOOL_EVAL_PLANNING_PROMPT": "",
                "TOOL_EVAL_ANALYSIS_PROMPT": "",
                "TOOL_EVAL_VALIDATION_PROMPT": "",
            },
            "agentic_eval.core.llm.llm": {"LLM": Mock()},
            "agentic_eval.core.utils.model_config": {
                "ModelConfigs": SimpleNamespace(VERTEX_GEMINI_3_7_FLASH=model_config)
            },
            "structlog": {"get_logger": Mock(return_value=Mock())},
            "simulate.models.test_execution": {
                "CallExecution": type("CallExecution", (), {}),
                "CallTranscript": self.transcripts,
            },
            "simulate.models.chat_message": {"ChatMessageModel": Mock()},
            "ee.voice.services.types.voice": {"TranscriptMessage": object},
            "tracer.models.observability_provider": {
                "ProviderChoices": SimpleNamespace(VAPI="vapi")
            },
            VAPI_MODULE: {"VapiService": self.service_factory},
        }
        self.enterContext(
            patch.dict(
                sys.modules,
                {name: _module(name, **attrs) for name, attrs in dependencies.items()},
            )
        )
        self.agent_class = _load_agent()
        self.agent = self.agent_class(llm=Mock())
        self.tool_calls = [
            {
                "id": "tool-1",
                "function": {"name": "lookup", "arguments": '{"item": "sample"}'},
            }
        ]
        self.raw_data = {
            "metadata": {"source": "test"},
            "artifact": {
                "messages": [
                    {
                        "role": "tool_call_result",
                        "time": 3000,
                        "toolCallId": "tool-1",
                        "result": '{"found": true}',
                    },
                    {"role": "bot", "time": 4000, "message": "Found it."},
                    {"role": "user", "time": 1000, "message": "Find sample."},
                    {"role": "tool_calls", "time": 2000, "toolCalls": self.tool_calls},
                ]
            },
        }
        self.service.fetch_call.return_value = self.raw_data
        self.expected_context = [
            {"timestamp_ms": 1000, "speaker_role": "user", "content": "Find sample."},
            {
                "timestamp_ms": 2000,
                "speaker_role": "tool_calls",
                "content": json.dumps(self.tool_calls),
            },
            {
                "timestamp_ms": 3000,
                "speaker_role": "tool_call_result",
                "content": '{"found": true}',
            },
            {"timestamp_ms": 4000, "speaker_role": "bot", "content": "Found it."},
        ]

    def test_default_service_fetches_raw_data_and_is_reused(self):
        self.service_factory.assert_not_called()

        result = self.agent._get_call_data_from_provider("call-1")
        self.agent._get_call_data_from_provider("call-2")

        self.service_factory.assert_called_once_with()
        self.assertEqual(
            self.service.fetch_call.call_args_list, [call("call-1"), call("call-2")]
        )
        self.assertIs(result["vapi_data"], self.raw_data)
        self.assertEqual(result["call_metadata"], {"source": "test"})
        self.assertEqual(result["conversation_context"], self.expected_context)

    def test_explicit_key_does_not_replace_default_service(self):
        explicit_service = Mock(spec=["fetch_call"])
        explicit_service.fetch_call.return_value = self.raw_data
        self.service_factory.side_effect = [explicit_service, self.service]

        result = self.agent._get_call_data_from_provider("customer-call", api_key="test-only")

        self.service_factory.assert_called_once_with(api_key="test-only")
        explicit_service.fetch_call.assert_called_once_with("customer-call")
        self.assertEqual(result["conversation_context"], self.expected_context)
        self.assertIs(result["vapi_data"], self.raw_data)
        self.assertEqual(result["call_metadata"], {"source": "test"})

        self.agent._get_call_data_from_provider("default-call")
        self.assertEqual(self.service_factory.call_count, 2)
        self.service_factory.assert_called_with()
        self.service.fetch_call.assert_called_once_with("default-call")

    def test_default_service_is_scoped_to_each_agent(self):
        other_service = Mock(spec=["fetch_call"])
        other_service.fetch_call.return_value = self.raw_data
        self.service_factory.side_effect = [self.service, other_service]
        other_agent = self.agent_class(llm=Mock())

        self.agent._get_call_data_from_provider("first-call")
        other_agent._get_call_data_from_provider("second-call")

        self.assertEqual(self.service_factory.call_count, 2)
        self.service.fetch_call.assert_called_once_with("first-call")
        other_service.fetch_call.assert_called_once_with("second-call")

    def test_database_fallback_fetches_raw_data(self):
        call_execution = SimpleNamespace(
            provider_call_data={}, service_provider_call_id="stored-call", call_metadata={}
        )
        self.transcripts.objects.filter.return_value.order_by.return_value = [
            SimpleNamespace(start_time_ms=1000, speaker_role="user", content="Hello")
        ]

        result = self.agent._get_call_data(call_execution)

        self.service.fetch_call.assert_called_once_with("stored-call")
        self.assertIs(result["vapi_data"], self.raw_data)
        self.assertEqual(
            result["conversation_context"],
            [{"timestamp_ms": 1000, "speaker_role": "user", "content": "Hello"}],
        )

    def test_stored_provider_data_does_not_construct_service(self):
        call_execution = SimpleNamespace(
            provider_call_data={"vapi": self.raw_data}, call_metadata={}
        )

        result = self.agent._get_call_data(call_execution)

        self.assertIs(result["vapi_data"], self.raw_data)
        self.service_factory.assert_not_called()

    def test_provider_agnostic_path_works_without_vapi_dependency(self):
        with patch.dict(sys.modules, {VAPI_MODULE: None}):
            agent = _load_agent()(llm=Mock())
            result = agent.evaluate_tool_calls([])

        self.assertEqual(result, {"tool_calls_data": [], "conversation_context": []})
        self.service_factory.assert_not_called()

    def test_fetch_error_keeps_existing_error_contract(self):
        self.service.fetch_call.side_effect = RuntimeError("provider unavailable")

        with self.assertRaisesRegex(
            ValueError, "Failed to fetch call data from VAPI API: provider unavailable"
        ):
            self.agent._get_call_data_from_provider("unavailable-call")

        self.service.fetch_call.assert_called_once_with("unavailable-call")


if __name__ == "__main__":
    unittest.main()
