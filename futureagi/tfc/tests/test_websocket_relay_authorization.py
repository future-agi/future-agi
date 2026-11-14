"""Exercise relay authorization over real HTTP and WebSocket connections.

Only synthetic account setup uses the ORM. Login, membership removal, relay
requests, authentication and subscriber delivery all use the real application.
The in-process publishers are the real helpers that Temporal and usage code use.
"""

import json
import socket
import threading
import time
from contextlib import contextmanager
from uuid import uuid4

import pytest
import requests
import uvicorn
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
from django.core.cache import cache
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.utils.utils import send_message_to_channel, send_message_to_uuid
from sockets.routing import websocket_urlpatterns
from tfc.constants.levels import Level
from tfc.middleware.jwt_auth import JWTAuthMiddleware
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tfc.settings.settings import MIDDLEWARE
from tfc.utils.websocket_relay_auth import relay_headers

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
PASSWORD = "Synthetic-relay-test-password"


@pytest.fixture
def relay_server(transactional_db, settings):
    # Use the production middleware/routing with the isolated test database.
    # A unique Redis prefix keeps subscriber groups separate from other tests.
    settings.MIDDLEWARE = MIDDLEWARE
    settings.CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": ["redis://127.0.0.1:16379/4"],
                "prefix": f"relay-test-{uuid4().hex}",
            },
        }
    }
    application = ProtocolTypeRouter(
        {
            "http": get_asgi_application(),
            "websocket": JWTAuthMiddleware(URLRouter(websocket_urlpatterns)),
        }
    )
    # Each test is an independent login journey, not one shared IP rate budget.
    cache.delete_many(["ip_requests_127.0.0.1", "blocked_ip_127.0.0.1"])
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                application,
                host="127.0.0.1",
                port=port,
                lifespan="off",
                access_log=False,
                log_level="error",
            )
        )
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while (
                not server.started and thread.is_alive() and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            assert server.started, "Relay test server did not start"
            with requests.Session() as session:
                session.trust_env = False
                yield session, f"http://localhost:{port}", f"ws://localhost:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=20)
            assert not thread.is_alive(), "Relay test server did not shut down"


def create_organization():
    suffix = uuid4().hex
    org = Organization.objects.create(name=f"Synthetic relay {suffix}")
    set_workspace_context(organization=org)
    try:
        owner = User.objects.create_user(
            email=f"owner-{suffix}@example.invalid",
            password=PASSWORD,
            name="Synthetic Owner",
            organization=org,
            organization_role="Owner",
        )
        member = User.objects.create_user(
            email=f"member-{suffix}@example.invalid",
            password=PASSWORD,
            name="Synthetic Member",
            organization=org,
            organization_role="Member",
        )
        workspace = Workspace.no_workspace_objects.create(
            name="Synthetic Workspace",
            organization=org,
            created_by=owner,
            is_active=True,
            is_default=True,
        )
        for user, role, level in (
            (owner, "Owner", Level.OWNER),
            (member, "Member", Level.MEMBER),
        ):
            membership = OrganizationMembership.no_workspace_objects.create(
                user=user,
                organization=org,
                role=role,
                level=level,
                is_active=True,
            )
            WorkspaceMembership.no_workspace_objects.create(
                user=user,
                workspace=workspace,
                organization_membership=membership,
                role="Workspace Admin" if user == owner else "Workspace Member",
                level=(
                    Level.WORKSPACE_ADMIN if user == owner else Level.WORKSPACE_MEMBER
                ),
                is_active=True,
            )
        key = OrgApiKey.no_workspace_objects.create(
            name="Synthetic relay control",
            organization=org,
            workspace=workspace,
            type="system",
            enabled=True,
        )
        return org, owner, member, key
    finally:
        clear_workspace_context()


def login(server, user):
    session, http, _ = server
    response = session.post(
        http + "/accounts/token/",
        json={"email": user.email, "password": PASSWORD},
        timeout=30,
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result.get("access")
    return result


def ping(ws):
    ws.send(json.dumps({"type": "ping"}))
    assert json.loads(ws.recv(timeout=10)) == {"type": "pong"}


def subscribe(ws, target):
    ws.send(json.dumps({"type": "subscribe", "uuid": target}))
    confirmed = json.loads(ws.recv(timeout=10))
    assert confirmed["type"] == "subscription_confirmed", confirmed
    return confirmed["current_subscription"]


@contextmanager
def subscriber(server, org, owner, target):
    token = login(server, owner)["access"]
    with connect(
        f"{server[2]}/ws/connect/?token={token}&organization_id={org.pk}",
        proxy=None,
        open_timeout=30,
    ) as ws:
        ping(ws)
        if target:
            assert subscribe(ws, target) == f"uuid_org_{org.pk}_{target}"
        yield ws


@contextmanager
def socket_or_refusal(url):
    """Yield ``(ws, None)`` for an open socket or ``(None, status)`` if refused.

    A consumer that closes before accepting is reported by the server as an
    HTTP status on the handshake, unlike a socket that opens and then drops.
    """
    try:
        ws = connect(url, proxy=None, open_timeout=30)
    except InvalidStatus as exc:
        yield None, exc.response.status_code
        return
    with ws:
        ping(ws)
        yield ws, None


def receive_optional(ws):
    try:
        return json.loads(ws.recv(timeout=1))
    except TimeoutError:
        return None


def relay(server, headers, target):
    """POST a synthetic message; ``headers`` may be built from the raw body."""

    message = {"alert_title": "Synthetic notification", "marker": uuid4().hex}
    body = {"message": message}
    if target:
        body.update(send_to_uuid=True, uuid=target)
        message["uuid"] = target
    raw = json.dumps(body).encode()
    if callable(headers):
        headers = headers(raw)
    response = server[0].post(
        server[1] + "/call-websocket/",
        data=raw,
        headers={"Content-Type": "application/json", **headers},
        timeout=30,
    )
    return response, message


def key_headers(key):
    return {"X-Api-Key": key.api_key, "X-Secret-Key": key.secret_key}


def internal_headers(key, signed_org=None, signed_body=None):
    """Headers the server-side relay caller sends: system key plus signature."""

    def build(raw):
        signed = relay_headers(
            signed_org or key.organization_id,
            raw if signed_body is None else signed_body,
        )
        return {**key_headers(key), **signed}

    return build


def remove_member_and_log_in_again(server, org, owner, member):
    """Remove the member through the real API and return their fresh login."""
    owner_token = login(server, owner)["access"]
    removed = server[0].delete(
        server[1] + "/accounts/organization/members/remove/",
        headers={"Authorization": "Bearer " + owner_token},
        json={"user_id": str(member.pk)},
        timeout=30,
    )
    assert removed.status_code == 200, removed.text
    member.refresh_from_db()
    assert member.is_active
    assert member.organization_id == org.pk
    assert not OrganizationMembership.no_workspace_objects.filter(
        user=member,
        is_active=True,
    ).exists()
    assert not WorkspaceMembership.no_workspace_objects.filter(
        user=member,
        is_active=True,
    ).exists()

    fresh_login = login(server, member)
    assert fresh_login["requires_org_setup"] is True
    return fresh_login


@pytest.mark.parametrize("targeted", [False, True], ids=["broadcast", "targeted"])
def test_system_key_delivers_only_to_its_organization(relay_server, targeted):
    org, owner, _, key = create_organization()
    other_org, other_owner, _, _ = create_organization()
    target = str(uuid4()) if targeted else None
    with (
        subscriber(relay_server, org, owner, target) as own,
        subscriber(relay_server, other_org, other_owner, target) as other,
    ):
        response, message = relay(relay_server, internal_headers(key), target)
        assert response.status_code == 200, response.text
        assert json.loads(own.recv(timeout=10)) == message
        assert receive_optional(other) is None
        denied, _ = relay(relay_server, {}, target)
        assert denied.status_code in (401, 403)
        assert receive_optional(own) is None
        assert receive_optional(other) is None


@pytest.mark.parametrize("targeted", [False, True], ids=["broadcast", "targeted"])
@pytest.mark.parametrize(
    "former_org_header", [False, True], ids=["implicit", "former-org-header"]
)
def test_removed_member_fresh_login_cannot_relay(
    relay_server, targeted, former_org_header
):
    org, owner, member, key = create_organization()
    target = str(uuid4()) if targeted else None
    old_login = login(relay_server, member)
    assert not old_login.get("requires_org_setup", False)
    with subscriber(relay_server, org, owner, target) as ws:
        # Member sessions never publish, before or after removal.
        before, _ = relay(
            relay_server,
            {"Authorization": "Bearer " + old_login["access"]},
            target,
        )
        assert (before.status_code, receive_optional(ws)) == (403, None)

        fresh_login = remove_member_and_log_in_again(relay_server, org, owner, member)
        assert fresh_login["access"] != old_login["access"]
        headers = {"Authorization": "Bearer " + fresh_login["access"]}
        if former_org_header:
            headers["X-Organization-Id"] = str(org.pk)
        response, _ = relay(relay_server, headers, target)
        delivered = receive_optional(ws)

        # A denied relay must not look successful just because the socket died.
        control, control_message = relay(relay_server, internal_headers(key), target)
        assert control.status_code == 200, control.text
        assert json.loads(ws.recv(timeout=10)) == control_message
        assert (response.status_code, delivered) == (403, None)
        assert response.json()["code"] == "permission_denied"


def caller_headers(server, caller, org, owner, member, key):
    """Return relay headers for a caller other than the internal relay client."""

    if caller in ("owner-session", "member-session"):
        user = owner if caller == "owner-session" else member
        return {"Authorization": "Bearer " + login(server, user)["access"]}
    if caller in ("user-api-key", "mcp-api-key"):
        user_key = OrgApiKey.no_workspace_objects.create(
            name="Synthetic personal key",
            organization=org,
            workspace=key.workspace,
            type=caller.split("-")[0],
            user=member if caller == "user-api-key" else owner,
            enabled=True,
        )
        return key_headers(user_key)
    if caller == "unsigned-system-key":
        return key_headers(key)
    if caller == "signature-for-another-organization":
        other_org, _, _, _ = create_organization()
        return internal_headers(key, signed_org=other_org.pk)
    if caller == "signature-for-another-body":
        return internal_headers(key, signed_body=b'{"message": {}}')
    if caller == "signature-without-system-key":
        session = {"Authorization": "Bearer " + login(server, owner)["access"]}
        return lambda raw: {
            **session,
            **relay_headers(org.pk, raw),
        }
    raise AssertionError(caller)


@pytest.mark.parametrize("targeted", [False, True], ids=["broadcast", "targeted"])
@pytest.mark.parametrize(
    "caller",
    [
        "owner-session",
        "member-session",
        "user-api-key",
        "mcp-api-key",
        "unsigned-system-key",
        "signature-for-another-organization",
        "signature-for-another-body",
        "signature-without-system-key",
    ],
)
def test_relay_accepts_only_internal_callers(relay_server, caller, targeted):
    org, owner, member, key = create_organization()
    target = str(uuid4()) if targeted else None
    headers = caller_headers(relay_server, caller, org, owner, member, key)
    with subscriber(relay_server, org, owner, target) as ws:
        response, _ = relay(relay_server, headers, target)
        delivered = receive_optional(ws)

        # The internal caller is the positive control on the same socket.
        control, control_message = relay(relay_server, internal_headers(key), target)
        assert control.status_code == 200, control.text
        assert json.loads(ws.recv(timeout=10)) == control_message
        assert (response.status_code, delivered) == (403, None), response.text
        assert response.json()["code"] == "permission_denied"


@pytest.mark.parametrize(
    "former_org_param", [False, True], ids=["implicit", "former-org-param"]
)
def test_removed_member_socket_is_refused(relay_server, former_org_param):
    org, owner, member, _ = create_organization()
    token = remove_member_and_log_in_again(relay_server, org, owner, member)["access"]
    query = f"token={token}"
    if former_org_param:
        query += f"&organization_id={org.pk}"
    with socket_or_refusal(f"{relay_server[2]}/ws/connect/?{query}") as (ws, status):
        group = subscribe(ws, str(uuid4())) if ws is not None else None
        assert (status, group) == (403, None)


@pytest.mark.parametrize("publisher", ["in_process", "system_key"])
@pytest.mark.parametrize("targeted", [False, True], ids=["broadcast", "targeted"])
def test_removed_member_socket_receives_no_former_org_traffic(
    relay_server, targeted, publisher
):
    org, owner, member, key = create_organization()
    target = str(uuid4()) if targeted else None
    with subscriber(relay_server, org, owner, target) as own:
        token = remove_member_and_log_in_again(relay_server, org, owner, member)
        url = f"{relay_server[2]}/ws/connect/?token={token['access']}"
        with socket_or_refusal(url) as (removed, status):
            if removed is not None and target:
                subscribe(removed, target)
            if publisher == "system_key":
                response, message = relay(relay_server, internal_headers(key), target)
                assert response.status_code == 200, response.text
            elif target:
                message = {"chunk": "synthetic", "marker": uuid4().hex, "uuid": target}
                send_message_to_uuid(target, message, org.pk)
            else:
                message = {"alert_title": "Synthetic usage", "marker": uuid4().hex}
                send_message_to_channel(org.pk, message)
            # The organization's own subscriber is the positive control.
            assert json.loads(own.recv(timeout=10)) == message
            delivered = receive_optional(removed) if removed is not None else None
            assert (status, delivered) == (403, None)


def test_client_frames_never_reach_stream_subscribers(relay_server):
    org, owner, member, _ = create_organization()
    target = str(uuid4())
    with subscriber(relay_server, org, owner, target) as own:
        # Stream frames come only from the server-side relay, even for members.
        with subscriber(relay_server, org, member, target) as peer:
            data = {"marker": uuid4().hex}
            peer.send(json.dumps({"type": "peer_msg", "uuid": target, "data": data}))
            assert json.loads(peer.recv(timeout=10)) == {
                "type": "error",
                "message": "Unsupported message type.",
            }
            assert receive_optional(own) is None

        token = remove_member_and_log_in_again(relay_server, org, owner, member)
        url = f"{relay_server[2]}/ws/connect/?token={token['access']}"
        with socket_or_refusal(url) as (removed, status):
            if removed is not None:
                subscribe(removed, target)
                frame = {"type": "client_stream", "uuid": target, "data": {"text": "x"}}
                removed.send(json.dumps(frame))
            assert (status, receive_optional(own)) == (403, None)


def close_code(ws):
    """Return the close code once the server closes ``ws``, failing on any frame."""

    try:
        frame = ws.recv(timeout=10)
    except ConnectionClosed as exc:
        return exc.rcvd.code if exc.rcvd is not None else None
    raise AssertionError(f"unexpected frame before close: {frame}")


@pytest.mark.parametrize("targeted", [False, True], ids=["broadcast", "targeted"])
def test_open_member_socket_closes_when_membership_is_removed(relay_server, targeted):
    org, owner, member, key = create_organization()
    target = str(uuid4()) if targeted else None
    with (
        subscriber(relay_server, org, owner, target) as own,
        subscriber(relay_server, org, member, target) as open_member,
    ):
        before, message = relay(relay_server, internal_headers(key), target)
        assert before.status_code == 200, before.text
        assert json.loads(own.recv(timeout=10)) == message
        assert json.loads(open_member.recv(timeout=10)) == message

        remove_member_and_log_in_again(relay_server, org, owner, member)
        after, message = relay(relay_server, internal_headers(key), target)
        assert after.status_code == 200, after.text
        # The organization's remaining member is the positive control.
        assert json.loads(own.recv(timeout=10)) == message
        assert close_code(open_member) == 4002


@pytest.mark.parametrize("targeted", [False, True], ids=["broadcast", "targeted"])
@pytest.mark.parametrize("organization_id", ["uuid", "str"])
def test_internal_relay_client_reaches_only_its_organization(
    relay_server, settings, targeted, organization_id
):
    from model_hub.utils import call_websocket

    org, owner, _, _ = create_organization()
    other_org, other_owner, _, _ = create_organization()
    settings.CLOUD_DEPLOYMENT = ""
    settings.WEBSOCKET_ENDPOINT = relay_server[1] + "/call-websocket/"
    target = str(uuid4()) if targeted else None
    message = {"alert_title": "Synthetic usage", "marker": uuid4().hex}
    if target:
        message["uuid"] = target
    with (
        subscriber(relay_server, org, owner, target) as own,
        subscriber(relay_server, other_org, other_owner, target) as other,
    ):
        # Callers pass the organization as a UUID or as its string form.
        result = call_websocket(
            org.pk if organization_id == "uuid" else str(org.pk),
            message,
            send_to_uuid=targeted,
            uuid=target,
        )
        assert result["status"] is True, result
        assert json.loads(own.recv(timeout=10)) == message
        assert receive_optional(other) is None
