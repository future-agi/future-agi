"""A SAML-scoped member with no workspace access reaches no workspace's data.

``_get_scoped_default_workspace`` returns None for a member of the bound org
who has no workspace membership and no global workspace access (a Member, not
an Admin), so ``request.organization`` is set and ``request.workspace`` is
None. The shared viewset mixins keep every query in the member's
organization and in the workspaces the member can access, whichever workspace
the request names, and every write goes through the write-permission check.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from model_hub.models.evals_metric import EvalTemplate
from tracer.models.custom_eval_config import CustomEvalConfig
from tracer.models.dashboard import Dashboard
from tracer.models.monitor import UserAlertMonitor, UserAlertMonitorLog
from tracer.models.project import Project

pytestmark = [pytest.mark.django_db, pytest.mark.integration]

REFUSED_DETAIL = (status.HTTP_404_NOT_FOUND, status.HTTP_400_BAD_REQUEST)


@pytest.fixture
def victim():
    org = Organization.objects.create(name="Victim Org")
    owner = User.objects.create_user(
        email=f"victim-{uuid.uuid4().hex[:8]}@victim.test",
        password="victim-password-not-a-secret",
        name="victim owner",
        organization=org,
    )
    workspace = Workspace.objects.create(
        name="victim ws",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=owner,
    )
    dashboard = Dashboard.no_workspace_objects.create(
        name="victim-dashboard",
        workspace=workspace,
        created_by=owner,
        updated_by=owner,
    )
    template = EvalTemplate.no_workspace_objects.create(
        name="victim-template",
        organization=org,
        workspace=workspace,
        config={"type": "pass_fail"},
    )
    project = Project.no_workspace_objects.create(
        name="victim-project",
        organization=org,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    config = CustomEvalConfig.no_workspace_objects.create(
        name="victim-eval-config", project=project, eval_template=template
    )
    return dashboard, config


@pytest.fixture
def member_without_workspace(idp_a, make_saml_token, saml_tenants):
    """a2 is a Member of org A with no workspace membership."""
    _row, token = make_saml_token(saml_tenants.a2, saml_tenants.org_a, idp_a)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def test_the_session_has_an_organization_and_no_workspace(
    member_without_workspace, saml_tenants
):
    """The precondition the other tests rely on."""
    from accounts.authentication import APIKeyAuthentication

    seen = {}
    original = APIKeyAuthentication._set_workspace_context

    def record(self, request, user):
        original(self, request, user)
        seen["org"] = getattr(request, "organization", None)
        seen["workspace"] = getattr(request, "workspace", None)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(APIKeyAuthentication, "_set_workspace_context", record)
        resp = member_without_workspace.get("/tracer/dashboard/")

    assert resp.status_code == status.HTTP_200_OK, resp.content
    assert seen["org"] == saml_tenants.org_a
    assert seen["workspace"] is None


def test_dashboard_list_does_not_show_another_orgs_dashboard(
    member_without_workspace, victim
):
    resp = member_without_workspace.get("/tracer/dashboard/")

    assert resp.status_code == status.HTTP_200_OK, resp.content
    assert "victim-dashboard" not in resp.content.decode(), (
        "a SAML member with no workspace listed another org's dashboard"
    )


def test_dashboard_detail_of_another_org_is_refused(member_without_workspace, victim):
    dashboard, _ = victim

    resp = member_without_workspace.get(f"/tracer/dashboard/{dashboard.id}/")

    # The dashboard view answers any id it cannot find with 400.
    assert resp.status_code in (
        status.HTTP_404_NOT_FOUND,
        status.HTTP_400_BAD_REQUEST,
    ), resp.content
    assert "victim-dashboard" not in resp.content.decode()


def test_eval_config_list_does_not_show_another_orgs_config(
    member_without_workspace, victim
):
    resp = member_without_workspace.get("/tracer/custom-eval-config/")

    assert resp.status_code == status.HTTP_200_OK, resp.content
    assert "victim-eval-config" not in resp.content.decode(), (
        "a SAML member with no workspace listed another org's eval config"
    )


# --- Org A's own workspaces ----------------------------------------------------
#
# a2 (Member) and v (Member, view only) belong to org A and to none of its
# workspaces, so neither reaches any of them, named or not.


@pytest.fixture
def restricted(saml_tenants):
    """Org A's non-default workspace, which only its owner works in, holding a
    dashboard, an eval config and an alert monitor with a log; plus a
    dashboard in org A's default workspace."""
    org, owner = saml_tenants.org_a, saml_tenants.a1
    workspace = Workspace.objects.create(
        name="Restricted ws",
        organization=org,
        is_default=False,
        is_active=True,
        created_by=owner,
    )
    dashboard = Dashboard.no_workspace_objects.create(
        name="restricted-dashboard",
        workspace=workspace,
        created_by=owner,
        updated_by=owner,
    )
    default_dashboard = Dashboard.no_workspace_objects.create(
        name="default-ws-dashboard",
        workspace=saml_tenants.workspace_a,
        created_by=owner,
        updated_by=owner,
    )
    template = EvalTemplate.no_workspace_objects.create(
        name="restricted-template",
        organization=org,
        workspace=workspace,
        config={"type": "pass_fail"},
    )
    project = Project.no_workspace_objects.create(
        name="restricted-project",
        organization=org,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    eval_config = CustomEvalConfig.no_workspace_objects.create(
        name="restricted-eval-config", project=project, eval_template=template
    )
    monitor = UserAlertMonitor.no_workspace_objects.create(
        organization=org,
        workspace=workspace,
        project=project,
        name="restricted-alert",
        metric_type="count_of_errors",
        threshold_operator="greater_than",
        threshold_type="static",
        critical_threshold_value=0.1,
        alert_frequency=60,
    )
    log = UserAlertMonitorLog.no_workspace_objects.create(
        alert=monitor, type="critical", message="restricted-alert-log"
    )
    return SimpleNamespace(
        workspace=workspace,
        dashboard=dashboard,
        default_dashboard=default_dashboard,
        eval_config=eval_config,
        monitor=monitor,
        log=log,
    )


def _saml_client(make_saml_token, user, organization, idp):
    _row, token = make_saml_token(user, organization, idp)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


@pytest.fixture
def view_only_without_workspace(idp_a, make_saml_token, saml_tenants):
    """v is a view-only Member of org A with no workspace membership."""
    return _saml_client(make_saml_token, saml_tenants.v, saml_tenants.org_a, idp_a)


def test_a_workspace_named_in_the_header_is_refused(
    member_without_workspace, restricted, saml_tenants
):
    """The control: naming either workspace is refused."""
    for workspace in (restricted.workspace, saml_tenants.workspace_a):
        resp = member_without_workspace.get(
            "/tracer/dashboard/", HTTP_X_WORKSPACE_ID=str(workspace.id)
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.content
        assert "saml_scope_conflict" in resp.content.decode()


def test_dashboard_list_shows_no_workspace_of_the_members_own_org(
    member_without_workspace, restricted
):
    resp = member_without_workspace.get("/tracer/dashboard/")

    assert resp.status_code == status.HTTP_200_OK, resp.content
    body = resp.content.decode()
    assert "restricted-dashboard" not in body, (
        "a member with no workspace listed a dashboard of a workspace they "
        "cannot access"
    )
    assert "default-ws-dashboard" not in body


def test_dashboard_detail_in_the_members_own_org_is_refused(
    member_without_workspace, restricted
):
    for dashboard in (restricted.dashboard, restricted.default_dashboard):
        resp = member_without_workspace.get(f"/tracer/dashboard/{dashboard.id}/")

        assert resp.status_code in REFUSED_DETAIL, resp.content
        assert dashboard.name not in resp.content.decode()


def test_eval_configs_of_the_members_own_org_are_not_shown(
    member_without_workspace, restricted
):
    listed = member_without_workspace.get("/tracer/custom-eval-config/")
    detail = member_without_workspace.get(
        f"/tracer/custom-eval-config/{restricted.eval_config.id}/"
    )

    assert listed.status_code == status.HTTP_200_OK, listed.content
    assert "restricted-eval-config" not in listed.content.decode()
    assert detail.status_code in REFUSED_DETAIL, detail.content
    assert "restricted-eval-config" not in detail.content.decode()


def test_alerts_and_alert_logs_of_the_members_own_org_are_not_shown(
    member_without_workspace, restricted
):
    """An alert log has no workspace of its own; the view narrows it through
    its monitor's workspace, by its own helper, not the mixin's."""
    alerts = member_without_workspace.get("/tracer/user-alerts/")
    logs = member_without_workspace.get("/tracer/user-alert-logs/")
    all_logs = member_without_workspace.get("/tracer/user-alert-logs/all/")

    assert alerts.status_code == status.HTTP_200_OK, alerts.content
    assert "restricted-alert" not in alerts.content.decode()
    for resp in (logs, all_logs):
        assert resp.status_code == status.HTTP_200_OK, resp.content
        assert "restricted-alert-log" not in resp.content.decode()


def test_a_member_without_workspace_cannot_change_a_dashboard(
    member_without_workspace, restricted
):
    resp = member_without_workspace.patch(
        f"/tracer/dashboard/{restricted.dashboard.id}/",
        {"name": "renamed-by-a2"},
        format="json",
    )

    restricted.dashboard.refresh_from_db()
    assert resp.status_code == status.HTTP_403_FORBIDDEN, resp.content
    assert restricted.dashboard.name == "restricted-dashboard"


def test_a_view_only_member_without_workspace_cannot_change_or_delete(
    view_only_without_workspace, restricted
):
    dashboard = restricted.dashboard

    patched = view_only_without_workspace.patch(
        f"/tracer/dashboard/{dashboard.id}/",
        {"name": "renamed-by-viewer"},
        format="json",
    )
    deleted = view_only_without_workspace.delete(f"/tracer/dashboard/{dashboard.id}/")

    dashboard.refresh_from_db()
    assert patched.status_code == status.HTTP_403_FORBIDDEN, patched.content
    assert deleted.status_code == status.HTTP_403_FORBIDDEN, deleted.content
    assert dashboard.name == "restricted-dashboard"
    assert dashboard.deleted is False


def test_an_admin_without_a_workspace_still_reads_and_changes_every_workspace(
    idp_a, make_saml_token, saml_tenants, restricted
):
    """The control: an Admin can access every workspace of the org, so a
    session that resolves no workspace (here the org has no active default
    workspace and a1b belongs to none) keeps reading and writing them."""
    Workspace.objects.filter(id=saml_tenants.workspace_a.id).update(is_active=False)
    client = _saml_client(make_saml_token, saml_tenants.a1b, saml_tenants.org_a, idp_a)

    listed = client.get("/tracer/dashboard/")
    patched = client.patch(
        f"/tracer/dashboard/{restricted.dashboard.id}/",
        {"name": "renamed-by-admin"},
        format="json",
    )

    restricted.dashboard.refresh_from_db()
    assert listed.status_code == status.HTTP_200_OK, listed.content
    assert "restricted-dashboard" in listed.content.decode()
    assert patched.status_code == status.HTTP_200_OK, patched.content
    assert restricted.dashboard.name == "renamed-by-admin"
