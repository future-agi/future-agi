"""Regression tests for FutureAGIChatService message assembly (TH-7101).

The simulator persona must experience a human-like conversation, so the
agent-under-test's tool machinery is scrubbed from what the persona LLM sees:
tool results and pure tool-call turns are dropped, and only the agent's text
replies are forwarded (as assistant turns). This also makes the malformed
tool-call sequence that caused the send-message 500 structurally impossible.

The forwarding logic (`_to_provider_messages`) is pure — no DB — so these run
as plain unit tests. The full tool data is still persisted to ChatMessageModel
(covered by the eval adapter's own tests), so scrubbing here loses nothing.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from simulate.models.chat_simulator import (
    ChatSimulatorAssistant,
    ChatSimulatorSession,
)
from simulate.pydantic_schemas.chat import (
    ChatMessage,
    ChatRole,
    ToolCall,
    ToolCallFunction,
)
from simulate.services.futureagi_chat.llm_client import generate_simulator_response
from simulate.services.futureagi_chat.service import (
    FutureAGIChatService,
    SimulatorMessage,
)
from simulate.services.types.chat import LLMUsage, SendMessageInput


def _tool_call(call_id: str = "call_1", name: str = "get_order") -> ToolCall:
    return ToolCall(
        id=call_id,
        type="function",
        function=ToolCallFunction(name=name, arguments='{"order_id": "ORD-1"}'),
    )


@pytest.mark.unit
class TestSimulatorMessage:
    def test_plain_message_dict_shape(self):
        assert SimulatorMessage(role="user", content="hi").to_dict() == {
            "role": "user",
            "content": "hi",
        }

    def test_tool_fields_included_only_when_set(self):
        # tool_calls / tool_call_id are omitted unless explicitly provided.
        assert SimulatorMessage(role="assistant", content="hey").to_dict() == {
            "role": "assistant",
            "content": "hey",
        }
        d = SimulatorMessage(
            role="tool", content="out", tool_call_id="call_1"
        ).to_dict()
        assert d == {"role": "tool", "content": "out", "tool_call_id": "call_1"}


@pytest.mark.unit
class TestToProviderMessagesScrubbing:
    def setup_method(self):
        self.svc = FutureAGIChatService()

    def test_agent_text_turn_becomes_assistant(self):
        out = self.svc._to_provider_messages(
            [ChatMessage(role=ChatRole.USER, content="Your order shipped.")]
        )
        assert out == [{"role": "assistant", "content": "Your order shipped."}]

    def test_pure_tool_call_turn_is_scrubbed(self):
        # Empty content + tool_calls (a pure tool call) contributes nothing to
        # the persona's view.
        out = self.svc._to_provider_messages(
            [ChatMessage(role=ChatRole.ASSISTANT, content="", tool_calls=[_tool_call()])]
        )
        assert out == []

    def test_tool_result_is_scrubbed(self):
        out = self.svc._to_provider_messages(
            [
                ChatMessage(
                    role=ChatRole.TOOL, content="ORD-1 shipped", tool_call_id="call_1"
                )
            ]
        )
        assert out == []

    def test_tool_call_turn_with_text_keeps_only_text(self):
        # If a turn carries both text and tool_calls, the text survives as an
        # assistant message and the tool_calls are dropped.
        out = self.svc._to_provider_messages(
            [
                ChatMessage(
                    role=ChatRole.ASSISTANT,
                    content="Let me check that for you.",
                    tool_calls=[_tool_call()],
                )
            ]
        )
        assert out == [
            {"role": "assistant", "content": "Let me check that for you."}
        ]
        assert "tool_calls" not in out[0]

    def test_genuinely_empty_message_is_skipped(self):
        out = self.svc._to_provider_messages(
            [
                ChatMessage(role=ChatRole.USER, content=""),
                ChatMessage(role=ChatRole.USER, content="   "),
            ]
        )
        assert out == []

    def test_full_agent_turn_yields_only_text_in_order(self):
        # A realistic multi-step agent turn: think -> tool call -> tool result
        # -> final answer. The persona should see only the two text replies,
        # in order, both as assistant, with no tool artifacts.
        out = self.svc._to_provider_messages(
            [
                ChatMessage(role=ChatRole.ASSISTANT, content="Checking your order…"),
                ChatMessage(
                    role=ChatRole.ASSISTANT, content="", tool_calls=[_tool_call()]
                ),
                ChatMessage(
                    role=ChatRole.TOOL, content="ORD-1 shipped", tool_call_id="call_1"
                ),
                ChatMessage(
                    role=ChatRole.ASSISTANT, content="It shipped yesterday."
                ),
            ]
        )
        assert out == [
            {"role": "assistant", "content": "Checking your order…"},
            {"role": "assistant", "content": "It shipped yesterday."},
        ]
        assert all("tool_calls" not in m and "tool_call_id" not in m for m in out)


def _fake_llm_response(
    content: str = "",
    tool_calls=None,
    model: str = "gpt-4o",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    total_tokens: int = 15,
):
    """Build a litellm-shaped completion response for the simulator LLM."""
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)], usage=usage, model=model
    )


@pytest.mark.unit
class TestGenerateSimulatorResponseRoleFlip:
    """The stored transcript is written from the agent-under-test's view (persona
    lines are ``user``, agent lines are ``assistant``). The persona LLM must speak
    as the customer, so the seating is flipped before the call — otherwise the
    context ends on an assistant turn and the model continues the agent's message
    instead of answering it.
    """

    def _run(self, messages):
        captured = {}

        with patch(
            "simulate.services.futureagi_chat.llm_client.ModelConfigs.get_provider",
            return_value="openai",
        ), patch("simulate.services.futureagi_chat.llm_client.LLM") as mock_llm_cls:

            def _complete(full_messages, _tools):
                captured["messages"] = full_messages
                return _fake_llm_response(content="I need help with my order.")

            mock_llm_cls.return_value._get_completion_with_tools.side_effect = _complete

            result = generate_simulator_response(
                messages=messages,
                system_prompt="You are a customer.",
                model="gpt-4o",
                temperature=0.9,
                max_tokens=800,
            )
        return captured["messages"], result

    def test_persona_and_agent_roles_are_swapped(self):
        sent, result = self._run(
            [
                {"role": "user", "content": "I need help with my order."},
                {"role": "assistant", "content": "Sure, what is the order id?"},
            ]
        )

        assert sent[0]["role"] == "system"
        assert sent[1]["role"] == "assistant"
        assert sent[1]["content"] == "I need help with my order."
        assert sent[2]["role"] == "user"
        assert sent[2]["content"] == "Sure, what is the order id?"
        assert result["content"] == "I need help with my order."

    def test_context_ends_on_a_user_turn(self):
        # The final flipped turn (the agent's last line) must land as ``user`` so
        # the model replies to it rather than continuing it.
        sent, _ = self._run(
            [{"role": "assistant", "content": "Anything else I can help with?"}]
        )
        assert sent[-1]["role"] == "user"

    def test_unknown_roles_pass_through(self):
        sent, _ = self._run(
            [
                {"role": "tool", "content": "lookup result", "tool_call_id": "c1"},
                {"role": "user", "content": "and my refund?"},
            ]
        )
        assert sent[1]["role"] == "tool"
        assert sent[1]["tool_call_id"] == "c1"
        assert sent[2]["role"] == "assistant"

    def test_input_messages_are_not_mutated(self):
        original = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        self._run(original)
        assert original[0]["role"] == "user"
        assert original[1]["role"] == "assistant"

    def test_system_prompt_prepended_with_cache_control(self):
        sent, _ = self._run([{"role": "user", "content": "hi"}])
        assert sent[0]["role"] == "system"
        block = sent[0]["content"][0]
        assert block["text"] == "You are a customer."
        assert block["cache_control"] == {"type": "ephemeral"}

    def test_endcall_tool_marks_chat_ended(self):
        end_call = SimpleNamespace(
            id="tc1",
            function=SimpleNamespace(name="endCall", arguments='{"reason": "done"}'),
        )
        with patch(
            "simulate.services.futureagi_chat.llm_client.ModelConfigs.get_provider",
            return_value="openai",
        ), patch("simulate.services.futureagi_chat.llm_client.LLM") as mock_llm_cls:
            mock_llm_cls.return_value._get_completion_with_tools.return_value = (
                _fake_llm_response(content="", tool_calls=[end_call])
            )
            result = generate_simulator_response(
                messages=[{"role": "user", "content": "bye"}],
                system_prompt="You are a customer.",
                model="gpt-4o",
                temperature=0.9,
                max_tokens=800,
            )
        assert result["has_chat_ended"] is True
        assert result["ended_reason"] == "done"


@pytest.fixture
def chat_assistant(db, organization, workspace):
    return ChatSimulatorAssistant.objects.create(
        name="Persona",
        system_prompt="You are a customer contacting support.",
        model="gpt-4o",
        temperature=0.9,
        max_tokens=800,
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def chat_session(db, organization, workspace, chat_assistant):
    return ChatSimulatorSession.objects.create(
        assistant=chat_assistant,
        messages=[{"role": "user", "content": "Hi, I need help."}],
        organization=organization,
        workspace=workspace,
    )


@pytest.mark.integration
class TestSendMessageEmptyResponseGate:
    """An empty persona turn is never a valid customer message, so ``send_message``
    retries once and otherwise fails loudly instead of delivering "" (which sends
    the agent into "your message didn't come through" spirals).
    """

    def _service(self, organization, workspace):
        return FutureAGIChatService(
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
        )

    def _send(self, chat_session, organization, workspace, content="anything"):
        return self._service(organization, workspace).send_message(
            SendMessageInput(
                session_id=str(chat_session.id),
                messages=[ChatMessage(role=ChatRole.USER, content=content)],
            )
        )

    @patch.object(FutureAGIChatService, "_call_llm")
    def test_empty_then_nonempty_retries_and_succeeds(
        self, mock_call, chat_session, organization, workspace
    ):
        mock_call.side_effect = [
            {
                "content": "   ",
                "has_chat_ended": False,
                "ended_reason": None,
                "usage": LLMUsage(input_tokens=3, output_tokens=0, total_tokens=3),
            },
            {
                "content": "Okay, my id is CI-789.",
                "has_chat_ended": False,
                "ended_reason": None,
                "usage": LLMUsage(input_tokens=4, output_tokens=6, total_tokens=10),
            },
        ]

        result = self._send(chat_session, organization, workspace)

        assert result.success is True
        assert result.output_messages[0].content == "Okay, my id is CI-789."
        assert mock_call.call_count == 2
        # Usage from both the discarded empty attempt and the retry is summed.
        assert result.usage.total_tokens == 13

        chat_session.refresh_from_db()
        assert chat_session.messages[-1] == {
            "role": "user",
            "content": "Okay, my id is CI-789.",
        }
        assert chat_session.total_tokens == 13
        assert chat_session.status != "error"

    @patch.object(FutureAGIChatService, "_call_llm")
    def test_empty_twice_fails_and_marks_session_error(
        self, mock_call, chat_session, organization, workspace
    ):
        empty = {
            "content": "  ",
            "has_chat_ended": False,
            "ended_reason": None,
            "usage": LLMUsage(),
        }
        mock_call.side_effect = [empty, empty]

        result = self._send(chat_session, organization, workspace)

        assert result.success is False
        assert result.error == "Simulator returned an empty message twice"
        assert mock_call.call_count == 2

        chat_session.refresh_from_db()
        assert chat_session.status == "error"
        # No corrupt empty turn is persisted to the transcript.
        assert chat_session.messages == [
            {"role": "user", "content": "Hi, I need help."}
        ]

    @patch.object(FutureAGIChatService, "_call_llm")
    def test_empty_endcall_is_not_retried(
        self, mock_call, chat_session, organization, workspace
    ):
        # An endCall with no text is a valid end signal, not an empty message.
        mock_call.return_value = {
            "content": "",
            "has_chat_ended": True,
            "ended_reason": "Chat ended by simulator",
            "usage": LLMUsage(),
        }

        result = self._send(chat_session, organization, workspace)

        assert result.success is True
        assert result.has_chat_ended is True
        assert result.ended_reason == "Chat ended by simulator"
        assert mock_call.call_count == 1

        chat_session.refresh_from_db()
        assert chat_session.has_chat_ended is True
        assert chat_session.status == "ended"

    @patch.object(FutureAGIChatService, "_call_llm")
    def test_nonempty_first_response_is_not_retried(
        self, mock_call, chat_session, organization, workspace
    ):
        mock_call.return_value = {
            "content": "Sure, it's CI-789.",
            "has_chat_ended": False,
            "ended_reason": None,
            "usage": LLMUsage(input_tokens=5, output_tokens=4, total_tokens=9),
        }

        result = self._send(chat_session, organization, workspace)

        assert result.success is True
        assert mock_call.call_count == 1
        chat_session.refresh_from_db()
        assert chat_session.messages[-1]["content"] == "Sure, it's CI-789."
        assert chat_session.total_tokens == 9


@pytest.mark.integration
class TestSendMessageRoleFlipReachesLLM:
    """End-to-end across the service and llm_client: the persona LLM must receive a
    flipped, human-shaped transcript (persona as assistant, agent as user).
    """

    @patch("simulate.services.futureagi_chat.llm_client.ModelConfigs.get_provider")
    @patch("simulate.services.futureagi_chat.llm_client.LLM")
    def test_stored_transcript_is_flipped_before_the_call(
        self, mock_llm_cls, mock_provider, chat_session, organization, workspace
    ):
        mock_provider.return_value = "openai"
        captured = {}

        def _complete(full_messages, _tools):
            captured["messages"] = full_messages
            return _fake_llm_response(content="Okay, thanks.")

        mock_llm_cls.return_value._get_completion_with_tools.side_effect = _complete

        service = FutureAGIChatService(
            organization_id=str(organization.id),
            workspace_id=str(workspace.id),
        )
        result = service.send_message(
            SendMessageInput(
                session_id=str(chat_session.id),
                messages=[
                    ChatMessage(role=ChatRole.USER, content="Let me look that up.")
                ],
            )
        )

        assert result.success is True
        sent = captured["messages"]
        assert sent[0]["role"] == "system"
        # Persona's stored "user" line is flipped to "assistant"...
        assert sent[1] == {"role": "assistant", "content": "Hi, I need help."}
        # ...and the agent's forwarded turn is flipped to "user".
        assert sent[2] == {"role": "user", "content": "Let me look that up."}
