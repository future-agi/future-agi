"""Gateway analytics count an organization's request logs as the Logs view does.

Ingestion writes an organization's request logs with workspace=NULL. The Logs
view keeps them visible to an active Owner or Admin whichever workspace is
selected (``AgentccRequestLogViewSet._get_authorized_queryset``). The
analytics viewset scoped by the selected workspace instead, so with a
non-default workspace selected a key's Requests, Cost and Tokens, the
Analytics page and the Budgets "Spent / Requests" summary (all read from
/agentcc/analytics/) showed 0 while Logs listed the calls.

Use real browser-session authentication, as the Logs test does. bulk_create
leaves workspace=NULL, as gateway ingestion does. Run with the database-backed
gateway suite; these are not local no-DB tests.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import AnonymousUser

from accounts.models import Organization, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.tests.personal_keys import session_client_for
from agentcc.models.request_log import AgentccRequestLog
from agentcc.views.analytics import AgentccAnalyticsViewSet
from agentcc.views.request_log import AgentccRequestLogViewSet
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

pytestmark = [pytest.mark.django_db, pytest.mark.integration, pytest.mark.api]

ANALYTICS = "/agentcc/analytics/"
LOGS = "/agentcc/request-logs/"
T0 = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
WINDOW = {
    "start": (T0 - timedelta(hours=1)).isoformat(),
    "end": (T0 + timedelta(hours=1)).isoformat(),
}
KEY_A = "key-analytics-a"
KEY_B = "key-analytics-b"
GLOBAL_ROLES = (
    (OrganizationRoles.OWNER, Level.OWNER),
    (OrganizationRoles.ADMIN, Level.ADMIN),
)

# Every analytics action that reads request logs, with how to read the number
# of requests it counted.
ACTIONS = {
    "overview": lambda result: result["total_requests"]["value"],
    "usage-timeseries": lambda result: sum(
        point["request_count"] for point in result["series"]
    ),
    "cost-breakdown": lambda result: sum(
        row["request_count"] for row in result["breakdown"]
    ),
    "latency-stats": lambda result: result["summary"]["total_requests"],
    "error-breakdown": lambda result: result["total_requests"],
    "model-comparison": lambda result: sum(
        row["request_count"] for row in result["models"]
    ),
    "guardrail-overview": lambda result: result["total_requests"],
}
BUCKETED = {"usage-timeseries", "latency-stats", "error-breakdown"}


def _actor(organization, role, level):
    user = User.objects.create_user(
        email=f"analytics-workspace-{uuid.uuid4().hex[:8]}@example.test",
        password="test-password",
        organization=organization,
        organization_role=role,
        config={},
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user, organization=organization, role=role, level=level, is_active=True
    )
    return user, membership


def _join(user, membership, workspaces):
    for workspace in workspaces:
        WorkspaceMembership.no_workspace_objects.create(
            user=user,
            workspace=workspace,
            organization_membership=membership,
            role=OrganizationRoles.WORKSPACE_MEMBER,
            level=Level.WORKSPACE_MEMBER,
            is_active=True,
        )


def _log(organization, request_id, workspace=None, **fields):
    values = {
        "model": "workspace-model",
        "provider": "openai",
        "started_at": T0,
        "status_code": 200,
        "latency_ms": 100,
    }
    values.update(fields)
    return AgentccRequestLog(
        organization=organization,
        workspace=workspace,
        request_id=request_id,
        **values,
    )


@pytest.fixture
def scope(organization, workspace, user):
    clear_workspace_context()
    second = Workspace.objects.create(
        name="Second analytics workspace", organization=organization, created_by=user
    )
    empty = Workspace.objects.create(
        name="Empty analytics workspace", organization=organization, created_by=user
    )
    other_org = Organization.objects.create(name="Unrelated analytics organization")
    org_key_a = _log(
        organization,
        "analytics-org-a",
        api_key_id=KEY_A,
        input_tokens=10,
        output_tokens=20,
        total_tokens=30,
        cost=Decimal("0.25"),
    )
    org_key_b = _log(
        organization,
        "analytics-org-b",
        api_key_id=KEY_B,
        input_tokens=20,
        output_tokens=40,
        total_tokens=60,
        cost=Decimal("0.50"),
    )
    in_second = _log(
        organization,
        "analytics-second",
        workspace=second,
        api_key_id=KEY_A,
        input_tokens=30,
        output_tokens=60,
        total_tokens=90,
        cost=Decimal("1.00"),
    )
    deleted = _log(
        organization,
        "analytics-deleted",
        api_key_id=KEY_A,
        total_tokens=1000,
        cost=Decimal("9.00"),
        deleted=True,
    )
    foreign = _log(
        other_org,
        "analytics-foreign",
        api_key_id=KEY_A,
        total_tokens=1000,
        cost=Decimal("9.00"),
    )
    AgentccRequestLog.no_workspace_objects.bulk_create(
        [org_key_a, org_key_b, in_second, deleted, foreign]
    )
    stored = AgentccRequestLog.no_workspace_objects.filter(
        pk__in=[org_key_a.pk, org_key_b.pk]
    )
    assert list(stored.values_list("workspace_id", flat=True)) == [None, None]
    yield SimpleNamespace(
        organization=organization,
        default=workspace,
        second=second,
        empty=empty,
        org_key_a=org_key_a,
        org_key_b=org_key_b,
        in_second=in_second,
    )
    clear_workspace_context()


def _analytics(client, action, **params):
    query = dict(WINDOW, **params)
    if action in BUCKETED:
        query["granularity"] = "hour"
    response = client.get(f"{ANALYTICS}{action}/", query)
    assert response.status_code == 200, response.content
    return response.json()["result"]


def _counted(client, action, **params):
    return ACTIONS[action](_analytics(client, action, **params))


def _logs_listed(client, **params):
    response = client.get(LOGS, dict(params, limit=100))
    assert response.status_code == 200, response.content
    return sorted(row["request_id"] for row in response.json()["results"])


@pytest.mark.parametrize("role,level", GLOBAL_ROLES)
@pytest.mark.parametrize("action", list(ACTIONS))
def test_global_org_role_counts_org_logs_across_workspace_selection(
    scope, role, level, action
):
    actor, _ = _actor(scope.organization, role, level)
    assert actor.has_global_workspace_access(scope.organization)
    # Default -> second -> default is the console walk's selection sequence.
    for selected in (scope.default, scope.second, scope.default):
        client = session_client_for(actor, scope.organization, selected)
        listed = _logs_listed(client)
        assert listed == ["analytics-org-a", "analytics-org-b", "analytics-second"]
        # Organization, soft delete and the Logs view's own scope all hold.
        assert _counted(client, action) == len(listed), selected.name


@pytest.mark.parametrize("role,level", GLOBAL_ROLES)
def test_global_org_role_overview_totals_with_workspace_selected(scope, role, level):
    actor, _ = _actor(scope.organization, role, level)
    client = session_client_for(actor, scope.organization, scope.second)
    result = _analytics(client, "overview")
    assert result["total_requests"]["value"] == 3
    assert result["total_tokens"]["value"] == 180
    assert Decimal(result["total_cost"]["value"]) == Decimal("1.75")


@pytest.mark.parametrize("role,level", GLOBAL_ROLES)
def test_key_usage_counts_org_logs_with_workspace_selected(scope, role, level):
    actor, _ = _actor(scope.organization, role, level)
    client = session_client_for(actor, scope.organization, scope.second)
    # The key drawer's Usage panel: overview and usage filtered by api_key_id.
    result = _analytics(client, "overview", api_key_id=KEY_A)
    assert result["total_requests"]["value"] == 2
    assert result["total_tokens"]["value"] == 120
    assert Decimal(result["total_cost"]["value"]) == Decimal("1.25")
    assert _counted(client, "usage-timeseries", api_key_id=KEY_A) == 2
    assert _logs_listed(client, api_key_id=KEY_A) == [
        "analytics-org-a",
        "analytics-second",
    ]
    other = _analytics(client, "overview", api_key_id=KEY_B)
    assert other["total_requests"]["value"] == 1
    assert other["total_tokens"]["value"] == 60


@pytest.mark.parametrize("default_access", [False, True])
@pytest.mark.parametrize("action", list(ACTIONS))
def test_restricted_member_counts_exactly_what_logs_show(
    scope, default_access, action
):
    actor, membership = _actor(
        scope.organization, OrganizationRoles.MEMBER, Level.MEMBER
    )
    workspaces = [scope.second] + ([scope.default] if default_access else [])
    _join(actor, membership, workspaces)
    assert not actor.has_global_workspace_access(scope.organization)
    expected = {
        # The selected workspace's own rows only: no organization rows.
        scope.second.pk: (["analytics-second"], ["analytics-second"]),
        # The default workspace covers rows with no workspace.
        scope.default.pk: (
            ["analytics-org-a", "analytics-org-b"],
            ["analytics-org-a"],
        ),
    }
    for selected in workspaces:
        client = session_client_for(actor, scope.organization, selected)
        every, key_a = expected[selected.pk]
        assert _logs_listed(client) == every
        assert _logs_listed(client, api_key_id=KEY_A) == key_a
        assert _counted(client, action) == len(every), selected.name
        assert _counted(client, action, api_key_id=KEY_A) == len(key_a), selected.name


def _direct_request(scope, actor, **overrides):
    request = SimpleNamespace(
        user=actor,
        organization=scope.organization,
        workspace=None,
        query_params={},
    )
    for name, value in overrides.items():
        setattr(request, name, value)
    return request


VIEWS = [AgentccAnalyticsViewSet, AgentccRequestLogViewSet]


def _request_ids(view_class, request):
    view = view_class()
    view.request = request
    return sorted(view.get_queryset().values_list("request_id", flat=True))


@pytest.mark.parametrize("view_class", VIEWS)
@pytest.mark.parametrize(
    "actor_kind,expected",
    [
        ("owner", ["analytics-org-a", "analytics-org-b", "analytics-second"]),
        ("admin", ["analytics-org-a", "analytics-org-b", "analytics-second"]),
        ("member-second", ["analytics-second"]),
        (
            "member-default-second",
            ["analytics-org-a", "analytics-org-b", "analytics-second"],
        ),
    ],
)
def test_no_workspace_selected_keeps_the_mixin_scope(
    scope, view_class, actor_kind, expected
):
    roles = {
        "owner": (OrganizationRoles.OWNER, Level.OWNER),
        "admin": (OrganizationRoles.ADMIN, Level.ADMIN),
    }
    role, level = roles.get(actor_kind, (OrganizationRoles.MEMBER, Level.MEMBER))
    actor, membership = _actor(scope.organization, role, level)
    if actor_kind == "member-second":
        _join(actor, membership, [scope.second])
    elif actor_kind == "member-default-second":
        _join(actor, membership, [scope.default, scope.second])
    clear_workspace_context()
    request = _direct_request(scope, actor)
    assert _request_ids(view_class, request) == expected
    request.query_params = {"api_key_id": KEY_A}
    assert _request_ids(view_class, request) == [
        request_id
        for request_id in expected
        if request_id in ("analytics-org-a", "analytics-second")
    ]


@pytest.mark.parametrize("view_class", VIEWS)
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
    scope, view_class, role, level, boundary
):
    actor, membership = _actor(scope.organization, role, level)
    # A workspace with no rows of its own: anything returned would be the
    # organization-wide widening applied past a guard.
    request = _direct_request(scope, actor, workspace=scope.empty)
    if boundary == "authenticated-empty":
        set_workspace_context(
            workspace=None,
            organization=scope.organization,
            user=actor,
            require_workspace=True,
        )
    elif boundary == "bound-without-workspace":
        request.workspace = None
        request.auth_scope = SimpleNamespace(kind="session")
    elif boundary == "organization-cleared":
        request.organization = None
    elif boundary == "inactive-membership":
        membership.is_active = False
        membership.save(update_fields=["is_active"])
    elif boundary == "anonymous":
        request.user = AnonymousUser()
        request.organization = None
    try:
        assert _request_ids(view_class, request) == []
    finally:
        clear_workspace_context()
