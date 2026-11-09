"""A workspace viewer cannot run the ensure: it creates a project and monitors, so the real
authentication class refuses the POST before the view runs (fail closed), the way it refuses
every other tracer write. The Deployments tab must treat a 403 here as "no link yet".

The API tests authenticate with ``force_authenticate``, which skips this check, so it is proven
here through ``APIKeyAuthentication`` itself, with the harness of
``accounts/tests/test_rbac_regressions.py``.
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
from tracer.views.omega_deployments import OmegaDeploymentObserveView

PATH = "/tracer/omega/deployments/dep-0123456789abcdef/observe/"


def _authenticate_as(role, monkeypatch):
    from accounts.models.organization import Organization
    from accounts.models.user import User
    from accounts.models.workspace import Workspace

    organization = Organization(id=uuid.UUID(int=801))
    workspace = Workspace(id=uuid.UUID(int=802), organization=organization)
    user = User(id=uuid.UUID(int=803), organization=organization, is_active=True)
    monkeypatch.setattr(user, "can_access_workspace", Mock(return_value=True))
    monkeypatch.setattr(user, "get_workspace_role", lambda _: role)
    auth = APIKeyAuthentication()
    monkeypatch.setattr(auth, "_resolve_organization", lambda *_: organization)
    monkeypatch.setattr(auth, "_get_requested_workspace", lambda *_: workspace)
    monkeypatch.setattr(
        "accounts.authentication.decode_token",
        # dev's decode_token returns (user, token, SAML credential or None).
        lambda _: (user, "fixture-token", None),
    )
    raw = APIRequestFactory().post(
        PATH, {"name": "support-agent"}, HTTP_AUTHORIZATION="Bearer offline-fixture"
    )
    raw.resolver_match = resolve(PATH.removeprefix("/tracer"), urlconf="tracer.urls")
    assert raw.resolver_match.func.view_class is OmegaDeploymentObserveView
    clear_workspace_context()
    return auth.authenticate(Request(raw)), workspace, user


def test_a_workspace_viewer_is_refused_before_the_view(monkeypatch):
    with pytest.raises(PermissionDenied, match="Write access denied"):
        _authenticate_as(OrganizationRoles.WORKSPACE_VIEWER, monkeypatch)
    assert get_current_workspace() is None
    clear_workspace_context()


def test_a_workspace_member_passes_authentication(monkeypatch):
    result, workspace, user = _authenticate_as(
        OrganizationRoles.WORKSPACE_MEMBER, monkeypatch
    )
    assert result == (user, "fixture-token")
    assert get_current_workspace() is workspace
    clear_workspace_context()
