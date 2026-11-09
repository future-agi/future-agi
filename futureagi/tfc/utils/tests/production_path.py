"""Requests on the production request path, for tenant conformance tests.

``tfc.settings.test`` runs a shortened middleware list (no
``WorkspaceContextMiddleware`` among others), and most API tests sign in with
``force_authenticate``, which skips the authentication class that resolves the
caller's organization and workspace. A conformance test built on those never
takes the path a production request takes. This module provides that path:

- :func:`use_production_request_settings` puts ``MIDDLEWARE`` and
  ``REST_FRAMEWORK`` back exactly as ``tfc.settings.settings`` defines them, and
  warms the URLconf the way ``tfc.asgi`` does when a worker starts
  (``warm_http_urlconf``), so view and serializer modules are imported with no
  caller bound;
- :func:`sign_in` signs in through ``/accounts/token/`` and sends the bearer
  token, workspace and organization headers the web app sends.

Tenants are built here without the test client that patches
``APIView.initial``, so authentication alone decides each caller's scope.
"""

import copy
import ipaddress
import itertools
import uuid
from dataclasses import dataclass

from rest_framework.test import APIClient
from rest_framework.views import APIView

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.asgi_startup import warm_http_urlconf
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.settings import settings as production_settings

WORKSPACE_CONTEXT_MIDDLEWARE = (
    "tfc.middleware.workspace_context.WorkspaceContextMiddleware"
)
PASSWORD = "conformance-pass-123"

# Each signed-in client gets its own address (198.18.0.0/15, reserved for
# testing), as separate browsers would: the sign-in monitor counts sign-ins
# per address for the whole process.
_ADDRESSES = itertools.count(1)


def use_production_request_settings(settings):
    """Apply the production request settings to pytest-django's ``settings``.

    Returns the number of URL patterns the warm-up resolved.
    """
    settings.MIDDLEWARE = list(production_settings.MIDDLEWARE)
    settings.REST_FRAMEWORK = copy.deepcopy(production_settings.REST_FRAMEWORK)
    clear_workspace_context()
    patterns = warm_http_urlconf()
    assert patterns > 0
    assert list(settings.MIDDLEWARE) == list(production_settings.MIDDLEWARE)
    assert WORKSPACE_CONTEXT_MIDDLEWARE in settings.MIDDLEWARE
    # No test patch of APIView.initial may inject a workspace: authentication
    # alone resolves the caller.
    assert APIView.initial.__module__ == "rest_framework.views"
    return patterns


@dataclass
class Tenant:
    organization: Organization
    workspace: Workspace
    user: User


def make_tenant(label):
    """An organization with a default workspace and its owner."""
    clear_workspace_context()
    organization = Organization.objects.create(name=f"Tenant {label}")
    user = User.objects.create_user(
        email=f"tenant-{label.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        password=PASSWORD,
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
    clear_workspace_context()
    return Tenant(organization, workspace, user)


def sign_in(email, password=PASSWORD, workspace=None):
    """A client signed in through the login endpoint.

    Every request carries ``Authorization: Bearer <access token>`` and, when
    ``workspace`` is given, the ``X-Workspace-Id`` and ``X-Organization-Id``
    headers, as the web app sends them.
    """
    address = str(ipaddress.ip_address("198.18.0.0") + next(_ADDRESSES))
    client = APIClient(enforce_csrf_checks=True, REMOTE_ADDR=address)
    login = client.post(
        "/accounts/token/", {"email": email, "password": password}, format="json"
    )
    assert login.status_code == 200, login.content
    headers = {"HTTP_AUTHORIZATION": f"Bearer {login.json()['access']}"}
    if workspace is not None:
        headers["HTTP_X_WORKSPACE_ID"] = str(workspace.id)
        headers["HTTP_X_ORGANIZATION_ID"] = str(workspace.organization_id)
    client.credentials(**headers)
    clear_workspace_context()
    return client


def removed_member(tenant, label):
    """A member of ``tenant`` whom its owner removed through the API, signed
    in afterwards.

    The client still sends the former workspace and organization headers, as
    a browser that kept them would. Returns the user and the client.
    """
    clear_workspace_context()
    user = User.objects.create_user(
        email=f"removed-{label.lower()}-{uuid.uuid4().hex[:8]}@example.com",
        password=PASSWORD,
        name=f"Removed {label} Member",
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
    owner = sign_in(tenant.user.email, workspace=tenant.workspace)
    removed = owner.delete(
        "/accounts/organization/members/remove/",
        {"user_id": str(user.id)},
        format="json",
    )
    assert removed.status_code == 200, removed.content
    clear_workspace_context()
    membership.refresh_from_db()
    assert membership.is_active is False
    return user, sign_in(user.email, workspace=tenant.workspace)


def same_answer(response_a, id_a, response_b, id_b):
    """True when two responses differ only by the id each one names."""
    if response_a.status_code != response_b.status_code:
        return False
    body_a = response_a.content.decode().replace(str(id_a), "<id>")
    body_b = response_b.content.decode().replace(str(id_b), "<id>")
    return body_a == body_b
