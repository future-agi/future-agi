"""A user with no organization reaches no organization's gateway data.

With request.organization None (a user whose memberships are all inactive), no
agentcc viewset serves any org's rows. These tests authenticate through the
real token endpoint and the real authentication class, never
``force_authenticate``.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from agentcc.models import (
    AgentccAPIKey,
    AgentccBlocklist,
    AgentccOrgConfig,
    AgentccRequestLog,
    AgentccSession,
    AgentccWebhook,
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.utils.base_viewset import (
    BaseModelViewSetMixin,
    BaseModelViewSetMixinWithUserOrg,
)
from tracer.models.dashboard import Dashboard

PASSWORD = "testpassword123"


def _org_with_member(name):
    org = Organization.objects.create(name=name)
    user = User.objects.create_user(
        email=f"orgless-{uuid.uuid4().hex[:8]}@futureagi.com",
        password=PASSWORD,
        name=name + " member",
        organization=org,
        organization_role=OrganizationRoles.MEMBER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=org,
        defaults={
            "role": OrganizationRoles.MEMBER,
            "level": Level.MEMBER,
            "is_active": True,
        },
    )
    workspace = Workspace.objects.create(
        name=name + " ws",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": "Workspace Member",
            "level": Level.MEMBER,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    return org, user, membership


def _remove_from_org(membership):
    """What rbac_views does when an org admin removes a member."""
    membership.is_active = False
    membership.save(update_fields=["is_active"])
    WorkspaceMembership.objects.filter(organization_membership=membership).update(
        is_active=False
    )


def _token_client(user):
    client = APIClient()
    resp = client.post(
        "/accounts/token/",
        {"email": user.email, "password": PASSWORD},
        format="json",
    )
    assert resp.status_code == status.HTTP_200_OK, resp.data
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access']}")
    return client


def _orgless_client():
    """A client authenticated as a user whose memberships are all inactive."""
    _, ex_member, membership = _org_with_member("Former Org")
    client = _token_client(ex_member)
    _remove_from_org(membership)
    return client


@pytest.fixture
def victim_org(db):
    return Organization.objects.create(name="Victim Org")


@pytest.fixture
def victim_key(victim_org):
    return AgentccAPIKey.no_workspace_objects.create(
        gateway_key_id="gw-victim-key",
        name="victim-production-key",
        organization=victim_org,
        workspace=None,
        key_hash="9" * 64,
        key_prefix="sk-agentcc-9...",
        metadata={"org_id": str(victim_org.id), "env": "prod"},
    )


@pytest.fixture
def gateway():
    """The gateway client auth_bridge would call; nothing may reach it."""
    mock_client = MagicMock()
    mock_client.revoke_key.return_value = {"status": "revoked"}
    mock_client.update_key.return_value = {}
    with patch(
        "agentcc.services.auth_bridge.get_gateway_client", return_value=mock_client
    ):
        yield mock_client


def _assert_key_untouched(key, gateway):
    row = AgentccAPIKey._base_manager.get(id=key.id)
    assert (row.deleted, row.status, row.name, row.allowed_models) == (
        False,
        AgentccAPIKey.ACTIVE,
        "victim-production-key",
        [],
    )
    gateway.revoke_key.assert_not_called()
    gateway.update_key.assert_not_called()


@pytest.mark.integration
@pytest.mark.api
class TestOrglessUserCannotReachAnotherOrgsKeys:
    def test_list_does_not_show_another_orgs_key(self, victim_key):
        client = _orgless_client()

        resp = client.get("/agentcc/api-keys/")

        assert resp.status_code == status.HTTP_200_OK, resp.content
        assert resp.json()["result"] == []

    def test_login_after_removal_lists_nothing(self, victim_key):
        _, ex_member, membership = _org_with_member("Former Org 2")
        _remove_from_org(membership)
        client = _token_client(ex_member)  # logs in only after removal

        resp = client.get("/agentcc/api-keys/")

        assert resp.status_code == status.HTTP_200_OK, resp.content
        assert resp.json()["result"] == []

    def test_retrieve_is_not_found(self, victim_key):
        client = _orgless_client()

        resp = client.get(f"/agentcc/api-keys/{victim_key.id}/")

        assert resp.status_code == status.HTTP_404_NOT_FOUND, resp.content
        assert "gw-victim-key" not in resp.content.decode()

    def test_update_is_not_found_and_changes_nothing(self, victim_key, gateway):
        client = _orgless_client()

        resp = client.patch(
            f"/agentcc/api-keys/{victim_key.id}/",
            {"allowed_models": ["nothing-real"], "name": "pwned"},
            format="json",
        )

        assert resp.status_code == status.HTTP_404_NOT_FOUND, resp.content
        _assert_key_untouched(victim_key, gateway)

    def test_revoke_is_not_found_and_revokes_nothing(self, victim_key, gateway):
        client = _orgless_client()

        resp = client.post(f"/agentcc/api-keys/{victim_key.id}/revoke/")

        assert resp.status_code == status.HTTP_404_NOT_FOUND, resp.content
        _assert_key_untouched(victim_key, gateway)

    def test_delete_is_not_found_and_revokes_nothing(self, victim_key, gateway):
        client = _orgless_client()

        resp = client.delete(f"/agentcc/api-keys/{victim_key.id}/")

        assert resp.status_code == status.HTTP_404_NOT_FOUND, resp.content
        _assert_key_untouched(victim_key, gateway)


def _victim_request_log(org):
    return AgentccRequestLog.no_workspace_objects.create(
        organization=org,
        request_id="req-victim",
        model="gpt-4o",
        request_body={"messages": [{"role": "user", "content": "victim secret"}]},
    )


def _victim_org_config(org):
    return AgentccOrgConfig.no_workspace_objects.create(
        organization=org,
        version=1,
        is_active=True,
        routing={"strategy": "victim-routing"},
    )


def _victim_session(org):
    return AgentccSession.no_workspace_objects.create(
        organization=org, session_id="victim-session", name="victim session"
    )


def _victim_webhook(org):
    return AgentccWebhook.no_workspace_objects.create(
        organization=org,
        name="victim webhook",
        url="https://victim.example.com/hook",
        secret="victim-webhook-signing-secret",
    )


def _victim_blocklist(org):
    return AgentccBlocklist.no_workspace_objects.create(
        organization=org, name="victim blocklist", words=["victim-word"]
    )


ORG_SCOPED_ENDPOINTS = [
    pytest.param("request-logs", _victim_request_log, id="request-logs"),
    pytest.param("org-configs", _victim_org_config, id="org-configs"),
    pytest.param("sessions", _victim_session, id="sessions"),
    pytest.param("webhooks", _victim_webhook, id="webhooks"),
    pytest.param("blocklists", _victim_blocklist, id="blocklists"),
]


@pytest.mark.integration
@pytest.mark.api
class TestOrglessUserCannotReadOtherOrgsGatewayData:
    """The viewsets that take their org scope from the shared mixin."""

    @pytest.mark.parametrize("path,make_row", ORG_SCOPED_ENDPOINTS)
    def test_list_shows_no_other_orgs_rows(self, victim_org, path, make_row):
        row = make_row(victim_org)
        client = _orgless_client()

        resp = client.get(f"/agentcc/{path}/")

        assert str(row.id) not in resp.content.decode(), (
            f"another org's {path} row in the list: "
            f"{resp.status_code} {resp.content[:400]!r}"
        )
        assert str(victim_org.id) not in resp.content.decode()

    @pytest.mark.parametrize("path,make_row", ORG_SCOPED_ENDPOINTS)
    def test_detail_is_refused_and_shows_nothing(self, victim_org, path, make_row):
        row = make_row(victim_org)
        client = _orgless_client()

        resp = client.get(f"/agentcc/{path}/{row.id}/")

        # 404, except sessions, whose retrieve answers any unknown id with 400.
        assert resp.status_code in (
            status.HTTP_404_NOT_FOUND,
            status.HTTP_400_BAD_REQUEST,
        ), (
            f"another org's {path} row in the detail: "
            f"{resp.status_code} {resp.content[:400]!r}"
        )
        assert str(victim_org.id) not in resp.content.decode()

    def test_request_log_body_is_not_returned(self, victim_org):
        log = _victim_request_log(victim_org)
        client = _orgless_client()

        resp = client.get(f"/agentcc/request-logs/{log.id}/")

        assert "victim secret" not in resp.content.decode()


def _victim_dashboard(org):
    """A tracer dashboard: workspace-scoped, with no organization field; the
    shared mixins scope it through its workspace's organization."""
    owner = User.objects.create_user(
        email=f"victim-{uuid.uuid4().hex[:8]}@futureagi.com",
        password=PASSWORD,
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
    return Dashboard.no_workspace_objects.create(
        name="victim-dashboard",
        workspace=workspace,
        created_by=owner,
        updated_by=owner,
    )


@pytest.mark.integration
@pytest.mark.api
class TestOrglessUserCannotReadWorkspaceScopedData:
    """The shared mixins also back viewsets whose model is scoped by
    workspace only; with no organization there is no workspace either, and
    the workspace filter still applies."""

    def test_dashboard_list_shows_no_other_orgs_dashboard(self, victim_org):
        _victim_dashboard(victim_org)
        client = _orgless_client()

        resp = client.get("/tracer/dashboard/")

        assert "victim-dashboard" not in resp.content.decode(), (
            f"another org's dashboard in the list: "
            f"{resp.status_code} {resp.content[:400]!r}"
        )

    def test_dashboard_detail_shows_nothing(self, victim_org):
        dashboard = _victim_dashboard(victim_org)
        client = _orgless_client()

        resp = client.get(f"/tracer/dashboard/{dashboard.id}/")

        assert resp.status_code != status.HTTP_200_OK, resp.content
        assert "victim-dashboard" not in resp.content.decode()


def _view(mixin, organization, user, queryset=None):
    class _Probe(mixin):
        pass

    _Probe.queryset = (
        queryset
        if queryset is not None
        else AgentccBlocklist.no_workspace_objects.all()
    )
    view = _Probe()
    view.request = SimpleNamespace(organization=organization, workspace=None, user=user)
    return view


@pytest.mark.integration
@pytest.mark.parametrize(
    "mixin",
    [BaseModelViewSetMixin, BaseModelViewSetMixinWithUserOrg],
    ids=["BaseModelViewSetMixin", "BaseModelViewSetMixinWithUserOrg"],
)
class TestViewSetMixinsFailClosedWithoutAnOrganization:
    def test_no_organization_returns_no_rows(self, db, mixin):
        org_a = Organization.objects.create(name="Org A")
        org_b = Organization.objects.create(name="Org B")
        _victim_blocklist(org_a)
        _victim_blocklist(org_b)
        _, ex_member, membership = _org_with_member("Former Org 3")
        _remove_from_org(membership)

        rows = _view(mixin, None, ex_member).get_queryset()

        assert list(rows) == []

    def test_no_organization_returns_no_rows_of_a_workspace_scoped_model(
        self, db, mixin
    ):
        _victim_dashboard(Organization.objects.create(name="Org A"))
        _, ex_member, membership = _org_with_member("Former Org 4")
        _remove_from_org(membership)

        view = _view(mixin, None, ex_member, Dashboard.no_workspace_objects.all())

        assert list(view.get_queryset()) == []

    def test_request_organization_scopes_to_that_org(self, db, mixin):
        org_a = Organization.objects.create(name="Org A")
        org_b = Organization.objects.create(name="Org B")
        mine = _victim_blocklist(org_a)
        _victim_blocklist(org_b)
        _, member, _ = _org_with_member("Org C")

        rows = _view(mixin, org_a, member).get_queryset()

        assert [r.id for r in rows] == [mine.id]

    def test_active_membership_is_the_fallback_organization(self, db, mixin):
        org_b = Organization.objects.create(name="Org B")
        _victim_blocklist(org_b)
        member_org, member, _ = _org_with_member("Member Org")
        mine = _victim_blocklist(member_org)

        rows = _view(mixin, None, member).get_queryset()

        assert [r.id for r in rows] == [mine.id]
