"""Sign-up provisions the new organization's default workspace.

Authentication and landing only read (accounts/services/workspace_landing.py;
the rule: "the default workspace comes only from the explicit
provisioning path"). The write that creates an account's first organization is
that path, so it must also create the default workspace and the creator's
workspace-admin access to it, as POST /accounts/organizations/create/ and
POST /accounts/organizations/new/ already do.

Before the fix ``first_signup`` created only the Organization and the
OrganizationMembership. A self-hosted owner who had just signed up got
``default_workspace_id: null`` from GET /accounts/user-info/
(e2e/lib/provisioning.ts: "no default workspace"), and so did every cloud,
OAuth/SSO and first-admin account until a background job or the setup-org
step happened to create one.
"""

import uuid
from unittest.mock import patch

import pytest
from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import Organization, OrgApiKey, User
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services.default_workspace import provision_default_workspace
from accounts.services.workspace_landing import resolve_user_workspace
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context

PASSWORD = "Futureagi@45xyz"
SIGNUP_URL = "/accounts/signup/"
TOKEN_URL = "/accounts/token/"
USER_INFO_URL = "/accounts/user-info/"
TEAM_SETUP_URL = "/accounts/team/users/"
ORG_CREATE_URL = "/accounts/organizations/create/"
ORG_NEW_URL = "/accounts/organizations/new/"

# What the organization-creation views create today; sign-up must match it.
DEFAULT_WORKSPACE_FIELDS = {
    "name": "Default Workspace",
    "display_name": "Default Workspace",
    "description": "Default workspace for the organization",
    "is_default": True,
    "is_active": True,
    "deleted": False,
}


@pytest.fixture
def quiet_signup():
    """Sign-up mails the owner, reports to Mixpanel and starts the Temporal
    post-registration job. None of that may run here: the default workspace
    must exist without the background worker."""
    with (
        patch("accounts.utils.process_post_registration") as post_registration,
        patch("accounts.utils.track_mixpanel_event"),
        patch("accounts.utils.mixpanel_tracker"),
        patch("accounts.utils._fire_deployment_telemetry_registration"),
        patch("accounts.views.signup.email_helper"),
        patch("accounts.views.signup.track_mixpanel_event"),
        patch("accounts.views.organization_views.process_post_registration"),
    ):
        yield post_registration


def _oss(enabled=True):
    return patch("accounts.views.signup.is_oss", return_value=enabled)


def _email(label):
    return f"default-ws-{label}-{uuid.uuid4().hex[:8]}@futureagi.com"


def _payload(email):
    return {
        "email": email,
        "full_name": "New Owner",
        "company_name": "",
        "recaptcha_response": "",
        "allow_email": True,
        "password": PASSWORD,
    }


def _first_signup(label):
    from accounts.utils import first_signup

    owner = first_signup(
        {
            "email": _email(label),
            "full_name": f"Owner {label}",
            "company_name": "",
            "password": PASSWORD,
        }
    )
    clear_workspace_context()
    return owner


def _workspace_ids(organization):
    return sorted(
        Workspace._base_manager.filter(organization=organization).values_list(
            "id", flat=True
        )
    )


def assert_creator_administers_the_default(user, organization):
    """Exactly one workspace, the default, which ``user`` administers."""
    clear_workspace_context()
    workspaces = list(Workspace._base_manager.filter(organization=organization))
    assert len(workspaces) == 1, workspaces
    workspace = workspaces[0]
    for field, value in DEFAULT_WORKSPACE_FIELDS.items():
        assert getattr(workspace, field) == value, field
    assert workspace.created_by_id == user.id
    org_membership = OrganizationMembership.no_workspace_objects.get(
        user=user, organization=organization, is_active=True
    )
    memberships = list(
        WorkspaceMembership._base_manager.filter(workspace=workspace, user=user)
    )
    assert len(memberships) == 1, memberships
    membership = memberships[0]
    assert membership.is_active is True
    assert membership.deleted is False
    assert membership.role == OrganizationRoles.WORKSPACE_ADMIN
    assert membership.level == Level.WORKSPACE_ADMIN
    assert membership.organization_membership_id == org_membership.id
    assert resolve_user_workspace(user, organization) == workspace
    return workspace


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestSignupProvisionsTheDefaultWorkspace:
    def test_oss_user_info_names_it_right_after_signup(self, api_client, quiet_signup):
        """The e2e harness sequence: sign up, sign in, read user-info."""
        email = _email("oss-info")
        with _oss():
            signup = api_client.post(SIGNUP_URL, _payload(email), format="json")
        assert signup.status_code == status.HTTP_200_OK, signup.content
        login = api_client.post(
            TOKEN_URL,
            {"email": email, "password": PASSWORD, "recaptcha_response": ""},
            format="json",
        )
        assert login.status_code == status.HTTP_200_OK, login.content

        info = api_client.get(
            USER_INFO_URL, HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}"
        )

        assert info.status_code == status.HTTP_200_OK, info.content
        body = info.json()
        assert body["default_workspace_id"] is not None, body
        owner = User.objects.get(email=email)
        assert body["organization"]["id"] == str(owner.organization_id)
        workspace = assert_creator_administers_the_default(owner, owner.organization)
        assert body["default_workspace_id"] == str(workspace.id)
        quiet_signup.assert_not_called()

    def test_cloud_signup_needs_no_background_worker(self, api_client, quiet_signup):
        email = _email("cloud")
        with (
            _oss(False),
            patch("accounts.views.signup.verify_recaptcha", return_value=True),
        ):
            response = api_client.post(SIGNUP_URL, _payload(email), format="json")
        assert response.status_code == status.HTTP_200_OK, response.content

        owner = User.objects.get(email=email)
        # The Temporal post-registration job (demo data, which used to be the
        # only cloud creator) is patched out, so this is sign-up's own write.
        quiet_signup.assert_called_once()
        assert_creator_administers_the_default(owner, owner.organization)

    def test_first_signup_provisions_it(self, quiet_signup):
        """OAuth/SSO (saml2_auth/views.py) and the API both call first_signup."""
        owner = _first_signup("direct")
        assert_creator_administers_the_default(owner, owner.organization)

    def test_first_admin_account_gets_it(self, quiet_signup):
        """manage.py create_user and bootstrap_install go through here."""
        from accounts.utils import create_owner_account

        owner = create_owner_account(
            _email("first-admin"), "First Admin", "Bootstrap-Passw0rd!"
        )
        assert_creator_administers_the_default(owner, owner.organization)

    def test_provisioning_issues_no_key(self, quiet_signup):
        """No organization or system key, and no automatic key."""
        owner = _first_signup("no-key")
        assert not OrgApiKey._base_manager.filter(
            organization=owner.organization
        ).exists()
        assert not OrgApiKey._base_manager.filter(user=owner).exists()

    def test_each_signup_provisions_only_its_own_organization(self, quiet_signup):
        a = _first_signup("tenant-a")
        a_workspaces = _workspace_ids(a.organization)

        b = _first_signup("tenant-b")

        assert a.organization_id != b.organization_id
        b_workspace = assert_creator_administers_the_default(b, b.organization)
        assert len(a_workspaces) == 1
        assert _workspace_ids(a.organization) == a_workspaces
        assert b_workspace.id not in a_workspaces
        assert not WorkspaceMembership._base_manager.filter(
            user=a, workspace__organization=b.organization
        ).exists()
        assert not WorkspaceMembership._base_manager.filter(
            user=b, workspace__organization=a.organization
        ).exists()
        assert resolve_user_workspace(a, b.organization) is None
        assert resolve_user_workspace(b, a.organization) is None

    def test_setup_org_step_reuses_the_signup_workspace(self, api_client, quiet_signup):
        """The onboarding page (setup-org.jsx -> POST /accounts/team/users/)
        must keep working and must not add a second workspace."""
        email = _email("setup-org")
        with _oss():
            signup = api_client.post(SIGNUP_URL, _payload(email), format="json")
        assert signup.status_code == status.HTTP_200_OK, signup.content
        owner = User.objects.get(email=email)
        before = _workspace_ids(owner.organization)
        assert len(before) == 1, before

        with (
            patch("accounts.views.workspace_management.email_helper"),
            patch("accounts.views.workspace_management.track_mixpanel_event"),
            patch("accounts.views.workspace_management.mixpanel_tracker"),
            patch(
                "accounts.views.workspace_management."
                "log_and_deduct_cost_for_resource_request",
                None,
            ),
        ):
            response = api_client.post(
                TEAM_SETUP_URL,
                {"org_name": "Acme", "members": []},
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {signup.json()['result']['access']}",
            )

        assert response.status_code == status.HTTP_201_CREATED, response.content
        assert _workspace_ids(owner.organization) == before
        assert_creator_administers_the_default(owner, owner.organization)


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.django_db
class TestOrganizationCreationKeepsItsDefaultWorkspace:
    """Regression guards: green before and after. Sign-up now shares these
    views' provisioning, and their result must not change."""

    def test_orgless_account_creating_an_organization(self, quiet_signup):
        person = User.objects.create_user(
            email=_email("orgless"), password=PASSWORD, name="Org-less"
        )
        client = APIClient()
        client.force_authenticate(user=person)

        response = client.post(
            ORG_CREATE_URL, {"organization_name": "Fresh Org"}, format="json"
        )
        clear_workspace_context()

        assert response.status_code == status.HTTP_201_CREATED, response.content
        organization = OrganizationMembership.no_workspace_objects.get(
            user=person, is_active=True
        ).organization
        assert_creator_administers_the_default(person, organization)

    def test_member_creating_an_additional_organization(self, quiet_signup):
        owner = _first_signup("additional")
        client = APIClient()
        client.force_authenticate(user=owner)

        response = client.post(
            ORG_NEW_URL,
            {"name": "Second Org", "display_name": "Second Org Inc"},
            format="json",
        )
        clear_workspace_context()

        assert response.status_code == status.HTTP_201_CREATED, response.content
        organization = (
            OrganizationMembership.no_workspace_objects.filter(
                user=owner, is_active=True
            )
            .exclude(organization=owner.organization)
            .get()
            .organization
        )
        assert_creator_administers_the_default(owner, organization)


def _owner_membership(owner, organization):
    return OrganizationMembership.no_workspace_objects.get(
        user=owner, organization=organization, is_active=True
    )


@pytest.mark.integration
@pytest.mark.django_db
class TestDefaultWorkspaceProvisioning:
    def test_provisioning_again_changes_nothing(self, quiet_signup):
        owner = _first_signup("again")
        organization = owner.organization
        workspace = assert_creator_administers_the_default(owner, organization)

        again = provision_default_workspace(
            organization,
            owner,
            organization_membership=_owner_membership(owner, organization),
        )

        assert again == workspace
        assert_creator_administers_the_default(owner, organization)

    def test_a_removed_membership_stays_removed(self, quiet_signup):
        owner = _first_signup("removed-member")
        organization = owner.organization
        workspace = assert_creator_administers_the_default(owner, organization)
        WorkspaceMembership._base_manager.filter(
            workspace=workspace, user=owner
        ).update(deleted=True, is_active=False)

        again = provision_default_workspace(
            organization,
            owner,
            organization_membership=_owner_membership(owner, organization),
        )

        assert again == workspace
        rows = WorkspaceMembership._base_manager.filter(user=owner).values_list(
            "workspace_id", "deleted", "is_active"
        )
        assert list(rows) == [(workspace.id, True, False)]

    def test_a_removed_default_is_neither_revived_nor_replaced(self, quiet_signup):
        owner = _first_signup("removed-default")
        organization = owner.organization
        workspace = assert_creator_administers_the_default(owner, organization)
        Workspace._base_manager.filter(pk=workspace.pk).update(
            deleted=True, is_active=False
        )
        membership = _owner_membership(owner, organization)

        with pytest.raises(IntegrityError), transaction.atomic():
            provision_default_workspace(
                organization, owner, organization_membership=membership
            )

        rows = Workspace._base_manager.filter(organization=organization).values_list(
            "id", "deleted", "is_active"
        )
        assert list(rows) == [(workspace.id, True, False)]

    def test_signup_is_one_transaction(self, quiet_signup):
        """No account, organization or membership survives a failed workspace."""
        from accounts.utils import first_signup

        email = _email("atomic")
        organizations = Organization.objects.count()

        with (
            patch(
                "accounts.services.default_workspace.provision_default_workspace",
                side_effect=RuntimeError("injected workspace failure"),
            ),
            pytest.raises(RuntimeError, match="injected workspace failure"),
        ):
            first_signup(
                {
                    "email": email,
                    "full_name": "Owner atomic",
                    "company_name": "",
                    "password": PASSWORD,
                }
            )
        clear_workspace_context()

        assert not User.objects.filter(email=email).exists()
        assert Organization.objects.count() == organizations
        assert not OrganizationMembership._base_manager.filter(
            user__email=email
        ).exists()
