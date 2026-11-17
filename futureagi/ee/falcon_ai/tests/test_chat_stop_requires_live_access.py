"""A stop frame stops the running chat turn only for an account that can still
use the chat.

The connection checks the account on a stop frame as it does on a feedback
frame: an account that is no longer active, or no longer acts in the
connection's organization, gets an error with the ``workspace_unavailable``
code, the connection closes with 4003, and the frame does not stop the turn.
The other socket tests' harness replaces the link fetch, stream buffer, quota
check and billing; this file also replaces its agent loop with one whose turn
runs until it is stopped.
"""

import asyncio
from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from ee.falcon_ai import consumers
from ee.falcon_ai.tests.test_chat_reconnect_access_gate import chat_frame
from ee.falcon_ai.tests.test_chat_reconnect_access_gate import (
    licensed_features as licensed_features,
)
from ee.falcon_ai.tests.test_chat_reconnect_access_gate import (
    scope_no_egress as scope_no_egress,
)
from ee.falcon_ai.tests.test_chat_reconnect_access_gate import (
    scope_state as scope_state,
)
from ee.falcon_ai.tests.test_chat_reconnect_access_gate import (
    socket_harness as socket_harness,
)

pytestmark = pytest.mark.django_db(transaction=True)

# The frame the web app sends when the person presses Stop.
STOP_FRAME = {"type": "stop"}


@pytest.fixture
def running_turn(socket_harness, monkeypatch):
    """A chat turn that runs until it is stopped."""
    turn = SimpleNamespace(started=None, stopped=False)

    def build_agent(*, tool_context, conversation):
        async def run(**kwargs):
            turn.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                turn.stopped = True
                raise

        return SimpleNamespace(
            run=run,
            partial_content="",
            llm_client=SimpleNamespace(_gateway_cost=0),
        )

    monkeypatch.setattr(consumers, "AgentLoop", build_agent)
    return turn


async def start_turn(harness, socket, consumer, turn):
    """Send a chat frame and return the turn's task once it is running."""
    turn.started = asyncio.Event()
    await socket.send_json_to(chat_frame(harness.conversation))
    await asyncio.wait_for(turn.started.wait(), timeout=5)
    return consumer._agent_task


async def end_turn(task):
    """End a turn the test left running."""
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def end_access(state, authority):
    if authority == "inactive-account":
        User._base_manager.filter(pk=state.member.pk).update(is_active=False)
        return
    rows = OrganizationMembership._base_manager.filter(pk=state.membership.pk)
    if authority == "inactive-membership":
        rows.update(is_active=False)
    else:
        rows.delete()


@pytest.mark.parametrize(
    "authority", ["inactive-account", "inactive-membership", "removed-membership"]
)
def test_stop_from_an_account_that_cannot_use_the_chat_closes_the_connection(
    socket_harness, running_turn, authority
):
    harness = socket_harness

    async def scenario():
        socket, consumer = await harness.connect()
        task = None
        try:
            task = await start_turn(harness, socket, consumer, running_turn)
            await database_sync_to_async(end_access)(harness.state, authority)
            await socket.send_json_to(dict(STOP_FRAME))
            event = await socket.receive_json_from(timeout=5)
            assert event["type"] == "error", event
            assert event["data"]["code"] == "workspace_unavailable", event
            assert await socket.receive_output(timeout=5) == {
                "type": "websocket.close",
                "code": 4003,
            }
            assert not task.done()
            assert not running_turn.stopped
        finally:
            await end_turn(task)
            await socket.disconnect()

    async_to_sync(scenario)()


def test_stop_from_an_account_that_can_use_the_chat_stops_its_turn(
    socket_harness, running_turn
):
    harness = socket_harness

    async def scenario():
        socket, consumer = await harness.connect()
        task = None
        try:
            task = await start_turn(harness, socket, consumer, running_turn)
            await socket.send_json_to(dict(STOP_FRAME))
            assert await socket.receive_json_from(timeout=5) == {"type": "stopped"}
            assert task.done()
            assert running_turn.stopped
            assert consumer._agent_task is None
            await socket.send_json_to({"type": "ping"})
            assert await socket.receive_json_from(timeout=5) == {"type": "pong"}
        finally:
            await end_turn(task)
            await socket.disconnect()

    async_to_sync(scenario)()
