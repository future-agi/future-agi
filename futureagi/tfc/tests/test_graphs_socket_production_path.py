"""The graphs socket answers only for the caller's projects.

``ws/graphs/`` takes a ``projectId`` (and evaluation ids) from each frame. A
frame naming a project of another organization must get the answer a frame
naming an unknown id gets, before any query runs. Driven through
``tfc.asgi.application`` with a token from ``/accounts/token/``, as the browser
connects.
"""

import ipaddress
import itertools
import uuid
from types import SimpleNamespace

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import WorkspaceMembership
from tfc.constants.levels import Level
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.tests.production_path import (
    PASSWORD,
    make_tenant,
    use_production_request_settings,
)
from tracer.models.project import Project

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]

_ADDRESSES = itertools.count(1)


def _project(tenant, name):
    clear_workspace_context()
    project = Project.no_workspace_objects.create(
        name=name,
        organization=tenant.organization,
        workspace=tenant.workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    clear_workspace_context()
    return project


def _token(email):
    address = str(ipaddress.ip_address("198.19.128.0") + next(_ADDRESSES))
    client = APIClient(enforce_csrf_checks=True, REMOTE_ADDR=address)
    login = client.post(
        "/accounts/token/", {"email": email, "password": PASSWORD}, format="json"
    )
    assert login.status_code == status.HTTP_200_OK, login.content
    return login.json()["access"]


def _member_of(*tenants):
    """An account with an active membership in each tenant's organization
    (and its default workspace); its organization field names the first."""
    clear_workspace_context()
    user = User.objects.create_user(
        email=f"member-of-{len(tenants)}-{uuid.uuid4().hex[:8]}@example.com",
        password=PASSWORD,
        name="Member of several organizations",
        organization=tenants[0].organization,
        organization_role="Member",
    )
    for tenant in tenants:
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


@pytest.fixture
def graphs(transactional_db, settings, monkeypatch):
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    use_production_request_settings(settings)
    a = make_tenant("A")
    b = make_tenant("B")
    yield SimpleNamespace(
        a_project=_project(a, "A project"),
        b_project=_project(b, "B project"),
        b_token=_token(b.user.email),
        ab_token=_token(_member_of(a, b).email),
    )
    clear_workspace_context()


async def _answer(token, project_id):
    """The close code the socket answers a frame with, or None if it stays
    open.

    ``receive_output`` cancels the consumer when it times out, so an open
    socket is told apart with ``receive_nothing`` first; the consumer then
    still runs when the client disconnects."""
    from saml2_auth.tests.ws_helpers import WebsocketCommunicator
    from tfc.asgi import application

    communicator = WebsocketCommunicator(application, f"/ws/graphs/?token={token}")
    connected, detail = await communicator.connect(timeout=5)
    assert connected, detail
    try:
        await communicator.send_json_to({"projectId": str(project_id), "graph": ""})
        if await communicator.receive_nothing(timeout=2.0):
            return None
        message = await communicator.receive_output(timeout=1.0)
        return message.get("code") if message.get("type") == "websocket.close" else None
    finally:
        await communicator.disconnect()


@pytest.mark.asyncio
async def test_graphs_socket_answers_only_for_the_callers_projects(graphs):
    """B's member names A's project, then an unknown id: both get the same
    answer, and it comes before any query runs."""
    foreign = await _answer(graphs.b_token, graphs.a_project.id)
    unknown = await _answer(graphs.b_token, uuid.uuid4())

    assert foreign == unknown == 4003


@pytest.mark.asyncio
async def test_graphs_socket_keeps_answering_for_the_callers_own_project(graphs):
    """Control: B's own project is accepted, as before."""
    assert await _answer(graphs.b_token, graphs.b_project.id) is None


@pytest.mark.asyncio
async def test_graphs_socket_answers_a_member_of_two_organizations_for_both(graphs):
    """Control: an active member of A and B is answered for a project of
    either organization, whichever membership the socket resolved first."""
    assert await _answer(graphs.ab_token, graphs.a_project.id) is None
    assert await _answer(graphs.ab_token, graphs.b_project.id) is None
