"""Two tenants for isolation tests.

Each tenant is an organization with a default workspace, an owner in it and
an API client bound to that workspace. Rows are created with no workspace
context bound, as an API request would find them.
"""

import uuid
from dataclasses import dataclass

from rest_framework.test import APIClient

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from conftest import WorkspaceAwareAPIClient
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context


@dataclass
class Tenant:
    organization: Organization
    workspace: Workspace
    user: User
    client: WorkspaceAwareAPIClient


def make_tenant(label):
    """Create one tenant; ``label`` only makes its names readable."""
    clear_workspace_context()
    organization = Organization.objects.create(name=f"Tenant {label}")
    user = User.objects.create_user(
        email=f"tenant-{label.lower()}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name=f"Tenant {label} Owner",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    workspace = Workspace.no_workspace_objects.create(
        name=f"Tenant {label} Workspace",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    client = WorkspaceAwareAPIClient()
    client.force_authenticate(user=user)
    client.set_workspace(workspace)
    clear_workspace_context()
    return Tenant(organization, workspace, user, client)


def signed_in_client(email, password):
    """An API client signed in through the login endpoint, as a browser is:
    authentication resolves the organization and workspace for each request."""
    client = APIClient()
    login = client.post(
        "/accounts/token/", {"email": email, "password": password}, format="json"
    )
    assert login.status_code == 200, login.content
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}")
    clear_workspace_context()
    return client


def former_member(tenant, label, password="formerpass123"):
    """A member of ``tenant`` whom its owner removed through the API.

    Removal deactivates the memberships and leaves the user's legacy
    ``organization`` field naming the tenant's organization. Returns the
    user and a client signed in as them.
    """
    clear_workspace_context()
    user = User.objects.create_user(
        email=f"former-{label.lower()}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password=password,
        name=f"Former {label} Member",
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
        role="Workspace Viewer",
        level=Level.WORKSPACE_VIEWER,
        is_active=True,
        organization_membership=membership,
    )
    removed = tenant.client.delete(
        "/accounts/organization/members/remove/",
        {"user_id": str(user.id)},
        format="json",
    )
    assert removed.status_code == 200, removed.content
    clear_workspace_context()
    user.refresh_from_db()
    membership.refresh_from_db()
    assert membership.is_active is False
    assert user.organization_id == tenant.organization.id
    return user, signed_in_client(user.email, password)


def same_answer(response_a, id_a, response_b, id_b):
    """True when two responses differ only by the id each one names."""
    if response_a.status_code != response_b.status_code:
        return False
    body_a = response_a.content.decode().replace(str(id_a), "<id>")
    body_b = response_b.content.decode().replace(str(id_b), "<id>")
    return body_a == body_b
