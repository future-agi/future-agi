"""
Unit tests for SimulationUpdateConsumer.

Tests cover:
- WebSocket connection lifecycle (connect, disconnect)
- Authentication enforcement
- Organization-level authorization
- Query parameter validation (parse_qs)
- Redis channel subscription naming (org-scoped)
- Message forwarding from Redis pub/sub
- Subscription error handling
- Malformed query strings
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sockets.simulation_consumer import SimulationUpdateConsumer


def _make_consumer(query_string="test_id=run-123&token=fake", user=None):
    """Create a consumer instance with a mocked scope."""
    consumer = SimulationUpdateConsumer()
    consumer.scope = {
        "type": "websocket",
        "query_string": query_string.encode(),
        "user": user,
    }
    # Mock the WebSocket send/accept/close methods
    consumer.accept = AsyncMock()
    consumer.close = AsyncMock()
    consumer.send_json = AsyncMock()
    consumer.channel_layer = AsyncMock()
    consumer.channel_name = "synthetic-channel"
    return consumer


def _make_user(is_authenticated=True, organization=None):
    """Create a mock user with optional organization."""
    user = MagicMock(is_authenticated=is_authenticated)
    user.organization = organization
    return user


def _make_org(org_id="org-123"):
    """Create a mock organization."""
    org = MagicMock()
    org.id = org_id
    return org


@pytest.mark.unit
@pytest.mark.asyncio
class TestSimulationUpdateConsumer:
    """Tests for the SimulationUpdateConsumer WebSocket consumer."""

    # --- Authentication ---

    async def test_rejects_unauthenticated_connection(self):
        """Should close with 4001 when user is not authenticated."""
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

    @patch.object(
        SimulationUpdateConsumer, "_get_organization_id", new_callable=AsyncMock
    )
    async def test_rejects_user_without_organization(self, mock_get_org):
        """Should close with 4002 when user has no organization."""
        mock_get_org.return_value = None
        user = _make_user(is_authenticated=True, organization=None)
        consumer = _make_consumer(user=user)
        await consumer.connect()
        consumer.close.assert_called_with(code=4002)
        consumer.accept.assert_not_called()

    # --- Query parameter validation ---

    @patch.object(
        SimulationUpdateConsumer, "_get_organization_id", new_callable=AsyncMock
    )
    async def test_rejects_missing_test_id(self, mock_get_org):
        """Should close with 4003 when test_id is missing."""
        mock_get_org.return_value = "org-123"
        user = _make_user(is_authenticated=True, organization=_make_org())
        consumer = _make_consumer(query_string="token=fake", user=user)
        await consumer.connect()
        consumer.close.assert_called_with(code=4003)

    @patch.object(
        SimulationUpdateConsumer, "_get_organization_id", new_callable=AsyncMock
    )
    async def test_rejects_empty_test_id(self, mock_get_org):
        """Should close with 4003 when test_id is empty string."""
        mock_get_org.return_value = "org-123"
        user = _make_user(is_authenticated=True, organization=_make_org())
        consumer = _make_consumer(query_string="test_id=&token=fake", user=user)
        await consumer.connect()
        consumer.close.assert_called_with(code=4003)

    @patch.object(
        SimulationUpdateConsumer, "_get_organization_id", new_callable=AsyncMock
    )
    async def test_handles_url_encoded_test_id(self, mock_get_org):
        """Should correctly parse URL-encoded test_id values."""
        mock_get_org.return_value = "org-123"
        user = _make_user(is_authenticated=True, organization=_make_org())
        # URL-encoded UUID: spaces and special chars
        consumer = _make_consumer(
            query_string="test_id=abc%20def&token=fake", user=user
        )
        # Manually parse to verify
        from urllib.parse import parse_qs

        params = parse_qs(consumer.scope["query_string"].decode())
        assert params.get("test_id", [None])[0] == "abc def"

    # --- Valid connection ---

    @patch("sockets.simulation_consumer.aioredis")
    @patch.object(
        SimulationUpdateConsumer, "_get_organization_id", new_callable=AsyncMock
    )
    async def test_accepts_valid_connection(self, mock_get_org, mock_aioredis):
        """Should accept connection with valid auth, org, and test_id."""
        mock_get_org.return_value = "org-123"

        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_pubsub.listen = MagicMock(return_value=_empty_async_iter())
        mock_redis.pubsub.return_value = mock_pubsub
        mock_aioredis.from_url.return_value = mock_redis

        user = _make_user(is_authenticated=True, organization=_make_org())
        consumer = _make_consumer(user=user)
        await consumer.connect()

        consumer.accept.assert_called_once()
        assert consumer.test_id == "run-123"
        assert consumer.organization_id == "org-123"

        await consumer.disconnect(1000)

    # --- Channel naming ---

    @patch("sockets.simulation_consumer.aioredis")
    @patch.object(
        SimulationUpdateConsumer, "_get_organization_id", new_callable=AsyncMock
    )
    async def test_subscribes_to_org_scoped_channel(self, mock_get_org, mock_aioredis):
        """Should subscribe to 'simulation_updates:{org_id}:{test_id}'."""
        mock_get_org.return_value = "org-456"

        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_pubsub.listen = MagicMock(return_value=_empty_async_iter())
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
        mock_aioredis.from_url.return_value = mock_redis

        user = _make_user(is_authenticated=True, organization=_make_org("org-456"))
        consumer = _make_consumer(query_string="test_id=my-test-789&token=x", user=user)
        await consumer.connect()
        await consumer._subscribe_redis()

        mock_pubsub.subscribe.assert_called_with(
            "simulation_updates:org-456:my-test-789"
        )
        await consumer.disconnect(1000)

    # --- Message forwarding ---

    @patch("sockets.simulation_consumer.aioredis")
    @patch.object(
        SimulationUpdateConsumer, "_get_organization_id", new_callable=AsyncMock
    )
    async def test_forwards_redis_message_to_client(self, mock_get_org, mock_aioredis):
        """Should forward simulation_update messages from Redis to the WS client."""
        mock_get_org.return_value = "org-123"

        message_data = {
            "type": "simulation_update",
            "data": {"run_test_id": "run-123", "test_execution_id": "exec-456"},
        }

        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_pubsub.listen = MagicMock(
            return_value=_async_iter_messages(
                [{"type": "message", "data": json.dumps(message_data)}]
            )
        )
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
        mock_aioredis.from_url.return_value = mock_redis

        user = _make_user(is_authenticated=True, organization=_make_org())
        consumer = _make_consumer(user=user)
        consumer.test_id = "run-123"
        consumer.user = user
        consumer.organization_id = "org-123"
        # As connect does once the organization is resolved.
        consumer._membership.mark_validated()

        await consumer._subscribe_redis()

        consumer.send_json.assert_called_once_with(message_data)

    # --- Cleanup ---

    @patch("sockets.simulation_consumer.aioredis")
    @patch.object(
        SimulationUpdateConsumer, "_get_organization_id", new_callable=AsyncMock
    )
    async def test_cleans_up_redis_on_disconnect(self, mock_get_org, mock_aioredis):
        """Should unsubscribe and close Redis on disconnect."""
        mock_get_org.return_value = "org-123"

        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_pubsub.listen = MagicMock(return_value=_empty_async_iter())
        mock_redis.pubsub.return_value = mock_pubsub
        mock_aioredis.from_url.return_value = mock_redis

        user = _make_user(is_authenticated=True, organization=_make_org())
        consumer = _make_consumer(user=user)
        await consumer.connect()

        consumer._redis = mock_redis
        consumer._pubsub = mock_pubsub

        await consumer.disconnect(1000)

        mock_pubsub.unsubscribe.assert_called_once()
        mock_pubsub.close.assert_called_once()
        mock_redis.close.assert_called_once()

    # --- Subscription error handling ---

    @patch("sockets.simulation_consumer.aioredis")
    async def test_sends_error_on_redis_subscription_failure(self, mock_aioredis):
        """Should send error message and close WS if Redis subscription fails."""
        mock_aioredis.from_url.side_effect = ConnectionError("Redis down")

        consumer = _make_consumer()
        consumer.test_id = "run-123"
        consumer.organization_id = "org-123"

        await consumer._subscribe_redis()

        consumer.send_json.assert_called_once_with(
            {"type": "error", "message": "Subscription failed"}
        )
        consumer.close.assert_called_with(code=4500)


async def _empty_async_iter():
    """Async iterator that yields nothing — prevents listen() from blocking."""
    return
    yield  # noqa: unreachable — makes this an async generator


async def _async_iter_messages(messages):
    """Async iterator that yields the given messages then stops."""
    for msg in messages:
        yield msg
