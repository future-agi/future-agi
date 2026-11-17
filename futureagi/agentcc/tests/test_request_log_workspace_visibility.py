"""W01-003: organization gateway logs survive an Owner/Admin workspace switch.

Use real browser-session authentication, not the workspace-injecting forced-auth
client. bulk_create deliberately leaves workspace=NULL, as gateway ingestion does.
Run with the database-backed gateway suite; these are not local no-DB tests.
"""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from django.contrib.auth.models import AnonymousUser

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.tests.personal_keys import session_client_for
from agentcc.models.request_log import AgentccRequestLog
from agentcc.views.request_log import AgentccRequestLogViewSet
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]

LOGS = "/agentcc/request-logs/"
T0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
ACTIONS = ("list", "retrieve", "search", "volume", "export")
GLOBAL_ROLES = (
    (OrganizationRoles.OWNER, Level.OWNER),
    (OrganizationRoles.ADMIN, Level.ADMIN),
)


def _actor(organization, role, level):
    user = User.objects.create_user(
        email="request-log-workspace@example.test",
        password="test-password",
        organization=organization,
        organization_role=role,
        config={},
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user, organization=organization, role=role, level=level, is_active=True
    )
    return user, membership


@pytest.fixture
def log_scope(organization, workspace, user):
    clear_workspace_context()
    second = Workspace.objects.create(
        name="Second logs workspace", organization=organization, created_by=user
    )
    other_org = Organization.objects.create(name="Unrelated gateway organization")
    visible = AgentccRequestLog(
        organization=organization,
        request_id="walk-visible",
        model="workspace-model",
        provider="openai",
        started_at=T0,
        status_code=200,
    )
    filtered = AgentccRequestLog(
        organization=organization,
        request_id="walk-filtered",
        model="another-model",
        provider="openai",
        started_at=T0,
        status_code=200,
    )
    deleted = AgentccRequestLog(
        organization=organization,
        request_id="walk-deleted",
        model="workspace-model",
        started_at=T0,
        deleted=True,
    )
    foreign = AgentccRequestLog(
        organization=other_org,
        request_id="walk-foreign",
        model="workspace-model",
        started_at=T0,
    )
    AgentccRequestLog.no_workspace_objects.bulk_create(
        [visible, filtered, deleted, foreign]
    )
    assert AgentccRequestLog.no_workspace_objects.get(pk=visible.pk).workspace_id is None
    yield SimpleNamespace(
        organization=organization,
        default=workspace,
        second=second,
        visible=visible,
        deleted=deleted,
        foreign=foreign,
    )
    clear_workspace_context()


def _read(client, action, log):
    params = {"model": "workspace-model"}
    suffix = "" if action == "list" else f"{action}/"
    if action == "retrieve":
        suffix = f"{log.pk}/"
    elif action == "search":
        params["q"] = "walk-"
    elif action == "volume":
        params.update(
            start=(T0 - timedelta(hours=1)).isoformat(),
            end=(T0 + timedelta(hours=1)).isoformat(),
            granularity="hour",
        )
    elif action == "export":
        params["export_format"] = "json"
    return client.get(f"{LOGS}{suffix}", params)


def _assert_visible(response, action, log):
    assert response.status_code == 200, response.content
    if action == "volume":
        series = response.json()["result"]["series"]
        assert sum(point["request_count"] for point in series) == 1
        return
    if action == "export":
        rows = json.loads(b"".join(response.streaming_content))
    elif action == "retrieve":
        rows = [response.json()["result"]]
    else:
        rows = response.json()["results"]
    # Also pins organization, soft-delete and model-filter confinement.
    assert [row["request_id"] for row in rows] == [log.request_id]


def _assert_hidden(response, action):
    if action == "retrieve":
        assert response.status_code == 404, response.content
    elif action == "export":
        assert response.status_code == 400, response.content
        assert response.json()["code"] == "export_empty"
    else:
        assert response.status_code == 200, response.content
        if action == "volume":
            series = response.json()["result"]["series"]
            assert sum(point["request_count"] for point in series) == 0
        else:
            assert response.json()["results"] == []


@pytest.mark.parametrize("role,level", GLOBAL_ROLES)
@pytest.mark.parametrize("action", ACTIONS)
def test_global_org_role_keeps_logs_across_workspace_selection(
    log_scope, role, level, action
):
    actor, _ = _actor(log_scope.organization, role, level)
    assert actor.has_global_workspace_access(log_scope.organization)
    # Default -> second -> default is the console walk's selection sequence.
    for selected in (log_scope.default, log_scope.second, log_scope.default):
        client = session_client_for(actor, log_scope.organization, selected)
        _assert_visible(_read(client, action, log_scope.visible), action, log_scope.visible)


@pytest.mark.parametrize("role,level", GLOBAL_ROLES)
def test_omitting_workspace_header_does_not_fix_persisted_selection(
    log_scope, role, level
):
    actor, _ = _actor(log_scope.organization, role, level)
    actor.config = {
        "orgWorkspaceMap": {str(log_scope.organization.pk): str(log_scope.second.pk)},
        "currentWorkspaceId": str(log_scope.second.pk),
    }
    actor.save(update_fields=["config"])
    # Authentication restores the persisted second workspace even without a
    # header. Merely suppressing X-Workspace-Id in Axios cannot fix this row.
    client = session_client_for(actor, log_scope.organization)
    _assert_visible(_read(client, "list", log_scope.visible), "list", log_scope.visible)


@pytest.mark.parametrize("default_access", [False, True])
@pytest.mark.parametrize("action", ACTIONS)
def test_restricted_member_keeps_selected_workspace_boundary(
    log_scope, default_access, action
):
    actor, membership = _actor(
        log_scope.organization, OrganizationRoles.MEMBER, Level.MEMBER
    )
    selected_workspaces = [log_scope.second]
    if default_access:
        selected_workspaces.append(log_scope.default)
    for selected in selected_workspaces:
        WorkspaceMembership.no_workspace_objects.create(
            user=actor,
            workspace=selected,
            organization_membership=membership,
            role=OrganizationRoles.WORKSPACE_MEMBER,
            level=Level.WORKSPACE_MEMBER,
            is_active=True,
        )
    assert not actor.has_global_workspace_access(log_scope.organization)
    second_client = session_client_for(actor, log_scope.organization, log_scope.second)
    _assert_hidden(_read(second_client, action, log_scope.visible), action)
    if default_access:
        default_client = session_client_for(actor, log_scope.organization, log_scope.default)
        _assert_visible(
            _read(default_client, action, log_scope.visible), action, log_scope.visible
        )


@pytest.mark.parametrize("role,level", GLOBAL_ROLES)
@pytest.mark.parametrize("hidden", ["deleted", "foreign"])
def test_global_org_role_cannot_retrieve_deleted_or_other_org_log(
    log_scope, role, level, hidden
):
    actor, _ = _actor(log_scope.organization, role, level)
    client = session_client_for(actor, log_scope.organization, log_scope.second)
    response = client.get(f"{LOGS}{getattr(log_scope, hidden).pk}/")
    assert response.status_code == 404, response.content


@pytest.mark.parametrize(
    "role,level",
    [*GLOBAL_ROLES, (OrganizationRoles.MEMBER, Level.MEMBER)],
)
def test_metadata_options_follow_log_authorization_not_query_filters(
    log_scope, role, level
):
    actor, membership = _actor(log_scope.organization, role, level)
    if role == OrganizationRoles.MEMBER:
        for selected in (log_scope.default, log_scope.second):
            WorkspaceMembership.no_workspace_objects.create(
                user=actor,
                workspace=selected,
                organization_membership=membership,
                role=OrganizationRoles.WORKSPACE_MEMBER,
                level=Level.WORKSPACE_MEMBER,
                is_active=True,
            )
    for request_id, application in (
        ("walk-visible", "visible-app"),
        ("walk-filtered", "other-model-app"),
        ("walk-deleted", "deleted-app"),
        ("walk-foreign", "foreign-app"),
    ):
        AgentccRequestLog.no_workspace_objects.filter(request_id=request_id).update(
            metadata={"application": application}
        )
    for selected in (log_scope.default, log_scope.second, log_scope.default):
        client = session_client_for(actor, log_scope.organization, selected)
        response = client.get(
            f"{LOGS}metadata-values/", {"model": "workspace-model"}
        )
        assert response.status_code == 200, response.content
        expected = ["other-model-app", "visible-app"]
        if role == OrganizationRoles.MEMBER and selected == log_scope.second:
            expected = []
        # Picker options intentionally ignore query filters but not authorization.
        assert response.json()["result"] == {
            "application": expected, "service": [], "tags": []
        }


@pytest.mark.parametrize("role,level", GLOBAL_ROLES)
@pytest.mark.parametrize(
    "boundary",
    [
        "authenticated-empty",
        "bound-without-workspace",
        "organization-cleared",
        "inactive-membership",
        "anonymous",
    ],
)
def test_global_role_cannot_bypass_request_scope_guards(
    log_scope, role, level, boundary
):
    actor, membership = _actor(log_scope.organization, role, level)
    request = SimpleNamespace(
        user=actor,
        organization=log_scope.organization,
        workspace=log_scope.second,
        query_params={"model": "workspace-model"},
    )
    if boundary == "authenticated-empty":
        # The sentinel must dominate even a stale explicit workspace attribute.
        set_workspace_context(
            workspace=None,
            organization=log_scope.organization,
            user=actor,
            require_workspace=True,
        )
    elif boundary == "bound-without-workspace":
        request.workspace = None
        request.auth_scope = SimpleNamespace(kind="session")
    elif boundary == "organization-cleared":
        # Authentication's explicit None must not fall back to the user's org.
        request.organization = None
    elif boundary == "inactive-membership":
        membership.is_active = False
        membership.save(update_fields=["is_active"])
    elif boundary == "anonymous":
        request.user = AnonymousUser()
        request.organization = None
    view = AgentccRequestLogViewSet()
    view.request = request
    try:
        assert list(view.get_queryset().values_list("request_id", flat=True)) == []
    finally:
        clear_workspace_context()
