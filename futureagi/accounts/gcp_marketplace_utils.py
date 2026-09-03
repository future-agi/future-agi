"""Sign-up flow for Google Cloud Marketplace customers.

Entry points converge on the same state:

    verify-token/  Google posts a signed JWT. We create the Organization and
                   mint an onboarding token.

    signup/        The customer submits name and email. We create the User,
                   approve the procurement account, and approve any entitlement
                   that arrived before they got here.

    OAuth callback The customer chose "Continue with Google" instead of the
                   form. The onboarding token rides the OAuth state parameter
                   there and back, and the callback calls process_signup with
                   the identity Google returned.

Pub/Sub can deliver ENTITLEMENT_CREATION_REQUESTED before or after sign-up
completes, so whichever runs second performs the entitlement approval.
"""

import uuid as _uuid

import structlog
from django.conf import settings
from django.core.cache import cache
from django.db import transaction

from accounts.models.gcp_marketplace import (
    GCPMarketplaceAccount,
    GCPMarketplaceAccountState,
    GCPMarketplaceEntitlement,
    GCPMarketplaceEntitlementState,
)
from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.services.gcp_procurement import gcp_procurement
from accounts.utils import (
    generate_password,
    is_disposable_email_domain,
    process_post_registration,
)
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles

logger = structlog.get_logger(__name__)

GCP_ISSUER = (
    "https://www.googleapis.com/robot/v1/metadata/x509/"
    "cloud-commerce-partner@system.gserviceaccount.com"
)

ONBOARDING_CACHE_PREFIX = "gcp_onboard"
ONBOARDING_TTL_SECONDS = 15 * 60

OAUTH_STATE_PREFIX = "gcp_onboarding:"

try:
    from ee.usage.models.usage import BillingMethodChoices, OrganizationSubscription
except ImportError:
    BillingMethodChoices = None
    OrganizationSubscription = None

try:
    from ee.usage.utils.usage_entries import (
        create_organization_subscription_if_not_exists,
    )
except ImportError:
    create_organization_subscription_if_not_exists = None


def verify_marketplace_token(token: str) -> tuple[str, str]:
    """Verify the x-gcp-marketplace-token JWT and return its two identifiers.

    Returns the procurement account id, which names the subscription, and the
    obfuscated Google user id, which names the person. The second is optional on
    Google's side, so it can come back empty.

    This endpoint is unauthenticated and provisions paid accounts, so every
    check matters: an unverified `sub` is a free-subscription vulnerability.

    Checks signature (RS256, against the certs Google publishes at the issuer
    URL), expiry, audience and issuer. Tokens live five minutes.
    """
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    audience = settings.GCP_MARKETPLACE_SERVICE_NAME
    if not audience:
        raise ValueError("GCP_MARKETPLACE_SERVICE_NAME is not set")

    payload = id_token.verify_token(
        token,
        google_requests.Request(),
        audience=audience,
        certs_url=GCP_ISSUER,
    )

    # verify_token checks signature, exp and aud. Issuer is not among them.
    if payload.get("iss") != GCP_ISSUER:
        raise ValueError("Unexpected issuer on marketplace token")

    account_id = payload.get("sub")
    if not account_id:
        raise ValueError("Marketplace token carries no subject")

    user_identity = (payload.get("google") or {}).get("user_identity") or ""

    return account_id, user_identity


def _mark_billed_by_marketplace(organization) -> None:
    """Set billing_method so the Stripe invoice run skips this organization.

    Done at organization creation rather than entitlement activation: the gap
    between sign-up and ENTITLEMENT_ACTIVE can be days if the customer abandons
    the form, and a monthly invoice run inside that window would bill them twice.
    """
    if OrganizationSubscription is None or BillingMethodChoices is None:
        return

    OrganizationSubscription.objects.filter(organization=organization).update(
        billing_method=BillingMethodChoices.GCP_MARKETPLACE
    )


def _create_organization(gcp_account: GCPMarketplaceAccount) -> Organization:
    """Google supplies no customer identity, so the name is a placeholder.

    The AWS path names the org after the customer's AWS account id. There is no
    GCP equivalent: the procurement account id is opaque and carries no name,
    email or company. Renamed from the sign-up form in process_signup.
    """
    placeholder = f"GCP Marketplace {gcp_account.procurement_account_id[:8]}"

    organization = Organization.objects.create(
        name=placeholder,
        display_name=placeholder,
        region=settings.REGION,
    )

    gcp_account.organization = organization
    gcp_account.save(update_fields=["organization", "updated_at"])

    if create_organization_subscription_if_not_exists is not None:
        create_organization_subscription_if_not_exists(organization)

    _mark_billed_by_marketplace(organization)

    return organization


def onboard_account(account_id: str, user_identity: str = "") -> tuple[str, bool]:
    """Link a procurement account to an Organization.

    Approval is not granted here. Google treats an account approval as an
    assertion that the customer signed up with us, and at this point they have
    only landed on the form, so _approve_signup makes the call once a User exists.

    Returns the onboarding token and whether the organization already has users,
    which decides between the sign-up form and the login page.
    """
    account = gcp_procurement.get_account(account_id)

    gcp_account, _ = GCPMarketplaceAccount.objects.get_or_create(
        procurement_account_id=account_id,
        defaults={
            "state": account.get("state", ""),
            "google_user_identity": user_identity,
            "raw_payload": account,
        },
    )

    # First writer wins. A second person clicking through must not overwrite the
    # identity that made the purchase.
    if user_identity and not gcp_account.google_user_identity:
        gcp_account.google_user_identity = user_identity
        gcp_account.save(update_fields=["google_user_identity", "updated_at"])

    if not gcp_account.organization:
        _create_organization(gcp_account)

    has_user = bool(
        gcp_account.organization and gcp_account.organization.members.exists()
    )

    return _mint_onboarding_token(gcp_account), has_user


def _now():
    from django.utils import timezone

    return timezone.now()


def _mint_onboarding_token(gcp_account: GCPMarketplaceAccount) -> str:
    token = _uuid.uuid4().hex
    cache.set(
        f"{ONBOARDING_CACHE_PREFIX}:{token}",
        {
            "procurement_account_id": gcp_account.procurement_account_id,
            "organization_id": (
                str(gcp_account.organization.id) if gcp_account.organization else None
            ),
        },
        timeout=ONBOARDING_TTL_SECONDS,
    )
    return token


def read_onboarding_token(token: str) -> dict:
    session = cache.get(f"{ONBOARDING_CACHE_PREFIX}:{token}") if token else None
    if not session:
        raise ValueError("Invalid or expired onboarding token")
    return session


def discard_onboarding_token(token: str) -> None:
    cache.delete(f"{ONBOARDING_CACHE_PREFIX}:{token}")


def encode_oauth_state(onboarding_token: str) -> str:
    """Wrap an onboarding token for the OAuth state parameter.

    "Continue with Google" leaves our origin, so a query param on the register
    page does not survive the round trip. state is the only field Google hands
    back untouched.
    """
    return f"{OAUTH_STATE_PREFIX}{onboarding_token}"


def read_oauth_state(state: str | None) -> str | None:
    """Return the onboarding token an OAuth state carries, or None.

    The prefix is what distinguishes a marketplace sign-up from every other
    OAuth login, which sends no state at all.
    """
    if not state or not state.startswith(OAUTH_STATE_PREFIX):
        return None
    return state[len(OAUTH_STATE_PREFIX) :] or None


def _approve_signup(gcp_account: GCPMarketplaceAccount) -> None:
    """Tell Google the customer completed sign-up with us.

    Google documents the approval as following account creation on the provider
    side, so it belongs here and not at verify-token time. An account left
    pending is the correct outcome for a customer who never finishes the form.
    """
    account = gcp_procurement.get_account(gcp_account.procurement_account_id)
    if not gcp_procurement.pending_approval(account):
        return

    gcp_procurement.approve_account(gcp_account.procurement_account_id)
    gcp_account.approved_at = _now()
    gcp_account.state = GCPMarketplaceAccountState.ACTIVE
    gcp_account.save(update_fields=["approved_at", "state", "updated_at"])


def approve_pending_entitlements(gcp_account: GCPMarketplaceAccount) -> int:
    """Approve entitlements that arrived before sign-up completed.

    An entitlement cannot be approved until its account is approved, so the
    Pub/Sub handler stores the row and skips. This is the other half: once the
    account is approved, anything waiting is approved here.
    """
    pending = GCPMarketplaceEntitlement.objects.filter(
        account=gcp_account,
        status=GCPMarketplaceEntitlementState.ACTIVATION_REQUESTED,
    )

    approved = 0
    for entitlement in pending:
        try:
            gcp_procurement.approve_entitlement(entitlement.entitlement_id)
            approved += 1
        except Exception:
            logger.exception(
                "gcp_marketplace_pending_entitlement_approve_failed",
                entitlement_id=entitlement.entitlement_id,
            )

    return approved


def process_signup(onboarding_token: str, email: str, full_name: str) -> User:
    """Create the owner user for a marketplace organization.

    Shared by the sign-up form and the Google OAuth callback, so the identity
    may come from either a submitted form or a verified Google profile.
    """
    session = read_onboarding_token(onboarding_token)

    # first_signup lowercases before anything else. Without it the duplicate
    # check below misses case variants and two users differ only in casing.
    email = (email or "").strip().lower()

    if is_disposable_email_domain(email.split("@")[-1]):
        raise ValueError("Disposable email addresses are not accepted")

    gcp_account = GCPMarketplaceAccount.objects.filter(
        procurement_account_id=session["procurement_account_id"]
    ).first()
    if not gcp_account or not gcp_account.organization:
        raise ValueError("Marketplace account is not linked to an organization")

    if gcp_account.organization.members.exists():
        raise ValueError("This Marketplace subscription already has an account")

    if User.objects.filter(email=email).exists():
        raise ValueError("An account with this email already exists")

    with transaction.atomic():
        generated_password = generate_password()
        user = User.objects.create_user(
            email=email,
            name=full_name,
            password=generated_password,
            organization=gcp_account.organization,
            organization_role=OrganizationRoles.OWNER,
        )
        organization = gcp_account.organization
        # Without this row get_org_membership returns None and RBAC denies the
        # owner everything. The FK on User alone is not enough.
        OrganizationMembership.objects.get_or_create(
            user=user,
            organization=organization,
            defaults={
                "role": OrganizationRoles.OWNER,
                "level": Level.OWNER,
                "is_active": True,
            },
        )
        organization.name = full_name or organization.name
        organization.display_name = full_name or organization.display_name
        organization.save(update_fields=["name", "display_name", "updated_at"])

    process_post_registration(user.id, generated_password)
    _approve_signup(gcp_account)
    approve_pending_entitlements(gcp_account)
    discard_onboarding_token(onboarding_token)

    return user
