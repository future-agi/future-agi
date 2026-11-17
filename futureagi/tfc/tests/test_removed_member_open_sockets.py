"""A removed member's open sockets for the organization are closed.

Each socket is opened as the web app opens one: a one-time ticket minted with
the member's access token (``handoff-ws-ticket``), then ``?ticket=`` through
``JWTAuthMiddleware`` and the real routes. Removing the member with
``offboard_organization_member`` closes its open ``ws/connect/``,
``ws/simulation-updates/`` and ``ws/falcon-ai/`` sockets with 4401 at once,
without waiting for the periodic session check, and the organization's
messages sent after the removal reach none of them. A member of the same
organization who is not removed keeps its sockets and keeps receiving the same
messages. If the close message cannot be sent, the removal still stands and
the session check closes the socket within one interval.
"""

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from channels.routing import URLRouter
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.authentication import generate_encrypted_message
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import WorkspaceMembership
from accounts.services.offboarding import offboard_organization_member
from saml2_auth.tests.ws_helpers import WebsocketCommunicator
from tfc.constants.levels import Level
from tfc.middleware.jwt_auth import JWTAuthMiddleware
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.middleware.ws_session_guard import WS_CLOSE_SESSION_ENDED
from tfc.utils.tests.production_path import PASSWORD, make_tenant

# The web app's origin, the only one that may mint a socket ticket (synthetic).
APP_ORIGIN = "https://app.member-sockets.test"
CLOSED = {"type": "websocket.close", "code": WS_CLOSE_SESSION_ENDED}
RECHECK_SECONDS = 0.3


def _member(tenant, label):
    """An active member of ``tenant`` and of its default workspace."""
    clear_workspace_context()
    person = User.objects.create_user(
        email=f"socket-{label}-{uuid.uuid4().hex[:8]}@example.com",
        password=PASSWORD,
        name=f"Socket {label}",
        organization=tenant.organization,
        organization_role="Member",
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=person,
        organization=tenant.organization,
        role="Member",
        level=Level.MEMBER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=person,
        workspace=tenant.workspace,
        role="Workspace Member",
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
        organization_membership=membership,
    )
    clear_workspace_context()
    return person


def _access_token(person):
    """A live access token of ``person``, not scoped to any organization."""
    row = AuthToken.objects.create(
        user=person,
        auth_type=AuthTokenType.ACCESS.value,
        is_active=True,
        last_used_at=timezone.now(),
    )
    return generate_encrypted_message({"user_id": str(person.id), "id": str(row.id)})


def _ticket(token, path, query):
    """A one-time socket ticket minted with ``token``, as the web app mints one."""
    response = APIClient().post(
        reverse("handoff-ws-ticket"),
        {"path": path, "query": query},
        format="json",
        HTTP_ORIGIN=APP_ORIGIN,
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    clear_workspace_context()
    assert response.status_code == 200, response.content
    return response.json()["ticket"]


def _application():
    from sockets.routing import websocket_urlpatterns

    return JWTAuthMiddleware(URLRouter(websocket_urlpatterns))


async def _open(application, person, path, query=None):
    """An accepted socket of ``person`` on ``path``."""
    token = await sync_to_async(_access_token)(person)
    ticket = await sync_to_async(_ticket)(token, path, query or {})
    socket = WebsocketCommunicator(application, f"{path}?ticket={ticket}")
    connected, detail = await socket.connect(timeout=30)
    assert connected, detail
    return socket


async def _next_frame(socket, timeout=10):
    """The next frame the server sends this client, or None when none comes."""
    try:
        return await socket.receive_output(timeout=timeout)
    except TimeoutError:
        return None


def _received(frame):
    """The JSON a data frame carries; any other frame (or None) as it is."""
    if frame is not None and frame.get("type") == "websocket.send":
        return json.loads(frame["text"])
    return frame


async def _ping(socket):
    await socket.send_json_to({"type": "ping"})
    return _received(await _next_frame(socket))


async def _send_to_organization(organization_id, message):
    """An organization-wide message, the event ``send_message_to_channel_async``
    sends to the organization's group."""
    await get_channel_layer().group_send(
        f"org_{organization_id}", {"type": "send_data", "data": message}
    )


async def _remove(person, tenant):
    """Remove ``person`` from the organization, as the removal endpoints do."""
    await sync_to_async(offboard_organization_member)(
        person, tenant.organization, actor=tenant.user
    )


def _has_active_membership(person, tenant):
    return OrganizationMembership._base_manager.filter(
        user=person, organization=tenant.organization, is_active=True
    ).exists()


class _SimulationChannels:
    """Redis pub/sub for the simulation channels, inside this process: what
    ``notify_simulation_update`` publishes reaches the consumer's subscription."""

    def __init__(self):
        self.subscriptions = {}

    def publish(self, channel, message):
        queues = list(self.subscriptions.get(channel, ()))
        for queue in queues:
            queue.put_nowait({"type": "message", "channel": channel, "data": message})
        return len(queues)

    def client(self, *args, **kwargs):
        return _SimulationClient(self)

    async def wait_for_subscribers(self, channel, count):
        for _ in range(1000):
            if len(self.subscriptions.get(channel, ())) >= count:
                break
            await asyncio.sleep(0.01)
        assert len(self.subscriptions.get(channel, ())) >= count, channel


class _SimulationClient:
    def __init__(self, channels):
        self.channels = channels

    def pubsub(self):
        return _SimulationSubscription(self.channels)

    async def close(self):
        return None


class _SimulationSubscription:
    def __init__(self, channels):
        self.channels = channels
        self.queue = asyncio.Queue()
        self.names = []

    async def subscribe(self, name):
        self.channels.subscriptions.setdefault(name, []).append(self.queue)
        self.names.append(name)

    async def listen(self):
        while True:
            yield await self.queue.get()

    async def unsubscribe(self):
        for name in self.names:
            self.channels.subscriptions[name].remove(self.queue)
        self.names = []

    async def close(self):
        return None


@pytest.fixture
def members(transactional_db, settings):
    """An organization with two active members: one that will be removed and
    one that stays."""
    settings.APP_BASE_URL = APP_ORIGIN
    settings.WS_SESSION_RECHECK_SECONDS = RECHECK_SECONDS
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }
    tenant = make_tenant("Sockets")
    yield SimpleNamespace(
        tenant=tenant,
        leaving=_member(tenant, "leaving"),
        staying=_member(tenant, "staying"),
    )
    clear_workspace_context()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
class TestRemovedMemberOpenSocketsClose:
    async def test_data_socket_closes_before_the_next_organization_message(
        self, members
    ):
        world = members
        application = _application()
        leaving = await _open(application, world.leaving, "/ws/connect/")
        staying = await _open(application, world.staying, "/ws/connect/")
        organization_id = world.tenant.organization.id
        before = {"type": "notification", "data": {"sent": "before the removal"}}
        after = {"type": "notification", "data": {"sent": "after the removal"}}

        await _send_to_organization(organization_id, before)
        assert _received(await _next_frame(leaving)) == before
        assert _received(await _next_frame(staying)) == before

        await _remove(world.leaving, world.tenant)
        # Sent at once: the socket is closed before this message reaches it.
        await _send_to_organization(organization_id, after)

        assert await _next_frame(leaving) == CLOSED
        assert await leaving.receive_nothing(timeout=0.25) is True
        await leaving.wait(timeout=5)
        # A member who is not removed keeps receiving the same messages.
        assert _received(await _next_frame(staying)) == after
        assert await staying.receive_nothing(timeout=0.25) is True
        await staying.disconnect()

    async def test_simulation_socket_closes_and_gets_no_further_update(
        self, members, monkeypatch
    ):
        from simulate.utils import websocket_notifications
        from sockets import simulation_consumer

        channels = _SimulationChannels()
        monkeypatch.setattr(
            simulation_consumer, "aioredis", SimpleNamespace(from_url=channels.client)
        )
        monkeypatch.setattr(websocket_notifications, "_get_redis", lambda: channels)
        world = members
        application = _application()
        run_test_id = str(uuid.uuid4())
        query = {"test_id": run_test_id}
        route = "/ws/simulation-updates/"
        leaving = await _open(application, world.leaving, route, query)
        staying = await _open(application, world.staying, route, query)
        organization_id = world.tenant.organization.id
        await channels.wait_for_subscribers(
            f"simulation_updates:{organization_id}:{run_test_id}", 2
        )
        update = {
            "type": "simulation_update",
            "data": {"run_test_id": run_test_id, "test_execution_id": None},
        }

        websocket_notifications.notify_simulation_update(organization_id, run_test_id)
        assert _received(await _next_frame(leaving)) == update
        assert _received(await _next_frame(staying)) == update

        await _remove(world.leaving, world.tenant)
        assert await _next_frame(leaving) == CLOSED
        # Updates do not travel through the channel layer: the next one is
        # published once the close has gone out, and reaches only the member
        # who stays.
        websocket_notifications.notify_simulation_update(organization_id, run_test_id)
        assert await leaving.receive_nothing(timeout=0.25) is True
        await leaving.wait(timeout=5)
        assert _received(await _next_frame(staying)) == update
        await staying.disconnect()

    @pytest.mark.requires_ee
    async def test_falcon_socket_closes_and_answers_nothing_more(
        self, members, monkeypatch
    ):
        from ee.falcon_ai.consumers import FalconAIConsumer
        from sockets.routing import websocket_urlpatterns

        assert any(
            getattr(pattern.callback, "consumer_class", None) is FalconAIConsumer
            for pattern in websocket_urlpatterns
        ), "ws/falcon-ai/ is served by the stub"
        # The organization's licence check would close every socket with 4003.
        monkeypatch.setattr(
            "ee.falcon_ai.consumers.check_ee_feature", lambda *args, **kwargs: None
        )
        world = members
        application = _application()
        query = {"workspace_id": str(world.tenant.workspace.id)}
        leaving = await _open(application, world.leaving, "/ws/falcon-ai/", query)
        staying = await _open(application, world.staying, "/ws/falcon-ai/", query)
        assert await _ping(leaving) == {"type": "pong"}
        assert await _ping(staying) == {"type": "pong"}

        await _remove(world.leaving, world.tenant)
        await _send_to_organization(
            world.tenant.organization.id,
            {"type": "notification", "data": {"sent": "after the removal"}},
        )

        assert await _next_frame(leaving) == CLOSED
        await leaving.send_json_to({"type": "ping"})
        assert await leaving.receive_nothing(timeout=0.25) is True
        await leaving.wait(timeout=5)
        # A member who is not removed keeps its socket.
        assert await _ping(staying) == {"type": "pong"}
        assert await staying.receive_nothing(timeout=0.25) is True
        await staying.disconnect()

    async def test_without_the_close_message_the_session_check_closes_the_socket(
        self, members, monkeypatch
    ):
        world = members
        application = _application()
        leaving = await _open(application, world.leaving, "/ws/connect/")

        def unavailable(*args, **kwargs):
            raise ConnectionError("channel layer unavailable")

        # From here on, no process can reach the channel layer to send a close.
        monkeypatch.setattr("channels.layers.get_channel_layer", unavailable)
        await _remove(world.leaving, world.tenant)

        is_member = sync_to_async(_has_active_membership)
        assert await is_member(world.leaving, world.tenant) is False
        # One re-check (0.3 s here) plus the read; generous for a loaded host.
        assert await _next_frame(leaving, timeout=15) == CLOSED
        await leaving.wait(timeout=5)
