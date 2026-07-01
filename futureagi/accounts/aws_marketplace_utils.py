import json
import uuid as _uuid
from datetime import date, datetime

import structlog
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from accounts.authentication import generate_encrypted_message
from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.aws_marketplace import AWSMarketplaceCustomer, SubscriptionStatus
from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.services.aws_marketplace import AWSMarketplaceService
from accounts.utils import generate_password, process_post_registration
from tfc.constants.roles import OrganizationRoles

logger = structlog.get_logger(__name__)
try:
    from ee.usage.models.usage import OrganizationSubscription, SubscriptionTier, SubscriptionTierChoices
except ImportError:
    OrganizationSubscription = None
    SubscriptionTier = None
    SubscriptionTierChoices = None
from tfc.billing.boundary import get_billing as _get_billing_sub
aws_marketplace_service = AWSMarketplaceService()


def get_aws_customer_defaults(
    customer_aws_account_id, product_code, product_id, agreement_id
):
    defaults = {
        "customer_aws_account_id": customer_aws_account_id,
        "product_code": product_code,
        "product_id": product_id,
        "agreement_id": agreement_id,
        "subscription_status": SubscriptionStatus.SUBSCRIBE_PENDING,
    }

    return defaults


def get_or_create_aws_customer(
    customer_identifier, customer_aws_account_id, product_code, product_id, agreement_id
):
    defaults = get_aws_customer_defaults(
        customer_aws_account_id, product_code, product_id, agreement_id
    )

    aws_customer, created = AWSMarketplaceCustomer.objects.get_or_create(
        customer_identifier=customer_identifier,
        defaults=defaults,
    )

    if not created:
        for k, v in defaults.items():
            setattr(aws_customer, k, v)
        aws_customer.save()

    return aws_customer


def create_organization_for_aws_customer(aws_customer, customer_aws_account_id):
    organization_name = f"AWS Account {customer_aws_account_id}"

    organization = Organization.objects.create(
        name=organization_name,
        display_name=organization_name,
        region=settings.REGION,
    )

    aws_customer.organization = organization
    aws_customer.save()

    _get_billing_sub().setup_org_subscription(organization)


def create_onboarding_token(aws_customer, customer_identifier, product_code):
    onboarding_token = _uuid.uuid4().hex

    cache_data = {
        "customer_identifier": customer_identifier,
        "organization_id": (
            str(aws_customer.organization.id) if aws_customer.organization else None
        ),
        "product_code": product_code,
    }

    cache.set(
        f"aws_onboard:{onboarding_token}",
        cache_data,
        timeout=15 * 60,
    )

    return onboarding_token


def has_user_in_organization(aws_customer):
    has_user = bool(
        aws_customer.organization and aws_customer.organization.members.exists()
    )
    return has_user


def onboard_aws_customer(
    customer_identifier, customer_aws_account_id, product_code, product_id, agreement_id
):
    aws_customer = get_or_create_aws_customer(
        customer_identifier,
        customer_aws_account_id,
        product_code,
        product_id,
        agreement_id,
    )

    if not aws_customer.organization:
        create_organization_for_aws_customer(aws_customer, customer_aws_account_id)

    onboarding_token = create_onboarding_token(
        aws_customer, customer_identifier, product_code
    )

    has_user = has_user_in_organization(aws_customer)

    return onboarding_token, has_user


def get_onboarding_session(onboarding_token):
    """
    Get the onboarding session from the cache
    """
    onboarding_session = None
    if onboarding_token:
        onboarding_session = cache.get(f"aws_onboard:{onboarding_token}")
        if not onboarding_session:
            raise ValueError("Invalid or expired onboarding token")

    customer_identifier = (onboarding_session or {}).get("customer_identifier")
    product_code = (onboarding_session or {}).get("product_code")

    if not product_code:
        raise ValueError("Invalid or expired onboarding token")

    return customer_identifier, product_code


def check_if_user_exists(email):
    """
    Check if a user with this email already exists
    """
    if User.objects.filter(email=email).exists():
        raise ValueError("An account with this email already exists")


def _create_user_account(email, full_name, aws_customer):
    """
    Create a user account
    """
    generated_password = generate_password()

    user = User.objects.create_user(
        email=email,
        name=full_name,
        password=generated_password,
        organization=aws_customer.organization,
        organization_role=OrganizationRoles.OWNER,
    )

    return user, generated_password


def process_aws_signup(email, full_name, customer_identifier):
    """
    Process the AWS signup process
    """
    aws_customer = AWSMarketplaceCustomer.objects.get(
        customer_identifier=customer_identifier
    )

    if (
        aws_customer
        and aws_customer.organization
        and aws_customer.organization.members.exists()
    ):
        raise ValueError("This AWS Marketplace customer already has an account")

    user, generated_password = _create_user_account(email, full_name, aws_customer)

    process_post_registration(user.id, generated_password)

    return aws_customer, user.email


def delete_onboarding_token(onboarding_token):
    """
    Delete the onboarding token from the cache
    """
    cache.delete(f"aws_onboard:{onboarding_token}")


def _serialize_entitlements(entitlements):
    """
    Serialize entitlements to JSON format, handling datetime objects
    """

    serialized = json.loads(
        json.dumps(
            entitlements,
            default=lambda o: o.isoformat() if isinstance(o, datetime | date) else o,
        )
    )

    return serialized


def _get_tier_mapping():
    """
    Get the mapping between AWS dimensions and subscription tiers
    """

    tier_map = {
        "enterprise_plan": SubscriptionTierChoices.CUSTOM.value,
        "pro_plan": SubscriptionTierChoices.BUSINESS.value,
    }

    return tier_map


def _extract_entitlement_info(entitlements):
    """
    Extract dimension and expiration date from the first entitlement
    """

    if not entitlements:
        return None, None

    first_entitlement = entitlements[0]
    dimension = first_entitlement.get("Dimension")
    expiration_date_str = first_entitlement.get("ExpirationDate")

    return dimension, expiration_date_str


def _parse_expiration_date(expiration_date_str):
    """
    Parse expiration date string to date object
    """

    if not expiration_date_str:
        return None

    try:
        if isinstance(expiration_date_str, datetime):
            parsed_date = expiration_date_str.date()
            return parsed_date

        elif isinstance(expiration_date_str, date):
            return expiration_date_str

        elif isinstance(expiration_date_str, str):
            parsed_date = datetime.fromisoformat(expiration_date_str).date()
            return parsed_date

        else:
            logger.error(
                f"Unexpected type for expiration date: {type(expiration_date_str)}"
            )
            return None

    except ValueError as e:
        logger.error(f"Failed to parse expiration date '{expiration_date_str}': {e}")
        return None
    except Exception as e:
        logger.exception(
            f"Unexpected error parsing expiration date '{expiration_date_str}': {e}"
        )
        return None


def _update_organization_subscription(aws_customer, tier_name, expiration_date):
    """
    Update or create organization subscription with the specified tier
    """
    try:
        subscription_tier = SubscriptionTier.objects.get(name=tier_name)

        OrganizationSubscription.objects.update_or_create(
            organization=aws_customer.organization,
            defaults={
                "subscription_tier": subscription_tier,
                "next_renewal_date": expiration_date,
                "wallet_balance": subscription_tier.wallet_refill_amount,
            },
        )
        logger.info(
            f"Set organization {aws_customer.organization.id} to tier {tier_name}"
        )
        return True
    except SubscriptionTier.DoesNotExist:
        logger.error(
            f"SubscriptionTier '{tier_name}' not found. Organization will remain on default tier."
        )
        return False


def _process_entitlement_tier(aws_customer, entitlements):
    """
    Process entitlements to determine and apply subscription tier
    """
    tier_map = _get_tier_mapping()
    dimension, expiration_date_str = _extract_entitlement_info(entitlements)

    if not dimension or dimension not in tier_map:
        logger.warning(f"Invalid or missing dimension '{dimension}' in entitlements")
        return False

    tier_name = tier_map[dimension]
    aws_customer.subscription_status = SubscriptionStatus.SUBSCRIBE_SUCCESS

    expiration_date = _parse_expiration_date(expiration_date_str)
    result = _update_organization_subscription(aws_customer, tier_name, expiration_date)

    return result


def process_aws_entitlements(customer_identifier, product_code):
    """
    Process the AWS entitlements for a customer
    """
    try:
        aws_customer = AWSMarketplaceCustomer.objects.get(
            customer_identifier=customer_identifier
        )
    except AWSMarketplaceCustomer.DoesNotExist:
        logger.error(f"AWS customer with identifier '{customer_identifier}' not found")
        return False

    entitlements = aws_marketplace_service.get_customer_entitlements(
        customer_identifier, product_code
    )

    if not entitlements:
        logger.warning(f"No entitlements found for customer {customer_identifier}")
        return False

    # Serialize entitlements and save to customer
    serialized_entitlements = _serialize_entitlements(entitlements)
    aws_customer.entitlements = serialized_entitlements

    # Process entitlements to update subscription
    _process_entitlement_tier(aws_customer, entitlements)

    aws_customer.save()


def _create_onboarding_token_for_launch(
    customer_identifier, aws_customer, product_code
):
    """
    Create onboarding token for users who need to sign up
    """
    onboarding_token = _uuid.uuid4().hex

    cache_data = {
        "customer_identifier": customer_identifier,
        "organization_id": (
            str(aws_customer.organization.id) if aws_customer.organization else None
        ),
        "product_code": product_code,
    }

    cache.set(
        f"aws_onboard:{onboarding_token}",
        cache_data,
        timeout=15 * 60,
    )

    return onboarding_token


def _deactivate_previous_tokens(user):
    """
    Deactivate previous refresh tokens for the user
    """
    AuthToken.objects.filter(
        user=user, auth_type=AuthTokenType.REFRESH.value, is_active=True
    ).update(is_active=False)


def _create_refresh_token(user):
    """
    Create and cache a new refresh token for the user
    """
    refresh_token = AuthToken.objects.create(
        user=user,
        auth_type=AuthTokenType.REFRESH.value,
        last_used_at=timezone.now(),
        is_active=True,
    )

    refresh_token_encrypted = generate_encrypted_message(
        {"user_id": str(user.id), "id": str(refresh_token.id)}
    )

    cache.set(
        f"refresh_token_{str(refresh_token.id)}",
        {"token": refresh_token_encrypted, "user": user},
        timeout=getattr(settings, "AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES", 60)
        * 60
        * 24
        * 7,
    )

    return refresh_token_encrypted


def _create_access_token(user):
    """
    Create and cache a new access token for the user
    """

    access_token = AuthToken.objects.create(
        user=user,
        auth_type=AuthTokenType.ACCESS.value,
        last_used_at=timezone.now(),
        is_active=True,
    )

    access_token_encrypted = generate_encrypted_message(
        {"user_id": str(user.id), "id": str(access_token.id)}
    )

    cache.set(
        f"access_token_{str(access_token.id)}",
        {"token": access_token_encrypted, "user": user},
        timeout=getattr(settings, "AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES", 60) * 60,
    )

    return access_token_encrypted


def _get_user_for_launch(aws_customer):
    """
    Get the appropriate user for launch (owner first, then first member)
    """

    owner_user = aws_customer.organization.members.filter(
        organization_role=OrganizationRoles.OWNER
    ).first()

    user = owner_user or aws_customer.organization.members.first()

    return user


def process_aws_launch_software(registration_token):
    """
    Process AWS Marketplace launch software request

    This function handles the business logic for launching software:
    1. Resolves the registration token to get customer info
    2. Checks if customer exists and has users
    3. Returns appropriate response (signup needed or access tokens)

    Returns:
        dict: Response data with either signup info or access tokens
        str: Error message if processing fails
    """
    try:
        customer_identifier, customer_aws_account_id, product_code = (
            aws_marketplace_service.resolve_customer_token(registration_token)
        )

        try:
            aws_customer = AWSMarketplaceCustomer.objects.get(
                customer_identifier=customer_identifier
            )
        except AWSMarketplaceCustomer.DoesNotExist:
            return None, "AWS Marketplace customer not found. Please sign up first."

        # Check if user exists
        has_user = bool(
            aws_customer.organization and aws_customer.organization.members.exists()
        )

        if not has_user:
            onboarding_token = _create_onboarding_token_for_launch(
                customer_identifier, aws_customer, product_code
            )
            response_data = {
                "valid": True,
                "action": "signup",
                "onboarding_token": onboarding_token,
            }
            return response_data, None

        # Check subscription status
        if aws_customer.subscription_status not in [
            SubscriptionStatus.SUBSCRIBE_SUCCESS,
            SubscriptionStatus.SUBSCRIBE_PENDING,
        ]:
            return None, "Your subscription is not active"

        # Get user for launch
        user = _get_user_for_launch(aws_customer)
        if not user:
            return None, "No user account found. Please complete signup first."

        _deactivate_previous_tokens(user)
        refresh_token_encrypted = _create_refresh_token(user)
        access_token_encrypted = _create_access_token(user)

        response_data = {
            "access": access_token_encrypted,
            "refresh": refresh_token_encrypted,
            "new_org": aws_customer.organization.is_new,
        }

        return response_data, None

    except Exception as e:
        logger.error(f"Error processing AWS launch software: {str(e)}")
        return None, f"Launch processing failed: {str(e)}"


def process_aws_onboarding(onboarding_token, email, full_name):
    """
    Process the AWS onboarding process
    """
    customer_identifier, product_code = get_onboarding_session(onboarding_token)
    process_aws_entitlements(customer_identifier, product_code)
    check_if_user_exists(email)
    aws_customer, user_email = process_aws_signup(email, full_name, customer_identifier)

    # delete the onboarding token after successful onboarding
    delete_onboarding_token(onboarding_token)

    return user_email
