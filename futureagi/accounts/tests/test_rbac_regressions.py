import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APIClient

from accounts.authentication import (
    APIKeyAuthentication,
    _is_workspace_write_exempt_view,
    workspace_read_only,
)
from tfc.constants.levels import Level

READ_POST_PATHS = (
    "/tracer/trace/list_traces_of_session/",
    "/tracer/observation-span/list_spans_observe/",
    "/tracer/trace-session/list_sessions/",
    "/tracer/users/",
    "/tracer/trace/list_traces/",
    "/tracer/observation-span/list_spans/",
    "/tracer/trace/list_voice_calls/",
    "/tracer/trace/get_trace_id_by_index/",
    "/tracer/trace/get_trace_id_by_index_observe/",
    "/tracer/observation-span/get_trace_id_by_index_spans_as_base/",
    "/tracer/observation-span/get_trace_id_by_index_spans_as_observe/",
    "/tracer/trace/agent_graph/",
)


@pytest.fixture
def reader_auth(monkeypatch):
    from accounts.models.organization import Organization
    from accounts.models.user import User
    from accounts.models.workspace import Workspace
    from tfc.constants.roles import OrganizationRoles
    from tfc.middleware.workspace_context import clear_workspace_context

    organization = Organization(id=uuid.UUID(int=701))
    workspace = Workspace(id=uuid.UUID(int=702), organization=organization)
    user = User(id=uuid.UUID(int=703), organization=organization, is_active=True)
    access = Mock(return_value=True)
    monkeypatch.setattr(user, "can_access_workspace", access)
    monkeypatch.setattr(
        user, "get_workspace_role", lambda _: OrganizationRoles.WORKSPACE_VIEWER
    )
    auth = APIKeyAuthentication()
    monkeypatch.setattr(auth, "_resolve_organization", lambda *_: organization)
    monkeypatch.setattr(auth, "_get_requested_workspace", lambda *_: workspace)
    monkeypatch.setattr(
        "accounts.authentication.decode_token", lambda _: (user, "fixture-token")
    )
    clear_workspace_context()
    yield auth, user, workspace, access
    clear_workspace_context()


def _read_auth_request(path, method):
    from django.urls import resolve
    from rest_framework.request import Request
    from rest_framework.test import APIRequestFactory

    raw = APIRequestFactory().generic(
        method, path, HTTP_AUTHORIZATION="Bearer offline-fixture"
    )
    raw.resolver_match = resolve(path.removeprefix("/tracer"), urlconf="tracer.urls")
    return Request(raw)


@pytest.mark.parametrize("path", READ_POST_PATHS)
def test_reader_real_authentication_get_post_parity(reader_auth, path):
    from tfc.middleware.workspace_context import get_current_workspace

    auth, user, workspace, access = reader_auth
    assert user.can_write_to_workspace(workspace) is False
    for method in ("GET", "POST"):
        request = _read_auth_request(path, method)
        assert auth.authenticate(request) == (user, "fixture-token")
        assert request.workspace is workspace
        assert request.organization is workspace.organization
        assert get_current_workspace() is workspace
    access.assert_called_with(workspace)


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/tracer/trace/"),
        ("PUT", "/tracer/trace/fixture/"),
        ("PATCH", "/tracer/trace/fixture/"),
        ("DELETE", "/tracer/trace/fixture/"),
    ],
)
def test_reader_mutating_routes_still_denied(reader_auth, method, path):
    from tfc.middleware.workspace_context import get_current_workspace

    auth, _, _, _ = reader_auth
    request = _read_auth_request(path, method)
    request.action = "list_traces_of_session"
    request._read_query_post = True
    request._full_data = {"action": "list_traces_of_session", "_read_query_post": True}
    request._request.GET = {
        "action": "list_traces_of_session",
        "_read_query_post": True,
    }
    with pytest.raises(PermissionDenied, match="Write access denied"):
        auth.authenticate(request)
    assert get_current_workspace() is None


@pytest.mark.parametrize("path", READ_POST_PATHS)
def test_read_post_does_not_bypass_tenant_membership(reader_auth, path):
    from tfc.middleware.workspace_context import get_current_workspace

    auth, _, _, access = reader_auth
    access.return_value = False
    with pytest.raises(PermissionDenied, match="Access denied to this workspace"):
        auth.authenticate(_read_auth_request(path, "POST"))
    assert get_current_workspace() is None


@pytest.mark.parametrize(
    "case,expected",
    [
        ("api_view", True),
        ("action", True),
        ("none_map", False),
        ("list_map", False),
        ("empty_map", False),
        ("missing_action", False),
        ("nonstring_action", False),
        ("get_only", False),
        ("create", False),
        ("false_marker", False),
        ("truthy_marker", False),
        ("unresolved", False),
        ("PUT", False),
        ("PATCH", False),
        ("DELETE", False),
    ],
)
def test_read_post_exemption_is_exact_action_and_method(reader_auth, case, expected):
    auth, user, workspace, _ = reader_auth
    request = _read_auth_request("/tracer/users/", "POST")
    read = request.resolver_match.func.cls.post

    def mutate(*args, **kwargs):
        pass

    mutate._read_query_post = 1 if case == "truthy_marker" else False
    view = type(
        "ScopedReadView",
        (),
        {"get": read, "post": read, "read": read, "create": mutate},
    )
    callback = SimpleNamespace(cls=view)
    maps = {
        "action": {"post": "read"},
        "none_map": None,
        "list_map": [],
        "empty_map": {},
        "missing_action": {"post": "missing"},
        "nonstring_action": {"post": 1},
        "get_only": {"get": "read"},
        "create": {"get": "read", "post": "create"},
    }
    if case in maps:
        callback.actions = maps[case]
    if case in ("false_marker", "truthy_marker"):
        view.post = mutate  # A marked GET sibling must not supply POST's marker.
    request._request.resolver_match = (
        None if case == "unresolved" else SimpleNamespace(func=callback)
    )
    if case in ("PUT", "PATCH", "DELETE"):
        request._request.method = case
    assert _is_workspace_write_exempt_view(request) is expected
    if expected:
        assert auth.authenticate(request) == (user, "fixture-token")
        assert request.workspace is workspace
    else:
        with pytest.raises(PermissionDenied, match="Write access denied"):
            auth.authenticate(request)


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
