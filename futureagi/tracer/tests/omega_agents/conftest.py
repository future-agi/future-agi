"""Two tenants for the Console agent Observe link tests (learning-service P1-08): a second
workspace in the same org, and another org.

Unless a test says otherwise:
- every org is admitted to the Console product only, never to Deployments (the agent route
  has no Deployments wall);
- the release declares the ``agent_observe`` part (off by default; tests that prove the
  default clear it);
- the deployment runs as hosted Future AGI whose plan includes Error Feed, so an agent's
  mistakes are analysed (``hosted`` and ``capabilities`` set another plan or flavor).
"""

import json
import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from omega_rollout import products as rollout_products
from omega_rollout import readiness
from omega_rollout.testing import admit_for_tests, declare_released
from tfc.capabilities import service as capability_service
from tfc.constants.roles import OrganizationRoles
from tfc.licensing.types import DeploymentFlavor, DeploymentLocation
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tracer.models.monitor import UserAlertMonitor
from tracer.models.omega_deployment_link import OmegaDeploymentObserveLink
from tracer.models.project import Project
from tracer.models.trace_scan import TraceScanConfig
from tracer.tests.omega_deployments.conftest import _member, client_for

AGENT_URL = "/tracer/omega/agents/{}/observe/"

# Everything the agent route can write.
STORES = (OmegaDeploymentObserveLink, Project, UserAlertMonitor, TraceScanConfig)


def all_rows():
    """Every row of every store the agent route writes, in every workspace, soft-deleted
    rows included.

    A test that claims "nothing is written" compares this before and after the call.
    Rows other tests committed in the same process (a session fixture's org, workspace
    and project) are in both snapshots, so the claim holds whatever ran before.
    """
    return {
        model.__name__: sorted(
            json.dumps(row, default=str, sort_keys=True)
            for row in model._base_manager.all().values()
        )
        for model in STORES
    }


def agent_url(agent_id: str) -> str:
    return AGENT_URL.format(agent_id)


def new_agent_id() -> str:
    """An id in EE's shape for a Console agent (``service.ts`` ``newId("workforce_agent")``)."""
    return f"workforce_agent_{uuid.uuid4()}"


def ensure(client, agent_id, name="Support Bot"):
    return client.post(agent_url(agent_id), {"name": name}, format="json")


def unlink(client, agent_id):
    return client.delete(agent_url(agent_id))


class Plan:
    """A hosted plan resolver (``tfc.capabilities.service.CloudPlanResolver``)."""

    def __init__(self, features):
        self.features = frozenset(features)

    def has_feature(self, org_id, feature_id):
        return feature_id in self.features

    def get_upgrade_cta(self, org_id, feature_id):
        return None


@contextmanager
def capabilities(flavor, location, features=()):
    """Run the capability service as ``flavor`` at ``location``, then put back what was there."""
    saved = (
        capability_service._deployment_flavor,
        capability_service._deployment_location,
        capability_service._license_resolver,
        capability_service._cloud_plan_resolver,
        capability_service._configured,
    )
    capability_service.configure(
        flavor=flavor, location=location, cloud_plan_resolver=Plan(features)
    )
    try:
        # No plan rows: on hosted only the plan resolver above decides.
        with patch(
            "ee.usage.services.entitlements.Entitlements.get_entitlement",
            return_value=None,
        ):
            yield
    finally:
        capability_service.configure(
            flavor=saved[0],
            location=saved[1],
            license_resolver=saved[2],
            cloud_plan_resolver=saved[3],
        )
        capability_service._configured = saved[4]


def hosted(*features):
    """Hosted Future AGI whose plan has exactly ``features``."""
    return capabilities(DeploymentFlavor.CLOUD, DeploymentLocation.CLOUD, features)


@pytest.fixture(autouse=True)
def agent_observe_released(settings):
    declare_released(settings, [readiness.AGENT_OBSERVE])


@pytest.fixture(autouse=True)
def console_admitted(organization):
    admit_for_tests(organization, products=[rollout_products.CONSOLE])


@pytest.fixture(autouse=True)
def error_feed_on_plan():
    with hosted("error_feed"):
        yield


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
