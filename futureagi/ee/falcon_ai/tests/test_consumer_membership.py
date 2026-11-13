"""Open Falcon AI sockets follow the user's organization membership.

Sockets go through the production JWT middleware with real access tokens, and
membership changes go through the real HTTP views. Only the agent run itself
is replaced, by a task that waits until it is stopped.
"""

import asyncio
from unittest.mock import patch

import pytest
from asgiref.sync import sync_to_async
from channels.routing import URLRouter
from django.urls import re_path

from accounts.services.token_service import issue_tokens
from ee.falcon_ai.consumers import FalconAIConsumer
from sockets.tests.test_membership_revalidation import (
    MEMBERSHIP_CLOSE_CODE,
    FakeClock,
    WebsocketCommunicator,
    closed,
    create_member,
    create_organization,
    deactivate_membership_rows,
    frames,
    outputs,
    remove_member,
)
from tfc.middleware.jwt_auth import JWTAuthMiddleware

pytestmark = pytest.mark.django_db(transaction=True)

APPLICATION = JWTAuthMiddleware(
    URLRouter([re_path(r"ws/falcon-ai/$", FalconAIConsumer.as_asgi())])
)


@pytest.fixture(autouse=True)
def falcon_environment(settings):
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "tfc.channel_layers.ThreadSafeInMemoryChannelLayer"}
    }
    runs = []

    async def start_run(self, content):
        # Stand in for the agent loop: a run that lasts until it is stopped.
        task = asyncio.create_task(asyncio.Event().wait())
        self._agent_task = task
        runs.append(task)

    with (
        patch("ee.falcon_ai.consumers.check_ee_feature"),
        patch.object(FalconAIConsumer, "_handle_chat", start_run),
    ):
        yield runs


async def open_falcon_socket(user, workspace):
    token = await sync_to_async(lambda: issue_tokens(user)["access"])()
    socket = WebsocketCommunicator(
        APPLICATION, f"/ws/falcon-ai/?token={token}&workspace_id={workspace.pk}"
    )
    connected, code = await socket.connect(timeout=10)
    assert connected, code
    await socket.send_json_to({"type": "ping"})
    assert await socket.receive_json_from(timeout=10) == {"type": "pong"}
    return socket


async def start_agent_run(socket, runs):
    await socket.send_json_to({"type": "chat", "message": "synthetic question"})
    for _ in range(100):
        if runs:
            return runs[-1]
        await asyncio.sleep(0.02)
    raise AssertionError("agent run did not start")


async def wait_until_done(task):
    for _ in range(100):
        if task.done():
            return
        await asyncio.sleep(0.02)


def test_open_falcon_socket_closes_and_stops_its_run_when_membership_is_removed(
    falcon_environment,
):
    org, owner, workspace, _ = create_organization()
    member = create_member(org, workspace)

    async def run():
        own = await open_falcon_socket(owner, workspace)
        removed = await open_falcon_socket(member, workspace)
        try:
            removed_run = await start_agent_run(removed, falcon_environment)
            own_run = await start_agent_run(own, falcon_environment)

            await sync_to_async(remove_member)(owner, member)
            events = await outputs(removed)
            assert (frames(events), closed(events)) == ([], [MEMBERSHIP_CLOSE_CODE])
            await wait_until_done(removed_run)
            assert removed_run.cancelled()

            # The organization's remaining member keeps its socket and run.
            assert not own_run.done()
            await own.send_json_to({"type": "ping"})
            assert await own.receive_json_from(timeout=10) == {"type": "pong"}
        finally:
            for task in falcon_environment:
                task.cancel()
            await own.disconnect()
            await removed.disconnect()

    asyncio.run(run())


def test_falcon_socket_rechecks_membership_after_the_default_interval(
    monkeypatch, falcon_environment
):
    clock = FakeClock()
    for module in ("sockets.membership", "ee.falcon_ai.consumers"):
        monkeypatch.setattr(f"{module}.time", clock.module)
    org, _, workspace, _ = create_organization()
    member = create_member(org, workspace)

    async def run():
        socket = await open_falcon_socket(member, workspace)
        try:
            agent_run = await start_agent_run(socket, falcon_environment)
            await sync_to_async(deactivate_membership_rows)(member, org)
            # Within the interval the result from connect is reused.
            clock.advance(9.5)
            await socket.send_json_to({"type": "ping"})
            assert await socket.receive_json_from(timeout=10) == {"type": "pong"}

            clock.advance(0.5)
            await socket.send_json_to({"type": "ping"})
            events = await outputs(socket)
            assert (frames(events), closed(events)) == ([], [MEMBERSHIP_CLOSE_CODE])
            await wait_until_done(agent_run)
            assert agent_run.cancelled()
        finally:
            for task in falcon_environment:
                task.cancel()
            await socket.disconnect()

    asyncio.run(run())


def test_falcon_socket_for_a_remaining_membership_stays_open(falcon_environment):
    from sockets.tests.test_membership_revalidation import add_membership

    org, owner, workspace, _ = create_organization()
    elsewhere, _, elsewhere_workspace, _ = create_organization()
    member = create_member(org, workspace)
    add_membership(member, elsewhere, elsewhere_workspace)

    async def run():
        remaining = await open_falcon_socket(member, elsewhere_workspace)
        try:
            agent_run = await start_agent_run(remaining, falcon_environment)
            await sync_to_async(remove_member)(owner, member)
            assert await remaining.receive_nothing(timeout=0.5)
            assert not agent_run.done()
            await remaining.send_json_to({"type": "ping"})
            assert await remaining.receive_json_from(timeout=10) == {"type": "pong"}
        finally:
            for task in falcon_environment:
                task.cancel()
            await remaining.disconnect()

    asyncio.run(run())
