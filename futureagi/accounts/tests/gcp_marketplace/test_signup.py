"""Sign-up: from the Producer Portal redirect to an approved entitlement.

Covers the token landing, the form and OAuth entry points that converge on
process_signup, the approval retry, and the two HTTP views.
"""

from unittest.mock import patch

import pytest

from accounts.gcp_marketplace_utils import (
    approve_pending_entitlements,
    encode_oauth_state,
    onboard_account,
    process_signup,
    read_oauth_state,
    read_onboarding_token,
    reconcile_unapproved_accounts,
)
from accounts.models.gcp_marketplace import (
    GCPMarketplaceAccount,
    GCPMarketplaceAccountState,
    GCPMarketplaceEntitlement,
    GCPMarketplaceEntitlementState,
)
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.tests.gcp_marketplace.support import (
    ACCOUNT_ID,
    ENTITLEMENT_ID,
    PROVIDER_ID,
    remote_entitlement,
)
from ee.usage.models.usage import (
    BillingMethodChoices,
    OrganizationSubscription,
    PlanChoices,
)
from tfc.constants.roles import OrganizationRoles

pytestmark = [pytest.mark.integration, pytest.mark.requires_ee, pytest.mark.django_db]

REQUESTED = GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED
EMAIL = "owner@acme-corp.com"
FULL_NAME = "Cherukuru Dileep Kumar"
SECOND_ID = "6450d107-8413-478d-be1b-142c5df5eb31"


@pytest.fixture(autouse=True)
def post_registration():
    with patch("accounts.gcp_marketplace_utils.process_post_registration") as mocked:
        yield mocked


@pytest.fixture
def onboarded(procurement, free_tier):
    """The state after Google's redirect: account and org exist, no user yet."""
    token, has_user = onboard_account(ACCOUNT_ID, "google-user-1")
    assert has_user is False
    return token, GCPMarketplaceAccount.objects.get(procurement_account_id=ACCOUNT_ID)


def pending_row(gcp_account, entitlement_id=ENTITLEMENT_ID):
    return GCPMarketplaceEntitlement.objects.create(
        entitlement_id=entitlement_id,
        account=gcp_account,
        organization=gcp_account.organization,
        plan_id="payg",
        status=REQUESTED,
        raw_payload=remote_entitlement(entitlement_id),
    )


def make_user(organization, email=EMAIL):
    return User.objects.create_user(
        email=email,
        password="testpassword123",
        name="Existing",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
    )


class TestOnboardAccount:
    def test_creates_the_account_its_organization_and_marketplace_billing(
        self, onboarded
    ):
        token, account = onboarded

        assert account.organization is not None
        assert account.organization.name == f"GCP Marketplace {ACCOUNT_ID[:8]}"
        assert account.google_user_identity == "google-user-1"
        assert account.approved_at is None

        subscription = OrganizationSubscription.objects.get(
            organization=account.organization
        )
        assert subscription.plan == PlanChoices.FREE
        assert subscription.billing_method == BillingMethodChoices.GCP_MARKETPLACE

        assert read_onboarding_token(token) == {
            "procurement_account_id": ACCOUNT_ID,
            "organization_id": str(account.organization.id),
        }

    def test_returning_customer_reuses_the_organization(self, onboarded):
        _, account = onboarded
        make_user(account.organization)

        token, has_user = onboard_account(ACCOUNT_ID)

        assert has_user is True
        assert GCPMarketplaceAccount.objects.count() == 1
        assert (
            Organization.objects.filter(name__startswith="GCP Marketplace").count() == 1
        )
        assert read_onboarding_token(token)["organization_id"] == str(
            account.organization.id
        )


class TestProcessSignup:
    def test_creates_the_owner_and_finishes_the_google_side(
        self, onboarded, procurement, post_registration
    ):
        token, account = onboarded
        pending_row(account)

        user = process_signup(token, "Owner@Acme-Corp.com", FULL_NAME)

        assert user.email == EMAIL
        assert user.organization == account.organization
        assert user.organization_role == OrganizationRoles.OWNER
        membership = OrganizationMembership.objects.get(
            user=user, organization=account.organization
        )
        assert membership.role == OrganizationRoles.OWNER

        account.organization.refresh_from_db()
        assert account.organization.name == FULL_NAME
        assert account.organization.display_name == FULL_NAME

        procurement.approve_account.assert_called_once_with(ACCOUNT_ID)
        account.refresh_from_db()
        assert account.approved_at is not None
        assert account.state == GCPMarketplaceAccountState.ACTIVE

        procurement.approve_entitlement.assert_called_once_with(ENTITLEMENT_ID)
        post_registration.assert_called_once()
        assert post_registration.call_args.args[0] == user.id

        with pytest.raises(ValueError, match="onboarding token"):
            read_onboarding_token(token)

    def test_retry_with_the_same_email_resumes_at_approval(
        self, onboarded, procurement
    ):
        token, account = onboarded
        first = process_signup(token, EMAIL, FULL_NAME)
        token, _ = onboard_account(ACCOUNT_ID)
        procurement.approve_account.reset_mock()

        again = process_signup(token, EMAIL.upper(), "Someone Else")

        assert again == first
        assert User.objects.filter(organization=account.organization).count() == 1
        procurement.approve_account.assert_called_once_with(ACCOUNT_ID)

    def test_disposable_email_is_refused(self, onboarded):
        token, _ = onboarded

        with (
            patch(
                "accounts.gcp_marketplace_utils.is_disposable_email_domain",
                return_value=True,
            ),
            pytest.raises(ValueError, match="Disposable"),
        ):
            process_signup(token, "x@mailinator.com", FULL_NAME)

    def test_second_person_cannot_claim_a_taken_subscription(self, onboarded):
        token, _ = onboarded
        process_signup(token, EMAIL, FULL_NAME)
        token, _ = onboard_account(ACCOUNT_ID)

        with pytest.raises(ValueError, match="already has an account"):
            process_signup(token, "other@acme-corp.com", "Other Person")

    def test_email_registered_elsewhere_is_refused(self, onboarded):
        token, _ = onboarded
        make_user(Organization.objects.create(name="Elsewhere"))

        with pytest.raises(ValueError, match="already exists"):
            process_signup(token, EMAIL, FULL_NAME)

    def test_unknown_token_is_refused(self, procurement, free_tier):
        with pytest.raises(ValueError, match="onboarding token"):
            process_signup("not-a-token", EMAIL, FULL_NAME)


class TestApprovePendingEntitlements:
    def test_links_orphans_and_approves_them(self, procurement, gcp_account):
        orphan = GCPMarketplaceEntitlement.objects.create(
            entitlement_id=ENTITLEMENT_ID,
            plan_id="payg",
            status=REQUESTED,
            raw_payload={"account": f"providers/{PROVIDER_ID}/accounts/{ACCOUNT_ID}"},
        )

        assert approve_pending_entitlements(gcp_account) == 1

        orphan.refresh_from_db()
        assert orphan.account == gcp_account
        assert orphan.organization == gcp_account.organization
        procurement.approve_entitlement.assert_called_once_with(ENTITLEMENT_ID)

    def test_ignores_orphans_of_other_accounts(self, procurement, gcp_account):
        orphan = GCPMarketplaceEntitlement.objects.create(
            entitlement_id=ENTITLEMENT_ID,
            plan_id="payg",
            status=REQUESTED,
            raw_payload={"account": f"providers/{PROVIDER_ID}/accounts/someone-else"},
        )

        assert approve_pending_entitlements(gcp_account) == 0

        orphan.refresh_from_db()
        assert orphan.account is None
        procurement.approve_entitlement.assert_not_called()

    def test_one_failed_approval_does_not_stop_the_rest(self, procurement, gcp_account):
        pending_row(gcp_account)
        pending_row(gcp_account, SECOND_ID)
        procurement.approve_entitlement.side_effect = [RuntimeError("timeout"), {}]

        assert approve_pending_entitlements(gcp_account) == 1
        assert procurement.approve_entitlement.call_count == 2

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Approval only sees rows the consumer wrote, so a lost or delayed "
            "creation event strands the purchase. Fixed on "
            "fix/th-7731-marketplace-event-dedupe."
        ),
    )
    def test_approves_a_pending_entitlement_google_holds_but_we_never_recorded(
        self, procurement, gcp_account
    ):
        procurement.iter_entitlements.side_effect = lambda **kwargs: iter(
            [remote_entitlement(state=REQUESTED)]
        )

        assert approve_pending_entitlements(gcp_account) == 1
        procurement.approve_entitlement.assert_called_once_with(ENTITLEMENT_ID)


class TestReconcileUnapprovedAccounts:
    def test_finishes_a_sign_up_whose_approval_call_failed(
        self, procurement, gcp_account
    ):
        gcp_account.approved_at = None
        gcp_account.save(update_fields=["approved_at"])
        make_user(gcp_account.organization)
        pending_row(gcp_account)

        counts = reconcile_unapproved_accounts()

        assert counts == {"checked": 1, "approved": 1, "failed": 0}
        procurement.approve_account.assert_called_once_with(ACCOUNT_ID)
        procurement.approve_entitlement.assert_called_once_with(ENTITLEMENT_ID)
        gcp_account.refresh_from_db()
        assert gcp_account.approved_at is not None

    def test_skips_organizations_nobody_signed_up_for(self, procurement, gcp_account):
        gcp_account.approved_at = None
        gcp_account.save(update_fields=["approved_at"])

        counts = reconcile_unapproved_accounts()

        assert counts == {"checked": 0, "approved": 0, "failed": 0}
        procurement.approve_account.assert_not_called()

    def test_a_google_failure_is_counted_and_retried_next_hour(
        self, procurement, gcp_account
    ):
        gcp_account.approved_at = None
        gcp_account.save(update_fields=["approved_at"])
        make_user(gcp_account.organization)
        procurement.approve_account.side_effect = RuntimeError("timeout")

        counts = reconcile_unapproved_accounts()

        assert counts == {"checked": 1, "approved": 0, "failed": 1}
        gcp_account.refresh_from_db()
        assert gcp_account.approved_at is None


class TestVerifyTokenView:
    URL = "/accounts/gcp-marketplace/verify-token/"

    def post(self, api_client, token="signed-jwt"):
        return api_client.post(
            self.URL,
            data=f"x-gcp-marketplace-token={token}" if token else "",
            content_type="application/x-www-form-urlencoded",
        )

    def test_new_customer_is_sent_to_register_with_a_token(
        self, api_client, procurement, free_tier
    ):
        with patch(
            "accounts.views.gcp_marketplace.verify_marketplace_token",
            return_value=(ACCOUNT_ID, "google-user-1", "api.futureagi.com"),
        ):
            response = self.post(api_client)

        assert response.status_code == 302
        location = response["Location"]
        assert "/auth/jwt/register?onboarding_gcp_token=" in location
        token = location.rsplit("=", 1)[1]
        assert read_onboarding_token(token)["procurement_account_id"] == ACCOUNT_ID

    def test_returning_customer_is_sent_to_login(self, api_client, onboarded):
        _, account = onboarded
        make_user(account.organization)

        with patch(
            "accounts.views.gcp_marketplace.verify_marketplace_token",
            return_value=(ACCOUNT_ID, "", "api.futureagi.com"),
        ):
            response = self.post(api_client)

        assert response.status_code == 302
        assert "/auth/jwt/login" in response["Location"]

    def test_invalid_token_is_rejected(self, api_client, procurement):
        with patch(
            "accounts.views.gcp_marketplace.verify_marketplace_token",
            side_effect=ValueError("bad signature"),
        ):
            response = self.post(api_client)

        assert response.status_code == 400
        assert GCPMarketplaceAccount.objects.count() == 0

    def test_missing_token_is_rejected(self, api_client, procurement):
        assert self.post(api_client, token="").status_code == 400


class TestSignupView:
    URL = "/accounts/gcp-marketplace/signup/"

    def test_completes_sign_up(self, api_client, onboarded, procurement):
        token, account = onboarded

        response = api_client.post(
            self.URL,
            {"onboarding_token": token, "email": EMAIL, "full_name": FULL_NAME},
            format="json",
        )

        assert response.status_code == 200
        assert response.json()["result"]["user_email"] == EMAIL
        assert User.objects.filter(
            email=EMAIL, organization=account.organization
        ).exists()
        procurement.approve_account.assert_called_once_with(ACCOUNT_ID)

    def test_unknown_fields_are_rejected(self, api_client, onboarded):
        token, _ = onboarded

        response = api_client.post(
            self.URL,
            {
                "onboarding_token": token,
                "email": EMAIL,
                "full_name": FULL_NAME,
                "password": "chosen-by-the-customer",
            },
            format="json",
        )

        assert response.status_code == 400
        assert not User.objects.filter(email=EMAIL).exists()

    def test_expired_token_returns_the_reason(self, api_client, procurement, free_tier):
        response = api_client.post(
            self.URL,
            {"onboarding_token": "expired", "email": EMAIL, "full_name": FULL_NAME},
            format="json",
        )

        assert response.status_code == 400
        assert "onboarding token" in response.content.decode()


class TestOAuthState:
    def test_round_trips_the_onboarding_token(self):
        assert read_oauth_state(encode_oauth_state("abc123")) == "abc123"

    @pytest.mark.parametrize("state", [None, "", "gcp_onboarding:", "csrf-token-only"])
    def test_anything_else_is_not_a_marketplace_sign_up(self, state):
        assert read_oauth_state(state) is None


class TestResolveSsoUser:
    @pytest.fixture(autouse=True)
    def quiet_analytics(self):
        with (
            patch("saml2_auth.views.track_mixpanel_event"),
            patch("saml2_auth.views.get_mixpanel_properties", return_value={}),
        ):
            yield

    def test_new_identity_with_a_token_goes_through_marketplace_sign_up(
        self, onboarded, procurement
    ):
        from saml2_auth.views import get_started_url, resolve_sso_user

        token, account = onboarded

        user, next_url, new_org = resolve_sso_user(EMAIL, FULL_NAME, token, "login")

        assert user.organization == account.organization
        assert (next_url, new_org) == (get_started_url, "true")
        procurement.approve_account.assert_called_once_with(ACCOUNT_ID)

    def test_existing_user_cannot_absorb_a_subscription(self, onboarded):
        from saml2_auth.views import resolve_sso_user

        token, _ = onboarded
        make_user(Organization.objects.create(name="Elsewhere"))

        with pytest.raises(Exception, match="already exists"):
            resolve_sso_user(EMAIL, FULL_NAME, token, "login")
