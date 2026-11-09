"""A Console agent's learning curves (learning-service P1-09): the route, two more tenants, and
a stand-in for the ClickHouse read where a test is about the walls and not the numbers.

Unless a test says otherwise:
- every org is admitted to the Console product only (the route has no Deployments wall);
- the release declares the ``learning_curves`` part (off by default; tests that prove the
  default clear it).
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from omega_rollout import products as rollout_products
from omega_rollout import readiness
from omega_rollout.testing import admit_for_tests, declare_released
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tracer.models.project import Project
from tracer.services.omega_agents.curves.aggregate import AgentRun
from tracer.tests.omega_deployments.conftest import _member, client_for

CURVES_URL = "/tracer/omega/agents/{}/curves/"
READ = "tracer.views.omega_agent_curves.read_agent_runs"


def curves_url(agent_id: str) -> str:
    return CURVES_URL.format(agent_id)


def new_agent_id() -> str:
    """An id in EE's shape for a Console agent (``service.ts`` ``newId("workforce_agent")``)."""
    return f"workforce_agent_{uuid.uuid4()}"


def read_curves(client, agent_id, project_id, **params):
    query = {"project_id": str(project_id), **params}
    return client.get(curves_url(agent_id), query)


def agent_project(organization, workspace, name=None, trace_type="observe"):
    """An Observe project of ``workspace`` such as P1-08's ensure makes for an agent."""
    return Project.no_workspace_objects.create(
        name=name or f"agent/support-bot-{uuid.uuid4().hex[:12]}",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type=trace_type,
    )


def run(**fields) -> AgentRun:
    """One run row as the ClickHouse read returns it, for tests about the walls."""
    values = {
        "trace_id": str(uuid.uuid4()),
        "task_id": f"task_{uuid.uuid4().hex[:8]}",
        "version": 1,
        "started_at": datetime.now(UTC) - timedelta(hours=1),
        "latency_ms": 1000,
        "approval_wait_ms": 0,
        "status": "OK",
        "llm_spans": 1,
        "unpriced_llm_spans": 0,
        "cost_micros": 1500,
    }
    values.update(fields)
    return AgentRun(**values)


class RecordingRead:
    """Stands in for ``read_agent_runs``: records each call, answers ``runs``."""

    def __init__(self, runs=(), error=None):
        self.runs = list(runs)
        self.error = error
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return list(self.runs)


@pytest.fixture(autouse=True)
def learning_curves_released(settings):
    declare_released(settings, [readiness.LEARNING_CURVES])


@pytest.fixture(autouse=True)
def console_admitted(organization):
    admit_for_tests(organization, products=[rollout_products.CONSOLE])


@pytest.fixture
def project_a(organization, workspace):
    return agent_project(organization, workspace)


@pytest.fixture
def recording_read(monkeypatch):
    read = RecordingRead(runs=[run()])
    monkeypatch.setattr(READ, read)
    return read


@pytest.fixture
def second_workspace(organization, user):
    """Tenant B in the same org: its own workspace, the same owner."""
    workspace = Workspace.no_workspace_objects.create(
        name="Second Workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    _member(user, workspace, organization)
    return workspace


@pytest.fixture
def second_workspace_client(user, second_workspace):
    client = client_for(user, second_workspace)
    yield client
    client.stop_workspace_injection()


@pytest.fixture
def other_org(db):
    """Tenant B in another org, admitted to the Console too."""
    clear_workspace_context()
    organization = Organization.objects.create(name="Other Organization")
    set_workspace_context(organization=organization)
    user = User.objects.create_user(
        email=f"other-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name="Other User",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
    )
    workspace = Workspace.no_workspace_objects.create(
        name="Other Workspace",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    _member(user, workspace, organization)
    admit_for_tests(organization, products=[rollout_products.CONSOLE])
    clear_workspace_context()
    return organization, user, workspace


@pytest.fixture
def other_org_client(other_org):
    _, user, workspace = other_org
    client = client_for(user, workspace)
    yield client
    client.stop_workspace_injection()
