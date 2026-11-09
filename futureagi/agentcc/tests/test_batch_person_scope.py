"""Batch reads and cancellation respect key ownership and workspace scope."""

import uuid

import pytest
from django.db import transaction

from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services.service_grants import issue_person_grant
from agentcc.models import AgentccAPIKey, AgentccBatchJob
from agentcc.tests.r7_batch_support import *  # noqa: F403
from agentcc.tests.r7_batch_support import GATEWAY, user_with_level
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

pytestmark = pytest.mark.django_db


def seed_batch(fake, org, owner, workspace, credential):
    batch_id = "batch-" + uuid.uuid4().hex[:12]
    ref = uuid.uuid4()
    key_id = ""
    if credential == "key":
        key_id = "test_only_person_" + uuid.uuid4().hex
        AgentccAPIKey._base_manager.create(
            organization=org,
            user=owner,
            workspace=workspace,
            gateway_key_id=key_id,
            name="test personal batch",
        )
    else:
        with transaction.atomic():
            issue_person_grant(
                kind="batch",
                org=org.pk,
                user=owner.pk,
                workspace=workspace.pk,
                sub="batch:" + str(ref),
                ttl=86400,
            )
    job = AgentccBatchJob.objects.create(
        id=ref,
        organization=org,
        created_by=owner,
        gateway_batch_id=batch_id,
        gateway_key_id=key_id,
        total=1,
    )
    fake.batches[batch_id] = {"org_id": str(org.pk), "status": "processing"}
    return job


def call(client, operation, batch_id):
    if operation == "get":
        return client.get(f"{GATEWAY}/get-batch/", {"batch_id": batch_id})
    return client.post(
        f"{GATEWAY}/cancel-batch/", {"batch_id": batch_id}, format="json"
    )


@pytest.mark.parametrize("credential", ["key", "grant"])
@pytest.mark.parametrize("operation", ["get", "cancel"])
def test_members_can_only_access_their_own_batch(
    organization, workspace, make_client, fake_gateway, credential, operation
):
    first, _ = user_with_level(organization, Level.MEMBER, workspace)
    second, _ = user_with_level(organization, Level.MEMBER, workspace)
    first_job = seed_batch(fake_gateway, organization, first, workspace, credential)
    second_job = seed_batch(fake_gateway, organization, second, workspace, credential)
    client = make_client(first, workspace)
    # created_by is not key ownership: deliberately disagree for both jobs.
    AgentccBatchJob.objects.filter(pk=first_job.pk).update(created_by=second)
    AgentccBatchJob.objects.filter(pk=second_job.pk).update(created_by=first)
    before = list(fake_gateway.calls)
    foreign = call(client, operation, second_job.gateway_batch_id)
    assert foreign.status_code == 404, foreign.content
    assert fake_gateway.calls == before
    own = call(client, operation, first_job.gateway_batch_id)
    assert own.status_code == 200, own.content
    assert len(fake_gateway.named(operation + "_batch")) == 1


@pytest.mark.parametrize("level", [Level.MEMBER, Level.ADMIN, Level.OWNER])
@pytest.mark.parametrize("credential", ["key", "grant"])
@pytest.mark.parametrize("operation", ["get", "cancel"])
def test_workspace_scope_applies_even_to_org_admins(
    organization, workspace, make_client, fake_gateway, level, credential, operation
):
    owner, _ = user_with_level(organization, level, workspace)
    other = Workspace.objects.create(
        name="Other " + uuid.uuid4().hex,
        organization=organization,
        created_by=owner,
    )
    WorkspaceMembership._base_manager.create(
        user=owner,
        workspace=other,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
    )
    job = seed_batch(fake_gateway, organization, owner, other, credential)
    before = list(fake_gateway.calls)
    wrong_workspace = call(
        make_client(owner, workspace), operation, job.gateway_batch_id
    )
    assert wrong_workspace.status_code == 404, wrong_workspace.content
    assert fake_gateway.calls == before
    own = call(make_client(owner, other), operation, job.gateway_batch_id)
    assert own.status_code == 200, own.content
    assert len(fake_gateway.named(operation + "_batch")) == 1


@pytest.mark.parametrize("level", [Level.ADMIN, Level.OWNER])
@pytest.mark.parametrize("credential", ["key", "grant"])
@pytest.mark.parametrize("operation", ["get", "cancel"])
def test_admins_can_access_other_members_batches_in_scope(
    organization, workspace, make_client, fake_gateway, level, credential, operation
):
    owner, _ = user_with_level(organization, Level.MEMBER, workspace)
    admin, _ = user_with_level(organization, level, workspace)
    job = seed_batch(fake_gateway, organization, owner, workspace, credential)
    response = call(make_client(admin, workspace), operation, job.gateway_batch_id)
    assert response.status_code == 200, response.content
    assert len(fake_gateway.named(operation + "_batch")) == 1
