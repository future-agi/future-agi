"""A workspace viewer cannot ensure or end a Console agent's Observe link: both write, so the
real authentication class refuses them before the view runs (fail closed), as for the
deployment ensure (``omega_deployments/test_ensure_viewer.py``, whose harness this reuses).
The API tests authenticate with ``force_authenticate``, which skips this check.
"""

import uuid
from unittest.mock import Mock

import pytest
from django.urls import resolve
from rest_framework.exceptions import PermissionDenied
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from accounts.authentication import APIKeyAuthentication
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    get_current_workspace,
)
from tracer.views.omega_agents import OmegaAgentObserveView

PATH = "/tracer/omega/agents/workforce_agent_0123/observe/"

# The autouse fixtures of this directory need a database; these tests use none of it.
pytestmark = pytest.mark.django_db


def _authenticate_as(role, method, monkeypatch):
    from accounts.models.organization import Organization
    from accounts.models.user import User
    from accounts.models.workspace import Workspace

    organization = Organization(id=uuid.UUID(int=901))
    workspace = Workspace(id=uuid.UUID(int=902), organization=organization)
    user = User(id=uuid.UUID(int=903), organization=organization, is_active=True)
    monkeypatch.setattr(user, "can_access_workspace", Mock(return_value=True))
    monkeypatch.setattr(user, "get_workspace_role", lambda _: role)
    auth = APIKeyAuthentication()
    monkeypatch.setattr(auth, "_resolve_organization", lambda *_: organization)
    monkeypatch.setattr(auth, "_get_requested_workspace", lambda *_: workspace)
    monkeypatch.setattr(
        "accounts.authentication.decode_token",
        lambda _: (user, "fixture-token", None),
    )
    factory = APIRequestFactory()
    if method == "post":
        raw = factory.post(
            PATH, {"name": "Support Bot"}, HTTP_AUTHORIZATION="Bearer offline-fixture"
        )
    else:
        raw = factory.delete(PATH, HTTP_AUTHORIZATION="Bearer offline-fixture")
    raw.resolver_match = resolve(PATH.removeprefix("/tracer"), urlconf="tracer.urls")
    assert raw.resolver_match.func.view_class is OmegaAgentObserveView
    clear_workspace_context()
    return auth.authenticate(Request(raw)), workspace, user


@pytest.mark.parametrize("method", ["post", "delete"])
def test_a_workspace_viewer_is_refused_before_the_view(method, monkeypatch):
    with pytest.raises(PermissionDenied, match="Write access denied"):
        _authenticate_as(OrganizationRoles.WORKSPACE_VIEWER, method, monkeypatch)
    assert get_current_workspace() is None
    clear_workspace_context()


@pytest.mark.parametrize("method", ["post", "delete"])
def test_a_workspace_member_passes_authentication(method, monkeypatch):
    result, workspace, user = _authenticate_as(
        OrganizationRoles.WORKSPACE_MEMBER, method, monkeypatch
    )
    assert result == (user, "fixture-token")
    assert get_current_workspace() is workspace
    clear_workspace_context()
