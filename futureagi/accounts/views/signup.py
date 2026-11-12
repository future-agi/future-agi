import binascii
import os
from datetime import timedelta

import requests
import structlog
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import (
    PasswordResetTokenGenerator,
)
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from accounts.authentication import (
    allows_no_organization,
    allows_no_workspace,
    decrypt_message,
    generate_encrypted_message,
)
from accounts.invitation_authentication import InvitationAuthentication
from accounts.models import User
from accounts.models.auth_token import (
    AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES,
    PASSWORD_RESET_TOKEN_EXPIRATION_TIME_IN_MINUTES,
    AuthToken,
    AuthTokenOrigin,
    AuthTokenType,
)
from accounts.models.organization import Organization
from accounts.serializers.contracts import (
    ACCOUNTS_ERROR_RESPONSES,
    AcceptInvitationPreviewResponseSerializer,
    AcceptInvitationRequestSerializer,
    AccountsBulkUserMutationItemSerializer,
    AccountsDirectMessageResponseSerializer,
    AccountsMessageResponseSerializer,
    AccountsStringResultResponseSerializer,
    AccountsTokenPairResponseSerializer,
    AccountsUserProfileResponseSerializer,
    LogoutRequestSerializer,
    PasswordResetConfirmRequestSerializer,
    PasswordResetInitiateRequestSerializer,
    PasswordResetInitiateResponseSerializer,
    SignupRequestSerializer,
    SignupResponseSerializer,
    UserFullNameUpdateRequestSerializer,
    UserIdsRequestSerializer,
)
from accounts.serializers.user import UpdateUserSerializer
from accounts.services.auth_cache import (
    cache_access_token,
    revoke_cached_access_token,
)
from accounts.services.hard_erase_lifecycle import identity_keys, lifecycle_lock
from accounts.services.identity_issuance import _exists, issue_membership
from accounts.services.invitation_links import (
    invitation_matches,
    make_invitation_token,
    pending_invitation,
    read_invitation_token,
)
from accounts.services.offboarding import offboard_organization_member
from accounts.services.token_service import issue_tokens
from accounts.utils import (
    WorkEmailRequired,
    build_password_reset_link,
    first_signup,
    refresh_pending_invite_expiration,
)
from analytics.utils import (
    MixpanelEvents,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from saml2_auth.models import SAMLMetadataModel
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.ee_gating import is_oss
from tfc.permissions.rbac import IsOrganizationAdmin
from tfc.permissions.utils import get_org_membership
from tfc.settings.settings import RECAPTCHA_ENABLED, RECAPTCHA_SECRET_KEY, ssl
from tfc.utils.api_contracts import validated_api_request
from tfc.utils.email import email_delivery_configured, email_helper
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.install_setup import manage_py_command
from tfc.utils.tenant_scope import counts_as_member

try:
    from ee.usage.utils.usage_entries import (
        create_organization_subscription_if_not_exists,
    )
except ImportError:
    create_organization_subscription_if_not_exists = None

logger = structlog.get_logger(__name__)
_gm = GeneralMethods()


def oss_reset_unavailable_message() -> str:
    """What a self-hosted install without email delivery tells the person who
    asked for a reset. They are signed out and usually cannot reach the host,
    so they only learn who can help; the recovery steps go to the server log
    (``log_oss_reset_unavailable``)."""
    return (
        "This site can't send email, so we couldn't send a reset link. "
        "Ask your administrator to reset your password."
    )


def log_oss_reset_unavailable() -> None:
    """Give the host's administrator the recovery steps for the setup this
    install runs in. Names no account and carries no token: the same entry is
    logged whether or not the address has an account here."""
    logger.warning(
        "password_reset_email_unavailable",
        recovery_command=manage_py_command("reset_password --email <address>"),
        email_setting="MAILGUN_API_KEY",
        link_in_response_setting="OSS_RETURN_PASSWORD_RESET_LINK",
        hint=(
            "A password reset was requested but this deployment cannot send "
            "email. Set a new password from the host with the command in "
            "recovery_command, or configure email (MAILGUN_API_KEY). "
            "OSS_RETURN_PASSWORD_RESET_LINK=true returns reset links in the "
            "browser instead; use it only on a network where every caller is "
            "already trusted, since the link takes over the account it names."
        ),
    )


def _oss_reset_unavailable_response():
    log_oss_reset_unavailable()
    return _gm.success_response(
        {"message": oss_reset_unavailable_message(), "email_unavailable": True}
    )


def oss_reset_link_in_response() -> bool:
    """Whether ``initiate_password_reset`` may hand the reset link straight back
    to the caller.

    The endpoint is unauthenticated, and the link sets the password on the named
    account, so returning it makes anyone who can reach the instance able to take
    over any account on it. Off by default: an operator opts in only for a
    deployment where network reach already implies full trust.
    """
    return os.getenv("OSS_RETURN_PASSWORD_RESET_LINK", "false").lower() == "true"


class AccountActivationTokenGenerator(PasswordResetTokenGenerator):
    def _make_hash_value(self, user, timestamp):
        return str(user.pk) + str(timestamp) + str(user.is_active)


account_activation_token = AccountActivationTokenGenerator()


def verify_recaptcha(token):
    """Verify the reCAPTCHA token with Google.

    Disabled (the self-hosted default unless RECAPTCHA_SECRET_KEY is set) it
    passes without a network call. Enabled without a secret it fails closed:
    RECAPTCHA_ENABLED=true was asked for explicitly, so a missing key is a
    misconfiguration rather than a reason to skip the check.
    """
    if not RECAPTCHA_ENABLED:
        logger.debug("recaptcha verification skipped (disabled)")
        return True

    secret_key = RECAPTCHA_SECRET_KEY
    if not secret_key:
        logger.error(
            "recaptcha enabled but RECAPTCHA_SECRET_KEY is missing; set the key "
            "or RECAPTCHA_ENABLED=false",
        )
        return False
    if not token:
        logger.warning("recaptcha token missing")
        return False

    url = "https://www.google.com/recaptcha/api/siteverify"

    data = {"secret": secret_key, "response": token}

    try:
        response = requests.post(url, data=data, timeout=10)
        response.raise_for_status()
        result = response.json()
    except Exception:
        logger.exception("recaptcha verification request failed")
        return False

    logger.info("recaptcha result", result=result)

    return result.get("success", False)


def _login_payload(user):
    """The body ``POST /accounts/token/`` returns on a successful login, built for
    a user who has just signed up.

    OSS signup logs the new owner straight in, so the frontend should be able to
    treat the signup response exactly as it treats a login response — same
    fields, same routing decision — instead of special-casing the one endpoint.
    Selecting the organization here is part of that: login does it from the first
    active membership, and the auth layer reads it back on the next request.
    """
    org = user.organization
    if org:
        user.config["selected_organization_id"] = str(org.id)
        user.config["currentOrganizationId"] = str(org.id)
        user.save(update_fields=["config"])
    membership = get_org_membership(user)
    new_org = bool(
        org
        and org.is_new
        and membership
        and membership.role == OrganizationRoles.OWNER.value
    )

    return {**issue_tokens(user), "new_org": new_org}


@swagger_auto_schema(
    method="post",
    request_body=SignupRequestSerializer,
    responses={200: SignupResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
)
@allows_no_organization
@api_view(["POST"])
@validated_api_request(
    request_serializer=SignupRequestSerializer,
    responses={200: SignupResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
    document=False,
)
def user_signup(request):
    try:
        data = request.validated_data
        recaptcha_token = data.get("recaptcha_response", "")
        logger.info(
            "signup_request",
            host=request.get_host(),
            payload={
                k: v
                for k, v in request.data.items()
                if k not in ("password", "recaptcha_response")
            },
        )

        email = data.get("email", "")
        email = email.lower()

        # Log and reject deprecated account-update parameters (security hardening)
        if request.data.get("update_true") or request.data.get("old_email"):
            logger.warning(
                "signup_blocked_update_attempt",
                ip=request.META.get(
                    "HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR")
                ),
                email=email,
                old_email=bool(request.data.get("old_email")),
            )

        is_local = os.getenv("ENV_TYPE") == "local"

        if is_local or is_oss():
            logger.debug(
                "recaptcha verification skipped",
                reason="local environment" if is_local else "oss deployment",
            )
        elif not verify_recaptcha(recaptcha_token):
            logger.error("recaptcha verification failed")
            return _gm.bad_request("reCAPTCHA verification failed")
        else:
            logger.info("recaptcha verification passed")

        # Check if a user with the provided email already exists
        if User.objects.filter(email=email).exists():
            return _gm.bad_request("User with this email already exists.")

        # Allowlist fields to prevent hidden-parameter attacks
        if is_oss():
            allowed_fields = {
                "email",
                "full_name",
                "company_name",
                "password",
            }
        else:
            allowed_fields = {
                "email",
                "full_name",
                "company_name",
                "allow_email",
            }

        sanitized_data = {k: v for k, v in data.items() if k in allowed_fields}
        user = first_signup(sanitized_data)

        if is_oss():
            logger.info("signup_auto_login", email=email, user_id=str(user.id))
            try:
                from tfc.deployment_telemetry.events import record_event

                record_event(
                    "user_logged_in",
                    actor_type="human_user",
                    actor_id=user.id,
                    source="web",
                    organization_id=user.organization_id,
                )
            except Exception:
                logger.debug(
                    "deployment_telemetry_signup_login_event_failed", exc_info=True
                )
            return _gm.success_response(_login_payload(user), status=status.HTTP_200_OK)

        return _gm.success_response(
            {"message": "User Created Successfully, Please Check your email to proceed"}
        )

    except WorkEmailRequired as exc:
        logger.info(
            "signup_rejected_personal_email",
            email=request.data.get("email", "").lower(),
        )
        return _gm.bad_request(
            {
                "error": str(exc),
                "error_code": "SIGNUP_VALIDATION_FAILED",
                "field_errors": {"email": [str(exc)]},
            }
        )

    except DRFValidationError as exc:
        logger.info(
            "signup_validation_failed",
            email=request.data.get("email", "").lower(),
            errors=exc.detail,
        )
        return _gm.bad_request(
            {
                "error": "Signup validation failed",
                "error_code": "SIGNUP_VALIDATION_FAILED",
                "field_errors": exc.detail,
            }
        )

    except Exception:
        logger.exception("Error during signup")
        return _gm.bad_request("An error occurred during signup.")


@swagger_auto_schema(
    method="post",
    request_body=LogoutRequestSerializer,
    responses={200: AccountsMessageResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
)
@allows_no_organization
@api_view(["POST"])
@validated_api_request(
    request_serializer=LogoutRequestSerializer,
    responses={200: AccountsMessageResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
    document=False,
)
def user_logout(request):
    try:
        auth_token = (
            request.META.get("HTTP_AUTHORIZATION", "")
            .replace("Bearer", "")
            .replace(" ", "")
        )
        if not auth_token:
            return _gm.bad_request("No auth token provided.")

        decrypted_token_obj = decrypt_message(auth_token)
        token_id = decrypted_token_obj.get("id")
        try:
            auth_token_obj = AuthToken.objects.get(
                id=token_id, auth_type=AuthTokenType.ACCESS.value
            )
        except AuthToken.DoesNotExist:
            return _gm.bad_request("Invalid token id.")

        auth_token_obj.is_active = False
        auth_token_obj.save()
        revoke_cached_access_token(token_id, cache_backend=cache)
        return _gm.success_response({"message": "User logged out successfully."})
    except Exception:
        logger.exception("Error in user logout")
        return _gm.bad_request("Error in user logout.")


@allows_no_organization
@api_view(["GET"])
@permission_classes([AllowAny])
def activate_account(request, uidb64, token):
    # Rate-limit by IP: 10 requests per minute. Skipped in OSS mode where all
    # traffic shares a single IP (localhost / Docker gateway).
    if not is_oss():
        ip = request.META.get(
            "HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", "")
        )
        if ip:
            ip = ip.split(",")[0].strip()
        rate_key = f"activate_account_rate:{ip}"
        attempts = cache.get(rate_key, 0)
        if attempts >= 10:
            return _gm.too_many_requests(
                "Too many activation attempts. Please try again later."
            )
        cache.set(rate_key, attempts + 1, timeout=60)

    try:
        # Decode the uidb64 to the user ID
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)

        # Check if the token is valid
        if account_activation_token.check_token(user, token):
            # Allocate the new identity before acquiring the complete entry set.
            organization = Organization(
                name=user.email.split("@")[0] + " Org", region=settings.REGION
            )
            with lifecycle_lock(
                keys=identity_keys(organizations=[organization.pk], people=[user.pk])
            ):
                _exists(User, user.pk, "default")
                # Activate the user and save the new status
                user.is_active = True

                organization.save(force_insert=True)

                user.organization = organization
                user.organization_role = OrganizationRoles.OWNER.value
                user.save()

                # Create OrganizationMembership for RBAC (1-user-1-org invariant)
                from accounts.models.organization_membership import (
                    OrganizationMembership,
                )
                from tfc.constants.levels import Level

                issue_membership(
                    OrganizationMembership.objects,
                    "get_or_create",
                    user=user,
                    organization=organization,
                    defaults={
                        "role": OrganizationRoles.OWNER.value,
                        "level": Level.OWNER,
                        "is_active": True,
                    },
                )

                # create organization subscription (skipped when ee is absent)
                if create_organization_subscription_if_not_exists is not None:
                    create_organization_subscription_if_not_exists(organization)

            return Response(
                {"message": "Account successfully activated."},
                status=status.HTTP_200_OK,
            )
        else:
            return _gm.bad_request("Activation link is invalid or has expired.")

    except User.DoesNotExist:
        return _gm.bad_request("User does not exist.")
    except Exception:
        logger.exception("Error during account activation")
        return _gm.internal_server_error_response(
            "An error occurred during account activation."
        )


@swagger_auto_schema(
    method="post",
    request_body=PasswordResetInitiateRequestSerializer,
    responses={
        200: PasswordResetInitiateResponseSerializer,
        **ACCOUNTS_ERROR_RESPONSES,
    },
)
@allows_no_organization
@api_view(["POST"])
@validated_api_request(
    request_serializer=PasswordResetInitiateRequestSerializer,
    responses={
        200: PasswordResetInitiateResponseSerializer,
        **ACCOUNTS_ERROR_RESPONSES,
    },
    document=False,
    reject_unknown_fields=True,
)
def initiate_password_reset(request):
    email = request.validated_data.get("email", None)
    if not email:
        return _gm.bad_request("Email is required.")
    email = email.lower()
    if email:
        try:
            # Use select_related to avoid N+1 query when accessing user.organization
            user = User.objects.select_related("organization").get(email=email)
            if user:
                if SAMLMetadataModel.objects.filter(
                    organization=user.organization, deleted=False
                ).exists():
                    return _gm.bad_request(
                        "Single sign-on (SSO) is enabled for your organization. Please log in using your organization's SSO provider."
                    )

                properties = get_mixpanel_properties(user=user)
                track_mixpanel_event(MixpanelEvents.RESET_PASS.value, properties)

                # A self-hosted install mails the link like Cloud does once
                # email delivery is configured; without it there is no way to
                # hand the link over short of the opt-in below.
                if (
                    is_oss()
                    and not oss_reset_link_in_response()
                    and not email_delivery_configured()
                ):
                    return _oss_reset_unavailable_response()

                # A reset link is its own token type: no bearer path accepts
                # it, and reset confirmation accepts nothing else.
                reset_token = AuthToken.objects.create(
                    user=user,
                    auth_type=AuthTokenType.PASSWORD_RESET.value,
                    is_active=True,
                    last_used_at=timezone.now(),
                )
                token = generate_encrypted_message(
                    {"user_id": str(user.id), "id": str(reset_token.id)}
                )

                # Generate uidb64
                uidb64 = urlsafe_base64_encode(force_bytes(user.id))

                reset_link = build_password_reset_link(str(uidb64), token)

                if settings.DEBUG:
                    logger.info(f"Password reset link {reset_link}")
                if is_oss() and oss_reset_link_in_response():
                    return _gm.success_response(
                        {
                            "message": "Use the link below to reset your password.",
                            "reset_link": reset_link,
                        }
                    )

                email_helper(
                    "Reset Password",
                    "reset_password.html",
                    {
                        "uid": str(uidb64),
                        "token": token,
                        "app_url": settings.APP_URL,
                        "ssl": ssl,
                    },
                    [user.email],
                )

                return _gm.success_response(
                    {
                        "message": f"If an account matches {email}, you will receive an email with instructions on how to reset your password shortly."
                    }
                )
        except User.DoesNotExist:
            if is_oss():
                # Naming the address only helps where the caller can act on the
                # answer. With the link withheld the response is identical for
                # every address, so the endpoint tells an anonymous caller
                # nothing about who has an account here.
                if oss_reset_link_in_response():
                    return _gm.bad_request(f"No account found for {email}.")
                if not email_delivery_configured():
                    return _oss_reset_unavailable_response()
            # Don't disclose that the user doesn't exist ,we just send a sucess response
            return _gm.success_response(
                {
                    "message": f"If an account matches {email}, you will receive an email with instructions on how to reset your password shortly."
                }
            )

    return _gm.bad_request("An error occurred, please try again.")


INVALID_RESET_LINK_MESSAGE = (
    "This password reset link is invalid or has expired. Please request a new one."
)


def _invalid_reset_link():
    # One answer for every refused link (unknown, a login or refresh token,
    # used, expired, someone else's) so the endpoint tells no one which it saw.
    return _gm.forbidden_response(INVALID_RESET_LINK_MESSAGE)


@swagger_auto_schema(
    method="post",
    request_body=PasswordResetConfirmRequestSerializer,
    responses={200: AccountsMessageResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
)
@allows_no_organization
@api_view(["POST"])
@validated_api_request(
    request_serializer=PasswordResetConfirmRequestSerializer,
    responses={200: AccountsMessageResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
    document=False,
    reject_unknown_fields=True,
)
def reset_password_confirm(request, uidb64, token):
    new_password = request.validated_data.get("new_password")
    repeat_password = request.validated_data.get("repeat_password")

    try:
        # Decode the uidb64 to the user ID
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)

        try:
            claims = decrypt_message(str(token))
        except AuthenticationFailed:
            return _invalid_reset_link()
        token_id = claims.get("id")
        if not token_id or str(claims.get("user_id")) != str(user.id):
            return _invalid_reset_link()

        # Only a live reset link issued to this very user: the row's type,
        # owner and age decide, not just what the payload says.
        reset_token = (
            AuthToken.objects.filter(
                id=token_id,
                user_id=user.id,
                auth_type=AuthTokenType.PASSWORD_RESET.value,
                is_active=True,
                created_at__gte=timezone.now()
                - timedelta(minutes=PASSWORD_RESET_TOKEN_EXPIRATION_TIME_IN_MINUTES),
            )
            .exclude(auth_origin=AuthTokenOrigin.SAML)
            .first()
        )
        if reset_token is None:
            return _invalid_reset_link()

        # The user can correct these, so they leave the link usable.
        if new_password != repeat_password:
            return _gm.bad_request("Passwords do not match.")

        try:
            validate_password(new_password)
        except ValidationError as e:
            return _gm.bad_request("\n".join(e.messages))

        if check_password(new_password, user.password):
            return _gm.bad_request(
                "New password cannot be the same as the old password."
            )

        with transaction.atomic():
            # Spend the link before the password changes: of two requests
            # racing with one link only one updates the row. A failure below
            # rolls this back, so the link survives a reset that did not happen.
            if (
                AuthToken.objects.filter(
                    id=reset_token.id,
                    auth_type=AuthTokenType.PASSWORD_RESET.value,
                    is_active=True,
                ).update(is_active=False)
                != 1
            ):
                return _invalid_reset_link()

            user.password = make_password(new_password)
            user.is_active = True
            user.save()

            # Any other reset link still out for this user dies with this one.
            AuthToken.objects.filter(
                user=user,
                is_active=True,
                auth_type=AuthTokenType.PASSWORD_RESET.value,
            ).update(is_active=False)

            # Revoke both session credentials so remembered browsers cannot
            # exchange a pre-reset refresh token for a new access token.
            token_ids = list(
                AuthToken.objects.filter(
                    user=user,
                    is_active=True,
                    auth_type__in=[
                        AuthTokenType.ACCESS.value,
                        AuthTokenType.REFRESH.value,
                    ],
                ).values_list("id", flat=True)
            )

            for token_id in token_ids:
                revoke_cached_access_token(str(token_id), cache_backend=cache)
                cache.delete(f"refresh_token_{str(token_id)}")

            AuthToken.objects.filter(id__in=token_ids).update(is_active=False)

        return _gm.success_response(
            {"message": "Password has been reset with the new password."}
        )

    except User.DoesNotExist:
        return _gm.bad_request("User does not exist.")
    except (ValueError, TypeError):
        # Invalid UUID format or type error
        return _gm.bad_request("Invalid reset link.")
    except Exception as e:
        # Check if it's a base64 decoding error
        if "binascii" in str(type(e).__module__) or "Incorrect padding" in str(e):
            return _gm.bad_request("Invalid reset link.")
        logger.exception("Error during password reset confirmation")
        return _gm.internal_server_error_response("An unexpected error occurred.")


@swagger_auto_schema(
    method="get",
    responses={
        200: AcceptInvitationPreviewResponseSerializer,
        **ACCOUNTS_ERROR_RESPONSES,
    },
)
@swagger_auto_schema(
    method="post",
    request_body=AcceptInvitationRequestSerializer,
    responses={200: AccountsTokenPairResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
)
@allows_no_organization
@api_view(["GET", "POST"])
@authentication_classes([InvitationAuthentication])
@validated_api_request(
    request_serializer=AcceptInvitationRequestSerializer,
    responses={200: AccountsTokenPairResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
    request_methods=["POST"],
    document=False,
    reject_unknown_fields=True,
)
def accept_invitation_mail(request, uidb64, token):
    """Accept an invitation link.

    GET  — validate the token without consuming it. Returns org info so the
           frontend can render a "Set Password" page.
    POST — accept the invite, set the user's password, activate the account,
           and return JWT tokens for auto-login.
    """
    try:
        return _accept_invitation_mail(request, uidb64, token)
    except (binascii.Error, TypeError, UnicodeDecodeError, ValueError, ValidationError):
        return _gm.bad_request("Invitation link is invalid or has expired.")
    except User.DoesNotExist:
        return _gm.bad_request(
            "An Error Occured! Please ask your Administrator to Resend Invitation Link"
        )
    except Exception:
        logger.exception("Error processing invitation acceptance")
        return _gm.internal_server_error_response(
            "An error occurred while processing the invitation."
        )


def _accept_invitation_mail(request, uidb64, token):
    # Catch failures OUTSIDE this transaction so accept() cannot partially
    # activate memberships or consume the password-reset token on refusal.
    uid = force_str(urlsafe_base64_decode(uidb64))
    snapshot = User.objects.get(pk=uid)
    from accounts.models.organization_invite import InviteStatus, OrganizationInvite

    try:
        payload = read_invitation_token(token, uid)
    except ValueError:
        return _gm.bad_request("Invitation link is invalid or has expired.")
    snapshot_invite = OrganizationInvite.objects.filter(
        pk=payload.get("invite"),
        organization_id=payload.get("organization"),
        target_email__iexact=snapshot.email,
        status=InviteStatus.PENDING,
    ).first()
    if not invitation_matches(payload, snapshot_invite, snapshot):
        return _gm.bad_request("This invitation has been cancelled or expired.")
    from accounts.services.hard_erase import _refuse
    from accounts.services.hard_erase_issuance_entry import issuance_entry

    with issuance_entry(
        organizations=[snapshot_invite.organization_id],
        people=[uid, snapshot_invite.invited_by_id],
    ):
        users = User.objects.select_related("organization")
        invites = OrganizationInvite.objects.select_related("organization")
        if request.method == "POST":
            users = users.select_for_update(of=("self",))
            invites = invites.select_for_update(of=("self",))
        user = users.get(pk=uid)
        invite = invites.filter(pk=snapshot_invite.pk).first()
        if invite is not None and invite.invited_by_id != snapshot_invite.invited_by_id:
            _refuse("invitation identities changed; retry the whole transaction")
        if not invitation_matches(payload, invite, user):
            return _gm.bad_request("This invitation has been cancelled or expired.")
        org = invite.organization

        # ------------------------------------------------------------------
        # Security check: If another user is logged in, reject the request.
        # This prevents User A from setting User B's password.
        # ------------------------------------------------------------------
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            try:
                access_token = auth_header.split(" ", 1)[1]
                token_data = decrypt_message(access_token)
                authenticated_user_id = token_data.get("user_id")
                if authenticated_user_id and str(authenticated_user_id) != str(user.id):
                    return _gm.custom_error_response(
                        status.HTTP_403_FORBIDDEN,
                        "You are logged in as a different user. Please logout first to use this invitation link.",
                        code="authenticated_user_mismatch",
                    )
            except Exception:
                # Token decryption failed - ignore and continue (treat as unauthenticated)
                pass

        recipient_authenticated = (
            getattr(request.user, "is_authenticated", False)
            and getattr(request.user, "pk", None) == user.pk
            and auth_header.startswith("Bearer ")
        )
        org_name = org.display_name if org.display_name else org.name

        # ------------------------------------------------------------------
        # GET — validate only (do NOT call user.save(); that invalidates the
        #       token).
        # ------------------------------------------------------------------
        if request.method == "GET":
            return Response(
                {
                    "valid": True,
                    "email": user.email,
                    "org_name": org_name,
                    "requires_password": not user.is_active,
                    "requires_login": user.is_active and not recipient_authenticated,
                },
                status=status.HTTP_200_OK,
            )

        # ------------------------------------------------------------------
        # POST — set password, activate, accept invite, return JWT tokens.
        # ------------------------------------------------------------------
        if user.is_active:
            # A formerly inactive account may have accepted a different invite.
            # Invitation links must not bypass an active account's login/2FA.
            if not recipient_authenticated:
                return _gm.custom_error_response(
                    status.HTTP_403_FORBIDDEN,
                    "Sign in as the invited recipient to continue.",
                    code="invitation_login_required",
                )
        else:
            new_password = request.validated_data.get("new_password")
            repeat_password = request.validated_data.get("repeat_password")
            if not new_password or not repeat_password:
                return _gm.bad_request("Both password fields are required.")
            if new_password != repeat_password:
                return _gm.bad_request("Passwords do not match.")
            try:
                validate_password(new_password)
            except ValidationError as e:
                return _gm.bad_request("\n".join(e.messages))

        invite.accept(user)
        if not user.is_active:
            user.password = make_password(new_password)
            user.is_active = True
            user.save(update_fields=["password", "is_active"])

        # Set selected org so the auth layer picks it up on first request.
        user.config["selected_organization_id"] = str(org.id)
        user.config["currentOrganizationId"] = str(org.id)
        user.save(update_fields=["config"])

        # Generate JWT tokens (same pattern as CustomTokenObtainPairView).
        AuthToken.objects.filter(
            user=user, auth_type=AuthTokenType.REFRESH.value, is_active=True
        ).update(is_active=False)

        refresh_obj = AuthToken.objects.create(
            user=user,
            auth_type=AuthTokenType.REFRESH.value,
            last_used_at=timezone.now(),
            is_active=True,
        )
        refresh_encrypted = generate_encrypted_message(
            {"user_id": str(user.id), "id": str(refresh_obj.id)}
        )
        cache.set(
            f"refresh_token_{refresh_obj.id}",
            {"token": refresh_encrypted, "user": user},
            timeout=7 * 24 * 60 * 60,  # 7 days in seconds
        )

        access_obj = AuthToken.objects.create(
            user=user,
            auth_type=AuthTokenType.ACCESS.value,
            last_used_at=timezone.now(),
            is_active=True,
        )
        access_encrypted = generate_encrypted_message(
            {"user_id": str(user.id), "id": str(access_obj.id)}
        )
        cache_access_token(
            access_obj.id,
            access_encrypted,
            user,
            timeout=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES * 60,
            cache_backend=cache,
        )

        # Determine if user has completed onboarding (role questionnaire).
        # Invited users who haven't set a role yet should be shown the
        # onboarding flow on first login.
        onboarding_completed = bool(user.role)

        return Response(
            {
                "access": access_encrypted,
                "refresh": refresh_encrypted,
                "org_name": org_name,
                "message": f"Welcome to {org_name}!",
                "is_first_login": not onboarding_completed,
            },
            status=status.HTTP_200_OK,
        )


@swagger_auto_schema(
    method="post",
    request_body=UserIdsRequestSerializer,
    responses={
        200: AccountsBulkUserMutationItemSerializer(many=True),
        **ACCOUNTS_ERROR_RESPONSES,
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated, IsOrganizationAdmin])
@validated_api_request(
    request_serializer=UserIdsRequestSerializer,
    responses={
        200: AccountsBulkUserMutationItemSerializer(many=True),
        **ACCOUNTS_ERROR_RESPONSES,
    },
    document=False,
    reject_unknown_fields=True,
)
def resend_invitation_emails(request):
    user_ids = [str(user_id) for user_id in request.validated_data.get("user_ids", [])]
    responses = []

    # Resolve the actor's current organization for scoping
    organization = getattr(request, "organization", None) or request.user.organization

    for user_id in user_ids:
        try:
            # Org-scoping: only allow resending for users in the actor's organization
            user = User.objects.select_related("organization").get(
                pk=user_id, organization=organization
            )
            # A member removed from the organization answers as an unknown id,
            # before any token or email is made; a pending invitation still
            # counts.
            if not counts_as_member(user, organization):
                raise User.DoesNotExist

            if user:
                logger.info(f"Resending invitation email to {user.email}")

                # Refresh the matching OrganizationInvite's expiry too, so
                # this bulk path behaves the same as the other resend
                # endpoints (#2437).
                refresh_pending_invite_expiration(organization, user.email)

                # Generate a token
                token = make_invitation_token(
                    user, pending_invitation(user, organization)
                )

                # Generate uidb64
                uidb64 = urlsafe_base64_encode(force_bytes(user.pk))

                email_helper(
                    f"You are invited by {user.organization.display_name if user.organization.display_name else user.organization.name} - Future AGI",
                    "member_invite.html",
                    {
                        "email": user.email,
                        "uid": str(uidb64),
                        "token": token,
                        "app_url": settings.APP_URL,
                        "ssl": ssl,
                    },
                    [user.email],
                )

                if settings.DEBUG:
                    logger.info(f"Invitation email resent to {user.email}")

                responses.append(
                    {"user_id": user_id, "message": "Invitation email has been resent."}
                )
        except User.DoesNotExist:
            responses.append({"user_id": user_id, "error": "User does not exist."})
        except Exception:
            logger.exception(
                "Failed to resend invitation email for user_id=%s", user_id
            )
            responses.append(
                {"user_id": user_id, "error": "Failed to send invitation."}
            )

    return Response(responses, status=status.HTTP_200_OK)


@swagger_auto_schema(
    method="delete",
    request_body=UserIdsRequestSerializer,
    responses={
        200: AccountsBulkUserMutationItemSerializer(many=True),
        **ACCOUNTS_ERROR_RESPONSES,
    },
)
@api_view(["DELETE"])
@permission_classes([IsAuthenticated, IsOrganizationAdmin])
@validated_api_request(
    request_serializer=UserIdsRequestSerializer,
    responses={
        200: AccountsBulkUserMutationItemSerializer(many=True),
        **ACCOUNTS_ERROR_RESPONSES,
    },
    document=False,
    reject_unknown_fields=True,
)
def delete_users(request):
    from accounts.models.organization_membership import OrganizationMembership
    from tfc.constants.levels import Level
    from tfc.permissions.utils import get_org_membership

    user_ids = [str(user_id) for user_id in request.validated_data.get("user_ids", [])]
    responses = []

    # Validate that user is not trying to delete themselves
    for user_id in user_ids:
        if user_id == str(request.user.id):
            return _gm.bad_request("Cannot delete your own account. Please try again.")

    organization = getattr(request, "organization", None) or request.user.organization
    actor_membership = get_org_membership(request.user)
    actor_level = actor_membership.level_or_legacy if actor_membership else 0

    for user_id in user_ids:
        try:
            # Only allow deleting users from the same organization: the
            # legacy organization field alone is not membership, so a member
            # removed from it answers like an id that does not exist; a
            # pending invitation still counts.
            user = User.objects.get(pk=user_id, organization=organization)
            if not counts_as_member(user, organization):
                raise User.DoesNotExist

            # Level check: actor must be strictly above target's level
            # (Owners can delete other Owners)
            try:
                target_membership = OrganizationMembership.objects.get(
                    user=user, organization=organization, is_active=True
                )
                target_level = target_membership.level_or_legacy
            except OrganizationMembership.DoesNotExist:
                target_level = 0  # no membership — actor can manage

            if actor_level < Level.OWNER and actor_level <= target_level:
                responses.append(
                    {
                        "user_id": user_id,
                        "error": "You cannot delete a user at or above your own level.",
                    }
                )
                continue

            offboard_organization_member(user, organization, actor=request.user)
            responses.append(
                {"user_id": user_id, "message": "User deleted successfully."}
            )
        except User.DoesNotExist:
            responses.append({"user_id": user_id, "error": "User does not exist."})
        except Exception:
            logger.exception("Failed to delete user_id=%s", user_id)
            responses.append({"user_id": user_id, "error": "Failed to delete user."})

    return Response(responses, status=status.HTTP_200_OK)


@swagger_auto_schema(
    method="post",
    request_body=UpdateUserSerializer,
    responses={
        200: AccountsStringResultResponseSerializer,
        **ACCOUNTS_ERROR_RESPONSES,
    },
)
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@validated_api_request(
    request_serializer=UpdateUserSerializer,
    responses={
        200: AccountsStringResultResponseSerializer,
        **ACCOUNTS_ERROR_RESPONSES,
    },
    document=False,
    reject_unknown_fields=True,
)
def update_user(request):
    _gm = GeneralMethods()
    data = request.validated_data

    organization = getattr(request, "organization", None) or request.user.organization
    try:
        user = User.objects.get(pk=data["user_id"], organization=organization)
        # The legacy organization field alone is not membership: a member
        # removed from the organization answers as an unknown id.
        if not counts_as_member(user, organization):
            raise User.DoesNotExist
    except User.DoesNotExist:
        return _gm.bad_request("User does not exist.")

    update_fields = []

    if "name" in data:
        # Issue 2 fix: only allow name change if actor is the target user
        # or actor is Admin+ in the org.
        actor_membership = get_org_membership(request.user)
        actor_level = actor_membership.level_or_legacy if actor_membership else 0
        if request.user.pk != user.pk and actor_level < Level.ADMIN:
            return _gm.bad_request(
                "You can only change your own name, or you must be an admin."
            )
        user.name = data["name"]
        update_fields.append("name")

    if "organization_role" in data:
        # Check if the REQUEST USER is an owner in the current org
        current_org = (
            getattr(request, "organization", None) or request.user.organization
        )
        _membership = None
        if current_org:
            from accounts.models.organization_membership import OrganizationMembership

            _membership = OrganizationMembership.no_workspace_objects.filter(
                user=request.user, organization=current_org, is_active=True
            ).first()
        _org_role = _membership.role if _membership else request.user.organization_role
        if _org_role != OrganizationRoles.OWNER:
            return _gm.bad_request("Only owners can change roles in organization")

        # Prevent demoting the last owner
        if data["organization_role"] != OrganizationRoles.OWNER:
            owner_count = User.objects.filter(
                organization=user.organization,
                organization_role=OrganizationRoles.OWNER,
                is_active=True,
            ).count()
            if owner_count <= 1 and user.organization_role == OrganizationRoles.OWNER:
                return _gm.bad_request("Cannot demote the last owner.")
        user.organization_role = data["organization_role"]
        update_fields.append("organization_role")

    if update_fields:
        user.save(update_fields=update_fields)

    if "organization_role" in data:
        from accounts.models.organization_membership import OrganizationMembership

        new_level = Level.STRING_TO_LEVEL.get(data["organization_role"])
        if new_level is not None:
            OrganizationMembership.objects.filter(
                user=user,
                organization=user.organization,
                is_active=True,
            ).update(level=new_level)

    return _gm.success_response("User updated successfully.")


@swagger_auto_schema(
    method="post",
    request_body=UserFullNameUpdateRequestSerializer,
    responses={
        200: AccountsDirectMessageResponseSerializer,
        **ACCOUNTS_ERROR_RESPONSES,
    },
)
@allows_no_workspace("POST")
@api_view(["POST"])
@permission_classes([IsAuthenticated])
@validated_api_request(
    request_serializer=UserFullNameUpdateRequestSerializer,
    responses={
        200: AccountsDirectMessageResponseSerializer,
        **ACCOUNTS_ERROR_RESPONSES,
    },
    document=False,
    reject_unknown_fields=True,
)
def update_user_full_name(request):
    try:
        user = User.objects.get(pk=request.user.id)
    except User.DoesNotExist:
        return _gm.not_found("User does not exist.")

    # Extract data from the request
    name = request.validated_data.get("name")

    # Update user fields if provided
    if name:
        user.name = name
        user.save(update_fields=["name"])

    return Response(
        {"message": "User updated successfully."}, status=status.HTTP_200_OK
    )


@swagger_auto_schema(
    method="get",
    responses={200: AccountsUserProfileResponseSerializer, **ACCOUNTS_ERROR_RESPONSES},
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_user_profile_details(request):
    try:
        # Use select_related to avoid N+1 query when accessing user.organization
        user = User.objects.select_related("organization").get(pk=request.user.id)
        # Handle case where user has no organization
        org_name = None
        if user.organization:
            org_name = user.organization.display_name or user.organization.name
        return Response(
            {
                "name": user.name,
                "email": user.email,
                "org_name": org_name,
            },
            status=status.HTTP_200_OK,
        )
    except User.DoesNotExist:
        return _gm.not_found("User does not exist.")
    except Exception:
        logger.exception("Error retrieving user profile")
        return _gm.internal_server_error_response(
            "An error occurred while retrieving profile."
        )
