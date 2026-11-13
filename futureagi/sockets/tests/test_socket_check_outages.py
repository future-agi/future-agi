"""A socket check that cannot complete refuses nothing.

When the database cannot be read, a SAML credential or membership check has no
answer. The socket then stays open but pending: nothing is delivered or
answered until a check succeeds, subscription requests are kept and answered
afterwards, and only an outage that outlasts its window closes the socket, with
a code the client retries. A refused credential still closes with 4001.
"""

import asyncio
import types
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from django.db import OperationalError

from accounts.saml_credential import SamlCredentialInvalid
from sockets.membership import MembershipSocketGuard
from sockets.saml_guard import SamlSocketGuard
from sockets.tests.test_membership_revalidation import (  # noqa: F401 - fixture
    FakeClock,
    WebsocketCommunicator,
    broadcast,
    closed,
    create_member,
    create_organization,
    frames,
    notify_membership_changed,
    open_direct_socket,
    outputs,
    process_channel_layer,
)

pytestmark = pytest.mark.django_db(transaction=True)

RETRYABLE = 1011
REFUSED = 4001


def saml_consumer():
    return types.SimpleNamespace(
        scope={
            "user": types.SimpleNamespace(id=uuid4()),
            "auth_scope": types.SimpleNamespace(org_id=uuid4()),
            "auth_token_id": uuid4(),
        },
        accept=AsyncMock(),
        close=AsyncMock(),
    )


class Credential:
    """Stands in for the credential check; ``failure`` is raised when set."""

    def __init__(self):
        self.failure = None
        self.checks = 0

    def patch(self):
        credential = self

        async def validate(guard):
            credential.checks += 1
            if credential.failure is not None:
                raise credential.failure

        return patch.object(SamlSocketGuard, "_validate", validate)


def test_unavailable_credential_check_stays_pending_then_closes_retryably(settings):
    settings.SAML_SOCKET_UNAVAILABLE_CLOSE_SECONDS = 60
    clock = FakeClock()
    credential = Credential()
    consumer = saml_consumer()
    guard = SamlSocketGuard(consumer)

    async def run():
        assert await guard.ensure_live(inbound=True)

        credential.failure = OperationalError("synthetic database outage")
        assert not await guard.ensure_live(inbound=True)
        clock.advance(59)
        # Not answered and not delivered, but not refused either.
        assert not await guard.ensure_live(inbound=False)
        assert (guard.pending, guard.revoked, guard.closed) == (True, False, False)
        consumer.close.assert_not_awaited()

        credential.failure = None
        assert await guard.ensure_live(inbound=True)
        assert not guard.pending

        # A new outage starts a new window.
        credential.failure = RuntimeError("synthetic cache outage")
        assert not await guard.ensure_live(inbound=True)
        clock.advance(59)
        assert not await guard.ensure_live(inbound=True)
        consumer.close.assert_not_awaited()
        clock.advance(1)
        assert not await guard.ensure_live(inbound=True)
        consumer.close.assert_awaited_once_with(code=RETRYABLE)
        assert (guard.revoked, guard.closed) == (False, True)

        # A closed socket stays closed.
        credential.failure = None
        checks = credential.checks
        assert not await guard.ensure_live(inbound=True)
        assert credential.checks == checks

    with patch("sockets.saml_guard.time", clock.module), credential.patch():
        asyncio.run(run())
    consumer.accept.assert_not_awaited()


def test_refused_credential_closes_with_4001_during_an_outage():
    credential = Credential()
    consumer = saml_consumer()
    guard = SamlSocketGuard(consumer)

    async def run():
        assert await guard.ensure_live(inbound=True)
        credential.failure = OperationalError("synthetic database outage")
        assert not await guard.ensure_live(inbound=True)
        credential.failure = SamlCredentialInvalid("token_inactive")
        assert not await guard.ensure_live(inbound=True)

    with credential.patch():
        asyncio.run(run())
    consumer.close.assert_awaited_once_with(code=REFUSED)
    assert guard.revoked


def test_unavailable_credential_check_at_connect_closes_with_a_retryable_code():
    from sockets.consumer import DataConsumer

    org, _, workspace, _ = create_organization()
    member = create_member(org, workspace)
    credential = Credential()
    credential.failure = OperationalError("synthetic database outage")

    async def run():
        socket = WebsocketCommunicator(
            DataConsumer.as_asgi(), f"/ws/connect/?organization_id={org.pk}"
        )
        socket.scope["user"] = member
        socket.scope["auth_scope"] = types.SimpleNamespace(org_id=org.pk)
        socket.scope["auth_token_id"] = uuid4()
        try:
            # Accepted first, so the client sees the retryable code.
            connected, _ = await socket.connect(timeout=10)
            assert connected
            events = await outputs(socket, settle=0.5)
            assert (frames(events), closed(events)) == ([], [RETRYABLE])
        finally:
            await socket.disconnect()

    with credential.patch():
        asyncio.run(run())


def test_saml_socket_answers_nothing_during_a_credential_outage_and_stays_open():
    org, _, workspace, _ = create_organization()
    member = create_member(org, workspace)
    credential = Credential()

    async def run():
        socket = await open_direct_socket(member, org, saml=True)
        try:
            credential.failure = OperationalError("synthetic database outage")
            await socket.send_json_to({"type": "ping"})
            assert await socket.receive_nothing(timeout=0.5)
            await broadcast(org, {"marker": uuid4().hex})
            events = await outputs(socket, settle=0.5)
            assert (frames(events), closed(events)) == ([], [])

            credential.failure = None
            after = {"marker": uuid4().hex}
            await broadcast(org, after)
            await socket.send_json_to({"type": "ping"})
            events = await outputs(socket, settle=0.5)
            assert (frames(events), closed(events)) == ([after, {"type": "pong"}], [])
        finally:
            await socket.disconnect()

    with credential.patch():
        asyncio.run(run())


def membership_outage():
    from sockets.org_scope import organization_membership_is_active

    database = {"available": True}

    def membership_check(user_id, organization_id):
        if not database["available"]:
            raise OperationalError("synthetic database outage")
        return organization_membership_is_active(user_id, organization_id)

    return database, patch(
        "sockets.membership.organization_membership_is_active",
        side_effect=membership_check,
    )


async def send_to_target(org, target, message):
    from channels.layers import get_channel_layer

    await get_channel_layer().group_send(
        f"uuid_org_{org.pk}_{target}", {"type": "send_data", "data": message}
    )


@pytest.mark.parametrize("answered_by", ["client-frame", "server-push"])
def test_subscription_sent_during_a_pending_check_is_answered_after_it(answered_by):
    org, _, workspace, _ = create_organization()
    member = create_member(org, workspace)
    database, membership = membership_outage()
    target = str(uuid4())

    async def run():
        socket = await open_direct_socket(member, org, saml=False)
        try:
            database["available"] = False
            await notify_membership_changed(member, org)
            assert await socket.receive_nothing(timeout=0.5)
            await socket.send_json_to({"type": "subscribe", "uuid": target})
            assert await socket.receive_nothing(timeout=0.5)

            database["available"] = True
            if answered_by == "client-frame":
                follow_up = {"type": "pong"}
                await socket.send_json_to({"type": "ping"})
            else:
                follow_up = {"marker": uuid4().hex}
                await broadcast(org, follow_up)
            confirmed, then = frames(await outputs(socket, settle=0.5))
            assert confirmed == {
                "type": "subscription_confirmed",
                "uuid": target,
                "current_subscription": f"uuid_org_{org.pk}_{target}",
            }
            assert then == follow_up

            # The run's stream now reaches the socket.
            chunk = {"uuid": target, "chunk": "synthetic", "marker": uuid4().hex}
            await send_to_target(org, target, chunk)
            events = await outputs(socket, settle=0.5)
            assert (frames(events), closed(events)) == ([chunk], [])
        finally:
            await socket.disconnect()

    with membership:
        asyncio.run(run())


def test_subscriptions_kept_during_a_pending_check_are_bounded():
    from sockets.consumer import MAX_PENDING_SUBSCRIPTION_FRAMES

    org, _, workspace, _ = create_organization()
    member = create_member(org, workspace)
    database, membership = membership_outage()
    targets = [str(uuid4()) for _ in range(MAX_PENDING_SUBSCRIPTION_FRAMES + 4)]

    async def run():
        socket = await open_direct_socket(member, org, saml=False)
        try:
            database["available"] = False
            await notify_membership_changed(member, org)
            assert await socket.receive_nothing(timeout=0.5)
            for target in targets:
                await socket.send_json_to({"type": "subscribe", "uuid": target})
            await socket.send_json_to({"type": "ping"})
            assert await socket.receive_nothing(timeout=0.5)

            database["available"] = True
            await socket.send_json_to({"type": "ping"})
            answered = frames(await outputs(socket, settle=0.5))
        finally:
            await socket.disconnect()
        # The newest subscriptions are kept, in order; the last one wins.
        assert [frame.get("uuid") for frame in answered[:-1]] == targets[
            -MAX_PENDING_SUBSCRIPTION_FRAMES:
        ]
        assert answered[-1] == {"type": "pong"}

    with membership:
        asyncio.run(run())


def test_check_running_before_a_failed_forced_check_does_not_clear_it():
    consumer = types.SimpleNamespace(
        organization_id=str(uuid4()),
        user=types.SimpleNamespace(id=uuid4()),
        scope={},
        close=AsyncMock(),
    )
    guard = MembershipSocketGuard(consumer)
    answers = []

    def database_sync_to_async(function):
        async def check(*args):
            return await answers.pop(0)

        return check

    async def run():
        loop = asyncio.get_running_loop()
        earlier = loop.create_future()
        answers.append(earlier)
        # A delivery's check starts, then the membership changes and the
        # forced check cannot complete.
        delivery = asyncio.create_task(guard.ensure_member())
        await asyncio.sleep(0)
        failed = loop.create_future()
        failed.set_exception(OperationalError("synthetic database outage"))
        answers.append(failed)
        assert not await guard.ensure_member(force=True)
        assert guard.revalidation_pending

        # The earlier check's answer may predate the change.
        earlier.set_result(True)
        assert not await delivery
        assert guard.revalidation_pending

        # A check that starts now may clear it.
        current = loop.create_future()
        current.set_result(True)
        answers.append(current)
        assert await guard.ensure_member()
        assert not guard.revalidation_pending

    with patch("sockets.membership.database_sync_to_async", database_sync_to_async):
        asyncio.run(run())
    consumer.close.assert_not_awaited()


def test_credential_check_running_before_expire_is_not_reused():
    consumer = saml_consumer()
    guard = SamlSocketGuard(consumer)
    release = {}

    async def validate(self):
        if "event" in release:
            await release["event"].wait()

    async def run():
        assert await guard.ensure_live(inbound=True)
        release["event"] = asyncio.Event()
        push = asyncio.create_task(guard.ensure_live(inbound=True))
        await asyncio.sleep(0)
        guard.expire()
        release["event"].set()
        assert await push
        del release["event"]
        with patch.object(
            SamlSocketGuard, "_validate", new_callable=AsyncMock
        ) as check:
            assert await guard.ensure_live(inbound=False)
        # The push after expire() checks again.
        assert check.await_count == 1

    with patch.object(SamlSocketGuard, "_validate", validate):
        asyncio.run(run())
