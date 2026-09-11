"""
Unit tests for FalconAIConsumer WebSocket consumer.

Tests cover:
- Authentication enforcement (connect/reject)
- Ping/pong
- Chat message handling
- Feedback handling
- Disconnect cleanup
"""

import asyncio
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from ee.falcon_ai.consumers import FalconAIConsumer

logger = logging.getLogger("tests.usage_events")


def _make_consumer(query_string="workspace_id=ws-123", user=None):
    """Create a consumer instance with a mocked scope."""
    consumer = FalconAIConsumer()
    consumer.scope = {
        "type": "websocket",
        "query_string": query_string.encode(),
        "user": user,
    }
    consumer.accept = AsyncMock()
    consumer.close = AsyncMock()
    consumer.send_json = AsyncMock()
    return consumer


def _make_user(is_authenticated=True, organization=None):
    """Create a mock authenticated user."""
    user = MagicMock(is_authenticated=is_authenticated)
    user.id = uuid.uuid4()
    user.email = "test@futureagi.com"
    user.organization = organization
    user.config = {}
    user.can_access_workspace = MagicMock(return_value=True)
    return user


def _make_org(org_id=None):
    """Create a mock organization."""
    org = MagicMock()
    org.id = org_id or uuid.uuid4()
    return org


@pytest.mark.asyncio
@pytest.mark.django_db
class TestFalconAIConsumer:
    # --- Authentication ---

    async def test_rejects_unauthenticated_connection(self):
        """Should close with 4001 when user is not in scope."""
        consumer = _make_consumer(user=None)
        await consumer.connect()
        consumer.close.assert_called_with(code=4001)
        consumer.accept.assert_not_called()

    async def test_rejects_anonymous_user(self):
        """Should close with 4001 when user.is_authenticated is False."""
        user = _make_user(is_authenticated=False)
        consumer = _make_consumer(user=user)
        await consumer.connect()
        consumer.close.assert_called_with(code=4001)

    # --- Organization authorization ---

    @patch.object(FalconAIConsumer, "_get_organization", new_callable=AsyncMock)
    async def test_rejects_user_without_organization(self, mock_get_org):
        """Should close with 4002 when user has no organization."""
        mock_get_org.return_value = None
        user = _make_user(is_authenticated=True)
        consumer = _make_consumer(query_string="", user=user)
        await consumer.connect()
        consumer.close.assert_called_with(code=4002)
        consumer.accept.assert_not_called()

    # --- Valid connection ---

    @patch("ee.falcon_ai.consumers.check_ee_feature")
    @patch.object(FalconAIConsumer, "_get_workspace", new_callable=AsyncMock)
    @patch.object(FalconAIConsumer, "_get_organization", new_callable=AsyncMock)
    async def test_accepts_valid_connection(
        self,
        mock_get_org,
        mock_get_ws,
        mock_check_feature,
    ):
        """Should accept connection with valid auth and org."""
        org = _make_org()
        mock_get_org.return_value = org
        mock_get_ws.return_value = MagicMock(id=uuid.uuid4())

        user = _make_user(is_authenticated=True, organization=org)
        consumer = _make_consumer(query_string="", user=user)
        await consumer.connect()

        consumer.accept.assert_called_once()
        assert consumer.organization == org
        mock_check_feature.assert_called_once()

    @patch("ee.falcon_ai.consumers.check_ee_feature")
    @patch.object(FalconAIConsumer, "_get_organization", new_callable=AsyncMock)
    async def test_rejects_unlicensed_organization(
        self,
        mock_get_org,
        mock_check_feature,
    ):
        from tfc.ee_gating import FeatureUnavailable

        org = _make_org()
        mock_get_org.return_value = org
        mock_check_feature.side_effect = FeatureUnavailable("falcon_ai")
        consumer = _make_consumer(query_string="", user=_make_user(organization=org))

        await consumer.connect()

        consumer.close.assert_called_once_with(code=4003)
        consumer.accept.assert_not_called()

    # --- Ping / pong ---

    async def test_ping_pong(self):
        """Should respond with pong for ping messages."""
        consumer = _make_consumer()
        consumer.user = _make_user()
        consumer.organization = _make_org()

        await consumer.receive_json({"type": "ping"})
        consumer.send_json.assert_called_with({"type": "pong"})

    # --- Unknown message type ---

    async def test_unknown_message_type(self):
        """Should return error for unknown message types."""
        consumer = _make_consumer()
        consumer.user = _make_user()
        consumer.organization = _make_org()

        await consumer.receive_json({"type": "unknown_type"})
        call_args = consumer.send_json.call_args[0][0]
        assert call_args["type"] == "error"
        assert "Unknown message type" in call_args["data"]["error"]

    # --- Chat message validation ---

    async def test_chat_requires_message(self):
        """Should error when chat has empty message."""
        consumer = _make_consumer()
        consumer.user = _make_user()
        consumer.organization = _make_org()

        await consumer.receive_json(
            {"type": "chat", "conversation_id": str(uuid.uuid4()), "message": ""}
        )
        call_args = consumer.send_json.call_args[0][0]
        assert call_args["type"] == "error"
        assert "Message is required" in call_args["data"]["error"]

    async def test_chat_requires_conversation_id(self):
        """Should error when chat has no conversation_id."""
        consumer = _make_consumer()
        consumer.user = _make_user()
        consumer.organization = _make_org()

        await consumer.receive_json({"type": "chat", "message": "Hello"})
        call_args = consumer.send_json.call_args[0][0]
        assert call_args["type"] == "error"
        assert "conversation_id is required" in call_args["data"]["error"]

    # --- Feedback ---

    async def test_feedback_requires_message_id(self):
        """Should error when feedback has no message_id."""
        consumer = _make_consumer()
        consumer.user = _make_user()
        consumer.organization = _make_org()

        await consumer.receive_json({"type": "feedback", "feedback": "thumbs_down"})
        call_args = consumer.send_json.call_args[0][0]
        assert call_args["type"] == "error"
        assert "message_id is required" in call_args["data"]["error"]

    @patch.object(FalconAIConsumer, "_update_message_feedback", new_callable=AsyncMock)
    async def test_feedback_success(self, mock_update):
        """Should send feedback_updated when feedback is saved."""
        mock_update.return_value = True
        consumer = _make_consumer()
        consumer.user = _make_user()
        consumer.organization = _make_org()

        msg_id = str(uuid.uuid4())
        await consumer.receive_json(
            {"type": "feedback", "message_id": msg_id, "feedback": "thumbs_down"}
        )
        call_args = consumer.send_json.call_args[0][0]
        assert call_args["type"] == "feedback_updated"
        assert call_args["message_id"] == msg_id

    @patch.object(FalconAIConsumer, "_update_message_feedback", new_callable=AsyncMock)
    async def test_feedback_message_not_found(self, mock_update):
        """Should error when message not found for feedback."""
        mock_update.return_value = False
        consumer = _make_consumer()
        consumer.user = _make_user()
        consumer.organization = _make_org()

        await consumer.receive_json(
            {
                "type": "feedback",
                "message_id": str(uuid.uuid4()),
                "feedback": "thumbs_down",
            }
        )
        call_args = consumer.send_json.call_args[0][0]
        assert call_args["type"] == "error"
        assert "Message not found" in call_args["data"]["error"]

    # --- Disconnect ---

    async def test_disconnect_leaves_agent_task_running(self):
        """Disconnect lets the agent finish so the response can be saved."""
        consumer = _make_consumer()
        consumer.user = _make_user()
        consumer.organization = _make_org()

        # Create a real asyncio task that we can cancel
        async def long_running():
            await asyncio.sleep(100)

        mock_task = asyncio.create_task(long_running())
        consumer._agent_task = mock_task

        await consumer.disconnect(1000)
        assert not mock_task.cancelled()
        mock_task.cancel()
        await asyncio.gather(mock_task, return_exceptions=True)

    async def test_disconnect_without_agent_task(self):
        """Should handle disconnect gracefully when no agent task is running."""
        consumer = _make_consumer()
        consumer.user = _make_user()
        consumer.organization = _make_org()
        consumer._agent_task = None

        # Should not raise
        await consumer.disconnect(1000)

    @patch("ee.falcon_ai.consumers.emit")
    @patch("ee.falcon_ai.consumers.BillingConfig")
    @patch("agentic_eval.core_evals.fi_utils.token_count_helper.calculate_total_cost")
    async def test_emit_chat_usage_includes_tokens_and_gateway_cost(
        self, mock_cost, mock_billing_config, mock_emit
    ):
        mock_cost.return_value = {
            "total_cost": 0.01,
            "pricing_source": "available_models",
        }
        mock_billing_config.get.return_value.calculate_ai_credits.return_value = 3

        consumer = _make_consumer()
        consumer.organization_id = "org-123"

        consumer._emit_chat_usage(
            input_tokens=100,
            output_tokens=40,
            model_used="turing_small",
            conversation_id="conv-123",
            gateway_cost_usd=0.02,
        )

        event = mock_emit.call_args.args[0]
        assert event.event_type == "falcon_ai_chat"
        assert event.amount == 3
        assert event.properties["prompt_tokens"] == 100
        assert event.properties["completion_tokens"] == 40
        assert event.properties["total_tokens"] == 140
        assert event.properties["gateway_cost_usd"] == "0.02"
        assert event.properties["raw_cost_usd"] == "0.02"
        assert event.properties["pricing_source"] == "gateway"

    @patch("ee.falcon_ai.consumers.check_usage")
    @patch("ee.falcon_ai.url_fetcher.fetch_urls_from_message")
    @patch("ee.falcon_ai.consumers.StreamBuffer")
    @patch("ee.falcon_ai.consumers.AgentLoop")
    @patch.object(
        FalconAIConsumer, "_update_conversation_tokens", new_callable=AsyncMock
    )
    @patch.object(FalconAIConsumer, "_save_assistant_message", new_callable=AsyncMock)
    @patch.object(
        FalconAIConsumer, "_get_conversation_and_history", new_callable=AsyncMock
    )
    @patch.object(FalconAIConsumer, "_save_user_message", new_callable=AsyncMock)
    @patch("ee.falcon_ai.consumers.emit")
    @patch("ee.falcon_ai.consumers.BillingConfig")
    @patch("agentic_eval.core_evals.fi_utils.token_count_helper.calculate_total_cost")
    async def test_chat_message_path_emits_usage_with_accumulated_tokens(
        self,
        mock_cost,
        mock_billing_config,
        mock_emit,
        mock_save_user_message,
        mock_get_history,
        mock_save_assistant,
        mock_update_tokens,
        mock_agent_loop,
        mock_stream_buffer,
        mock_fetch_urls,
        mock_check_usage,
    ):
        mock_fetch_urls.return_value = ""
        mock_check_usage.return_value = SimpleNamespace(allowed=True, reason="")
        mock_cost.return_value = {
            "total_cost": 0.01,
            "pricing_source": "available_models",
        }
        mock_billing_config.get.return_value.calculate_ai_credits.return_value = 5

        stream_buffer = mock_stream_buffer.return_value
        stream_buffer.clear.return_value = None
        stream_buffer.set_status.return_value = None
        stream_buffer.append_event.return_value = None
        stream_buffer.cleanup_after_done.return_value = None

        agent = mock_agent_loop.return_value
        agent.llm_client = SimpleNamespace(_gateway_cost=0.07)
        agent.run = AsyncMock(
            return_value={
                "id": str(uuid.uuid4()),
                "content": "Here is the answer.",
                "tool_calls": [],
                "completion_card": None,
                "model_used": "turing_large",
                "mode": "general",
                "input_tokens": 120,
                "output_tokens": 45,
            }
        )

        mock_save_user_message.return_value = MagicMock(id=uuid.uuid4())
        mock_get_history.return_value = (MagicMock(id=uuid.uuid4()), [])

        org = _make_org("org-123")
        workspace = MagicMock(id=uuid.uuid4())
        consumer = _make_consumer(
            query_string=f"workspace_id={workspace.id}",
            user=_make_user(organization=org),
        )
        consumer.organization = org
        consumer.organization_id = str(org.id)
        consumer.workspace = workspace
        consumer.workspace_id = str(workspace.id)

        conversation_id = str(uuid.uuid4())
        await consumer.receive_json(
            {
                "type": "chat",
                "conversation_id": conversation_id,
                "message": "Summarize usage for this turn",
                "context": {"page": "usage"},
            }
        )
        task = consumer._agent_task
        assert task is not None
        await asyncio.wait_for(task, timeout=2)

        event = mock_emit.call_args.args[0]
        logger.info("captured_falcon_usage_event %s", event.model_dump(mode="json"))

        assert mock_save_user_message.await_count == 1
        assert mock_save_assistant.await_count == 1
        mock_update_tokens.assert_awaited_once_with(conversation_id, 165)

        assert event.event_type == "falcon_ai_chat"
        assert event.amount == 5
        assert event.properties["source"] == "falcon_ai"
        assert event.properties["source_id"] == conversation_id
        assert event.properties["prompt_tokens"] == 120
        assert event.properties["completion_tokens"] == 45
        assert event.properties["total_tokens"] == 165
        assert event.properties["gateway_cost_usd"] == "0.07"
        assert event.properties["pricing_source"] == "gateway"
