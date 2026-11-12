"""Two tenants for the Observe link tests: a second workspace in the same org, and another org.

Every org here is admitted to the Deployments product the way a staff entry is, except where
a test removes the admission to prove the rollout wall.
"""

import uuid
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from omega_rollout import products as rollout_products
from omega_rollout.testing import admit_for_tests
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

ENSURE_URL = "/tracer/omega/deployments/{}/observe/"


def ensure_url(deployment_id: str) -> str:
    return ENSURE_URL.format(deployment_id)


class CloudPlan:
    """A Cloud deployment whose plan allows ``limit`` monitors per org (-1: unlimited).

    Only the deployment mode, the plan's number and the upgrade offer are stood in;
    ``Entitlements.can_create`` and ``check_ee_can_create`` run as they do on Cloud.
    Change ``limit`` to model an upgrade.
    """

    UPGRADE_CTA = {"text": "Add Boost", "plan": "boost"}

    def __init__(self, limit: int):
        self.limit = limit
        self.limit_reads = 0

    def get_limit(self, org_id, feature):
        assert feature == "monitors", feature
        self.limit_reads += 1
        return self.limit


@contextmanager
def cloud_plan(limit: int):
    from ee.usage.deployment import DeploymentMode
    from ee.usage.schemas.events import UpgradeCTA
    from ee.usage.services import entitlements

    plan = CloudPlan(limit)
    with (
        patch.object(DeploymentMode, "is_cloud", staticmethod(lambda: True)),
        patch.object(
            entitlements.Entitlements, "get_limit", staticmethod(plan.get_limit)
        ),
        patch.object(
            entitlements,
            "_find_upgrade_cta",
            lambda org_id, feature: UpgradeCTA(**CloudPlan.UPGRADE_CTA),
        ),
    ):
        yield plan


def new_deployment_id() -> str:
    """An id in EE's shape (``resources.ts``): ``dep-`` and 16 hex digits."""
    return f"dep-{uuid.uuid4().hex[:16]}"


def _member(user, workspace, organization):
    org_membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": org_membership,
        },
    )


def client_for(user, workspace):
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    return client


@pytest.fixture(autouse=True)
def deployments_admitted(organization):
    admit_for_tests(organization, products=[rollout_products.DEPLOYMENTS])


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
    """Tenant B in another org, admitted to Deployments too."""
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
    admit_for_tests(organization, products=[rollout_products.DEPLOYMENTS])
    clear_workspace_context()
    return organization, user, workspace


@pytest.fixture
def other_org_client(other_org):
    _, user, workspace = other_org
    client = client_for(user, workspace)
    yield client
    client.stop_workspace_injection()
