"""Every organization, workspace and member creation path honours the
Community edition rule (TH-8084, architecture section 3).

Self-hosted without a usable licence: 1 organization, 1 workspace, up to 3
organization members. Refusals are HTTP 402 ENTERPRISE_FEATURE_REQUIRED with an
enterprise_gate block. Cloud and licensed installs are unaffected, and installs
already over a limit keep everything.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode

from accounts.models.organization import Organization
from accounts.models.organization_invite import OrganizationInvite
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace
from tfc.capabilities import edition
from tfc.capabilities.errors import EnterpriseFeatureRequired
from tfc.capabilities.tests.edition_factories import (
    make_invite,
    make_member,
    make_org,
    make_owner,
    make_user,
    make_workspace,
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

try:
    from ee.licensing.tests.fixtures import (  # noqa: F401
        install_license,
        test_signing_keypair,
    )
except ImportError:  # OSS lane: licence cases carry requires_ee and are skipped
    pass

pytestmark = [pytest.mark.edition_rule, pytest.mark.django_db]

SIGNUP_URL = "/accounts/signup/"
ORG_CREATE_URL = "/accounts/organizations/create/"
ORG_NEW_URL = "/accounts/organizations/new/"
APPSMITH_URL = "/accounts/appsmith/users/"
WORKSPACES_URL = "/accounts/workspaces/"
TEAM_URL = "/accounts/team/users/"
WS_INVITE_URL = "/accounts/workspace/invite/"
INVITE_URL = "/accounts/organization/invite/"
INVITE_RESEND_URL = "/accounts/organization/invite/resend/"
REACTIVATE_URL = "/accounts/organization/members/reactivate/"
MEMBERS_URL = "/accounts/organization/members/"

COMMUNITY_ORG_REFUSAL = (
    "Community includes one organization; invite the user from Settings, "
    "or activate an Enterprise license."
)


@pytest.fixture
def community(monkeypatch):
    monkeypatch.setattr(edition, "is_cloud", lambda: False)
    monkeypatch.setattr(edition, "enterprise_license_usable", lambda: False)


@pytest.fixture
def self_hosted(monkeypatch):
    monkeypatch.setattr(edition, "is_cloud", lambda: False)


@pytest.fixture
def oss_signup(monkeypatch):
    """Self-hosted signup: password accepted, no reCAPTCHA, auto-login."""
    import accounts.views.signup as signup_views

    monkeypatch.setattr(signup_views, "is_oss", lambda: True)


def assert_gate(response, feature):
    assert response.status_code == 402, response.content
    body = response.json()
    assert body["code"] == "ENTERPRISE_FEATURE_REQUIRED"
    assert body["upgrade_required"] is True
    gate = body["enterprise_gate"]
    assert gate["feature"] == feature
    assert gate["edition"] == "community"
    assert gate["contact"] == "sales@futureagi.com"
    assert gate["activation_route"] == "/dashboard/settings/ee-licenses"
    return gate


def fill_seats(organization, total=3):
    """Top the organization up to ``total`` active members (owner included)."""
    current = edition.count(edition.EditionResource.MEMBER, organization=organization)
    return [make_member(organization) for _ in range(total - current)]


def _signup_payload(email):
    return {
        "email": email,
        "full_name": "New Person",
        "password": "Edition-Passw0rd!9",
    }


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------


class TestSignupAndBootstrap:
    def test_first_signup_on_an_empty_install_is_allowed(
        self, community, oss_signup, api_client
    ):
        """AC-01 / O1: the first signup creates the install's organization."""
        assert Organization.objects.count() == 0
        response = api_client.post(
            SIGNUP_URL, _signup_payload("first@futureagi.com"), format="json"
        )
        assert response.status_code == 200, response.content
        assert Organization.objects.count() == 1

    def test_second_self_signup_returns_the_gate(
        self, community, oss_signup, api_client, organization
    ):
        """AC-08 / O1: a second self-signup would create a 2nd organization."""
        response = api_client.post(
            SIGNUP_URL,
            _signup_payload("second@futureagi.com"),
            format="json",
            HTTP_X_EE_LICENSE_KEY="forged",
            HTTP_X_DEPLOYMENT_MODE="cloud",
        )
        gate = assert_gate(response, "organizations")
        assert (gate["limit"], gate["current"], gate["requested"]) == (1, 1, 1)
        assert not User.objects.filter(email="second@futureagi.com").exists()
        assert Organization.objects.count() == 1

    def test_create_user_bootstraps_an_empty_install(self, community):
        """AC-01 / O8: manage.py create_user on an empty database succeeds."""
        call_command(
            "create_user",
            "--email",
            "admin@example.com",
            "--name",
            "Admin",
            "--password",
            "Edition-Passw0rd!9",
        )
        assert Organization.objects.count() == 1

    def test_second_create_user_gives_an_operator_error(self, community, organization):
        """AC-08 / O8 (C1): a second create_user is a readable CommandError."""
        with pytest.raises(CommandError) as exc_info:
            call_command(
                "create_user",
                "--email",
                "second-admin@example.com",
                "--name",
                "Second",
                "--password",
                "Edition-Passw0rd!9",
            )
        assert str(exc_info.value) == COMMUNITY_ORG_REFUSAL
        assert not User.objects.filter(email="second-admin@example.com").exists()

    def test_bootstrap_install_first_admin_reports_the_rule(
        self, community, organization
    ):
        """AC-08 / O8 (C1): the Helm first-admin path fails with the same text."""
        from tfc.management.commands.bootstrap_install import (
            BootstrapError,
            first_admin,
        )

        with pytest.raises(BootstrapError) as exc_info:
            first_admin(
                lambda line: None,
                env={
                    "FAGI_ADMIN_EMAIL": "helm-admin@example.com",
                    "FAGI_ADMIN_NAME": "Helm Admin",
                    "FAGI_ADMIN_PASSWORD": "Edition-Passw0rd!9",
                },
            )
        assert COMMUNITY_ORG_REFUSAL in str(exc_info.value)

    @pytest.mark.requires_ee
    def test_second_create_user_succeeds_with_a_test_signed_licence(
        self, self_hosted, install_license, organization
    ):
        """AC-10 / O8 (C1): a usable licence lifts the organization limit."""
        install_license("active")
        call_command(
            "create_user",
            "--email",
            "licensed-admin@example.com",
            "--name",
            "Licensed",
            "--password",
            "Edition-Passw0rd!9",
        )
        assert Organization.objects.count() == 2


class TestSsoJustInTime:
    def test_new_sso_user_is_refused_when_an_org_exists(self, community, organization):
        """AC-08 / O1: Auth0/GitHub JIT would create a 2nd organization."""
        from saml2_auth.views import resolve_sso_user

        with pytest.raises(EnterpriseFeatureRequired):
            resolve_sso_user("jit@example.com", "JIT User", None, "google")
        assert not User.objects.filter(email="jit@example.com").exists()

    def test_microsoft_jit_signup_is_refused_when_an_org_exists(
        self, community, organization
    ):
        """AC-08 / O1: the Microsoft callback calls first_signup directly."""
        from accounts.utils import first_signup

        with pytest.raises(EnterpriseFeatureRequired):
            first_signup(
                {"full_name": "MS", "email": "ms@example.com"}, mode="microsoft"
            )
        assert not User.objects.filter(email="ms@example.com").exists()

    def test_existing_sso_user_still_logs_in(self, community, user, monkeypatch):
        """AC-11: SSO for an existing (invited) user never reaches first_signup."""
        import saml2_auth.views as sso_views

        monkeypatch.setattr(sso_views, "track_mixpanel_event", lambda *a, **k: None)
        resolved, _next_url, new_org = sso_views.resolve_sso_user(
            user.email, user.name, None, "google"
        )
        assert resolved == user
        assert new_org == "false"


class TestOrganizationViews:
    def test_activation_that_would_create_an_org_returns_the_gate(
        self, community, organization, api_client
    ):
        """AC-08 / O2: email activation creates an organization."""
        from accounts.views.signup import account_activation_token

        pending = User.objects.create_user(
            email="activate@example.com", password="x", name="A", is_active=False
        )
        uid = urlsafe_base64_encode(force_bytes(pending.pk))
        token = account_activation_token.make_token(pending)
        response = api_client.get(f"/accounts/activate/{uid}/{token}/")
        assert_gate(response, "organizations")
        assert Organization.objects.count() == 1

    def test_orgless_user_cannot_start_a_second_org(
        self, community, organization, api_client
    ):
        """AC-08 / O3: an org-less user starting a new organization."""
        orgless = make_user()
        api_client.force_authenticate(user=orgless)
        response = api_client.post(
            ORG_CREATE_URL, {"organization_name": "Mine"}, format="json"
        )
        assert_gate(response, "organizations")
        assert Organization.objects.count() == 1

    def test_additional_org_returns_the_gate(self, community, auth_client):
        """AC-08 / O4: creating an additional organization."""
        response = auth_client.post(ORG_NEW_URL, {"name": "Second Org"}, format="json")
        assert_gate(response, "organizations")
        assert Organization.objects.count() == 1
        assert Workspace.no_workspace_objects.count() == 1

    def test_appsmith_operator_api_returns_the_gate(
        self, community, organization, api_client, monkeypatch
    ):
        """AC-08 / O5: the operator API creating an organization and user."""
        monkeypatch.setenv("API_KEY", "operator-key")
        response = api_client.post(
            APPSMITH_URL,
            {
                "email": "appsmith@example.com",
                "password": "Edition-Passw0rd!9",
                "organization_name": "Appsmith Org",
                "send_credential": False,
            },
            format="json",
            HTTP_X_API_KEY="operator-key",
        )
        assert_gate(response, "organizations")
        assert not User.objects.filter(email="appsmith@example.com").exists()

    def test_marketplace_org_creation_is_gated_off_cloud(self, community, organization):
        """AC-08 / O6: defence in depth for marketplace URLs on expired-key installs."""
        from accounts.aws_marketplace_utils import create_organization_for_aws_customer
        from accounts.gcp_marketplace_utils import _create_organization

        with pytest.raises(EnterpriseFeatureRequired):
            create_organization_for_aws_customer(MagicMock(), "123456789012")
        with pytest.raises(EnterpriseFeatureRequired):
            _create_organization(MagicMock(procurement_account_id="abcdef0123456789"))
        assert Organization.objects.count() == 1


# ---------------------------------------------------------------------------
# Workspaces
# ---------------------------------------------------------------------------


class TestWorkspaceCreation:
    def test_second_workspace_returns_the_gate(self, community, auth_client):
        """AC-08 / W1: WorkspaceManagementView.post."""
        response = auth_client.post(WORKSPACES_URL, {"name": "Second"}, format="json")
        gate = assert_gate(response, "workspaces")
        assert (gate["limit"], gate["current"]) == (1, 1)
        assert Workspace.no_workspace_objects.count() == 1

    def test_workspace_create_with_invites_checks_member_seats(
        self, community, auth_client, organization, workspace
    ):
        """AC-08 / W1: emails invited while creating a workspace need seats."""
        Workspace.no_workspace_objects.filter(pk=workspace.pk).update(is_active=False)
        fill_seats(organization)
        response = auth_client.post(
            WORKSPACES_URL,
            {
                "name": "Only Active",
                "emails": ["fourth@example.com"],
                "role": OrganizationRoles.WORKSPACE_MEMBER,
            },
            format="json",
        )
        assert_gate(response, "members")
        assert not User.objects.filter(email="fourth@example.com").exists()

    def test_team_view_named_workspace_returns_the_gate(self, community, auth_client):
        """AC-08 / W2: ManageTeamView.post with a new named workspace."""
        response = auth_client.post(
            TEAM_URL, {"workspace": {"name": "Team Space"}}, format="json"
        )
        assert_gate(response, "workspaces")
        assert not Workspace.no_workspace_objects.filter(name="Team Space").exists()

    def test_ai_tool_create_workspace_returns_a_gate_error(
        self, community, user, organization, workspace
    ):
        """AC-08 / W3: the AI tool returns a tool error carrying the gate."""
        from ai_tools.base import ToolContext
        from ai_tools.tools.users.create_workspace import (
            CreateWorkspaceInput,
            CreateWorkspaceTool,
        )

        result = CreateWorkspaceTool().execute(
            CreateWorkspaceInput(name="Tool Space"),
            ToolContext(user=user, organization=organization, workspace=workspace),
        )
        assert result.is_error
        assert result.error_code == "ENTERPRISE_FEATURE_REQUIRED"
        assert result.data["enterprise_gate"]["feature"] == "workspaces"
        assert "sales@futureagi.com" in result.content
        assert not Workspace.no_workspace_objects.filter(name="Tool Space").exists()

    def test_default_workspace_self_heal_is_exempt(
        self, community, auth_client, organization, user, workspace
    ):
        """AC-11 / W4: a missing default workspace is recreated, never gated."""
        Workspace.no_workspace_objects.filter(pk=workspace.pk).update(is_default=False)
        response = auth_client.post(TEAM_URL, {}, format="json")
        assert response.status_code in (200, 201), response.content
        assert Workspace.no_workspace_objects.filter(
            organization=organization, is_default=True
        ).exists()


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


def _invite(client, emails, workspace):
    return client.post(
        INVITE_URL,
        {
            "emails": emails,
            "org_level": Level.MEMBER,
            "workspace_access": [
                {"workspace_id": str(workspace.id), "level": Level.WORKSPACE_VIEWER}
            ],
        },
        format="json",
    )


class TestMemberPaths:
    def test_invite_up_to_three_then_gate_all_or_nothing(
        self, community, auth_client, organization, workspace
    ):
        """AC-08 / M1: the 3rd seat is allowed; a request past 3 is refused whole."""
        make_member(organization)
        assert _invite(auth_client, ["third@example.com"], workspace).status_code == 200

        response = _invite(
            auth_client, ["fourth@example.com", "fifth@example.com"], workspace
        )
        gate = assert_gate(response, "members")
        assert (gate["limit"], gate["current"], gate["requested"]) == (3, 3, 2)
        assert not OrganizationInvite.objects.filter(
            target_email__in=["fourth@example.com", "fifth@example.com"]
        ).exists()

    def test_reinviting_a_pending_email_needs_no_seat(
        self, community, auth_client, organization, workspace
    ):
        """AC-08 / M1: an email that already holds a seat is not refused."""
        make_member(organization)
        make_invite(organization, "pending@example.com")
        response = _invite(auth_client, ["pending@example.com"], workspace)
        assert response.status_code == 200, response.content

    def test_resend_of_an_unexpired_invite_is_allowed(
        self, community, auth_client, organization
    ):
        """AC-11 / M2: the seat was counted at invite time."""
        make_member(organization)
        invite = make_invite(organization, "pending@example.com")
        response = auth_client.post(
            INVITE_RESEND_URL, {"invite_id": str(invite.id)}, format="json"
        )
        assert response.status_code == 200, response.content

    def test_resend_reviving_an_expired_invite_returns_the_gate(
        self, community, auth_client, organization
    ):
        """AC-08 / M2: reviving an expired invite needs a new seat."""
        fill_seats(organization)
        invite = make_invite(organization, "stale@example.com", expired=True)
        response = auth_client.post(
            INVITE_RESEND_URL, {"invite_id": str(invite.id)}, format="json"
        )
        assert_gate(response, "members")
        invite.refresh_from_db()
        assert invite.is_expired

    def test_reactivating_a_member_returns_the_gate_when_full(
        self, community, auth_client, organization
    ):
        """AC-08 / M4: reactivation is an add."""
        fill_seats(organization)
        gone = make_member(organization, active=False)
        response = auth_client.post(
            REACTIVATE_URL, {"user_id": str(gone.id)}, format="json"
        )
        assert_gate(response, "members")
        assert not OrganizationMembership.no_workspace_objects.get(
            user=gone, organization=organization
        ).is_active

    def test_workspace_invite_returns_the_gate_when_full(
        self, community, auth_client, organization, workspace
    ):
        """AC-08 / M5: WorkspaceInviteAPIView."""
        fill_seats(organization)
        response = auth_client.post(
            WS_INVITE_URL,
            {
                "emails": ["fourth@example.com"],
                "role": OrganizationRoles.WORKSPACE_MEMBER,
                "workspace_ids": [str(workspace.id)],
            },
            format="json",
        )
        assert_gate(response, "members")
        assert not User.objects.filter(email="fourth@example.com").exists()

    def test_workspace_member_add_returns_the_gate_when_full(
        self, community, auth_client, organization, workspace
    ):
        """AC-08 / M6: WorkspaceMembershipView.post."""
        fill_seats(organization)
        response = auth_client.post(
            f"{WORKSPACES_URL}{workspace.id}/members/",
            {
                "users": [
                    {
                        "email": "fourth@example.com",
                        "role": OrganizationRoles.WORKSPACE_MEMBER,
                    }
                ]
            },
            format="json",
        )
        assert_gate(response, "members")
        assert not User.objects.filter(email="fourth@example.com").exists()

    def test_team_view_members_return_the_gate_not_a_429(
        self, community, auth_client, organization
    ):
        """AC-08 / M7: the A3 site off-cloud is the Enterprise gate."""
        fill_seats(organization)
        response = auth_client.post(
            TEAM_URL,
            {
                "members": [
                    {
                        "email": "fourth@example.com",
                        "name": "Fourth",
                        "role": OrganizationRoles.WORKSPACE_MEMBER,
                    }
                ]
            },
            format="json",
        )
        assert response.status_code != 429
        assert_gate(response, "members")
        assert not User.objects.filter(email="fourth@example.com").exists()

    def test_team_view_members_within_three_are_allowed(
        self, community, auth_client, organization
    ):
        """AC-08 / M7 (A3 replacement): no Free-tier USERS cap applies off-cloud."""
        response = auth_client.post(
            TEAM_URL,
            {
                "members": [
                    {
                        "email": f"m{i}@example.com",
                        "name": f"M{i}",
                        "role": OrganizationRoles.WORKSPACE_MEMBER,
                    }
                    for i in (2, 3)
                ]
            },
            format="json",
        )
        assert response.status_code in (200, 201), response.content
        assert (
            edition.count(edition.EditionResource.MEMBER, organization=organization)
            == 3
        )

    def test_ai_tool_invite_users_returns_a_gate_error(
        self, community, user, organization, workspace
    ):
        """AC-08 / M8: the AI invite tool is gated before creating users."""
        from ai_tools.base import ToolContext
        from ai_tools.tools.users.invite_users import InviteUsersInput, InviteUsersTool

        fill_seats(organization)
        result = InviteUsersTool().execute(
            InviteUsersInput(emails=["tool-invite@example.com"]),
            ToolContext(user=user, organization=organization, workspace=workspace),
        )
        assert result.is_error
        assert result.error_code == "ENTERPRISE_FEATURE_REQUIRED"
        assert result.data["enterprise_gate"]["feature"] == "members"
        assert not User.objects.filter(email="tool-invite@example.com").exists()

    def test_membership_backstop_refuses_unlisted_paths(self, community, organization):
        """AC-08 / M11: ensure_org_membership refuses a 4th seat off-cloud."""
        from accounts.services.workspace_membership import ensure_org_membership

        fill_seats(organization)
        stranger = make_user()
        with pytest.raises(EnterpriseFeatureRequired):
            ensure_org_membership(stranger, organization)
        assert not OrganizationMembership.all_objects.filter(user=stranger).exists()

    def test_membership_backstop_leaves_existing_members_alone(
        self, community, organization
    ):
        """AC-11 / M11: an existing membership never needs a seat."""
        from accounts.services.workspace_membership import ensure_org_membership

        members = fill_seats(organization)
        make_member(organization)  # now over the limit
        assert ensure_org_membership(members[0], organization) is not None

    def test_membership_backstop_is_inert_on_cloud(self, edition_cloud, organization):
        """AC-13 / M11: Cloud never consults the edition rule."""
        from accounts.services.workspace_membership import ensure_org_membership

        fill_seats(organization, total=5)
        assert ensure_org_membership(make_user(), organization) is not None


# ---------------------------------------------------------------------------
# Over-limit installs, licences, Cloud
# ---------------------------------------------------------------------------


@pytest.fixture
def over_limit_install(organization, user, workspace):
    """Two organizations, two workspaces and five members."""
    make_owner(make_org("Legacy Second Org"))
    make_workspace(organization, user, name="Legacy Second WS")
    for _ in range(4):
        make_member(organization)
    return organization


class TestOverLimitInstalls:
    def test_everything_existing_keeps_working(
        self, community, over_limit_install, auth_client
    ):
        """AC-11: lists and existing objects keep working on an over-limit install."""
        response = auth_client.get(WORKSPACES_URL)
        assert response.status_code == 200, response.content
        assert response.json()["result"]["total"] == 2
        members = auth_client.get(MEMBERS_URL)
        assert members.status_code == 200, members.content
        assert Organization.objects.count() == 2

    def test_only_new_creation_is_blocked(
        self, community, over_limit_install, auth_client, workspace
    ):
        """AC-11: every new create returns the gate; nothing is removed."""
        assert_gate(
            auth_client.post(WORKSPACES_URL, {"name": "Third"}, format="json"),
            "workspaces",
        )
        assert_gate(
            auth_client.post(ORG_NEW_URL, {"name": "Third Org"}, format="json"),
            "organizations",
        )
        assert_gate(_invite(auth_client, ["sixth@example.com"], workspace), "members")
        assert Workspace.no_workspace_objects.count() == 2
        assert (
            edition.count(
                edition.EditionResource.MEMBER, organization=over_limit_install
            )
            == 5
        )


@pytest.mark.requires_ee
class TestLicence:
    def test_licence_lifts_then_expiry_restores_the_rule(
        self, self_hosted, install_license, auth_client, workspace, monkeypatch
    ):
        """AC-10 / AC-11 / AC-17: licensed creation succeeds; after expiry data
        stays and only the next create is refused."""
        import tfc.licensing.types as types_module

        snapshot = install_license("active")
        fill_seats(workspace.organization)
        assert (
            _invite(auth_client, ["fourth@example.com"], workspace).status_code == 200
        )
        response = auth_client.post(WORKSPACES_URL, {"name": "Licensed"}, format="json")
        assert response.status_code in (200, 201), response.content
        response = auth_client.post(
            ORG_NEW_URL, {"name": "Licensed Org"}, format="json"
        )
        assert response.status_code in (200, 201), response.content

        later = snapshot.expires_at + timedelta(days=1)

        class _Later(datetime):
            @classmethod
            def now(cls, tz=None):
                return later

        monkeypatch.setattr(types_module, "datetime", _Later)
        assert_gate(
            auth_client.post(WORKSPACES_URL, {"name": "Expired"}, format="json"),
            "workspaces",
        )
        assert Workspace.no_workspace_objects.filter(name="Licensed").exists()
        assert Organization.objects.filter(name="Licensed Org").exists()
        assert OrganizationInvite.objects.filter(
            target_email="fourth@example.com"
        ).exists()


class TestCloudUnchanged:
    def test_cloud_creates_freely(self, edition_cloud, auth_client, workspace):
        """AC-13: on Cloud the edition rule never applies."""
        fill_seats(workspace.organization)
        assert (
            _invite(auth_client, ["fourth@example.com"], workspace).status_code == 200
        )
        response = auth_client.post(WORKSPACES_URL, {"name": "Cloud WS"}, format="json")
        assert response.status_code in (200, 201), response.content
        response = auth_client.post(ORG_NEW_URL, {"name": "Cloud Org"}, format="json")
        assert response.status_code in (200, 201), response.content

    @pytest.mark.requires_ee
    def test_cloud_team_view_keeps_the_a3_path(
        self, edition_cloud, auth_client, organization, monkeypatch
    ):
        """AC-13 / A3: Cloud still runs the Free-tier USERS check (a billable
        USER_ADD) and never shows the edition gate."""
        from ee.usage.utils import usage_entries

        calls = []
        real_check = usage_entries.check_if_user_creation_is_allowed

        def _spy(org, config):
            result = real_check(org, config)
            calls.append(result)
            return result

        monkeypatch.setattr(usage_entries, "check_if_user_creation_is_allowed", _spy)
        fill_seats(organization)
        response = auth_client.post(
            TEAM_URL,
            {
                "members": [
                    {
                        "email": "cloud-fourth@example.com",
                        "name": "Fourth",
                        "role": OrganizationRoles.WORKSPACE_MEMBER,
                    }
                ]
            },
            format="json",
        )
        assert response.status_code != 402, response.content
        assert "enterprise_gate" not in response.json()
        assert calls == [
            (False, {"resource_name": "users", "limit": 3, "subscription_name": "free"})
        ]

    @pytest.mark.requires_ee
    def test_a3_check_is_inert_off_cloud_and_unchanged_on_cloud(
        self, organization, monkeypatch
    ):
        """AC-03 / AC-13: check_if_user_creation_is_allowed only caps on Cloud."""
        from ee.usage.utils.usage_entries import check_if_user_creation_is_allowed

        config = {"user_count": 3, "extra_users": 5}
        monkeypatch.setattr(edition, "is_cloud", lambda: False)
        assert check_if_user_creation_is_allowed(organization, config) == (True, {})
        monkeypatch.setattr(edition, "is_cloud", lambda: True)
        allowed, detail = check_if_user_creation_is_allowed(organization, config)
        assert allowed is False
        assert detail["resource_name"] == "users"


# ---------------------------------------------------------------------------
# R3: no unlisted creation path
# ---------------------------------------------------------------------------

_CREATION_PATTERN = (
    r"\b(Organization|Workspace|OrganizationInvite|OrganizationMembership)"
    r"\.(objects|all_objects|no_workspace_objects)"
    r"\.(create|get_or_create|update_or_create|bulk_create)\("
    r"|\bWorkspace\("
    r"|\bOrganization\("
)

# Every non-test creation site (path relative to futureagi/ -> count) and why
# it is covered. A new site fails this test until it calls
# tfc.capabilities.edition.assert_can_create (or is exempt with a reason).
_ALLOWED_CREATION_SITES = {
    # O1/O8 first_signup + create_owner_account (gated) and the new owner's
    # membership; pending-invite persistence for invited users (seat counted
    # by the calling view).
    "accounts/utils.py": 3,
    # O3/O4 (gated) incl. their owner membership and default workspace (W5).
    "accounts/views/organization_views.py": 6,
    # O2 activation (gated) and its owner membership.
    "accounts/views/signup.py": 2,
    # O5 operator API (gated).
    "accounts/views/appsmith.py": 1,
    # O6 marketplace (Cloud-only, gated off-cloud) and the owner membership of
    # the org it just created (M12).
    "accounts/aws_marketplace_utils.py": 1,
    "accounts/gcp_marketplace_utils.py": 2,
    # W1 (gated); the membership create at :769 is unreachable (after a return).
    "accounts/views/workspace.py": 2,
    # W2 (gated), W4 default-workspace self-heal (exempt).
    "accounts/views/workspace_management.py": 2,
    # W3 AI tool (gated).
    "ai_tools/tools/users/create_workspace.py": 1,
    # W4 API-key auth default-workspace self-heal (exempt).
    "accounts/authentication.py": 1,
    # W4 switch-organization default-workspace self-heal (exempt).
    "accounts/views/organization_selection.py": 1,
    # W4 demo-data default workspace for the user's own org (exempt).
    "accounts/user_onboard.py": 1,
    # M1 invite (gated) incl. the dual-written legacy memberships.
    "accounts/views/rbac_views.py": 3,
    # M3 invite accept (exempt: the seat was counted at invite).
    "accounts/models/organization_invite.py": 1,
    # M11 backstop itself.
    "accounts/services/workspace_membership.py": 1,
    # Docstring example, not a call.
    "tfc/utils/audit.py": 1,
    # O7 operator diagnostic (exempt, self-cleaning).
    "model_hub/management/commands/gt_roundtrip_test.py": 3,
}


def _creation_sites():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    pattern = re.compile(_CREATION_PATTERN)
    found: dict[str, int] = {}
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if (
            "/tests/" in f"/{rel}"
            or "/migrations/" in rel
            or rel.startswith(("ee/cloud/", "."))
            or path.name.startswith("test_")
            or path.name == "conftest.py"
        ):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits = [
            line
            for line in text.splitlines()
            if pattern.search(line) and not line.lstrip().startswith(("#", "class "))
        ]
        if hits:
            found[rel] = len(hits)
    return found


def test_no_unlisted_org_workspace_or_member_creation_path():
    """AC-08 / R3: any new creation site must be gated or explicitly exempted."""
    found = _creation_sites()
    unexpected = {
        rel: count
        for rel, count in found.items()
        if count > _ALLOWED_CREATION_SITES.get(rel, 0)
    }
    assert not unexpected, (
        "New organization/workspace/member creation sites: "
        f"{unexpected}. Call tfc.capabilities.edition.assert_can_create inside "
        "edition.creation_lock() (or document the exemption) and update "
        "_ALLOWED_CREATION_SITES."
    )
