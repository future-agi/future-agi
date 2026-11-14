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
            checks = credential.checks
            await socket.send_json_to({"type": "ping"})
            for _ in range(200):
                if credential.checks > checks:
                    break
                await asyncio.sleep(0.025)
            assert credential.checks > checks
            await broadcast(org, {"marker": uuid4().hex})
            events = await outputs(socket, settle=0.5)
            assert (frames(events), closed(events)) == ([], [])

            credential.failure = None
            after = {"marker": uuid4().hex}
            await broadcast(org, after)
            await socket.send_json_to({"type": "ping"})
            events = await outputs(socket, settle=0.5)
            # The push and the client frame arrive on separate queues.
            delivered = frames(events)
            assert len(delivered) == 2 and closed(events) == []
            assert after in delivered and {"type": "pong"} in delivered
        finally:
            await socket.disconnect()

    with credential.patch():
        asyncio.run(run())


def membership_outage():
    from sockets.org_scope import organization_membership_is_active

    database = {"available": True, "failed_checks": 0}

    def membership_check(user_id, organization_id):
        if not database["available"]:
            database["failed_checks"] += 1
            raise OperationalError("synthetic database outage")
        return organization_membership_is_active(user_id, organization_id)

    return database, patch(
        "sockets.membership.organization_membership_is_active",
        side_effect=membership_check,
    )


async def until_failed_checks(database, count):
    """Wait until the socket has run ``count`` failed membership checks."""

    for _ in range(200):
        if database["failed_checks"] >= count:
            return
        await asyncio.sleep(0.025)
    raise AssertionError(f"only {database['failed_checks']} failed checks")


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
            await until_failed_checks(database, 1)
            await socket.send_json_to({"type": "subscribe", "uuid": target})
            # The subscription meets a pending check and is not answered.
            await until_failed_checks(database, 2)
            assert await socket.receive_nothing(timeout=0.2)

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
            await until_failed_checks(database, 1)
            for target in targets:
                await socket.send_json_to({"type": "subscribe", "uuid": target})
            await socket.send_json_to({"type": "ping"})
            await until_failed_checks(database, len(targets) + 2)
            assert await socket.receive_nothing(timeout=0.2)

            database["available"] = True
            await socket.send_json_to({"type": "ping"})
            answered = frames(await outputs(socket, settle=0.5))
        finally:
            await socket.disconnect()
        # Each subscribe replaces the socket's one subscription, so only the
        # last one is answered; it is the one the client still wants.
        assert answered == [
            {
                "type": "subscription_confirmed",
                "uuid": targets[-1],
                "current_subscription": f"uuid_org_{org.pk}_{targets[-1]}",
            },
            {"type": "pong"},
        ]

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


@pytest.mark.parametrize(
    "frame",
    [{"type": []}, {"type": {}}, ["subscribe"], "subscribe"],
    ids=["list-type", "object-type", "list", "string"],
)
def test_unexpected_frame_during_a_pending_check_keeps_the_socket_open(frame):
    org, _, workspace, _ = create_organization()
    member = create_member(org, workspace)
    database, membership = membership_outage()

    async def run():
        socket = await open_direct_socket(member, org, saml=False)
        try:
            database["available"] = False
            await notify_membership_changed(member, org)
            await until_failed_checks(database, 1)
            await socket.send_json_to(frame)
            await until_failed_checks(database, 2)
            database["available"] = True
            await socket.send_json_to({"type": "ping"})
            events = await outputs(socket, settle=0.5)
            assert (frames(events), closed(events)) == ([{"type": "pong"}], [])
        finally:
            await socket.disconnect()

    with membership:
        asyncio.run(run())


def test_denial_read_before_a_membership_change_does_not_close_the_socket():
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
        delivery = asyncio.create_task(guard.ensure_member())
        await asyncio.sleep(0)
        # The membership is restored and the forced check sees it.
        current = loop.create_future()
        current.set_result(True)
        answers.append(current)
        assert await guard.ensure_member(force=True)
        # The older read still saw the removed membership.
        earlier.set_result(False)
        assert not await delivery
        assert not guard.closed

    with patch("sockets.membership.database_sync_to_async", database_sync_to_async):
        asyncio.run(run())
    consumer.close.assert_not_awaited()


def test_pending_socket_without_traffic_closes_when_the_window_ends(settings):
    settings.SAML_SOCKET_UNAVAILABLE_CLOSE_SECONDS = 0.3
    credential = Credential()
    consumer = saml_consumer()
    guard = SamlSocketGuard(consumer)

    async def run():
        assert await guard.ensure_live(inbound=True)
        credential.failure = OperationalError("synthetic database outage")
        assert not await guard.ensure_live(inbound=True)
        consumer.close.assert_not_awaited()
        # Nothing else arrives; the guard checks again when the window ends.
        for _ in range(100):
            if consumer.close.await_count:
                break
            await asyncio.sleep(0.02)

    with credential.patch():
        asyncio.run(run())
    consumer.close.assert_awaited_once_with(code=RETRYABLE)
    assert not guard.revoked


def test_window_check_is_not_run_after_recovery_or_disconnect(settings):
    settings.SAML_SOCKET_UNAVAILABLE_CLOSE_SECONDS = 0.2
    credential = Credential()

    async def run(stop):
        consumer = saml_consumer()
        guard = SamlSocketGuard(consumer)
        assert await guard.ensure_live(inbound=True)
        credential.failure = OperationalError("synthetic database outage")
        assert not await guard.ensure_live(inbound=True)
        credential.failure = None
        checks = credential.checks
        if stop:
            guard.stop()
        else:
            assert await guard.ensure_live(inbound=True)
            checks += 1
        await asyncio.sleep(0.4)
        assert credential.checks == checks
        consumer.close.assert_not_awaited()

    with credential.patch():
        asyncio.run(run(stop=False))
        asyncio.run(run(stop=True))


def test_kept_frames_never_lose_a_still_wanted_subscription():
    import json

    from sockets.consumer import MAX_PENDING_SUBSCRIPTION_FRAMES, DataConsumer

    consumer = DataConsumer()
    consumer.scope = {}
    consumer.room_group_name = "org_synthetic"
    wanted = str(uuid4())
    others = [str(uuid4()) for _ in range(MAX_PENDING_SUBSCRIPTION_FRAMES + 4)]

    def keep(frame):
        consumer._keep_subscription_frame(json.dumps(frame))

    keep({"type": "subscribe", "uuid": wanted})
    for other in others:
        keep({"type": "unsubscribe", "uuid": other})
        keep({"type": "unsubscribe", "uuid": other})  # repeated: kept once
    kept = consumer._pending_subscription_frames
    assert len(kept) == MAX_PENDING_SUBSCRIPTION_FRAMES
    assert kept[0] == {"type": "subscribe", "uuid": wanted}
    assert consumer._unanswered_subscription_frames == len(others) - (
        MAX_PENDING_SUBSCRIPTION_FRAMES - 1
    )

    # An unsubscribe of the wanted target is never the one dropped.
    keep({"type": "unsubscribe", "uuid": wanted})
    assert {"type": "unsubscribe", "uuid": wanted} in kept
    assert kept[0] == {"type": "subscribe", "uuid": wanted}

    # A later subscribe makes everything kept before it irrelevant.
    latest = str(uuid4())
    keep({"type": "subscribe", "uuid": latest})
    assert kept == [{"type": "subscribe", "uuid": latest}]
    assert consumer._unanswered_subscription_frames == 0


class _Finished:
    """Set once a wrapped consumer coroutine has returned, raised or been
    cancelled, so a test waits for it instead of sleeping."""

    def __init__(self, owner, name, *, when=lambda *args, **kwargs: True):
        self.owner = owner
        self.name = name
        self.when = when
        self.event = asyncio.Event()

    def patch(self):
        finished = self
        original = getattr(self.owner, self.name)

        async def wrapped(*args, **kwargs):
            try:
                return await original(*args, **kwargs)
            finally:
                if finished.when(*args, **kwargs):
                    finished.event.set()

        return patch.object(self.owner, self.name, wrapped)

    async def wait(self, timeout=10):
        await asyncio.wait_for(self.event.wait(), timeout=timeout)


def _recovery_finished():
    from sockets.consumer import DataConsumer

    return _Finished(DataConsumer, "_on_saml_check_recovered")


def test_window_check_recovery_answers_kept_subscriptions(settings):
    """pending -> the window timer's check succeeds -> a targeted chunk with no
    client frame in between is delivered."""

    settings.SAML_SOCKET_UNAVAILABLE_CLOSE_SECONDS = 0.6
    org, _, workspace, _ = create_organization()
    member = create_member(org, workspace)
    credential = Credential()
    target = str(uuid4())
    recovered = _recovery_finished()

    async def run():
        socket = await open_direct_socket(member, org, saml=True)
        try:
            credential.failure = OperationalError("synthetic database outage")
            checks = credential.checks
            await socket.send_json_to({"type": "subscribe", "uuid": target})
            await _until(lambda: credential.checks > checks, "a failed check")
            assert await socket.receive_nothing(timeout=0.1)

            credential.failure = None
            checks = credential.checks
            # Only the window timer checks again; nothing is sent meanwhile.
            await recovered.wait()
            assert credential.checks > checks

            chunk = {"uuid": target, "chunk": "synthetic", "marker": uuid4().hex}
            await send_to_target(org, target, chunk)
            events = await outputs(socket, settle=0.5)
            assert closed(events) == []
            assert frames(events) == [
                {
                    "type": "subscription_confirmed",
                    "uuid": target,
                    "current_subscription": f"uuid_org_{org.pk}_{target}",
                },
                chunk,
            ]
        finally:
            await socket.disconnect()

    with credential.patch(), recovered.patch():
        asyncio.run(run())


def _letter_case_spellings(target, count):
    """Return ``count`` spellings of ``target`` that differ only in case."""

    letters = [index for index, char in enumerate(target) if char.isalpha()]
    assert 2 ** len(letters) >= count
    spellings = []
    for mask in range(count):
        chars = list(target)
        for bit, index in enumerate(letters):
            if mask >> bit & 1:
                chars[index] = chars[index].upper()
        spellings.append("".join(chars))
    return spellings


def _queue_consumer():
    from sockets.consumer import DataConsumer

    consumer = DataConsumer()
    consumer.scope = {}
    consumer.room_group_name = "org_synthetic"
    return consumer


def _keep(consumer, frame):
    import json

    from sockets.consumer import MAX_PENDING_SUBSCRIPTION_FRAMES

    consumer._keep_subscription_frame(json.dumps(frame))
    assert len(consumer._pending_subscription_frames) <= MAX_PENDING_SUBSCRIPTION_FRAMES


@pytest.mark.parametrize(
    "kept_target", ["subscribed-while-pending", "already-subscribed"]
)
def test_unsubscribes_of_the_kept_target_in_any_letter_case_count_once(kept_target):
    consumer = _queue_consumer()
    wanted = "abcdefab-cdef-4abc-8def-abcdefabcdef"
    if kept_target == "subscribed-while-pending":
        _keep(consumer, {"type": "subscribe", "uuid": wanted})
    else:
        consumer.current_subscription = consumer._uuid_group_name(wanted)
    spellings = _letter_case_spellings(wanted, 40)
    for spelling in spellings:
        _keep(consumer, {"type": "unsubscribe", "uuid": spelling})

    last = {"type": "unsubscribe", "uuid": spellings[-1]}
    if kept_target == "subscribed-while-pending":
        expected = [{"type": "subscribe", "uuid": wanted}, last]
    else:
        expected = [last]
    assert consumer._pending_subscription_frames == expected
    assert consumer._unanswered_subscription_frames == 0


def test_unsubscribes_of_many_distinct_targets_stay_bounded():
    from sockets.consumer import MAX_PENDING_SUBSCRIPTION_FRAMES

    consumer = _queue_consumer()
    wanted = str(uuid4())
    others = [str(uuid4()).upper() for _ in range(40)]
    _keep(consumer, {"type": "subscribe", "uuid": wanted})
    for other in others:
        _keep(consumer, {"type": "unsubscribe", "uuid": other})
    _keep(consumer, {"type": "unsubscribe", "uuid": wanted.upper()})

    kept = consumer._pending_subscription_frames
    assert len(kept) == MAX_PENDING_SUBSCRIPTION_FRAMES
    assert kept[0] == {"type": "subscribe", "uuid": wanted}
    assert kept[-1] == {"type": "unsubscribe", "uuid": wanted.upper()}
    # The newest unsubscribes of other targets are kept, the oldest dropped.
    assert kept[1:-1] == [
        {"type": "unsubscribe", "uuid": other}
        for other in others[-(MAX_PENDING_SUBSCRIPTION_FRAMES - 2) :]
    ]
    assert consumer._unanswered_subscription_frames == len(others) - (
        MAX_PENDING_SUBSCRIPTION_FRAMES - 2
    )


def _subscription_after(consumer, frames, subscribed):
    """The socket's subscription after answering ``frames`` in order."""

    for frame in frames:
        target = frame.get("uuid")
        if not target:
            continue
        group = consumer._uuid_group_name(target)
        if frame["type"] == "subscribe":
            subscribed = group
        elif group == subscribed:
            subscribed = None
    return subscribed


@pytest.mark.parametrize("seed", range(40))
def test_kept_frames_stay_bounded_and_keep_the_wanted_subscription(seed):
    import random

    rng = random.Random(seed)
    consumer = _queue_consumer()
    targets = [str(uuid4()) for _ in range(rng.randint(1, 30))]
    targets += ["legacy-target", "Legacy-Target"]
    subscribed = consumer._uuid_group_name(rng.choice(targets))
    consumer.current_subscription = subscribed
    received = []
    for _ in range(rng.randint(1, 400)):
        roll = rng.random()
        target = rng.choice(targets)
        if not target.lower().startswith("legacy"):
            # The same target, spelled in a random letter case.
            target = "".join(
                char.upper() if rng.random() < 0.5 else char for char in target
            )
        if roll < 0.05:
            frame = {"type": rng.choice(["subscribe", "unsubscribe"])}
        elif roll < 0.15:
            frame = {"type": "subscribe", "uuid": target}
        else:
            frame = {"type": "unsubscribe", "uuid": target}
        received.append(frame)
        _keep(consumer, frame)  # asserts the bound after every frame
        assert _subscription_after(
            consumer, consumer._pending_subscription_frames, subscribed
        ) == _subscription_after(consumer, received, subscribed)


def test_unsubscribe_of_a_subscribe_being_answered_is_never_dropped():
    from sockets.consumer import MAX_PENDING_SUBSCRIPTION_FRAMES

    consumer = _queue_consumer()
    previous, answering = str(uuid4()), str(uuid4())
    consumer.current_subscription = consumer._uuid_group_name(previous)
    # Taken off the queue and still being answered: the socket has not
    # switched its subscription yet.
    consumer._answering_frame = {"type": "subscribe", "uuid": answering}
    _keep(consumer, {"type": "unsubscribe", "uuid": answering})
    for _ in range(MAX_PENDING_SUBSCRIPTION_FRAMES):
        _keep(consumer, {"type": "unsubscribe", "uuid": str(uuid4())})

    assert consumer._pending_subscription_frames[0] == {
        "type": "unsubscribe",
        "uuid": answering,
    }
    assert consumer._unanswered_subscription_frames == 1


class _BlockedGroupAdd:
    """Holds ``group_add`` for one group open until ``release``."""

    def __init__(self, group):
        self.group = group
        self.entered = None
        self.released = None

    def patch(self):
        from tfc.channel_layers import ThreadSafeInMemoryChannelLayer

        blocker = self
        group_add = ThreadSafeInMemoryChannelLayer.group_add

        async def blocked_group_add(layer, group, channel):
            await group_add(layer, group, channel)
            if group == blocker.group:
                blocker.entered.set()
                await blocker.released.wait()

        return patch.object(
            ThreadSafeInMemoryChannelLayer, "group_add", blocked_group_add
        )

    def start(self):
        self.entered = asyncio.Event()
        self.released = asyncio.Event()


async def _until(condition, what):
    for _ in range(200):
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def test_window_recovery_stops_answering_when_a_new_check_is_pending(settings):
    """A forced membership check fails while the window check's recovery is
    answering kept frames: frames kept after it are answered only once a
    check succeeds again."""

    settings.SAML_SOCKET_UNAVAILABLE_CLOSE_SECONDS = 0.6
    org, _, workspace, _ = create_organization()
    member = create_member(org, workspace)
    credential = Credential()
    database, membership = membership_outage()
    first, second = str(uuid4()), str(uuid4())
    blocked = _BlockedGroupAdd(f"uuid_org_{org.pk}_{first}")

    async def run():
        blocked.start()
        socket = await open_direct_socket(member, org, saml=True)
        try:
            credential.failure = OperationalError("synthetic database outage")
            checks = credential.checks
            await socket.send_json_to({"type": "subscribe", "uuid": first})
            await _until(lambda: credential.checks > checks, "a failed check")
            credential.failure = None
            # The window check succeeds and answers the kept subscribe; its
            # group_add is held open.
            await asyncio.wait_for(blocked.entered.wait(), timeout=5)

            database["available"] = False
            await notify_membership_changed(member, org)
            await until_failed_checks(database, 1)
            await socket.send_json_to({"type": "subscribe", "uuid": second})
            await until_failed_checks(database, 2)
            blocked.released.set()

            events = await outputs(socket, settle=0.5)
            assert closed(events) == []
            # Only the frame kept before the new check is answered.
            assert frames(events) == [
                {
                    "type": "subscription_confirmed",
                    "uuid": first,
                    "current_subscription": f"uuid_org_{org.pk}_{first}",
                }
            ]

            database["available"] = True
            await socket.send_json_to({"type": "ping"})
            assert frames(await outputs(socket, settle=0.5)) == [
                {
                    "type": "subscription_confirmed",
                    "uuid": second,
                    "current_subscription": f"uuid_org_{org.pk}_{second}",
                },
                {"type": "pong"},
            ]
        finally:
            blocked.released.set()
            await socket.disconnect()

    with credential.patch(), membership, blocked.patch():
        asyncio.run(run())


def test_frames_kept_meanwhile_are_answered_when_the_forced_check_succeeds(settings):
    """After answering stopped for a forced check, its success answers the
    rest without waiting for another client frame.

    Code that never stops answering also passes; this guards the stop added
    above against leaving frames unanswered after the check succeeds."""

    settings.SAML_SOCKET_UNAVAILABLE_CLOSE_SECONDS = 0.6
    org, _, workspace, _ = create_organization()
    member = create_member(org, workspace)
    credential = Credential()
    first, second = str(uuid4()), str(uuid4())
    blocked = _BlockedGroupAdd(f"uuid_org_{org.pk}_{first}")
    forced_check = _Finished(
        MembershipSocketGuard,
        "ensure_member",
        when=lambda *args, force=False: force,
    )

    async def run():
        blocked.start()
        socket = await open_direct_socket(member, org, saml=True)
        try:
            credential.failure = OperationalError("synthetic database outage")
            checks = credential.checks
            await socket.send_json_to({"type": "subscribe", "uuid": first})
            await socket.send_json_to({"type": "unsubscribe", "uuid": second})
            await _until(lambda: credential.checks > checks + 1, "failed checks")
            credential.failure = None
            await asyncio.wait_for(blocked.entered.wait(), timeout=5)

            # The membership check this notification forces succeeds.
            await notify_membership_changed(member, org)
            await forced_check.wait()
            blocked.released.set()

            events = await outputs(socket, settle=0.5)
            assert closed(events) == []
            assert frames(events) == [
                {
                    "type": "subscription_confirmed",
                    "uuid": first,
                    "current_subscription": f"uuid_org_{org.pk}_{first}",
                },
                {"type": "info", "message": f"Not subscribed to {second}"},
            ]
        finally:
            blocked.released.set()
            await socket.disconnect()

    with credential.patch(), blocked.patch(), forced_check.patch():
        asyncio.run(run())


def test_window_recovery_joins_no_group_after_disconnect(settings):
    from channels.layers import get_channel_layer

    settings.SAML_SOCKET_UNAVAILABLE_CLOSE_SECONDS = 0.6
    org, _, workspace, _ = create_organization()
    member = create_member(org, workspace)
    credential = Credential()
    target = str(uuid4())
    group = f"uuid_org_{org.pk}_{target}"
    blocked = _BlockedGroupAdd(group)
    recovered = _recovery_finished()

    async def run():
        blocked.start()
        socket = await open_direct_socket(member, org, saml=True)
        credential.failure = OperationalError("synthetic database outage")
        checks = credential.checks
        await socket.send_json_to({"type": "subscribe", "uuid": target})
        await _until(lambda: credential.checks > checks, "a failed check")
        credential.failure = None
        # The window check's recovery is inside group_add when the client
        # goes away.
        await asyncio.wait_for(blocked.entered.wait(), timeout=5)
        await socket.disconnect()
        blocked.released.set()
        # Returns once the recovery has finished, cancelled or not.
        await recovered.wait()
        assert not get_channel_layer().groups.get(group)

    with credential.patch(), blocked.patch(), recovered.patch():
        asyncio.run(run())
