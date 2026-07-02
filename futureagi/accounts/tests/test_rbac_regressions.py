import uuid

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from accounts.authentication import (
    _is_workspace_write_exempt_view,
    workspace_read_only,
)
from tfc.constants.levels import Level


def test_owner_level_maps_to_workspace_admin_label():
    assert Level.to_ws_string(Level.OWNER) == "Workspace Admin"
    assert Level.to_ws_role(Level.OWNER) == "workspace_admin"


class _FakeRequest:
    """Mimics the resolver_match -> func.cls chain Django sets on requests."""

    def __init__(self, view_cls):
        self.resolver_match = type(
            "Match", (), {"func": type("Func", (), {"cls": view_cls})}
        )


def test_workspace_read_only_marks_the_view():
    @workspace_read_only
    class View:
        pass

    assert View.workspace_write_exempt is True


def test_marked_view_is_write_exempt():
    @workspace_read_only
    class View:
        pass

    assert _is_workspace_write_exempt_view(_FakeRequest(View)) is True


def test_unmarked_view_is_not_write_exempt():
    class View:
        pass

    assert _is_workspace_write_exempt_view(_FakeRequest(View)) is False


def test_unresolvable_view_fails_closed():
    class NoMatch:
        resolver_match = None

    assert _is_workspace_write_exempt_view(NoMatch()) is False


def test_read_only_eval_views_are_write_exempt():
    """Every read-only POST view must carry the marker.

    Regression: the ground-truth similarity search was a read-only POST that
    the old path allow-list missed, so viewers got 403 on it. This asserts the
    whole read-only group (including search) is exempt, and fails loudly if a
    future read-only POST view forgets @workspace_read_only.
    """
    from model_hub.views.separate_evals import (
        EvalTemplateListChartsView,
        EvalTemplateListView,
        GetEvalTemplateNameView,
        GetEvalTemplates,
    )

    for view in (
        GetEvalTemplates,
        GetEvalTemplateNameView,
        EvalTemplateListView,
        EvalTemplateListChartsView,
    ):
        assert getattr(view, "workspace_write_exempt", False) is True, view.__name__


def test_mutating_eval_views_are_not_write_exempt():
    from model_hub.views.separate_evals import (
        EvalTemplateBulkDeleteView,
        EvalTemplateCreateV2View,
        EvalTemplateUpdateView,
    )

    for view in (
        EvalTemplateCreateV2View,
        EvalTemplateUpdateView,
        EvalTemplateBulkDeleteView,
    ):
        assert getattr(view, "workspace_write_exempt", False) is False, view.__name__



EVAL_READ_ENDPOINTS = (
    ("/model-hub/eval-templates/list/", {}),
    ("/model-hub/eval-templates/list-charts/", {"template_ids": []}),
    ("/model-hub/get-eval-template-names", {}),
    ("/model-hub/get-eval-templates", {}),
)
EVAL_WRITE_ENDPOINTS = (
    "/model-hub/eval-templates/create-v2/",
    "/model-hub/eval-templates/bulk-delete/",
)
WORKSPACE_WRITE_DENIED = "Write access denied to this workspace"


def _make_workspace_user(
    organization,
    workspace,
    *,
    org_role,
    org_level,
    ws_role,
    ws_level,
    prefix,
):
    """Create a user with real org + workspace memberships at the given roles."""
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.user import User
    from accounts.models.workspace import WorkspaceMembership

    user = User.objects.create_user(
        email=f"{prefix}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="testpassword123",
        name=prefix,
        organization=organization,
        organization_role=org_role,
    )
    org_membership, _ = OrganizationMembership.no_workspace_objects.update_or_create(
        user=user,
        organization=organization,
        defaults={"role": org_role, "level": org_level, "is_active": True},
    )
    WorkspaceMembership.no_workspace_objects.update_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": ws_role,
            "level": ws_level,
            "organization_membership": org_membership,
            "is_active": True,
        },
    )
    return user


def _jwt_client(user, organization, workspace):
    """Log in through /accounts/token/ so the real auth class runs on requests."""
    client = APIClient()
    login = client.post(
        "/accounts/token/",
        {"email": user.email, "password": "testpassword123"},
        format="json",
    )
    assert login.status_code == status.HTTP_200_OK, login.data
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {login.data['access']}",
        HTTP_X_ORGANIZATION_ID=str(organization.id),
        HTTP_X_WORKSPACE_ID=str(workspace.id),
    )
    return client


def _viewer_client(organization, workspace):
    from tfc.constants.roles import OrganizationRoles

    user = _make_workspace_user(
        organization,
        workspace,
        org_role=OrganizationRoles.MEMBER_VIEW_ONLY,
        org_level=Level.VIEWER,
        ws_role=OrganizationRoles.WORKSPACE_VIEWER,
        ws_level=Level.WORKSPACE_VIEWER,
        prefix="rbac-viewer",
    )
    return _jwt_client(user, organization, workspace)


def _member_client(organization, workspace):
    from tfc.constants.roles import OrganizationRoles

    user = _make_workspace_user(
        organization,
        workspace,
        org_role=OrganizationRoles.MEMBER,
        org_level=Level.MEMBER,
        ws_role=OrganizationRoles.WORKSPACE_MEMBER,
        ws_level=Level.WORKSPACE_MEMBER,
        prefix="rbac-member",
    )
    return _jwt_client(user, organization, workspace)


def _admin_client(organization, workspace):
    from tfc.constants.roles import OrganizationRoles

    user = _make_workspace_user(
        organization,
        workspace,
        org_role=OrganizationRoles.ADMIN,
        org_level=Level.ADMIN,
        ws_role=OrganizationRoles.WORKSPACE_ADMIN,
        ws_level=Level.WORKSPACE_ADMIN,
        prefix="rbac-admin",
    )
    return _jwt_client(user, organization, workspace)


@pytest.mark.django_db
def test_workspace_viewer_allowed_on_read_only_eval_post_endpoints(
    organization, workspace
):
    """Viewer reaches every read-only eval POST (no write-block 403)."""
    client = _viewer_client(organization, workspace)
    for url, body in EVAL_READ_ENDPOINTS:
        resp = client.post(url, body, format="json")
        assert resp.status_code == status.HTTP_200_OK, (
            url,
            resp.status_code,
            resp.data,
        )
        assert WORKSPACE_WRITE_DENIED not in str(resp.data)


@pytest.mark.django_db
def test_workspace_viewer_denied_on_mutating_eval_endpoints(organization, workspace):
    """Viewer is blocked by the workspace write-check on mutating eval endpoints."""
    client = _viewer_client(organization, workspace)
    for url in EVAL_WRITE_ENDPOINTS:
        resp = client.post(url, {}, format="json")
        assert resp.status_code == status.HTTP_403_FORBIDDEN, (
            url,
            resp.status_code,
            resp.data,
        )
        assert WORKSPACE_WRITE_DENIED in str(resp.data)


@pytest.mark.django_db
def test_workspace_member_and_admin_allowed_on_read_only_eval_post_endpoints(
    organization, workspace
):
    """The allow-list -> decorator rewire must not regress writers' read access."""
    for make_client in (_member_client, _admin_client):
        client = make_client(organization, workspace)
        for url, body in EVAL_READ_ENDPOINTS:
            resp = client.post(url, body, format="json")
            assert resp.status_code == status.HTTP_200_OK, (
                url,
                resp.status_code,
                resp.data,
            )


@pytest.mark.django_db
def test_other_write_skip_conditions_still_resolve_for_viewer(organization, workspace):
    """The decorator rewire must not break the other write-check skips:
    the annotation-queue role-scoped paths and the excluded_paths list."""
    client = _viewer_client(organization, workspace)

    role_scoped = client.post(
        f"/model-hub/annotation-queues/{uuid.uuid4()}/items/{uuid.uuid4()}/skip/",
        {},
        format="json",
    )
    assert WORKSPACE_WRITE_DENIED not in str(role_scoped.data)

    excluded = client.post(
        "/accounts/update-user-full-name/",
        {"full_name": "Viewer Rename"},
        format="json",
    )
    assert WORKSPACE_WRITE_DENIED not in str(excluded.data)
