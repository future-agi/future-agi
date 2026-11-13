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
        runs.append(self._start_agent_run(asyncio.Event().wait()))

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
    started = len(runs)
    await socket.send_json_to({"type": "chat", "message": "synthetic question"})
    for _ in range(100):
        if len(runs) > started:
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
            # A new chat does not stop an earlier run on the same socket.
            earlier_run = await start_agent_run(removed, falcon_environment)
            removed_run = await start_agent_run(removed, falcon_environment)
            assert earlier_run is not removed_run
            own_run = await start_agent_run(own, falcon_environment)

            await sync_to_async(remove_member)(owner, member)
            events = await outputs(removed)
            assert (frames(events), closed(events)) == ([], [MEMBERSHIP_CLOSE_CODE])
            for run in (earlier_run, removed_run):
                await wait_until_done(run)
                assert run.cancelled()

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


def test_repeated_stop_requests_let_a_run_finish_its_cleanup():
    consumer = FalconAIConsumer()
    cleanup = []

    async def agent_run():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Stands in for the partial save and usage emission on cancel.
            await asyncio.sleep(0.1)
            cleanup.append("finished")
            raise

    async def run():
        task = consumer._start_agent_run(agent_run())
        await asyncio.sleep(0)
        # The run is already stopping (a delivery found the membership gone)
        # when a membership notification asks again.
        task.cancel()
        await asyncio.sleep(0.02)
        await consumer._stop_all_agent_runs()
        assert task.cancelled()

    asyncio.run(run())
    assert cleanup == ["finished"]


def test_stopping_runs_asks_every_run_to_stop_before_awaiting_cleanup():
    consumer = FalconAIConsumer()
    events = []

    async def agent_run(name):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            events.append(f"{name} stopping")
            # Stands in for the partial save and usage emission on cancel.
            await asyncio.sleep(0.1)
            events.append(f"{name} cleaned up")
            raise

    async def run():
        first = consumer._start_agent_run(agent_run("first"))
        second = consumer._start_agent_run(agent_run("second"))
        await asyncio.sleep(0)
        await consumer._stop_all_agent_runs()
        assert first.cancelled()
        assert second.cancelled()

    asyncio.run(run())
    assert set(events[:2]) == {"first stopping", "second stopping"}
    assert set(events[2:]) == {"first cleaned up", "second cleaned up"}


def test_runs_stop_when_the_sign_in_behind_the_socket_is_refused():
    import types
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from sockets.saml_guard import SamlSocketGuard

    consumer = FalconAIConsumer()
    consumer.scope = {
        "user": types.SimpleNamespace(id=uuid4()),
        "auth_scope": types.SimpleNamespace(org_id=uuid4()),
        "auth_token_id": uuid4(),
    }
    consumer.close = AsyncMock()

    async def run():
        agent_run = consumer._start_agent_run(asyncio.Event().wait())
        await asyncio.sleep(0)
        # The run's next event finds the sign-in refused; the socket closes and
        # will no longer hear about membership changes, so the run stops too.
        await consumer.send_json({"type": "stream", "data": {}})
        await wait_until_done(agent_run)
        assert agent_run.cancelled()

    with patch.object(
        SamlSocketGuard,
        "_validate",
        new_callable=AsyncMock,
        side_effect=RuntimeError("synthetic credential store outage"),
    ):
        asyncio.run(run())
    consumer.close.assert_awaited_once_with(code=4001)
