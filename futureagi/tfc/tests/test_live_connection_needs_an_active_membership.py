"""A live connection opens only for an active member of its organization.

The web app opens its live connection (``ws/connect/``) in two steps: the
signed-in person asks ``handoff/ws-ticket/`` for a one-time ticket, then
connects with that ticket. These tests take both steps on the production
request path (sign-in through ``/accounts/token/``, the application in
``tfc.asgi``) and read what follows the connect, so an accept alone never
counts as an open connection. Each row ends in one of three answers:

- the ticket request is refused, with its status and code;
- the connection is closed, with its close code and whether an accept came
  first;
- the connection stays open: no close arrives within a bounded wait.

The organization groups a connection is in are read from what it receives. A
message sent to an organization's group (``org_<id>``, the group the consumer
adds a connection to) is first received by an open connection of that
organization's owner; a connection that then receives nothing is not in that
group.
"""

import asyncio
import ipaddress
import itertools
import json
import uuid
from dataclasses import dataclass, replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from channels.layers import get_channel_layer
from django.db.models import Q
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models.organization_invite import InviteStatus, OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import WorkspaceMembership
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.tenant_scope import has_membership_rows
from tfc.utils.tests.production_path import (
    PASSWORD,
    make_tenant,
    removed_member,
    sign_in,
    use_production_request_settings,
)

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]

# The web app's origin: the one origin that may ask for a ticket.
APP_ORIGIN = "https://app.example.com"
ROUTE = "/ws/connect/"

REFUSED = "the ticket request is refused"
CLOSED = "the connection is closed"
OPEN = "the connection stays open"

# The consumer's close for a person with no organization.
NO_ORGANIZATION = 4002
# The socket sign-in's close for a connect without one valid ticket.
NO_TICKET = 4003

# How long a connection is watched for a frame that does not come.
QUIET_SECONDS = 1.0
# How long a frame that comes may take.
FRAME_SECONDS = 10.0

WITH_WORKSPACE = "with-workspace"
ORGANIZATION_ONLY = "organization-only"

# Separate browsers, separate addresses (198.19.192.0 onward lies in the
# 198.18.0.0/15 test range); the sign-in monitor counts per address.
_ADDRESSES = itertools.count(1)


def _address():
    return str(ipaddress.ip_address("198.19.192.0") + next(_ADDRESSES))


@dataclass(frozen=True)
class Answer:
    """What a person gets when the web app opens its live connection."""

    outcome: str
    # The ticket request's status, when it is refused.
    status: int | None = None
    # The refusal's code, or the close code.
    code: object = None
    # Whether an accept came before the close.
    accepted: bool = False
    # The organization groups whose messages reach the connection.
    groups: frozenset = frozenset()


def _group(organization):
    """The group the consumer adds a connection in ``organization`` to."""
    return f"org_{organization.id}"


@pytest.fixture
def world(transactional_db, settings, monkeypatch):
    """Organizations A and B, each with its owner signed in, on the production
    request path.

    Tickets are kept in a local cache of their own (the ticket request writes
    one; the connection, served on another thread, reads it), and every
    connection shares one in-memory channel layer.
    """
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    use_production_request_settings(settings)
    settings.APP_BASE_URL = APP_ORIGIN
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": f"live-connection-{uuid.uuid4()}",
        }
    }
    settings.CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
    }
    a = make_tenant("A")
    b = make_tenant("B")
    yield SimpleNamespace(
        a=a,
        b=b,
        owners={
            _group(a.organization): sign_in(a.user.email),
            _group(b.organization): sign_in(b.user.email),
        },
    )
    clear_workspace_context()


# ------------------------------------------------------------------ accounts


def _active_member(tenant):
    """An active member of ``tenant``'s organization and of its workspace."""
    clear_workspace_context()
    user = User.objects.create_user(
        email=f"member-{uuid.uuid4().hex[:8]}@example.com",
        password=PASSWORD,
        name="Active Member",
        organization=tenant.organization,
        organization_role="Member",
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=tenant.organization,
        role="Member",
        level=Level.MEMBER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=tenant.workspace,
        role="Workspace Member",
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
        organization_membership=membership,
    )
    clear_workspace_context()
    return user


def _account_older_than_memberships(tenant):
    """An account whose organization field names ``tenant``'s organization
    and that has no membership row in any state."""
    clear_workspace_context()
    user = User.objects.create_user(
        email=f"older-{uuid.uuid4().hex[:8]}@example.com",
        password=PASSWORD,
        name="Older Account",
        organization=tenant.organization,
        organization_role="Member",
    )
    clear_workspace_context()
    assert not has_membership_rows(user)
    return user


def _set_a_password_through_the_reset_link(email):
    """Ask for a reset link and set the password with the link the e-mail
    carries, as the sign-in page does."""
    visitor = APIClient(enforce_csrf_checks=True, REMOTE_ADDR=_address())
    with (
        patch("accounts.views.signup.is_oss", return_value=False),
        patch("accounts.views.signup.email_helper") as mailed,
    ):
        asked = visitor.post(
            "/accounts/password-reset-initiate/", {"email": email}, format="json"
        )
    assert asked.status_code == status.HTTP_200_OK, asked.status_code
    link = mailed.call_args.args[2]
    confirmed = visitor.post(
        f"/accounts/password-reset-confirm/{link['uid']}/{link['token']}/",
        {"new_password": PASSWORD, "repeat_password": PASSWORD},
        format="json",
    )
    assert confirmed.status_code == status.HTTP_200_OK, confirmed.status_code
    clear_workspace_context()


def _cancelled_invitee(world, invitation):
    """A person A's owner invited and then uninvited, who afterwards set a
    password through the reset link.

    The account's organization field still names A, and its membership rows
    in A are all inactive and removed. ``with-workspace`` invites to A's
    workspace, as the web app's invite dialog does; ``organization-only``
    invites to A alone.
    """
    owner = sign_in(world.a.user.email, workspace=world.a.workspace)
    email = f"invitee-{uuid.uuid4().hex[:8]}@example.com"
    body = {"emails": [email], "org_level": Level.MEMBER}
    if invitation == WITH_WORKSPACE:
        body["workspace_access"] = [
            {"workspace_id": str(world.a.workspace.id), "level": Level.WORKSPACE_MEMBER}
        ]
    invited = owner.post("/accounts/organization/invite/", body, format="json")
    assert invited.status_code == status.HTTP_200_OK, invited.status_code
    clear_workspace_context()
    invite = OrganizationInvite.objects.get(
        organization=world.a.organization,
        target_email=email,
        status=InviteStatus.PENDING,
    )
    cancelled = owner.delete(
        "/accounts/organization/invite/cancel/",
        {"invite_id": str(invite.id)},
        format="json",
    )
    assert cancelled.status_code == status.HTTP_200_OK, cancelled.status_code
    clear_workspace_context()

    invitee = User.objects.get(email=email)
    assert invitee.organization_id == world.a.organization.id
    rows = OrganizationMembership._base_manager.filter(user=invitee)
    assert rows.exists()
    assert not rows.filter(Q(is_active=True) | Q(deleted=False)).exists()
    workspace_rows = WorkspaceMembership._base_manager.filter(user=invitee)
    assert workspace_rows.exists() == (invitation == WITH_WORKSPACE)
    assert not workspace_rows.filter(is_active=True).exists()

    _set_a_password_through_the_reset_link(email)
    invitee.refresh_from_db()
    assert invitee.is_active
    # Setting a password makes no membership row active.
    assert not rows.filter(is_active=True).exists()
    return invitee


def _session_token(email):
    """The session (access) token the sign-in answers with."""
    client = APIClient(enforce_csrf_checks=True, REMOTE_ADDR=_address())
    login = client.post(
        "/accounts/token/", {"email": email, "password": PASSWORD}, format="json"
    )
    assert login.status_code == status.HTTP_200_OK, login.status_code
    clear_workspace_context()
    return login.json()["access"]


# ----------------------------------------------------------- the connection


def _refusal_code(response):
    """The code a refusal names (never its body)."""
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    return body.get("code") or body.get("error")


def _ticket(client):
    """Ask for a ticket for the live connection, as the web app does.

    Returns ``(ticket, None)``, or ``(None, answer)`` when it is refused.
    """
    response = client.post(
        reverse("handoff-ws-ticket"),
        {"path": ROUTE, "query": {}},
        format="json",
        HTTP_ORIGIN=APP_ORIGIN,
    )
    clear_workspace_context()
    if response.status_code != status.HTTP_200_OK:
        refusal = Answer(
            REFUSED, status=response.status_code, code=_refusal_code(response)
        )
        return None, refusal
    return response.json()["ticket"], None


async def _first_answer(connection):
    """What follows the connect: a close (in place of the accept, or after
    it), or no frame within the quiet wait, which leaves it open."""
    accepted, code = await connection.connect(timeout=FRAME_SECONDS)
    if not accepted:
        return Answer(CLOSED, code=code)
    if await connection.receive_nothing(timeout=QUIET_SECONDS):
        return Answer(OPEN, accepted=True)
    frame = await connection.receive_output(timeout=FRAME_SECONDS)
    assert frame["type"] == "websocket.close", frame["type"]
    return Answer(CLOSED, code=frame.get("code"), accepted=True)


async def _received(connection, probe):
    """Whether ``probe`` reaches an open ``connection`` within the quiet wait."""
    if await connection.receive_nothing(timeout=QUIET_SECONDS):
        return False
    frame = await connection.receive_output(timeout=FRAME_SECONDS)
    assert frame["type"] == "websocket.send", frame["type"]
    assert json.loads(frame["text"]) == probe
    return True


async def _connect(connection, query, owner_tickets):
    """Open each owner's connection, then the person's, and send one message
    to each organization's group. Returns the person's answer, with the
    groups whose message reached it."""
    owners = {}
    for group, ticket in owner_tickets.items():
        owner = connection(f"ticket={ticket}")
        assert await _first_answer(owner) == Answer(OPEN, accepted=True), group
        owners[group] = owner

    person = connection(query)
    answer = await _first_answer(person)

    layer = get_channel_layer()
    reached = set()
    for group, owner in owners.items():
        probe = {"type": "membership_probe", "nonce": uuid.uuid4().hex}
        await layer.group_send(group, {"type": "send_data", "data": probe})
        # The message is in the group: the owner's connection receives it.
        frame = await owner.receive_output(timeout=FRAME_SECONDS)
        assert frame["type"] == "websocket.send", (group, frame["type"])
        assert json.loads(frame["text"]) == probe, group
        if answer.outcome == CLOSED:
            # A closed connection is only watched, never awaited on: what its
            # application does after the close stays out of this answer.
            await asyncio.sleep(QUIET_SECONDS)
            if not person.output_queue.empty():
                reached.add(group)
        elif await _received(person, probe):
            reached.add(group)

    for owner in owners.values():
        await owner.disconnect(timeout=FRAME_SECONDS)
    if answer.outcome == OPEN:
        await person.disconnect(timeout=FRAME_SECONDS)
    return replace(answer, groups=frozenset(reached))


def _open_live_connection(world, client=None, *, query=None):
    """What the person signed in on ``client`` gets when the web app opens its
    live connection. ``query`` connects with that query in place of a
    ticket."""
    from saml2_auth.tests.ws_helpers import WebsocketCommunicator
    from tfc.asgi import application

    if query is None:
        ticket, refusal = _ticket(client)
        if refusal is not None:
            return refusal
        query = f"ticket={ticket}"
    owner_tickets = {}
    for group, owner in world.owners.items():
        ticket, refusal = _ticket(owner)
        assert refusal is None, refusal
        owner_tickets[group] = ticket
    clear_workspace_context()

    def connection(socket_query):
        return WebsocketCommunicator(application, f"{ROUTE}?{socket_query}")

    return asyncio.run(_connect(connection, query, owner_tickets))


# ---------------------------------------------------------------------- rows


@pytest.mark.parametrize("invitation", [WITH_WORKSPACE, ORGANIZATION_ONLY])
def test_a_cancelled_invitation_opens_no_live_connection(world, invitation):
    """Invited (to A's workspace, or to A alone), uninvited, then a password
    set through the reset link: the connection is closed with 4002 and is in
    no organization's group."""
    invitee = _cancelled_invitee(world, invitation)

    answer = _open_live_connection(world, sign_in(invitee.email))

    assert answer.outcome == CLOSED, answer
    assert answer.code == NO_ORGANIZATION, answer
    assert answer.groups == frozenset(), answer


def test_an_active_member_has_a_live_connection_in_its_own_organization(world):
    """An active member of A: the connection stays open and is in A's group
    alone."""
    member = _active_member(world.a)

    answer = _open_live_connection(world, sign_in(member.email))

    assert answer.outcome == OPEN, answer
    assert answer.groups == {_group(world.a.organization)}, answer


def test_a_removed_member_opens_no_live_connection(world):
    """A member whom A's owner removed, signed in afterwards: the connection
    is closed with 4002 and is in no organization's group."""
    _, client = removed_member(world.a, "A")

    answer = _open_live_connection(world, client)

    assert answer.outcome == CLOSED, answer
    assert answer.code == NO_ORGANIZATION, answer
    assert answer.groups == frozenset(), answer


def test_an_account_older_than_memberships_keeps_its_live_connection(world):
    """An account with no membership row in any state, whose organization
    field names B: the connection stays open and is in B's group alone."""
    account = _account_older_than_memberships(world.b)
    client = sign_in(account.email)
    assert not has_membership_rows(account)

    answer = _open_live_connection(world, client)

    assert answer.outcome == OPEN, answer
    assert answer.groups == {_group(world.b.organization)}, answer


def test_a_session_token_in_place_of_a_ticket_is_closed_after_the_accept(world):
    """An active member of A connects with its session token in place of a
    ticket: an accept, then a close with 4003, and no organization's group."""
    member = _active_member(world.a)
    token = _session_token(member.email)

    answer = _open_live_connection(world, query=f"token={token}")

    assert answer.outcome == CLOSED, answer
    assert answer.accepted is True, answer
    assert answer.code == NO_TICKET, answer
    assert answer.groups == frozenset(), answer
