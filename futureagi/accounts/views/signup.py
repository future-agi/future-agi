import os
import traceback
from datetime import timedelta

import requests
import structlog
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import (
    PasswordResetTokenGenerator,
    default_token_generator,
)
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.authentication import decrypt_message, generate_encrypted_message
from accounts.models import OrgApiKey, User
from accounts.models.auth_token import (
    AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES,
    AuthToken,
    AuthTokenType,
)
from accounts.models.organization import Organization
from accounts.serializers import UserSignupSerializer
from accounts.serializers.user import UpdateUserSerializer
from accounts.utils import first_signup
from accounts.views.workspace_management import clear_user_redis_cache

logger = structlog.get_logger(__name__)
from analytics.utils import (
    MixpanelEvents,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from saml2_auth.models import SAMLMetadataModel
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.permissions.rbac import IsOrganizationAdmin
from tfc.permissions.utils import get_org_membership
from tfc.settings.settings import RECAPTCHA_ENABLED, RECAPTCHA_SECRET_KEY, ssl
from tfc.utils.email import email_helper
from tfc.utils.general_methods import GeneralMethods

try:
    from ee.usage.utils.usage_entries import (
        create_organization_subscription_if_not_exists,
    )
except ImportError:
    create_organization_subscription_if_not_exists = None

_gm = GeneralMethods()


class AccountActivationTokenGenerator(PasswordResetTokenGenerator):
    def _make_hash_value(self, user, timestamp):
        return str(user.pk) + str(timestamp) + str(user.is_active)


account_activation_token = AccountActivationTokenGenerator()


def verify_recaptcha(token):
    """Verify the reCAPTCHA token with Google"""
    if not RECAPTCHA_ENABLED:
        logger.info("recaptcha verification skipped (disabled)")
        return True

    secret_key = RECAPTCHA_SECRET_KEY
    if not secret_key:
        logger.error(
            "recaptcha enabled but RECAPTCHA_SECRET_KEY is missing",
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


@api_view(["POST"])
def user_signup(request):
    try:
        recaptcha_token = request.data.get("recaptcha-response")
        logger.info(
            "signup_request",
            host=request.get_host(),
            payload={
                k: v
                for k, v in request.data.items()
                if k not in ("password", "recaptcha-response")
            },
        )

        email = request.data.get("email", "")
        if not email:
            return _gm.bad_request("Email is required.")
        email = email.lower()

        # Log and reject deprecated account-update parameters (security hardening)
        if request.data.get("update_true") or request.data.get("old_email"):
            logger.warning(
                "signup_blocked_update_attempt",
                ip=request.META.get(
                    "HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR")
                ),
                email=email,
                old_email=request.data.get("old_email"),
            )

        is_local = os.getenv("ENV_TYPE") == "local"

        if not is_local:
            if not verify_recaptcha(recaptcha_token):
                logger.error("recaptcha verification failed")
                return Response(
                    {"error": "reCAPTCHA verification failed"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            else:
                logger.info("recaptcha verification passed")
        else:
            logger.info("recaptcha verification skipped (local environment)")

        # Check if a user with the provided email already exists
        if User.objects.filter(email=email).exists():
            return _gm.bad_request("User with this email already exists.")

        # Allowlist fields to prevent hidden-parameter attacks
        allowed_fields = {
            "email",
            "full_name",
            "company_name",
            "recaptcha-response",
            "allow_email",
        }
        sanitized_data = {k: v for k, v in request.data.items() if k in allowed_fields}
        first_signup(sanitized_data)

        return _gm.success_response(
            {"message": "User Created Successfully, Please Check your email to proceed"}
        )

    except Exception:
        logger.exception("Error during signup")
        return _gm.bad_request("An error occurred during signup.")


@api_view(["POST"])
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
        cache.delete(f"access_token_{token_id}")
        return _gm.success_response({"message": "User logged out successfully."})
    except Exception:
        logger.exception("Error in user logout")
        return _gm.bad_request("Error in user logout.")


def activate_account(request, uidb64, token):
    # Rate-limit by IP: 10 requests per minute.
    ip = request.META.get("HTTP_X_FORWARDED_FOR", request.META.get("REMOTE_ADDR", ""))
    if ip:
        ip = ip.split(",")[0].strip()
    rate_key = f"activate_account_rate:{ip}"
    attempts = cache.get(rate_key, 0)
    if attempts >= 10:
        return Response(
            {"error": "Too many activation attempts. Please try again later."},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    cache.set(rate_key, attempts + 1, timeout=60)

    try:
        # Decode the uidb64 to the user ID
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)

        # Check if the token is valid
        if account_activation_token.check_token(user, token):
            # Use transaction to ensure atomicity of account activation
            with transaction.atomic():
                # Activate the user and save the new status
                user.is_active = True

                # Extract initials from the email
                username, domain = user.email.split("@")
                initials = username + " Org"

                # Create a new organization with the initials
                organization = Organization.objects.create(
                    name=initials, region=settings.REGION
                )

                user.organization = organization
                user.organization_role = OrganizationRoles.OWNER.value
                user.save()

                # Create OrganizationMembership for RBAC (1-user-1-org invariant)
                from accounts.models.organization_membership import (
                    OrganizationMembership,
                )
                from tfc.constants.levels import Level

                OrganizationMembership.objects.get_or_create(
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
                # Use .exists() for efficiency instead of len()
                if not OrgApiKey.no_workspace_objects.filter(
                    organization=organization, type="system", enabled=True
                ).exists():
                    OrgApiKey.no_workspace_objects.create(
                        organization=organization, type="system"
                    )

            return Response(
                {"message": "Account successfully activated."},
                status=status.HTTP_200_OK,
            )
        else:
            return Response(
                {"error": "Activation link is invalid or has expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    except User.DoesNotExist:
        return Response(
            {"error": "User does not exist."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception:
        logger.exception("Error during account activation")
        return Response(
            {"error": "An error occurred during account activation."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
def initiate_password_reset(request):
    email = request.data.get("email", None)
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

                # Send reset password email
                # Generate a token
                access_token = AuthToken.objects.create(
                    user=user,
                    auth_type=AuthTokenType.ACCESS.value,
                    is_active=True,
                    last_used_at=timezone.now(),
                )
                token = generate_encrypted_message(
                    {"user_id": str(user.id), "id": str(access_token.id)}
                )

                # Generate uidb64
                uidb64 = urlsafe_base64_encode(force_bytes(user.id))

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

                if settings.DEBUG:
                    logger.info(
                        f"Password reset link {settings.APP_URL}/auth/jwt/verify/{str(uidb64)}/{token}"
                    )

                return _gm.success_response(
                    {
                        "message": f"If an account matches {email}, you will receive an email with instructions on how to reset your password shortly."
                    }
                )
        except (
            User.DoesNotExist
        ):  # Don't disclose that the user doesn't exist ,we just send a sucess response
            return _gm.success_response(
                {
                    "message": f"If an account matches {email}, you will receive an email with instructions on how to reset your password shortly."
                }
            )

    return _gm.bad_request("An error occurred, please try again.")


@api_view(["POST"])
def reset_password_confirm(request, uidb64, token):
    new_password = request.data.get("new_password")
    repeat_password = request.data.get("repeat_password")

    if new_password != repeat_password:
        return _gm.bad_request("Passwords do not match.")

    try:
        # Decode the uidb64 to the user ID
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)

        # Check if the token is valid
        decrypted_token_obj = decrypt_message(str(token))
        user_id = decrypted_token_obj.get("user_id")
        token_id = decrypted_token_obj.get("id")

        if not user_id or not token_id:
            return _gm.bad_request("Invalid token.")

        try:
            auth_token_obj = AuthToken.objects.get(
                id=token_id, auth_type=AuthTokenType.ACCESS.value
            )
        except AuthToken.DoesNotExist:
            return _gm.forbidden_response("Invalid token id.")

        if (
            not auth_token_obj.is_active
            or auth_token_obj.last_used_at
            < timezone.now() - timedelta(minutes=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES)
        ):
            return _gm.forbidden_response("Token has expired.")

        if str(user_id) != str(user.id):
            return _gm.forbidden_response("Invalid token.")

        # Set the new password and save the user object
        try:
            validate_password(new_password)
        except ValidationError as e:
            return _gm.bad_request("\n".join(e.messages))

        if check_password(new_password, user.password):
            return _gm.bad_request(
                "New password cannot be the same as the old password."
            )

        user.password = make_password(new_password)
        user.is_active = True
        user.save()

        # invalidate all auth tokens for the user
        token_ids = AuthToken.objects.filter(
            user=user, is_active=True, auth_type=AuthTokenType.ACCESS.value
        ).values_list("id", flat=True)

        updated_token_ids = [f"access_token_{str(token_id)}" for token_id in token_ids]

        for token_id in updated_token_ids:
            cache.delete(token_id)

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


def _activate_memberships(user):
    """Activate org & workspace memberships for *user* (fallback when invite is
    missing or expired)."""
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import WorkspaceMembership

    OrganizationMembership.all_objects.filter(
        user=user,
        organization=user.organization,
        is_active=False,
    ).update(is_active=True)

    WorkspaceMembership.no_workspace_objects.filter(
        user=user,
        workspace__organization=user.organization,
        is_active=False,
    ).update(is_active=True)


@api_view(["GET", "POST"])
def accept_invitation_mail(request, uidb64, token):
    """Accept an invitation link.

    GET  — validate the token without consuming it. Returns org info so the
           frontend can render a "Set Password" page.
    POST — set the user's password, activate the account, accept the invite,
           and return JWT tokens for auto-login.
    """
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.select_related("organization").get(pk=uid)

        if not default_token_generator.check_token(user, token):
            return Response(
                {"error": "Invitation link is invalid or has expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ------------------------------------------------------------------
        # Check if invite was cancelled - OrganizationInvite must be pending
        # ------------------------------------------------------------------
        from accounts.models.organization_invite import InviteStatus, OrganizationInvite

        org = user.organization
        if not org:
            return Response(
                {"error": "Invitation link is invalid or has expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        invite_exists = OrganizationInvite.objects.filter(
            target_email__iexact=user.email,
            organization=org,
            status=InviteStatus.PENDING,
        ).exists()

        if not invite_exists:
            return Response(
                {"error": "This invitation has been cancelled or expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
                    return Response(
                        {
                            "code": "authenticated_user_mismatch",
                            "error": "You are logged in as a different user. Please logout first to use this invitation link.",
                        },
                        status=status.HTTP_403_FORBIDDEN,
                    )
            except Exception:
                # Token decryption failed - ignore and continue (treat as unauthenticated)
                pass

        org = user.organization
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
                },
                status=status.HTTP_200_OK,
            )

        # ------------------------------------------------------------------
        # POST — set password, activate, accept invite, return JWT tokens.
        # ------------------------------------------------------------------
        new_password = request.data.get("new_password")
        repeat_password = request.data.get("repeat_password")

        if not new_password or not repeat_password:
            return Response(
                {"error": "Both password fields are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new_password != repeat_password:
            return Response(
                {"error": "Passwords do not match."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            validate_password(new_password)
        except ValidationError as e:
            return Response(
                {"error": "\n".join(e.messages)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.password = make_password(new_password)
        user.is_active = True
        user.save()  # this consumes the token

        # Accept the invite — activates OrganizationMembership and
        # WorkspaceMembership records created during dual-write.
        from accounts.models.organization_invite import InviteStatus, OrganizationInvite

        invite = OrganizationInvite.objects.filter(
            target_email__iexact=user.email,
            organization=org,
            status=InviteStatus.PENDING,
        ).first()
        if invite:
            try:
                invite.accept(user)
            except ValueError:
                invite.status = InviteStatus.EXPIRED
                invite.save(update_fields=["status"])
                _activate_memberships(user)
        else:
            _activate_memberships(user)

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
        cache.set(
            f"access_token_{access_obj.id}",
            {"token": access_encrypted, "user": user},
            timeout=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES * 60,
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

    except User.DoesNotExist:
        return Response(
            {
                "error": "An Error Occured! Please ask your Administrator to Resend Invitation Link"
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    except Exception:
        logger.exception("Error processing invitation acceptance")
        return Response(
            {"error": "An error occurred while processing the invitation."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsOrganizationAdmin])
def resend_invitation_emails(request):
    user_ids = request.data.get("user_ids", [])
    responses = []

    # Resolve the actor's current organization for scoping
    organization = getattr(request, "organization", None) or request.user.organization

    for user_id in user_ids:
        try:
            # Org-scoping: only allow resending for users in the actor's organization
            user = User.objects.select_related("organization").get(
                pk=user_id, organization=organization
            )

            if user:
                logger.info(f"Resending invitation email to {user.email}")
                # Generate a token
                token = default_token_generator.make_token(user)

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


@api_view(["DELETE"])
@permission_classes([IsAuthenticated, IsOrganizationAdmin])
def delete_users(request):
    from accounts.models.organization_membership import OrganizationMembership
    from tfc.constants.levels import Level
    from tfc.permissions.utils import get_org_membership

    user_ids = request.data.get("user_ids", [])
    responses = []

    # Validate that user is not trying to delete themselves
    for user_id in user_ids:
        if user_id == str(request.user.id):
            return Response(
                {"error": "Cannot delete your own account. Please try again."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    organization = getattr(request, "organization", None) or request.user.organization
    actor_membership = get_org_membership(request.user)
    actor_level = actor_membership.level_or_legacy if actor_membership else 0

    for user_id in user_ids:
        try:
            # Only allow deleting users from the same organization
            user = User.objects.get(pk=user_id, organization=organization)

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

            # Clear Redis cache for immediate logout before deleting user
            clear_user_redis_cache(user_id)

            user.delete()
            responses.append(
                {"user_id": user_id, "message": "User deleted successfully."}
            )
        except User.DoesNotExist:
            responses.append({"user_id": user_id, "error": "User does not exist."})
        except Exception:
            logger.exception("Failed to delete user_id=%s", user_id)
            responses.append({"user_id": user_id, "error": "Failed to delete user."})

    return Response(responses, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def update_user(request):
    _gm = GeneralMethods()
    serializer = UpdateUserSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data

    try:
        user = User.objects.get(
            pk=data["user_id"],
            organization=getattr(request, "organization", None)
            or request.user.organization,
        )
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


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def update_user_full_name(request):
    try:
        user = User.objects.get(pk=request.user.id)
    except User.DoesNotExist:
        return Response(
            {"error": "User does not exist."}, status=status.HTTP_404_NOT_FOUND
        )

    # Extract data from the request
    name = request.data.get("name")

    # Update user fields if provided
    if name:
        user.name = name
        user.save(update_fields=["name"])

    return Response(
        {"message": "User updated successfully."}, status=status.HTTP_200_OK
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
        return Response(
            {"error": "User does not exist."},
            status=status.HTTP_404_NOT_FOUND,
        )
    except Exception:
        logger.exception("Error retrieving user profile")
        return Response(
            {"error": "An error occurred while retrieving profile."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
