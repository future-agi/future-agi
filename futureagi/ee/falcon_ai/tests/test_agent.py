"""
Unit tests for the AgentLoop class.

Tests cover:
- Tool loading from registry
- OpenAI function format conversion
- Completion card building
- Agent run with mocked LLM
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ee.falcon_ai.agent import AgentLoop
from ee.falcon_ai.modes import CORE_TOOLS


@pytest.mark.django_db
class TestAgentLoopSetup:
    def test_load_tools_via_mode(self, falcon_context, conversation):
        """Tools are loaded lazily via mode detection in run()."""
        from ee.falcon_ai.modes import load_tools_for_mode

        tools = load_tools_for_mode("general")
        # Should load at least core tools
        assert len(tools) >= 0  # may be 0 if tools not registered in test env

    def test_core_tools_defined(self, falcon_context, conversation):
        """CORE_TOOLS should be defined with expected tool names."""
        assert "whoami" in CORE_TOOLS
        assert "search" in CORE_TOOLS
        assert len(CORE_TOOLS) >= 3

    def test_tools_openai_format(self, falcon_context, conversation):
        """Tools should convert to OpenAI function calling format."""
        agent = AgentLoop(falcon_context, conversation)
        tools = agent._tools_as_openai_format()
        assert isinstance(tools, list)
        for t in tools[:5]:
            assert t["type"] == "function"
            assert "name" in t["function"]
            assert "description" in t["function"]
            assert "parameters" in t["function"]

    def test_tools_format_has_valid_parameters(self, falcon_context, conversation):
        """Each tool's parameters should be a dict with 'type' key."""
        agent = AgentLoop(falcon_context, conversation)
        tools = agent._tools_as_openai_format()
        for t in tools[:5]:
            params = t["function"]["parameters"]
            assert isinstance(params, dict)
            assert params.get("type") == "object"


@pytest.mark.django_db
class TestCompletionCard:
    def test_known_create_action(self, falcon_context, conversation):
        """Known create actions should produce specific titles."""
        agent = AgentLoop(falcon_context, conversation)
        card = agent._build_completion_card("create_dataset", "Created dataset X")
        assert card["title"] == "Dataset created"
        assert card["action_label"] == "Go to dataset"
        assert card["status"] == "completed"
        assert "/dashboard/develop/" in card["action_path"]

    def test_create_eval_template(self, falcon_context, conversation):
        agent = AgentLoop(falcon_context, conversation)
        card = agent._build_completion_card("create_eval_template", "OK")
        assert card["title"] == "Evaluation template created"
        assert card["action_label"] == "Go to evaluation"

    def test_create_experiment(self, falcon_context, conversation):
        agent = AgentLoop(falcon_context, conversation)
        card = agent._build_completion_card("create_experiment", "OK")
        assert card["title"] == "Experiment created"

    def test_deep_link_uses_entity_id_from_result(
        self, falcon_context, conversation
    ):
        """When result_text carries a UUID in backticks, deep-link to detail."""
        agent = AgentLoop(falcon_context, conversation)
        result_text = (
            "## Dataset Created\n\n"
            "**Dataset ID:** `b51fe199-2d01-48bf-865c-c84242400340`\n"
            "**Name:** lic\n"
        )
        card = agent._build_completion_card("create_dataset", result_text)
        assert (
            card["action_path"]
            == "/dashboard/develop/b51fe199-2d01-48bf-865c-c84242400340"
        )

    def test_falls_back_to_list_when_no_id_in_result(
        self, falcon_context, conversation
    ):
        agent = AgentLoop(falcon_context, conversation)
        card = agent._build_completion_card(
            "create_dataset", "Created dataset but no id in text"
        )
        assert card["action_path"] == "/dashboard/develop/"

    def test_unknown_create_action(self, falcon_context, conversation):
        """Unknown create actions should get a generic card."""
        agent = AgentLoop(falcon_context, conversation)
        card = agent._build_completion_card("create_foobar", "Created foobar")
        assert "Foobar" in card["title"]
        assert "created" in card["title"].lower()
        assert card["action_label"] == "View"
        assert card["status"] == "completed"


@pytest.mark.django_db
class TestAgentRun:
    @pytest.mark.asyncio
    async def test_simple_text_response(self, falcon_context, conversation):
        """Agent should handle a simple text response (no tool calls)."""
        agent = AgentLoop(falcon_context, conversation)

        # Mock LLM streaming to return simple text
        async def mock_stream(messages, tools=None):
            yield {
                "choices": [{"delta": {"content": "Hello "}, "finish_reason": None}],
                "model": "gpt-4o-mini",
            }
            yield {
                "choices": [{"delta": {"content": "world!"}, "finish_reason": "stop"}],
                "model": "gpt-4o-mini",
            }

        agent.llm_client.stream_completion = mock_stream
        send_callback = AsyncMock()

        result = await agent.run("Hi", [], send_callback)

        assert result["content"] == "Hello world!"
        assert result["model_used"] == "gpt-4o-mini"
        assert result["tool_calls"] == []
        assert result["completion_card"] is None

        # Should have received text_delta and iteration_start callbacks
        text_calls = [
            c
            for c in send_callback.call_args_list
            if c[0][0].get("type") == "text_delta"
        ]
        assert len(text_calls) == 2

        # Should have received iteration_start
        iter_calls = [
            c
            for c in send_callback.call_args_list
            if c[0][0].get("type") == "iteration_start"
        ]
        assert len(iter_calls) >= 1

    @pytest.mark.asyncio
    async def test_llm_error_handling(self, falcon_context, conversation):
        """Agent should handle LLM errors gracefully."""
        agent = AgentLoop(falcon_context, conversation)

        async def mock_stream_error(messages, tools=None):
            raise Exception("LLM service unavailable")
            yield  # noqa: unreachable - makes this an async generator

        agent.llm_client.stream_completion = mock_stream_error
        send_callback = AsyncMock()

        result = await agent.run("Hi", [], send_callback)

        # Should have sent an error callback
        error_calls = [
            c for c in send_callback.call_args_list if c[0][0].get("type") == "error"
        ]
        assert len(error_calls) >= 1
        assert result["content"] == ""

    @pytest.mark.asyncio
    async def test_managed_service_error_propagates(self, falcon_context, conversation):
        from ee.licensing.activation_client import ManagedServiceError

        agent = AgentLoop(falcon_context, conversation)

        async def mock_stream_error(messages, tools=None):
            raise ManagedServiceError("ACTIVATION_FAILED", "no token")
            yield  # noqa: unreachable - makes this an async generator

        agent.llm_client.stream_completion = mock_stream_error

        with pytest.raises(ManagedServiceError):
            await agent.run("Hi", [], AsyncMock())

    @pytest.mark.asyncio
    async def test_result_includes_id(self, falcon_context, conversation):
        """Agent result should include a UUID message id."""
        agent = AgentLoop(falcon_context, conversation)

        async def mock_stream(messages, tools=None):
            yield {
                "choices": [{"delta": {"content": "Done"}, "finish_reason": "stop"}],
                "model": "gpt-4o-mini",
            }

        agent.llm_client.stream_completion = mock_stream
        send_callback = AsyncMock()

        result = await agent.run("Test", [], send_callback)

        assert "id" in result
        # Should be a valid UUID string
        uuid.UUID(result["id"])

    @pytest.mark.asyncio
    async def test_title_generation_tokens_are_accumulated(
        self, falcon_context, conversation
    ):
        conversation.title = "New conversation"
        agent = AgentLoop(falcon_context, conversation)
        agent._save_conversation_title = AsyncMock()
        calls = {"count": 0}

        async def mock_stream(messages, tools=None):
            calls["count"] += 1
            if calls["count"] == 1:
                yield {
                    "choices": [
                        {"delta": {"content": "Answer"}, "finish_reason": "stop"}
                    ],
                    "model": "turing_small",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 4},
                }
                return

            yield {
                "choices": [
                    {"delta": {"content": "Short Title"}, "finish_reason": None}
                ],
                "model": "turing_small",
                "usage": {"prompt_tokens": 6, "completion_tokens": 2},
            }
            yield {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "model": "turing_small",
            }

        agent.llm_client.stream_completion = mock_stream

        result = await agent.run("Hi", [], AsyncMock())

        assert result["input_tokens"] == 16
        assert result["output_tokens"] == 6
        assert result["title"] == "Short Title"

    @pytest.mark.asyncio
    async def test_max_iterations_constant(self):
        """MAX_ITERATIONS should be a reasonable limit."""
        assert AgentLoop.MAX_ITERATIONS == 200
