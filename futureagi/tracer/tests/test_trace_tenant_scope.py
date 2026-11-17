"""POST /tracer/trace/ resolves a project only within the caller's organization.

A signed-in user who belongs to no organization owns no project, so every
project id answers like one that does not exist.
"""

import uuid

import pytest
from rest_framework import status

import tracer.serializers.trace  # noqa: F401  (imported with no workspace bound)
import tracer.views.trace  # noqa: F401
from accounts.models.user import User
from conftest import WorkspaceAwareAPIClient
from model_hub.models.ai_model import AIModel
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.tests.two_tenants import make_tenant, same_answer
from tracer.models.project import Project
from tracer.models.trace import Trace

URL = "/tracer/trace/"


@pytest.fixture
def setup(db):
    tenant_a = make_tenant("A")
    clear_workspace_context()
    project = Project.no_workspace_objects.create(
        name="A project",
        organization=tenant_a.organization,
        workspace=tenant_a.workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    loner = User.objects.create_user(
        email=f"no-org-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="No Organization",
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=loner)
    yield {"a": tenant_a, "a_project": project, "client": client}
    tenant_a.client.stop_workspace_injection()


@pytest.mark.integration
@pytest.mark.api
def test_trace_create_without_organization_rejects_other_organization_project(setup):
    foreign_id = setup["a_project"].id
    unknown_id = uuid.uuid4()

    foreign = setup["client"].post(
        URL, {"project": str(foreign_id), "name": "trace"}, format="json"
    )
    unknown = setup["client"].post(
        URL, {"project": str(unknown_id), "name": "trace"}, format="json"
    )

    assert foreign.status_code == status.HTTP_400_BAD_REQUEST, foreign.content
    assert same_answer(foreign, foreign_id, unknown, unknown_id)
    assert not Trace.no_workspace_objects.filter(project_id=foreign_id).exists()
