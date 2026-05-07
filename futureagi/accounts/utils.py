import os
import re
import secrets
import string

import requests
import structlog
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from slack_sdk import WebhookClient

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey, User
from accounts.serializers.user import UserSignupSerializer
from accounts.user_onboard import (
    create_demo_traces_and_spans,
    upload_demo_dataset,
)
from analytics.mixpanel_util import mixpanel_tracker
from analytics.utils import (
    MixpanelEvents,
    get_mixpanel_properties,
    mixpanel_slack_notfy,
    track_mixpanel_event,
)
from saml2_auth.models import SAMLMetadataModel
from tfc.constants.email import FREE_EMAIL_DOMAINS
from tfc.settings.settings import ssl
from tfc.utils.email import email_helper
from tfc.utils.parse_errors import parse_serialized_errors

logger = structlog.get_logger(__name__)


def resolve_org(request):
    """Return the organization resolved by auth middleware, falling back to the user FK."""
    return getattr(request, "organization", None) or request.user.organization


def resolve_org_role(user, organization):
    """Return the user's role in the given organization via membership lookup.

    Falls back to user.organization_role only for truly legacy accounts
    (users with no membership records at all for this org).
    """
    mem = OrganizationMembership.no_workspace_objects.filter(
        user=user, organization=organization, is_active=True
    ).first()
    if mem:
        return mem.role

    # Fallback: legacy field (only for truly legacy accounts)
    # Users with deactivated memberships should NOT get this fallback.
    if user.organization_id and user.organization_id == organization.id:
        has_any_membership = OrganizationMembership.no_workspace_objects.filter(
            user=user, organization=organization
        ).exists()
        if not has_any_membership:
            return user.organization_role
    return None


def get_user_organization(user):
    """Get the user's active organization from membership (not the FK)."""
    from accounts.models.organization_membership import OrganizationMembership

    membership = (
        OrganizationMembership.no_workspace_objects.filter(user=user, is_active=True)
        .select_related("organization")
        .first()
    )
    return membership.organization if membership else None


def get_request_organization(request):
    """Get org from middleware (X-Organization-Id header) or user's active membership."""
    org = getattr(request, "organization", None)
    if not org:
        org = get_user_organization(request.user)
    return org


def generate_password(
    length=12,
    include_uppercase=True,
    include_lowercase=True,
    include_digits=True,
    include_special=True,
):
    # Define character sets
    uppercase_letters = string.ascii_uppercase
    lowercase_letters = string.ascii_lowercase
    digits = string.digits
    special_characters = string.punctuation

    # Create a pool of characters based on inclusion criteria
    character_pool = ""
    if include_uppercase:
        character_pool += uppercase_letters
    if include_lowercase:
        character_pool += lowercase_letters
    if include_digits:
        character_pool += digits
    if include_special:
        character_pool += special_characters

    # Ensure at least one character from each included set
    password = []
    if include_uppercase:
        password.append(secrets.choice(uppercase_letters))
    if include_lowercase:
        password.append(secrets.choice(lowercase_letters))
    if include_digits:
        password.append(secrets.choice(digits))
    if include_special:
        password.append(secrets.choice(special_characters))

    # Fill the rest of the password length with cryptographically secure random characters
    for _ in range(length - len(password)):
        password.append(secrets.choice(character_pool))

    # Shuffle the password to mix up the guaranteed characters
    # Using secrets.SystemRandom for cryptographically secure shuffling
    secrets.SystemRandom().shuffle(password)

    # Convert the list of characters to a string
    return "".join(password)


def is_work_email(email):
    """
    Returns True if the email appears to be a work email,
    i.e. its domain is not one of the common free providers.
    """
    # Normalize the email
    email = email.strip().lower()

    # Basic email format validation
    if not re.match(r"^[^@]+@[^@]+\.[^@]+$", email):
        return False

    # List of free email domains (modify as needed)
    free_domains = {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "msn.com",
        "yahoo.com",
        "noreply.github.com",  # GitHub's no-reply emails
        "github.com",  # In case GitHub emails are used
    }

    saml_org_ids = SAMLMetadataModel.objects.values_list("organization_id", flat=True)
    logger.info(f"ORG IDS: {saml_org_ids}")
    saml_user_ids = OrganizationMembership.no_workspace_objects.filter(
        organization_id__in=saml_org_ids, is_active=True
    ).values_list("user_id", flat=True)
    users = User.objects.filter(id__in=saml_user_ids).values_list("email", flat=True)
    if email in users:
        raise Exception(
            "SAML authentication is required for your organization. Please log in using SAML."
        )

    # Extract the domain part from the email
    domain = email.split("@")[-1]

    # Return False if the domain is in the free domains list
    return domain not in free_domains


def first_signup(data, mode=None):
    if not data.get("email"):
        raise Exception("Email not provided")

    data["email"] = data["email"].lower()
    # Defensive stripping of deprecated account-update parameters
    data.pop("old_email", None)
    data.pop("update_true", None)
    user_provided_password = data.get("password") or ""
    if user_provided_password:
        generated_password = None
    else:
        generated_password = generate_password()
    data["password"] = generated_password
    data.pop("allow_email", False)  # Remove but don't store
    # Extract domain from email
    email_parts = data["email"].split("@")
    domain = email_parts[1]

    if domain in FREE_EMAIL_DOMAINS:
        # For free email providers, use the username part and create org name
        username = email_parts[0]
        # Remove numbers and special characters, capitalize first letter of each word
        # org_name = re.sub(r'[0-9._-]+', ' ', username)
        # org_name = ' '.join(word.capitalize() for word in org_name.split())
        # data["company_name"] = org_name + " Org"
        data["company_name"] = ""
    else:
        # For work emails, use domain as before
        data["company_name"] = domain.split(".")[0]

    allow_any_email = os.getenv("ALLOW_ANY_EMAIL", "false").lower() == "true"
    if not allow_any_email and not is_work_email(data.get("email")):
        raise Exception("Provided Email is not work email")

    serializer = UserSignupSerializer(data=data)
    if serializer.is_valid():
        user = serializer.save()
        organization = Organization.objects.create(
            name=data["company_name"], region=settings.REGION
        )
        user.organization = organization
        user.organization_role = "Owner"
        user.is_active = True
        user.save()

        # Create OrganizationMembership for RBAC (1-user-1-org invariant)
        from accounts.models.organization_membership import OrganizationMembership
        from tfc.constants.levels import Level

        OrganizationMembership.objects.get_or_create(
            user=user,
            organization=organization,
            defaults={
                "role": "Owner",
                "level": Level.OWNER,
                "is_active": True,
            },
        )

        # Create billing subscription (Free plan default).
        # Skip when ee is absent — no subscription model.
        try:
            from ee.usage.utils.usage_entries import (
                create_organization_subscription_if_not_exists,
            )

            create_organization_subscription_if_not_exists(organization)
        except ImportError:
            pass
    else:
        raise Exception(f"Invalid data: {serializer.errors}")

    email = data.get("email", None)
    organization = get_user_organization(user)

    if email:
        user = User.objects.get(email=email)
        apiKeys = OrgApiKey.no_workspace_objects.filter(
            organization=organization, type="system", enabled=True
        )
        properties = get_mixpanel_properties(user=user, mode=mode)
        if mode:
            event_name = MixpanelEvents.SSO_SIGNUP.value
        else:
            event_name = MixpanelEvents.SIGNUP.value
        mixpanel_tracker.set_details(user)
        track_mixpanel_event(event_name, properties)

        if len(apiKeys) == 0:
            OrgApiKey.no_workspace_objects.create(
                organization=organization, type="system"
            )
        if generated_password:
            process_post_registration(user.id, generated_password)

        return user

    else:
        error_messages = parse_serialized_errors(serializer)
        raise Exception(str(error_messages))


def send_invite_email(email, organization, inviter):
    """Send invite email to the target user."""
    try:
        org_name = organization.display_name or organization.name

        existing = User.objects.filter(email__iexact=email).first()
        if existing and existing.is_active:
            template = "existing_user_invite.html"
            extra_context = {}
        elif existing:
            # Inactive user — send invite with activation token
            uid = urlsafe_base64_encode(force_bytes(existing.pk))
            token = default_token_generator.make_token(existing)
            template = "invite_user.html"
            extra_context = {"uid": uid, "token": token}
        else:
            # No user record yet; email will be sent once the user signs up.
            logger.info("invite_email_skipped_no_user", email=email)
            return

        email_helper(
            f"You are invited to join {org_name} - Future AGI",
            template,
            {
                "org_name": org_name,
                "invited_by": inviter.name,
                "app_url": settings.APP_URL,
                "ssl": ssl,
                **extra_context,
            },
            [email],
        )
    except Exception:
        logger.exception("invite_email_failed", email=email)


def send_signup_email(generated_password, user_email, user_name):
    email_helper(
        "Welcome to Future AGI - Your Account is Ready!",
        "user_onboard.html",
        {
            "first_name": user_name.split(" ")[0],
            "generated_password": generated_password,
            # "token": token,
            "app_url": settings.APP_URL,
        },
        [user_email],
    )


def send_slack_notification(user, updated=False, err=None):
    try:
        org = get_user_organization(user)
        org_name = (org.display_name or org.name) if org else "Unknown"
        data = f"🎉 New User Joined! \n👤 Name: {user.name} \n📧 Email: {user.email} \n🏢 Company: {org_name} \nEnv: {os.getenv('ENV_TYPE')}"
        if updated:
            data += "\n✅ Contact Updated in HubSpot"
        if err:
            data += f"\n❌ Error (HUBSPOT): {err}"
        webhook = WebhookClient(settings.SLACK_WEBHOOK_CHANNEL)
        webhook.send(text=data)
        logger.info("Slack notification sent successfully")
    except Exception as e:
        logger.error(f"Failed to send Slack notification: {str(e)}")


def send_hubspot_notification(user):
    updated = False
    err = None

    headers = {
        "Authorization": f"Bearer {settings.HUBSPOT_API_TOKEN}",
        "Content-Type": "application/json",  # Add content type header
    }

    # Initialize variables with default values before try block
    firstname = "Unknown"
    lastname = ""
    company_name = "Unknown Company"

    # Better data validation
    try:
        name_parts = user.name.strip().split() if user.name else ["Unknown"]
        firstname = name_parts[0] if len(name_parts) > 0 else "Unknown"
        lastname = name_parts[-1] if len(name_parts) > 1 else ""

        org = get_user_organization(user)
        if org:
            company_name = org.display_name or org.name or "Unknown Company"
    except Exception as e:
        logger.error(f"Error processing user data: {str(e)}")
        # Variables already have default values, no need to reassign

    contact = {
        "properties": {
            "email": user.email,
            "firstname": firstname,
            "lastname": lastname,
            "company": company_name,
            "in_trial": "Yes",
            "lead_type": (
                user.organization_role
                if hasattr(user, "organization_role")
                else "Unknown"
            ),
            "lead_source": "App",
            "logged_in": "No",
            "sign_up": "Yes",
            # "env": os.getenv("ENV_TYPE")
        }
    }

    logger.info(f"CONTACT: {contact}")
    response_text = "No Response"
    response = (
        None  # Initialize before try block to avoid NameError in exception handler
    )

    try:
        response = requests.post(
            settings.HUBSPOT_URL, json=contact, headers=headers, timeout=10
        )
        # Get response text before checking status
        try:
            response_text = response.json()
        except Exception:
            response_text = "No Response"
        response.raise_for_status()
        logger.info("Contact Created Successfully in HubSpot")
        updated = True
    except requests.exceptions.RequestException as e:
        logger.error(
            f"Failed to Create Contact in HubSpot: {str(e)}, Response: {response_text}"
        )

        # Only attempt to update if contact already exists (409 Conflict)
        # Other errors (auth, validation, etc.) should not trigger an update attempt
        should_try_update = response is not None and response.status_code == 409

        if not should_try_update:
            err = f"Create failed: {str(e)}, Response: {response_text}"
            return updated, err

        update_contact = {
            "properties": {
                "email": user.email,
                "firstname": firstname,
                "lastname": lastname,
                "company": company_name,
                "in_trial": "Yes",
                "lead_type": (
                    user.organization_role
                    if hasattr(user, "organization_role")
                    else "Unknown"
                ),
                "lead_source": "App",
                "logged_in": "No",
                "sign_up": "Yes",
            }
        }

        # Get response text before checking status
        try:
            response = requests.patch(
                settings.HUBSPOT_UPDATE_URL.format(user.email),
                json=update_contact,
                headers=headers,
                timeout=10,
            )

            try:
                response_text = response.json()
            except Exception:
                response_text = "No Response"
            response.raise_for_status()
            logger.info("Contact Updated Successfully in HubSpot")
            updated = True
        except requests.exceptions.RequestException as e:
            err = f"{str(e)}, Response: {response_text}"
            logger.error(
                f"Failed to Update Contact in HubSpot: {str(e)}, Response: {response_text}"
            )
            mixpanel_slack_notfy(
                f"Failed to Update Contact in HubSpot: {str(e)}, Response: {response_text}"
            )

    return updated, err


def _run_post_registration(user_id, generated_password):
    """Process post-registration steps in a separate thread"""
    user = User.objects.get(id=user_id)
    if user:
        send_signup_email(generated_password, user.email, user.name)

        if os.getenv("ENV_TYPE") not in ["local"]:
            updated, err = send_hubspot_notification(user)
            send_slack_notification(user, updated=updated, err=err)

        org = get_user_organization(user)
        if org:
            upload_demo_dataset(org.id, str(user.id))
            # create_demo_prompt_template(str(org.id), str(user.id))
            create_demo_traces_and_spans(str(org.id))


# TODO: use async views to replace this code. its wrong
def process_post_registration(user_id, generated_password):
    """Process post-registration steps using Temporal"""
    # Import the activity to register it
    import tfc.temporal.background_tasks.activities  # noqa: F401
    from tfc.temporal.drop_in import start_activity

    start_activity(
        "run_post_registration_activity",
        args=(user_id, generated_password),
        queue="default",
    )
