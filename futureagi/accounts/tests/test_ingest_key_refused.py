"""The ``ingest`` key writes spans to the collector and nothing else.

It is handed to a customer's own, untrusted process, so every pair
authentication of the platform must refuse it, and each agent of a workspace
holds one.
"""

import base64
import uuid
from unittest.mock import MagicMock

import pytest
from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed

from accounts.authentication import LangfuseBasicAuthentication
from accounts.models.user import OrgApiKey
from accounts.models.workspace import Workspace

USER_INFO_URL = "/accounts/user-info/"


def _ingest_key(organization, workspace, *, agent_id=None, **extra):
    return OrgApiKey.no_workspace_objects.create(
        name="simulation_export_key",
        organization=organization,
        workspace=workspace,
        type="ingest",
        user=None,
        simulation_agent_id=uuid.uuid4() if agent_id is None else agent_id,
        **extra,
    )


def _system_key(organization, workspace):
    return OrgApiKey.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        type="system",
    )


def _basic_request(key):
    # Built as test_multi_org_auth builds one: a bare mock with no auto-attributes.
    encoded = base64.b64encode(f"{key.api_key}:{key.secret_key}".encode()).decode(
        "ascii"
    )
    request = MagicMock(spec=[])
    request.method = "GET"
    request.path = "/api/test/"
    request.headers = {}
    request.GET = {}
    request.META = {"HTTP_AUTHORIZATION": f"Basic {encoded}"}
    return request


@pytest.mark.integration
@pytest.mark.django_db
def test_ingest_key_is_refused_by_the_api_key_class(
    api_client, user, organization, workspace
):
    ingest = _ingest_key(organization, workspace)
    system = _system_key(organization, workspace)

    refused = api_client.get(
        USER_INFO_URL,
        HTTP_X_API_KEY=ingest.api_key,
        HTTP_X_SECRET_KEY=ingest.secret_key,
    )
    assert refused.status_code == status.HTTP_401_UNAUTHORIZED
    assert "ingest keys cannot call the API" in refused.content.decode()

    accepted = api_client.get(
        USER_INFO_URL,
        HTTP_X_API_KEY=system.api_key,
        HTTP_X_SECRET_KEY=system.secret_key,
    )
    assert accepted.status_code == status.HTTP_200_OK


@pytest.mark.integration
@pytest.mark.django_db
@pytest.mark.parametrize("with_user", [False, True], ids=["no_user", "user_attached"])
def test_ingest_key_is_refused_by_langfuse_basic_and_mcp(
    user, organization, workspace, with_user
):
    from mcp_server import mcp_app

    ingest = _ingest_key(organization, workspace)
    system = _system_key(organization, workspace)
    if with_user:
        # Nothing keeps an ingest key user-less (the admin exposes the field),
        # so the refusal must come from the key's type, not from a missing user.
        ingest.user = user
        ingest.save(update_fields=["user"])

    with pytest.raises(AuthenticationFailed, match="ingest keys cannot call the API"):
        LangfuseBasicAuthentication().authenticate(_basic_request(ingest))
    assert (
        mcp_app._authenticate_and_set_context(ingest.api_key, ingest.secret_key) is None
    )

    # The same calls with the system pair succeed, so the refusals above do not
    # come from a badly built request.
    authenticated_user, _ = LangfuseBasicAuthentication().authenticate(
        _basic_request(system)
    )
    assert authenticated_user == user
    try:
        context = mcp_app._authenticate_and_set_context(
            system.api_key, system.secret_key
        )
        assert context is not None
        assert context.user == user
    finally:
        mcp_app._clear_context()


@pytest.mark.integration
@pytest.mark.django_db
def test_one_ingest_key_per_organisation_workspace_and_agent(
    user, organization, workspace
):
    agent_id = uuid.uuid4()
    first = _ingest_key(organization, workspace, agent_id=agent_id)

    with pytest.raises(IntegrityError), transaction.atomic():
        _ingest_key(organization, workspace, agent_id=agent_id)

    # A second agent of the same workspace gets its own key.
    second_agent = _ingest_key(organization, workspace, agent_id=uuid.uuid4())
    assert second_agent.id != first.id

    # Other key types are untouched by the constraint: two of them naming the
    # same agent in the same workspace, beside the ingest key, do not collide.
    _system_key(organization, workspace)
    for _ in range(2):
        OrgApiKey.no_workspace_objects.create(
            organization=organization,
            workspace=workspace,
            type="user",
            user=user,
            simulation_agent_id=agent_id,
        )

    other_workspace = Workspace.objects.create(
        name="Second Workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    _ingest_key(organization, other_workspace, agent_id=agent_id)

    OrgApiKey.no_workspace_objects.filter(id=first.id).update(deleted=True)
    replacement = _ingest_key(organization, workspace, agent_id=agent_id)
    assert replacement.id != first.id
    assert (
        OrgApiKey.all_objects.filter(
            organization=organization,
            workspace=workspace,
            type="ingest",
            simulation_agent_id=agent_id,
        ).count()
        == 2
    )


@pytest.mark.integration
@pytest.mark.django_db
def test_ingest_keys_without_an_agent_do_not_block_each_other(
    organization, workspace
):
    # Keys from before the column existed carry no agent; NULLs never collide.
    first = OrgApiKey.no_workspace_objects.create(
        organization=organization, workspace=workspace, type="ingest"
    )
    second = OrgApiKey.no_workspace_objects.create(
        organization=organization, workspace=workspace, type="ingest"
    )
    with_agent = _ingest_key(organization, workspace)

    assert len({first.id, second.id, with_agent.id}) == 3
    assert first.simulation_agent_id is None
