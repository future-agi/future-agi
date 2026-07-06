"""Tests for the workspace→org membership FK fix (TH-5928)."""

import pytest
from rest_framework import status

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import WorkspaceMembership
from accounts.services.workspace_members import list_workspace_members
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)

WS_MEMBERS_URL = "/accounts/workspace/{workspace_id}/members/"
WS_MEMBER_ADD_URL = "/accounts/workspaces/{workspace_id}/members/"


def _make_member(organization, email, level=Level.MEMBER, role="Member"):
    set_workspace_context(organization=organization)
    u = User.objects.create_user(
        email=email,
        password="pass123",
        name=role,
        organization=organization,
        organization_role=role,
    )
    om = OrganizationMembership.objects.create(
        user=u,
        organization=organization,
        role=role,
        level=level,
        is_active=True,
    )
    return u, om


def _add_to_workspace(workspace, user, org_membership):
    return WorkspaceMembership.objects.create(
        workspace=workspace,
        user=user,
        role="workspace_member",
        level=Level.WORKSPACE_MEMBER,
        organization_membership=org_membership,
        is_active=True,
    )


def _null_the_fk(ws_mem):
    WorkspaceMembership.objects.filter(pk=ws_mem.pk).update(
        organization_membership=None
    )


@pytest.mark.django_db
class TestListWorkspaceMembersSelector:

    def test_returns_member_with_org_role_when_fk_set(
        self, organization, workspace, user
    ):
        member, om = _make_member(organization, "alice@futureagi.com")
        _add_to_workspace(workspace, member, om)

        page = list_workspace_members(workspace=workspace, organization=organization)

        rows = {r["email"]: r for r in page["results"]}
        assert "alice@futureagi.com" in rows
        assert rows["alice@futureagi.com"]["org_role"] is not None
        assert rows["alice@futureagi.com"]["org_level"] == Level.MEMBER

    def test_fallback_resolves_org_role_when_fk_null(
        self, organization, workspace, user
    ):
        member, om = _make_member(organization, "bob@futureagi.com")
        ws_mem = _add_to_workspace(workspace, member, om)
        _null_the_fk(ws_mem)

        page = list_workspace_members(workspace=workspace, organization=organization)

        row = next(r for r in page["results"] if r["email"] == "bob@futureagi.com")
        assert row["org_role"] is not None
        assert row["org_level"] == Level.MEMBER

    def test_fallback_emits_warning_with_workspace_and_user_ids(
        self, organization, workspace, user, caplog
    ):
        member, om = _make_member(organization, "carol@futureagi.com")
        ws_mem = _add_to_workspace(workspace, member, om)
        _null_the_fk(ws_mem)

        with caplog.at_level("WARNING"):
            list_workspace_members(workspace=workspace, organization=organization)

        hits = [
            r
            for r in caplog.records
            if "workspace_member_list_null_org_fk" in r.getMessage()
        ]
        assert hits, "expected fallback warning to fire"
        msg = hits[0].getMessage()
        assert str(member.id) in msg
        assert str(workspace.id) in msg

    def test_no_warning_when_all_fks_populated(
        self, organization, workspace, user, caplog
    ):
        member, om = _make_member(organization, "dave@futureagi.com")
        _add_to_workspace(workspace, member, om)

        with caplog.at_level("WARNING"):
            list_workspace_members(workspace=workspace, organization=organization)

        assert not any(
            "workspace_member_list_null_org_fk" in r.getMessage()
            for r in caplog.records
        )

    def test_excludes_inactive_workspace_memberships(
        self, organization, workspace, user
    ):
        member, om = _make_member(organization, "eve@futureagi.com")
        ws_mem = _add_to_workspace(workspace, member, om)
        WorkspaceMembership.objects.filter(pk=ws_mem.pk).update(is_active=False)

        page = list_workspace_members(workspace=workspace, organization=organization)

        emails = {r["email"] for r in page["results"]}
        assert "eve@futureagi.com" not in emails

    def test_pagination_returns_correct_slice(self, organization, workspace, user):
        for i in range(5):
            m, om = _make_member(organization, f"u{i}@futureagi.com")
            _add_to_workspace(workspace, m, om)

        page1 = list_workspace_members(
            workspace=workspace, organization=organization, page=1, limit=2
        )
        page2 = list_workspace_members(
            workspace=workspace, organization=organization, page=2, limit=2
        )

        assert page1["total"] == page2["total"]
        assert len(page1["results"]) == 2
        assert len(page2["results"]) == 2
        emails1 = {r["email"] for r in page1["results"]}
        emails2 = {r["email"] for r in page2["results"]}
        assert emails1.isdisjoint(emails2)

    def test_fallback_query_count_is_bounded(self, organization, workspace, user):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        for i in range(10):
            m, om = _make_member(organization, f"q{i}@futureagi.com")
            ws_mem = _add_to_workspace(workspace, m, om)
            _null_the_fk(ws_mem)

        with CaptureQueriesContext(connection) as ctx:
            list_workspace_members(workspace=workspace, organization=organization)
        small_count = len(ctx)

        for i in range(40):
            m, om = _make_member(organization, f"qq{i}@futureagi.com")
            ws_mem = _add_to_workspace(workspace, m, om)
            _null_the_fk(ws_mem)

        with CaptureQueriesContext(connection) as ctx:
            list_workspace_members(workspace=workspace, organization=organization)
        large_count = len(ctx)

        # Fallback uses one membership map per call, not one query per row.
        assert large_count <= small_count + 2


@pytest.mark.django_db
class TestWorkspaceMemberListViewFKFallback:

    def test_response_renders_org_role_when_fk_null(
        self, auth_client, organization, workspace
    ):
        member, om = _make_member(organization, "view-null@futureagi.com")
        ws_mem = _add_to_workspace(workspace, member, om)
        _null_the_fk(ws_mem)

        resp = auth_client.get(WS_MEMBERS_URL.format(workspace_id=workspace.id))

        assert resp.status_code == status.HTTP_200_OK
        rows = {r["email"]: r for r in resp.data["result"]["results"]}
        assert "view-null@futureagi.com" in rows
        # CamelCaseJSONRenderer flips org_role → orgRole on the wire.
        row = rows["view-null@futureagi.com"]
        assert row.get("orgRole") or row.get("org_role")


@pytest.mark.django_db
class TestWorkspaceMembershipFKInvariant:

    def test_no_null_fk_rows_for_active_memberships(
        self, organization, workspace, user
    ):
        for i in range(3):
            m, om = _make_member(organization, f"inv{i}@futureagi.com")
            _add_to_workspace(workspace, m, om)

        null_count = WorkspaceMembership.no_workspace_objects.filter(
            workspace__organization=organization,
            is_active=True,
            organization_membership__isnull=True,
        ).count()

        assert null_count == 0


@pytest.mark.django_db
class TestCreateWorkspaceMembershipFactory:
    """The single create path must always resolve + set the org FK — the
    invariant that prevents the NULL-FK drift this PR fixes."""

    def test_factory_sets_org_fk(self, organization, workspace):
        from accounts.services.workspace_membership import create_workspace_membership

        member, om = _make_member(organization, "factory@futureagi.com")
        ws_mem = create_workspace_membership(
            workspace=workspace,
            user=member,
            role="workspace_member",
            level=Level.WORKSPACE_MEMBER,
            invited_by=member,
        )
        ws_mem.refresh_from_db()
        assert ws_mem.organization_membership_id == om.id

    def test_factory_no_fk_when_no_active_org_membership(self, organization, workspace):
        from accounts.services.workspace_membership import create_workspace_membership

        member, om = _make_member(organization, "noactive@futureagi.com")
        # Only an inactive org membership -> nothing legitimate to attach.
        OrganizationMembership.objects.filter(pk=om.pk).update(is_active=False)
        ws_mem = create_workspace_membership(
            workspace=workspace,
            user=member,
            role="workspace_member",
            level=Level.WORKSPACE_MEMBER,
            invited_by=member,
        )
        ws_mem.refresh_from_db()
        assert ws_mem.organization_membership_id is None

    def test_resolve_skips_inactive_membership(self, organization):
        from accounts.services.workspace_membership import resolve_org_membership

        member, om = _make_member(organization, "resolve@futureagi.com")
        assert resolve_org_membership(member, organization).id == om.id
        # An inactive/cancelled membership must never be resolved (the migration
        # bug the review flagged: attaching it would *hide* the member).
        OrganizationMembership.objects.filter(pk=om.pk).update(is_active=False)
        assert resolve_org_membership(member, organization) is None

    def test_explicit_org_membership_skips_resolution(
        self, organization, workspace, monkeypatch
    ):
        # When the caller already passes ``organization_membership``, the factory
        # must NOT re-query for it (the old ``setdefault`` evaluated the resolver
        # unconditionally — a wasted query per call, N× inside bulk loops).
        from accounts.services import workspace_membership as svc

        member, om = _make_member(organization, "explicit@futureagi.com")
        calls = []
        real_resolve = svc.resolve_org_membership

        def _spy(*args, **kwargs):
            calls.append((args, kwargs))
            return real_resolve(*args, **kwargs)

        monkeypatch.setattr(svc, "resolve_org_membership", _spy)

        ws_mem = svc.create_workspace_membership(
            workspace=workspace,
            user=member,
            role="workspace_member",
            level=Level.WORKSPACE_MEMBER,
            organization_membership=om,
        )

        ws_mem.refresh_from_db()
        assert ws_mem.organization_membership_id == om.id
        assert calls == [], "resolver must not run when FK is passed explicitly"


@pytest.mark.django_db
def test_workspace_member_row_serializer_accepts_built_row(organization):
    from accounts.serializers.rbac import WorkspaceMemberRowSerializer
    from accounts.services.workspace_members import _member_row

    member, om = _make_member(organization, "row@futureagi.com")
    row = _member_row(
        row_id=member.id,
        name="Row",
        email="row@futureagi.com",
        ws_level=Level.WORKSPACE_MEMBER,
        org_level=Level.MEMBER,
        status="Active",
        created_at="",
        member_type="member",
    )
    serializer = WorkspaceMemberRowSerializer(data=row)
    assert serializer.is_valid(), serializer.errors


@pytest.mark.django_db
def test_list_output_validates_against_response_contract(organization, workspace):
    """Every row source from the de-duped builder (explicit member +
    auto-access admin) must satisfy WorkspaceMemberListResponse's contract."""
    from accounts.serializers.rbac import WorkspaceMemberListResultSerializer

    member, om = _make_member(organization, "rowmember@futureagi.com")
    _add_to_workspace(workspace, member, om)
    # An org Admin who is not an explicit ws member -> auto-access row source.
    _make_member(
        organization, "rowadmin@futureagi.com", level=Level.ADMIN, role="Admin"
    )

    page = list_workspace_members(workspace=workspace, organization=organization)

    serializer = WorkspaceMemberListResultSerializer(data=page)
    assert serializer.is_valid(), serializer.errors
    assert "member" in {r["type"] for r in page["results"]}


@pytest.mark.django_db
class TestWorkspaceMemberAddEndpointFKRegression:
    """End-to-end guard for the real write path the bug came from.

    Unlike ``TestWorkspaceMembershipFKInvariant`` (which seeds rows via a helper
    that sets the FK by hand, so it can't catch a regression), this drives the
    actual ``POST /accounts/workspaces/<id>/members/`` endpoint and asserts the
    created ``WorkspaceMembership`` has its ``organization_membership`` FK set.
    It fails the moment a write path stops routing through
    ``create_workspace_membership`` — the exact drift TH-5928 fixes.
    """

    def test_add_existing_member_via_endpoint_populates_org_fk(
        self, auth_client, organization, workspace
    ):
        member, om = _make_member(organization, "ws-add@futureagi.com")

        resp = auth_client.post(
            WS_MEMBER_ADD_URL.format(workspace_id=workspace.id),
            {"users": [{"email": member.email, "role": "workspace_member"}]},
            format="json",
        )

        assert resp.status_code == status.HTTP_201_CREATED, resp.data
        ws_mem = WorkspaceMembership.no_workspace_objects.get(
            workspace=workspace, user=member
        )
        assert ws_mem.organization_membership_id == om.id

    def test_added_member_shows_org_role_on_members_list(
        self, auth_client, organization, workspace
    ):
        # The user-visible symptom: a freshly added member must carry an Org Role
        # chip on the Members page (no NULL FK -> no blank org_role).
        member, om = _make_member(organization, "ws-add-role@futureagi.com")

        add = auth_client.post(
            WS_MEMBER_ADD_URL.format(workspace_id=workspace.id),
            {"users": [{"email": member.email, "role": "workspace_member"}]},
            format="json",
        )
        assert add.status_code == status.HTTP_201_CREATED, add.data

        listing = auth_client.get(WS_MEMBERS_URL.format(workspace_id=workspace.id))
        assert listing.status_code == status.HTTP_200_OK
        rows = {r["email"]: r for r in listing.data["result"]["results"]}
        assert "ws-add-role@futureagi.com" in rows
        # CamelCaseJSONRenderer flips org_role -> orgRole on the wire.
        row = rows["ws-add-role@futureagi.com"]
        assert row.get("orgRole") or row.get("org_role")


@pytest.mark.django_db
class TestGetOrCreateSitesSetOrgFK:
    """The two ``get_or_create`` root-cause sites (TH-5928) don't route through
    ``create_workspace_membership`` — they inline ``organization_membership`` in
    ``defaults``. The factory tests can't cover them, so assert the FK directly
    on each site. Each fails if its ``defaults`` line drops the FK again.
    """

    def test_switch_org_default_workspace_bootstrap_sets_fk(self, organization):
        # organization_selection.py::SwitchOrganizationView._resolve_workspace_for_org
        # bootstraps a default workspace + membership when the org has none.
        from accounts.views.organization_selection import SwitchOrganizationView

        member, om = _make_member(organization, "switch-boot@futureagi.com")

        ws = SwitchOrganizationView()._resolve_workspace_for_org(member, organization)

        ws_mem = WorkspaceMembership.no_workspace_objects.get(workspace=ws, user=member)
        assert ws_mem.organization_membership_id == om.id

    def test_workspace_invite_existing_member_sets_fk(
        self, auth_client, organization, workspace
    ):
        # workspace_management.py::WorkspaceInviteAPIView.post — existing active
        # org member added to a workspace via the bulk-invite get_or_create path
        # (no email send), reachable end-to-end at POST /accounts/workspace/invite/.
        member, om = _make_member(organization, "ws-invite@futureagi.com")

        resp = auth_client.post(
            "/accounts/workspace/invite/",
            {
                "emails": [member.email],
                "role": OrganizationRoles.WORKSPACE_MEMBER,
                "workspace_ids": [str(workspace.id)],
                "select_all": False,
            },
            format="json",
        )

        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
        ), resp.data
        ws_mem = WorkspaceMembership.no_workspace_objects.get(
            workspace=workspace, user=member
        )
        assert ws_mem.organization_membership_id == om.id


@pytest.mark.django_db
class TestBackfillMigration:
    """Exercises the 0022 data migration that heals already-drifted NULL FKs.

    Verifies the three behaviours the raw SQL encodes: it links NULL rows to the
    active org membership, never attaches an inactive one, and is idempotent.
    """

    def _run_backfill(self):
        import importlib

        from django.apps import apps as global_apps
        from django.db import connection

        migration = importlib.import_module(
            "accounts.migrations.0022_backfill_ws_org_membership_fk"
        )

        class _SchemaEditorShim:
            pass

        shim = _SchemaEditorShim()
        shim.connection = connection
        # Mimic migration-time: no request workspace context, so the NULL-row
        # scan isn't narrowed by the workspace-scoped default manager.
        clear_workspace_context()
        migration.backfill_ws_org_membership_fk(global_apps, shim)

    def test_backfill_links_null_fk_to_active_org_membership(
        self, organization, workspace
    ):
        member, om = _make_member(organization, "heal@futureagi.com")
        ws_mem = _add_to_workspace(workspace, member, om)
        _null_the_fk(ws_mem)

        self._run_backfill()

        ws_mem.refresh_from_db()
        assert ws_mem.organization_membership_id == om.id

    def test_backfill_never_attaches_inactive_org_membership(
        self, organization, workspace
    ):
        member, om = _make_member(organization, "heal-inactive@futureagi.com")
        ws_mem = _add_to_workspace(workspace, member, om)
        _null_the_fk(ws_mem)
        # Only an inactive org membership exists -> attaching it would hide the
        # member, so the migration must leave the FK NULL.
        OrganizationMembership.objects.filter(pk=om.pk).update(is_active=False)

        self._run_backfill()

        ws_mem.refresh_from_db()
        assert ws_mem.organization_membership_id is None

    def test_backfill_is_idempotent_on_correct_rows(self, organization, workspace):
        member, om = _make_member(organization, "heal-idem@futureagi.com")
        ws_mem = _add_to_workspace(workspace, member, om)  # FK already correct

        self._run_backfill()
        self._run_backfill()  # second run must be a no-op

        ws_mem.refresh_from_db()
        assert ws_mem.organization_membership_id == om.id
