"""Eval admission must consume existing authority, never manufacture it."""

import pytest
from django.test import RequestFactory
from rest_framework.exceptions import PermissionDenied

from accounts.models import Workspace
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from accounts.services.eval_envelope import _bind_context
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.service_envelope import EnvelopeRejected

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("has_default", [True, False])
def test_unscoped_member_without_workspaces_is_refused_without_writes(
    user, workspace, has_default
):
    OrganizationMembership._base_manager.filter(user=user).update(
        role=OrganizationRoles.MEMBER, level=Level.MEMBER
    )
    user.organization_role = OrganizationRoles.MEMBER
    WorkspaceMembership._base_manager.filter(user=user).delete()
    if not has_default:
        Workspace._base_manager.filter(pk=workspace.pk).update(is_active=False)
    before = (
        Workspace._base_manager.count(),
        WorkspaceMembership._base_manager.count(),
    )
    request = RequestFactory().post("/sdk/api/v1/eval/", data={})
    with pytest.raises((EnvelopeRejected, PermissionDenied)):
        _bind_context(request, user, user.organization, None)
    assert (
        Workspace._base_manager.count(),
        WorkspaceMembership._base_manager.count(),
    ) == before
    assert not WorkspaceMembership._base_manager.filter(user=user).exists()


def test_viewer_cannot_write_via_default_workspace(user, workspace):
    OrganizationMembership._base_manager.filter(user=user).update(
        role=OrganizationRoles.MEMBER, level=Level.MEMBER
    )
    WorkspaceMembership._base_manager.filter(user=user).update(
        role=OrganizationRoles.WORKSPACE_VIEWER, level=Level.WORKSPACE_VIEWER
    )
    request = RequestFactory().post("/sdk/api/v1/eval/", data={})
    with pytest.raises((EnvelopeRejected, PermissionDenied)):
        _bind_context(request, user, user.organization, None)
    assert (
        WorkspaceMembership._base_manager.get(user=user).level == Level.WORKSPACE_VIEWER
    )


def test_existing_member_workspace_is_used_and_unsigned_headers_cannot_override(
    user, workspace
):
    before = WorkspaceMembership._base_manager.count()
    request = RequestFactory().post(
        "/sdk/api/v1/eval/",
        data={},
        HTTP_X_WORKSPACE_ID="00000000-0000-0000-0000-000000000000",
    )
    _bind_context(request, user, user.organization, None)
    assert request.workspace.pk == workspace.pk
    assert request.organization.pk == user.organization_id
    assert WorkspaceMembership._base_manager.count() == before
