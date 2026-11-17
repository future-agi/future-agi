"""A WebSocket opened before its session ended is closed by the guard (READINESS 3.22).

The guard runs beside every authenticated socket (``JWTAuthMiddleware``). These tests drive it
with a fake app and a fake clock-free check, then through the real middleware and the real
``DataConsumer`` with real users and tokens: deactivating user A by a bulk update (no signal, no
cache eviction) closes A's open socket with 4401 within one re-check, while user B's socket keeps
serving.
"""

import asyncio
import json
import uuid

import pytest
from asgiref.sync import sync_to_async
from asgiref.testing import ApplicationCommunicator
from channels.routing import URLRouter
from django.db import OperationalError
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import generate_encrypted_message
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from saml2_auth.tests.ws_helpers import WebsocketCommunicator
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware import ws_session_guard
from tfc.middleware.jwt_auth import JWTAuthMiddleware
from tfc.middleware.ws_session_guard import (
    WS_CLOSE_SESSION_ENDED,
    SessionGuard,
    session_is_live,
)


class _Client:
    """The server side of a socket: what the app sends, and what it is handed."""

    def __init__(self):
        self.sent = []
        self.incoming = asyncio.Queue()

    async def send(self, message):
        self.sent.append(message)

    async def receive(self):
        return await self.incoming.get()


async def _echo_app(scope, receive, send):
    """Accepts, echoes text, and keeps sending after a disconnect (it must go nowhere)."""
    seen = []
    while True:
        event = await receive()
        seen.append(event["type"])
        if event["type"] == "websocket.connect":
            await send({"type": "websocket.accept"})
        elif event["type"] == "websocket.receive":
            await send({"type": "websocket.send", "text": event.get("text", "")})
        elif event["type"] == "websocket.disconnect":
            await send({"type": "websocket.send", "text": "late"})
            return seen


def _live(answers):
    calls = []

    async def is_live(token_id, user_id):
        calls.append((token_id, user_id))
        answer = answers[min(len(calls) - 1, len(answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return is_live, calls


def test_a_session_that_ends_closes_the_socket_and_disconnects_the_app():
    async def scenario():
        client = _Client()
        is_live, calls = _live([True, False])
        guard = SessionGuard("token-a", "user-a", interval=0.02, is_live=is_live)
        await client.incoming.put({"type": "websocket.connect"})
        seen = await asyncio.wait_for(
            guard.run(_echo_app, {"type": "websocket"}, client.receive, client.send),
            timeout=5,
        )
        return client, guard, seen, calls

    client, guard, seen, calls = asyncio.run(scenario())
    assert guard.ended is True
    assert seen == ["websocket.connect", "websocket.disconnect"]
    assert client.sent == [
        {"type": "websocket.accept"},
        {"type": "websocket.close", "code": WS_CLOSE_SESSION_ENDED},
    ]
    assert calls == [("token-a", "user-a"), ("token-a", "user-a")]


def test_a_live_session_keeps_serving_and_a_failed_check_keeps_the_socket():
    async def scenario():
        client = _Client()
        is_live, calls = _live([True, RuntimeError("database down"), True])
        guard = SessionGuard("token-b", "user-b", interval=0.02, is_live=is_live)
        run = asyncio.ensure_future(
            guard.run(_echo_app, {"type": "websocket"}, client.receive, client.send)
        )
        await client.incoming.put({"type": "websocket.connect"})
        while len(calls) < 4:
            await asyncio.sleep(0.01)
        await client.incoming.put({"type": "websocket.receive", "text": "hello"})
        await client.incoming.put({"type": "websocket.disconnect", "code": 1000})
        seen = await asyncio.wait_for(run, timeout=5)
        return client, guard, seen

    client, guard, seen = asyncio.run(scenario())
    assert guard.ended is False
    assert seen == ["websocket.connect", "websocket.receive", "websocket.disconnect"]
    assert {"type": "websocket.send", "text": "hello"} in client.sent
    assert not any(message["type"] == "websocket.close" for message in client.sent)


class _GranianLikeClient(_Client):
    """Models granian 2.8.3 as measured on the cloud rig: a close is held back while a receive()
    is pending, and goes out once that read is cancelled."""

    def __init__(self):
        super().__init__()
        self.reading = 0
        self.idle = asyncio.Event()
        self.idle.set()

    async def receive(self):
        self.reading += 1
        self.idle.clear()
        try:
            return await self.incoming.get()
        finally:
            self.reading -= 1
            if self.reading == 0:
                self.idle.set()

    async def send(self, message):
        if message["type"] == "websocket.close":
            await self.idle.wait()
        self.sent.append(message)


async def _closing_app(scope, receive, send):
    """Accepts, closes itself when asked to, and returns on the disconnect."""
    seen = []
    while True:
        event = await receive()
        seen.append(event["type"])
        if event["type"] == "websocket.connect":
            await send({"type": "websocket.accept"})
        elif event["type"] == "websocket.receive" and event.get("text") == "bye":
            await send({"type": "websocket.close", "code": 1000})
        elif event["type"] == "websocket.disconnect":
            return seen


def test_the_guards_close_reaches_a_server_that_holds_closes_behind_a_pending_read():
    async def scenario():
        client = _GranianLikeClient()
        is_live, _ = _live([True, False])
        guard = SessionGuard("token-g", "user-g", interval=0.02, is_live=is_live)
        await client.incoming.put({"type": "websocket.connect"})
        seen = await asyncio.wait_for(
            guard.run(_echo_app, {"type": "websocket"}, client.receive, client.send),
            timeout=5,
        )
        return client, guard, seen

    client, guard, seen = asyncio.run(scenario())
    assert guard.ended is True
    assert seen == ["websocket.connect", "websocket.disconnect"]
    assert client.sent[-1] == {
        "type": "websocket.close",
        "code": WS_CLOSE_SESSION_ENDED,
    }


def test_a_consumers_own_close_reaches_that_server_and_the_consumer_still_ends():
    async def scenario():
        client = _GranianLikeClient()
        is_live, _ = _live([True])
        guard = SessionGuard("token-h", "user-h", interval=0.05, is_live=is_live)
        for event in (
            {"type": "websocket.connect"},
            {"type": "websocket.receive", "text": "bye"},
        ):
            await client.incoming.put(event)
        seen = await asyncio.wait_for(
            guard.run(_closing_app, {"type": "websocket"}, client.receive, client.send),
            timeout=5,
        )
        return client, guard, seen

    client, guard, seen = asyncio.run(scenario())
    assert guard.ended is False
    assert seen == ["websocket.connect", "websocket.receive", "websocket.disconnect"]
    assert client.sent == [
        {"type": "websocket.accept"},
        {"type": "websocket.close", "code": 1000},
    ]


def _access_token(person):
    row = AuthToken.objects.create(
        user=person,
        auth_type=AuthTokenType.ACCESS.value,
        is_active=True,
        last_used_at=timezone.now(),
    )
    token = generate_encrypted_message({"user_id": str(person.id), "id": str(row.id)})
    return token, row


@pytest.mark.django_db
def test_the_check_reads_the_token_row_and_the_user(user, organization):
    other = User.objects.create_user(
        email="ws-guard-other@futureagi.com",
        password="test-password-123",
        name="Other",
        organization=organization,
    )
    _, mine = _access_token(user)
    _, theirs = _access_token(other)
    assert session_is_live(mine.id, user.id) is True
    # Another person's token row never answers for this user.
    assert session_is_live(theirs.id, user.id) is False
    User.objects.filter(id=user.id).update(is_active=False)
    assert session_is_live(mine.id, user.id) is False
    assert session_is_live(theirs.id, other.id) is True
    AuthToken.objects.filter(id=theirs.id).update(is_active=False)
    assert session_is_live(theirs.id, other.id) is False


@pytest.mark.django_db(transaction=True)
@override_settings(
    WS_SESSION_RECHECK_SECONDS=0.3,
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
def test_deactivating_a_user_closes_their_open_socket_and_not_another_users(
    user, organization
):
    from sockets.routing import websocket_urlpatterns

    other = User.objects.create_user(
        email="ws-guard-b@futureagi.com",
        password="test-password-123",
        name="Tenant B",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
    )
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=other,
        organization=organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    token_a, _ = _access_token(user)
    token_b, _ = _access_token(other)
    # Each socket signs in with its own one-time ticket, as the web app does.
    with override_settings(APP_BASE_URL=APP_ORIGIN):
        ticket_a, ticket_b = _socket_ticket(token_a), _socket_ticket(token_b)
    application = JWTAuthMiddleware(URLRouter(websocket_urlpatterns))

    def socket(ticket):
        return ApplicationCommunicator(
            application,
            {
                "type": "websocket",
                "path": "/ws/connect/",
                "query_string": f"ticket={ticket}".encode(),
                "headers": [],
                "subprotocols": [],
            },
        )

    async def send_json(peer, payload):
        await peer.send_input(
            {"type": "websocket.receive", "text": json.dumps(payload)}
        )

    async def receive_json(peer):
        message = await peer.receive_output(timeout=30)
        assert message["type"] == "websocket.send", message
        return json.loads(message["text"])

    async def scenario():
        a, b = socket(ticket_a), socket(ticket_b)
        for peer in (a, b):
            await peer.send_input({"type": "websocket.connect"})
            assert (await peer.receive_output(timeout=30))["type"] == "websocket.accept"
        await send_json(a, {"type": "ping"})
        assert await receive_json(a) == {"type": "pong"}
        # A bulk update: no signal fires and no cache entry is evicted.
        await sync_to_async(User.objects.filter(id=user.id).update)(is_active=False)
        started = asyncio.get_running_loop().time()
        closed = await a.receive_output(timeout=30)
        # One re-check (0.3 s here) plus the read; generous for a loaded test host.
        assert asyncio.get_running_loop().time() - started < 15
        await send_json(b, {"type": "subscribe", "uuid": "still-here"})
        reply = await receive_json(b)
        await asyncio.sleep(0.8)
        assert await b.receive_nothing(timeout=0.2) is True
        await send_json(b, {"type": "ping"})
        pong = await receive_json(b)
        for peer in (a, b):
            await peer.send_input({"type": "websocket.disconnect", "code": 1000})
            await peer.wait(timeout=30)
        return closed, reply, pong

    closed, reply, pong = asyncio.run(scenario())
    assert closed == {"type": "websocket.close", "code": WS_CLOSE_SESSION_ENDED}
    assert reply["type"] == "subscription_confirmed"
    assert pong == {"type": "pong"}


# The web app's origin, the only one that may mint a socket ticket (synthetic).
APP_ORIGIN = "https://app.session-guard.test"
SOCKET_SETTINGS = {
    "APP_BASE_URL": APP_ORIGIN,
    "WS_SESSION_RECHECK_SECONDS": 0.3,
    "CHANNEL_LAYERS": {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
}


def _socket_ticket(token, path="/ws/connect/"):
    """A one-time socket ticket minted with ``token``, as the web app mints one."""
    response = APIClient().post(
        reverse("handoff-ws-ticket"),
        {"path": path},
        format="json",
        HTTP_ORIGIN=APP_ORIGIN,
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert response.status_code == 200, response.content
    return response.json()["ticket"]


def _person(organization, label, *, member):
    """An account naming ``organization``; with ``member``, an active member."""
    person = User.objects.create_user(
        email=f"ws-guard-{label}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="test-password-123",
        name=f"Guard {label}",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )
    if member:
        OrganizationMembership.no_workspace_objects.create(
            user=person,
            organization=organization,
            role=OrganizationRoles.MEMBER,
            level=Level.MEMBER,
            is_active=True,
        )
    return person


async def _next_frame(peer, timeout):
    """The next frame the server sends this client, or None when none comes."""
    try:
        return await peer.receive_output(timeout=timeout)
    except TimeoutError:
        return None


async def _ping(peer):
    await peer.send_json_to({"type": "ping"})
    return await peer.receive_json_from(timeout=30)


@pytest.mark.django_db(transaction=True)
@override_settings(**SOCKET_SETTINGS)
def test_an_ended_membership_closes_the_open_socket_at_the_next_check(
    user, organization
):
    """No removal message is sent: the membership is made inactive by a bulk
    update. The next check closes that member's ``ws/connect/`` socket with
    4401. Another member's socket, and that of an account with no membership
    rows at all whose legacy organization is this one, keep serving."""
    from sockets.routing import websocket_urlpatterns

    staying = _person(organization, "staying", member=True)
    legacy = _person(organization, "legacy", member=False)
    tickets = {
        "ended": _socket_ticket(_access_token(user)[0]),
        "staying": _socket_ticket(_access_token(staying)[0]),
        "legacy": _socket_ticket(_access_token(legacy)[0]),
    }
    application = JWTAuthMiddleware(URLRouter(websocket_urlpatterns))

    async def scenario():
        loop = asyncio.get_running_loop()
        peers = {}
        for name, ticket in tickets.items():
            peer = WebsocketCommunicator(application, f"/ws/connect/?ticket={ticket}")
            connected, detail = await peer.connect(timeout=30)
            assert connected, (name, detail)
            peers[name] = peer
        # A bulk update: no signal fires and no removal message is sent.
        memberships = OrganizationMembership._base_manager.filter(
            user=user, organization=organization
        )
        await sync_to_async(memberships.update)(is_active=False)
        started = loop.time()
        closed = await _next_frame(peers["ended"], timeout=15)
        elapsed = loop.time() - started
        await peers["ended"].wait(timeout=5)
        # At least one more check of the other sockets passes meanwhile.
        quiet = [
            await peers[name].receive_nothing(timeout=0.25)
            for name in ("staying", "legacy")
        ]
        pongs = [await _ping(peers[name]) for name in ("staying", "legacy")]
        for name in ("staying", "legacy"):
            await peers[name].disconnect()
        return closed, elapsed, quiet, pongs

    closed, elapsed, quiet, pongs = asyncio.run(scenario())
    assert closed == {"type": "websocket.close", "code": WS_CLOSE_SESSION_ENDED}
    # One re-check (0.3 s here) plus the read; generous for a loaded test host.
    assert elapsed < 15
    assert quiet == [True, True]
    assert pongs == [{"type": "pong"}, {"type": "pong"}]


@pytest.mark.django_db(transaction=True)
@override_settings(**SOCKET_SETTINGS)
def test_a_failed_check_of_the_served_organization_keeps_the_socket(
    user, organization, monkeypatch
):
    """Every check of the socket's session and organization fails with a
    database error: the socket stays open and keeps serving, and each check
    was made for the organization the socket serves."""
    from sockets.routing import websocket_urlpatterns

    token, row = _access_token(user)
    ticket = _socket_ticket(token)
    calls = []

    def unavailable(*arguments):
        calls.append(arguments)
        raise OperationalError("database unavailable")

    monkeypatch.setattr(ws_session_guard, "session_is_live", unavailable)
    application = JWTAuthMiddleware(URLRouter(websocket_urlpatterns))

    async def scenario():
        peer = WebsocketCommunicator(application, f"/ws/connect/?ticket={ticket}")
        connected, detail = await peer.connect(timeout=30)
        assert connected, detail
        # The socket names its organization before it accepts; at most one
        # check started earlier may still land after the accept.
        accepted = len(calls) + 1
        for _ in range(300):
            if len(calls) >= accepted + 3:
                break
            await asyncio.sleep(0.05)
        quiet = await peer.receive_nothing(timeout=0.25)
        pong = await _ping(peer)
        await peer.disconnect()
        return calls[accepted:], quiet, pong

    checks, quiet, pong = asyncio.run(scenario())
    assert len(checks) >= 3
    assert quiet is True
    assert pong == {"type": "pong"}
    assert set(checks) == {(str(row.id), user.id, organization.id)}
