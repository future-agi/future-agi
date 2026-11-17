"""A marketplace sign-up gives the new organization its default workspace.

A buyer who subscribes through AWS Marketplace or Google Cloud Marketplace
finishes on our sign-up form. That step creates the owner's account in the
organization the subscription's registration created and, as an e-mail
sign-up does, the organization's default workspace with the owner as its
administrator. Signing in only reads it, so the owner's first request already
lands in that workspace.

The sign-up steps run here as the sign-up endpoints run them
(``process_aws_signup`` and ``process_signup``). Google's Procurement calls
and the background post-registration job are replaced, so nothing leaves the
test.
"""

import ipaddress
import itertools
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models.aws_marketplace import AWSMarketplaceCustomer
from accounts.models.gcp_marketplace import GCPMarketplaceAccount
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.services.workspace_landing import resolve_user_workspace
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.tests.production_path import PASSWORD, use_production_request_settings

TOKEN_URL = "/accounts/token/"
USER_INFO_URL = "/accounts/user-info/"
MARKETPLACES = ("aws", "gcp")
OWNER_NAME = "Marketplace Owner"

# Separate browsers, separate addresses (198.19.64.0/18 lies in the
# 198.18.0.0/15 test range); the sign-in monitor counts sign-ins per address.
_ADDRESSES = itertools.count(1)


def _email():
    return f"owner-{uuid.uuid4().hex[:8]}@example.test"


def _subscription(marketplace):
    """A subscription whose registration has run: its organization exists
    and has no member yet. The marketplace account ids are made up."""
    clear_workspace_context()
    account_id = uuid.uuid4().hex[:12]
    if marketplace == "aws":
        name = f"AWS Account {account_id}"
        organization = Organization.objects.create(name=name, display_name=name)
        customer = AWSMarketplaceCustomer.objects.create(
            customer_identifier=f"example-buyer-{account_id}",
            customer_aws_account_id=account_id,
            product_code="example-product",
            organization=organization,
        )
        account = None
    else:
        name = f"GCP Marketplace {account_id[:8]}"
        organization = Organization.objects.create(name=name, display_name=name)
        account = GCPMarketplaceAccount.objects.create(
            procurement_account_id=f"example-{account_id}",
            organization=organization,
        )
        customer = None
    clear_workspace_context()
    return SimpleNamespace(
        marketplace=marketplace,
        organization=organization,
        customer=customer,
        account=account,
    )


def _aws_sign_up(subscription, email):
    from accounts.aws_marketplace_utils import process_aws_signup

    with (
        patch(
            "accounts.aws_marketplace_utils.generate_password", return_value=PASSWORD
        ),
        patch("tfc.temporal.drop_in.start_activity"),
    ):
        process_aws_signup(email, OWNER_NAME, subscription.customer.customer_identifier)


def _gcp_sign_up(subscription, email):
    from accounts import gcp_marketplace_utils as utils

    # Each visit from Google's sign-up link hands the form a new session.
    onboarding_token = utils._mint_onboarding_token(subscription.account)
    with (
        patch.object(utils.gcp_procurement, "get_account", return_value={}),
        patch.object(utils.gcp_procurement, "approve_account"),
        patch.object(utils.gcp_procurement, "iter_entitlements", return_value=[]),
        patch(
            "accounts.gcp_marketplace_utils.generate_password", return_value=PASSWORD
        ),
        patch("tfc.temporal.drop_in.start_activity"),
    ):
        utils.process_signup(onboarding_token, email, OWNER_NAME)


def _sign_up(subscription, email):
    """The marketplace's sign-up step for ``email``, as its endpoint runs it
    once the buyer sends the form. Returns the new owner."""
    if subscription.marketplace == "aws":
        _aws_sign_up(subscription, email)
    else:
        _gcp_sign_up(subscription, email)
    clear_workspace_context()
    return User.objects.get(email=email)


def _sign_up_again(subscription, email):
    """The same form sent again for the same subscription. AWS refuses it,
    because the subscription already has its account; Google Cloud resumes
    the finished sign-up for the same e-mail."""
    if subscription.marketplace == "aws":
        with pytest.raises(ValueError, match="already has an account"):
            _aws_sign_up(subscription, email)
    else:
        _gcp_sign_up(subscription, email)
    clear_workspace_context()


def _the_default_workspace(owner, organization):
    """The organization's one workspace: its active default, which ``owner``
    administers as the owner of an e-mail sign-up does."""
    clear_workspace_context()
    workspaces = list(Workspace._base_manager.filter(organization=organization))
    assert len(workspaces) == 1
    workspace = workspaces[0]
    assert workspace.is_default is True
    assert workspace.is_active is True
    assert workspace.deleted is False
    assert workspace.created_by_id == owner.id
    org_memberships = list(
        OrganizationMembership.no_workspace_objects.filter(
            user=owner, organization=organization, is_active=True
        )
    )
    assert len(org_memberships) == 1
    assert org_memberships[0].level == Level.OWNER
    memberships = list(
        WorkspaceMembership._base_manager.filter(workspace=workspace, user=owner)
    )
    assert len(memberships) == 1
    membership = memberships[0]
    assert membership.is_active is True
    assert membership.deleted is False
    assert membership.role == OrganizationRoles.WORKSPACE_ADMIN
    assert membership.level == Level.WORKSPACE_ADMIN
    assert membership.organization_membership_id == org_memberships[0].id
    assert resolve_user_workspace(owner, organization) == workspace
    return workspace


def _signed_in(email):
    """A client signed in through the login endpoint with the password the
    sign-up e-mails, sending only its bearer token, as the web app does right
    after sign-in."""
    address = str(ipaddress.ip_address("198.19.64.0") + next(_ADDRESSES))
    client = APIClient(enforce_csrf_checks=True, REMOTE_ADDR=address)
    login = client.post(
        TOKEN_URL, {"email": email, "password": PASSWORD}, format="json"
    )
    assert login.status_code == status.HTTP_200_OK
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}")
    clear_workspace_context()
    return client


@pytest.fixture
def production_request(db, settings, monkeypatch):
    """The production request path, with a cache of its own for the
    onboarding session the registration leaves for the form."""
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_DISABLED", "true")
    use_production_request_settings(settings)
    settings.CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": f"marketplace-sign-up-{uuid.uuid4()}",
        }
    }
    cache.clear()
    yield
    cache.clear()
    clear_workspace_context()


@pytest.mark.integration
@pytest.mark.api
@pytest.mark.parametrize("marketplace", MARKETPLACES)
class TestMarketplaceSignUpGivesTheOwnerTheDefaultWorkspace:
    def test_marketplace_owner_administers_one_default_workspace_after_sign_up(
        self, production_request, marketplace
    ):
        subscription = _subscription(marketplace)

        owner = _sign_up(subscription, _email())

        _the_default_workspace(owner, subscription.organization)

    def test_marketplace_owner_signs_in_to_their_default_workspace(
        self, production_request, marketplace
    ):
        subscription = _subscription(marketplace)
        email = _email()
        owner = _sign_up(subscription, email)
        workspace = _the_default_workspace(owner, subscription.organization)
        client = _signed_in(email)

        info = client.get(USER_INFO_URL)

        assert info.status_code == status.HTTP_200_OK
        body = info.json()
        requires_org_setup = body.get("requires_org_setup")
        organization_id = (body.get("organization") or {}).get("id")
        default_workspace_id = body.get("default_workspace_id")
        assert requires_org_setup is not True
        assert organization_id == str(subscription.organization.id)
        assert default_workspace_id == str(workspace.id)

    def test_sending_the_sign_up_form_again_keeps_one_default_workspace(
        self, production_request, marketplace
    ):
        subscription = _subscription(marketplace)
        email = _email()
        owner = _sign_up(subscription, email)
        first = _the_default_workspace(owner, subscription.organization)

        _sign_up_again(subscription, email)

        again = _the_default_workspace(owner, subscription.organization)
        assert again.id == first.id
        assert WorkspaceMembership._base_manager.filter(user=owner).count() == 1
        assert User.objects.filter(organization=subscription.organization).count() == 1

    def test_a_failed_sign_up_leaves_no_account_and_the_owner_can_sign_up_again(
        self, production_request, marketplace
    ):
        """A sign-up that fails while it creates the default workspace leaves no
        account, membership or workspace behind, so the owner sends the form
        again and gets their default workspace."""
        subscription = _subscription(marketplace)
        organization = subscription.organization
        email = _email()

        with (
            patch(
                "accounts.services.default_workspace.provision_default_workspace",
                side_effect=RuntimeError("injected workspace failure"),
            ),
            pytest.raises(RuntimeError, match="injected workspace failure"),
        ):
            _sign_up(subscription, email)
        clear_workspace_context()

        assert not User.objects.filter(email=email).exists()
        assert not OrganizationMembership._base_manager.filter(
            organization=organization
        ).exists()
        assert not Workspace._base_manager.filter(organization=organization).exists()
        assert not WorkspaceMembership._base_manager.filter(
            workspace__organization=organization
        ).exists()

        owner = _sign_up(subscription, email)

        _the_default_workspace(owner, organization)
