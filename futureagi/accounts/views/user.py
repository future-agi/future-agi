# views.py
from datetime import datetime, timedelta

import requests
import structlog
from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from accounts.authentication import (
    decrypt_message,
    generate_encrypted_message,
    get_client_ip,
)
from accounts.models import User
from accounts.models.auth_token import (
    AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES,
    AuthToken,
    AuthTokenType,
)
from accounts.models.workspace import OrganizationRoles, Workspace, WorkspaceMembership
from accounts.serializers import UserSerializer
from accounts.serializers.user import UserOnboardingSerializer

# from accounts.user_onboard import upload_demo_dataset
from accounts.views.signup import verify_recaptcha

logger = structlog.get_logger(__name__)
from analytics.utils import (
    MixpanelEvents,
    MixpanelModes,
    get_mixpanel_properties,
    track_mixpanel_event,
)
from model_hub.models.api_key import ApiKey
from model_hub.models.develop_dataset import Dataset
from model_hub.models.evals_metric import UserEvalMetric
from model_hub.models.experiments import ExperimentsTable
from tfc.settings import settings
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods
from tracer.models.project import Project


@api_view(["POST", "DELETE"])
@permission_classes([IsAuthenticated])
def manage_redis_key(request):
    gm = GeneralMethods()
    try:
        # Verify access token
        access_token_id = request.data.get("access_token_id")
        if access_token_id != settings.SECRET_KEY:
            return gm.bad_request("Invalid or expired token")

        key = request.data.get("key")
        if not key:
            return gm.bad_request("Key is required")

        if request.method == "POST":
            value = request.data.get("value")
            if value is None:
                return gm.bad_request("Value is required")

            # Optional: Set expiration time in seconds
            expiry = request.data.get("expiry")
            if expiry:
                cache.set(key, value, timeout=int(expiry))
            else:
                cache.set(key, value)

            return gm.success_response(
                {"message": "Key set successfully", "key": key, "value": value}
            )

        elif request.method == "DELETE":
            # Check if key exists before deleting
            if not cache.get(key):
                return gm.bad_request("Key not found")

            cache.delete(key)
            return gm.success_response(
                {"message": "Key deleted successfully", "key": key}
            )

    except Exception as e:
        logger.exception(f"Error in manage_redis_key: {str(e)}")
        return gm.bad_request(
            {"error": "An error occurred while processing the request."}
        )


class CustomTokenObtainPairView(TokenObtainPairView):
    _gm = GeneralMethods()

    def post(self, request, *args, **kwargs):
        try:
            email = request.data.get("email", "").lower()
            remember_me = request.data.get("remember_me", False)
            client_ip, _ = get_client_ip(request)

            # Check if user is blocked
            block_key = f"user_blocked_{email}"
            block_data = cache.get(block_key)
            if block_data:
                current_time = datetime.now().timestamp()
                block_expiry = block_data.get("expiry", 0)

                if current_time < block_expiry:
                    minutes_left = int((block_expiry - current_time) / 60)
                    return self._gm.bad_request(
                        {
                            "error": f"Account temporarily blocked. Please try again in {minutes_left} minutes.",
                            "blocked": True,
                            "block_time_remaining": int(block_expiry - current_time),
                        }
                    )

            # Get failed attempts
            attempts_key = f"login_attempts_{email}"
            failed_attempts = cache.get(attempts_key, 0)

            # Recaptcha verification
            recaptcha_token = request.data.get("recaptcha-response")
            is_localhost = "localhost" in request.get_host()
            is_special_email = "futureagi" in email or "oodles" in email

            if not (is_localhost or is_special_email):
                if not verify_recaptcha(recaptcha_token):
                    logger.error("recaptcha verification failed")
                    return self._gm.bad_request(
                        {"error": "reCAPTCHA verification failed"}
                    )
                else:
                    logger.info("recaptcha verification passed")
            else:
                logger.info(
                    "recaptcha verification bypassed for localhost or special email"
                )

            request.data["email"] = email

            try:
                # Query without is_active filter so we can distinguish
                # "user not found" from "account deactivated".
                user = User.objects.select_related("organization").get(email=email)
            except User.DoesNotExist:
                failed_attempts += 1
                cache.set(
                    attempts_key, failed_attempts, settings.FAILED_ATTEMPTS_TIMEOUT
                )
                remaining_attempts = settings.MAX_LOGIN_ATTEMPTS - failed_attempts
                return self._gm.bad_request(
                    {
                        "error": "Invalid credentials",
                        "remaining_attempts": remaining_attempts,
                    }
                )

            if not user.is_active:
                logger.warning(
                    "login_deactivated_user",
                    email=email,
                    user_id=str(user.id),
                )
                return self._gm.bad_request(
                    {
                        "error": "Account deactivated",
                        "message": "Your account has been deactivated. Please contact your organization admin.",
                    }
                )

            try:
                user.config["remember_me"] = remember_me
                user.save(update_fields=["config"])
            except Exception:
                logger.exception("login_config_save_failed", email=email)

            # --- Password check (must come before any token issuance) ---
            password_entered = request.data.get("password")
            if password_entered and not check_password(password_entered, user.password):
                failed_attempts += 1
                cache.set(
                    attempts_key, failed_attempts, settings.FAILED_ATTEMPTS_TIMEOUT
                )
                remaining_attempts = settings.MAX_LOGIN_ATTEMPTS - failed_attempts
                return self._gm.bad_request(
                    {
                        "error": "Invalid credentials",
                        "remaining_attempts": remaining_attempts,
                    }
                )

            # --- 2FA check ---
            if user.has_2fa_enabled:
                methods = []
                try:
                    if user.totp_device.confirmed:
                        methods.append("totp")
                except Exception:
                    pass
                if user.webauthn_credentials.exists():
                    methods.append("passkey")
                methods.append("recovery")

                from accounts.services.two_factor_challenge import create_challenge

                challenge_id = create_challenge(user, methods)

                # Clear login attempts on successful credential validation
                cache.delete(block_key)
                cache.delete(attempts_key)

                return Response(
                    {
                        "requires_two_factor": True,
                        "challenge_token": challenge_id,
                        "methods": methods,
                    },
                    status=status.HTTP_200_OK,
                )

            # --- Org membership check (after credentials verified) ---
            from accounts.models.organization_membership import (
                OrganizationMembership as _OrgMembership,
            )

            _first_active_membership = (
                _OrgMembership.no_workspace_objects.filter(user=user, is_active=True)
                .select_related("organization")
                .order_by("created_at")
                .first()
            )

            # Deactivate all previous refresh tokens for this user
            AuthToken.objects.filter(
                user=user, auth_type=AuthTokenType.REFRESH.value, is_active=True
            ).update(is_active=False)

            # Create new refresh token
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
                timeout=7 * 24 * 60 * 60,  # 7 days in seconds
            )

            # Create new access token
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
                timeout=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES * 60,
            )

            # If user has no active org membership, signal frontend for org setup
            if not _first_active_membership:
                cache.delete(block_key)
                cache.delete(attempts_key)
                return Response(
                    {
                        "access": access_token_encrypted,
                        "refresh": refresh_token_encrypted,
                        "requires_org_setup": True,
                        "message": "You are not part of any organization.",
                    },
                    status=status.HTTP_200_OK,
                )

            _login_org = _first_active_membership.organization
            user.config["selected_organization_id"] = str(_login_org.id)
            user.config["currentOrganizationId"] = str(_login_org.id)
            user.save(update_fields=["config"])

            # Auto-accept any pending invites for this active user.
            from accounts.models.organization_invite import (
                InviteStatus,
                OrganizationInvite,
            )

            OrganizationInvite.objects.filter(
                target_email__iexact=user.email,
                organization=_login_org,
                status=InviteStatus.PENDING,
            ).update(status=InviteStatus.ACCEPTED)

            response = Response(
                {
                    "access": access_token_encrypted,
                    "refresh": refresh_token_encrypted,
                },
                status=status.HTTP_200_OK,
            )

            try:
                new_org = _login_org.is_new
                if new_org:
                    if _first_active_membership.role != OrganizationRoles.OWNER.value:
                        new_org = False

                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {settings.HUBSPOT_API_TOKEN}",
                }

                contact = {
                    "properties": {
                        "lead_type": user.organization_role,
                        "logged_in": "Yes",
                    }
                }

                try:
                    resp = requests.patch(
                        settings.HUBSPOT_UPDATE_URL.format(user.email),
                        json=contact,
                        headers=headers,
                        timeout=10,
                    )
                    resp.raise_for_status()
                    logger.info("Contact Created Successfully in HubSpot")
                except requests.exceptions.RequestException as e:
                    logger.error(f"Failed to Create Contact in HubSpot: {str(e)}")

                response.data["new_org"] = new_org  # Add the extra key
            except Exception as e:
                logger.error(f"Failed to Create Contact in HubSpot: {str(e)}")
            cache.delete(block_key)
            cache.delete(attempts_key)

            properties = get_mixpanel_properties(
                user=user, mode=MixpanelModes.EMAIL.value
            )
            track_mixpanel_event(MixpanelEvents.LOGIN_CLICK.value, properties)

            return response

        except Exception:
            # Log the full traceback so masked login errors are debuggable.
            # Previously this returned "Invalid credentials" for every
            # exception, hiding the real cause (TH-3463).
            email = request.data.get("email", "").lower()
            client_ip, _ = get_client_ip(request)
            logger.exception(
                "login_unexpected_error",
                email=email,
                client_ip=client_ip,
            )

            block_key = f"user_blocked_{email}"
            attempts_key = f"login_attempts_{email}"
            failed_attempts = cache.get(attempts_key, 0)

            failed_attempts += 1
            remaining_attempts = settings.MAX_LOGIN_ATTEMPTS - failed_attempts
            cache.set(attempts_key, failed_attempts, settings.FAILED_ATTEMPTS_TIMEOUT)

            if failed_attempts >= settings.MAX_LOGIN_ATTEMPTS:
                block_data = {
                    "blocked": True,
                    "expiry": datetime.now().timestamp()
                    + settings.FAILED_ATTEMPTS_TIMEOUT,
                }
                cache.set(block_key, block_data, settings.FAILED_ATTEMPTS_TIMEOUT)

                return self._gm.bad_request(
                    {
                        "error": "Too many failed login attempts. Account blocked for 1 hour.",
                        "blocked": True,
                        "block_time": settings.FAILED_ATTEMPTS_TIMEOUT,
                    }
                )

            return self._gm.bad_request(
                {
                    "error": "Login failed",
                    "message": "An unexpected error occurred. Please try again.",
                    "remaining_attempts": remaining_attempts,
                }
            )


class CustomTokenRefreshView(APIView):
    _gm = GeneralMethods()
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        try:
            # Recaptcha verification
            recaptcha_token = request.data.get("recaptcha-response")
            is_localhost = "localhost" in request.get_host()
            # Only allow localhost_bypass in DEBUG mode (security fix)
            localhost_bypass = (
                request.data.get("localhost_bypass", False) and settings.DEBUG
            )

            if not (is_localhost or localhost_bypass):
                if not verify_recaptcha(recaptcha_token):
                    logger.error("Refresh recaptcha verification failed")
                    return self._gm.bad_request("Verification failed.")
                else:
                    logger.info("Refresh recaptcha verification passed")
            else:
                logger.info(
                    "Refresh recaptcha verification bypassed for localhost or special email"
                )

            encrypted_refresh_token = request.data.get("refresh")
            if not encrypted_refresh_token:
                return self._gm.bad_request("Refresh token is required.")

            try:
                decrypted = decrypt_message(encrypted_refresh_token)
                user_id = decrypted.get("user_id")
                token_id = decrypted.get("id")
            except Exception as e:
                logger.error(f"Token decryption failed: {str(e)}")
                return self._gm.bad_request("Invalid token.")

            if not AuthToken.objects.filter(
                id=token_id, auth_type=AuthTokenType.REFRESH.value, is_active=True
            ).exists():
                return self._gm.bad_request("Invalid token.")

            # Check if refresh token is older than 30 days

            refresh_token_obj = AuthToken.objects.get(
                id=token_id, auth_type=AuthTokenType.REFRESH.value
            )
            token_time = (
                refresh_token_obj.created_at
                if hasattr(refresh_token_obj, "created_at")
                else refresh_token_obj.last_used_at
            )
            if not token_time:
                return self._gm.bad_request("Invalid token.")
            token_cache_key = f"refresh_token_{token_id}"
            if timezone.now() - token_time > timedelta(days=30):
                # Deactivate the used refresh token (rotation)
                AuthToken.objects.filter(
                    id=token_id, auth_type=AuthTokenType.REFRESH.value
                ).update(is_active=False)
                cache.delete(token_cache_key)
                return self._gm.bad_request(
                    {"error": "Refresh token expired or invalid."}
                )

            cached_token = cache.get(token_cache_key)
            try:
                user = cached_token.get("user") if cached_token else None
            except AttributeError:
                user = None

            if not user:
                user = User.objects.get(id=user_id)

            if not user or not user.is_active:
                return self._gm.bad_request("User is inactive or not found.")

            # Create new access token
            new_access_token = AuthToken.objects.create(
                user=user,
                auth_type=AuthTokenType.ACCESS.value,
                last_used_at=timezone.now(),
                is_active=True,
            )
            new_access_token_encrypted = generate_encrypted_message(
                {"user_id": str(user.id), "id": str(new_access_token.id)}
            )
            cache.set(
                f"access_token_{str(new_access_token.id)}",
                {"token": new_access_token_encrypted, "user": user},
                timeout=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES * 60,
            )

            return Response(
                {
                    "access": new_access_token_encrypted,
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            logger.error(f"Token refresh failed: {str(e)}")
            return self._gm.bad_request("Failed to refresh token.")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def get_user_info(request):
    try:
        # Use select_related to avoid N+1 queries when accessing user.organization
        user = User.objects.select_related("organization").get(id=request.user.id)
    except User.DoesNotExist:
        return Response({"error": "User not found."}, status=status.HTTP_404_NOT_FOUND)

    remember_me = user.config.get("remember_me", False)
    user_serializer = UserSerializer(user)
    user_checks = get_user_checks(user)
    # Invited users (who joined an existing org) should skip onboarding
    # since the org is already set up with datasets, keys, etc.
    is_invited_user = user.invited_by is not None
    get_started_completed = is_invited_user or all(user_checks.values())
    # Role/goals onboarding: both role and at least one goal required.
    # Falls back to legacy config storage for users predating the model fields.
    onboarding_completed = (bool(user.role) and bool(user.goals)) or user.config.get(
        "onboarding", {}
    ).get("completed", False)
    data = user_serializer.data
    data["remember_me"] = remember_me
    data["get_started_completed"] = get_started_completed
    data["onboarding_completed"] = onboarding_completed

    # Resolve current organization from middleware (respects X-Organization-Id
    # header, user.config, etc.) instead of using user.organization FK directly.
    # This is essential for multi-org switching to work correctly.
    from accounts.models.organization_membership import OrganizationMembership as _OM

    current_org = getattr(request, "organization", None)

    # Validate the resolved org against active membership
    if (
        current_org
        and not _OM.no_workspace_objects.filter(
            user=user, organization=current_org, is_active=True
        ).exists()
    ):
        current_org = None

    # Fallback: first active membership
    if not current_org:
        _first_membership = (
            _OM.no_workspace_objects.filter(user=user, is_active=True)
            .select_related("organization")
            .first()
        )
        if _first_membership:
            current_org = _first_membership.organization

    # Handle org-less users (removed from their org)
    if not current_org:
        data["ws_enabled"] = False
        data["requires_org_setup"] = True
        data["default_workspace_id"] = None
        data["default_workspace_name"] = None
        data["default_workspace_display_name"] = None
        data["default_workspace_role"] = None
        data["org_level"] = None
        data["ws_level"] = None
        data["effective_level"] = None
        return Response(data)

    # Override the serialized organization with the resolved current org
    # (UserSerializer always serializes user.organization FK which may differ
    # from the currently-selected org in a multi-org setup)
    data["organization"] = {
        "id": str(current_org.id),
        "name": current_org.name,
        "display_name": current_org.display_name,
        "ws_enabled": current_org.ws_enabled,
    }

    # Get the user's role in the current org (not necessarily the FK org)
    from accounts.models.organization_membership import (
        OrganizationMembership as OrgMembership,
    )

    current_membership = OrgMembership.no_workspace_objects.filter(
        user=user, organization=current_org, is_active=True
    ).first()
    current_org_role = (
        current_membership.role if current_membership else user.organization_role
    )
    data["organization_role"] = current_org_role

    data["ws_enabled"] = current_org.ws_enabled

    # Helper function to determine workspace role based on hierarchy
    def get_effective_workspace_role(org_role, workspace_role):
        """Determine the effective role showing org-level roles take precedence"""
        from tfc.constants.levels import Level
        from tfc.constants.roles import RoleMapping, RolePermissions

        # If org role is superior (global access), use that
        if org_role in RolePermissions.GLOBAL_ACCESS_ROLES:
            return org_role

        # Otherwise use workspace role if available, or map org role
        if workspace_role:
            return Level.normalize_ws_role(workspace_role)
        else:
            return str(RoleMapping.get_workspace_role(org_role))

    # Get current workspace from user config or default workspace
    current_workspace_id = user.config.get("currentWorkspaceId") or user.config.get(
        "defaultWorkspaceId"
    )

    if current_workspace_id:
        try:
            current_workspace = Workspace.objects.get(
                id=current_workspace_id, organization=current_org, is_active=True
            )
            data["default_workspace_id"] = str(current_workspace.id)
            data["default_workspace_name"] = current_workspace.name
            data["default_workspace_display_name"] = (
                current_workspace.display_name or current_workspace.name
            )

            # Get user's role in current workspace
            try:
                workspace_membership = WorkspaceMembership.no_workspace_objects.get(
                    workspace=current_workspace, user=user, is_active=True
                )
                workspace_role = workspace_membership.role
            except WorkspaceMembership.DoesNotExist:
                workspace_role = None

            # Determine effective role based on hierarchy
            data["default_workspace_role"] = get_effective_workspace_role(
                current_org_role, workspace_role
            )

        except Workspace.DoesNotExist:
            # Fallback to default workspace if current workspace not found
            try:
                default_workspace = Workspace.objects.get(
                    organization=current_org, is_default=True, is_active=True
                )
                data["default_workspace_id"] = str(default_workspace.id)
                data["default_workspace_name"] = default_workspace.name
                data["default_workspace_display_name"] = (
                    default_workspace.display_name or default_workspace.name
                )

                # Get user's role in default workspace
                try:
                    workspace_membership = WorkspaceMembership.no_workspace_objects.get(
                        workspace=default_workspace, user=user, is_active=True
                    )
                    workspace_role = workspace_membership.role
                except WorkspaceMembership.DoesNotExist:
                    workspace_role = None

                # Determine effective role based on hierarchy
                data["default_workspace_role"] = get_effective_workspace_role(
                    current_org_role, workspace_role
                )

            except Workspace.DoesNotExist:
                data["default_workspace_id"] = None
                data["default_workspace_name"] = None
                data["default_workspace_display_name"] = None
                data["default_workspace_role"] = None
    else:
        # No current workspace set — prefer a workspace the user actually
        # has membership in, falling back to the org's default workspace.
        _user_ws_mem = (
            WorkspaceMembership.no_workspace_objects.filter(
                user=user,
                workspace__organization=current_org,
                workspace__is_active=True,
                is_active=True,
            )
            .select_related("workspace")
            .first()
        )
        _fallback_ws = _user_ws_mem.workspace if _user_ws_mem else None

        if _fallback_ws is None:
            # No membership found — try the org's default workspace
            try:
                _fallback_ws = Workspace.objects.get(
                    organization=current_org, is_default=True, is_active=True
                )
            except Workspace.DoesNotExist:
                _fallback_ws = None

        if _fallback_ws:
            data["default_workspace_id"] = str(_fallback_ws.id)
            data["default_workspace_name"] = _fallback_ws.name
            data["default_workspace_display_name"] = (
                _fallback_ws.display_name or _fallback_ws.name
            )

            # Get user's role in this workspace
            try:
                workspace_membership = WorkspaceMembership.no_workspace_objects.get(
                    workspace=_fallback_ws, user=user, is_active=True
                )
                workspace_role = workspace_membership.role
            except WorkspaceMembership.DoesNotExist:
                workspace_role = None

            data["default_workspace_role"] = get_effective_workspace_role(
                current_org_role, workspace_role
            )
        else:
            data["default_workspace_id"] = None
            data["default_workspace_name"] = None
            data["default_workspace_display_name"] = None
            data["default_workspace_role"] = None

    # Add integer RBAC levels alongside existing string roles
    try:
        from accounts.models.organization_membership import OrganizationMembership
        from tfc.constants.levels import Level
        from tfc.permissions.utils import get_effective_workspace_level

        org_membership = OrganizationMembership.objects.filter(
            user=user,
            organization=current_org,
            is_active=True,
        ).first()
        if org_membership:
            data["org_level"] = org_membership.level_or_legacy
        else:
            data["org_level"] = None

        ws_id = data.get("default_workspace_id")
        if ws_id and org_membership:
            data["ws_level"] = get_effective_workspace_level(user, ws_id)
        else:
            data["ws_level"] = None

        if data.get("org_level") is not None and data.get("ws_level") is not None:
            data["effective_level"] = max(data["org_level"], data["ws_level"])
        else:
            data["effective_level"] = data.get("org_level") or data.get("ws_level")
    except Exception:
        data["org_level"] = None
        data["ws_level"] = None
        data["effective_level"] = None

    # 2FA status
    data["has_2fa_enabled"] = user.has_2fa_enabled
    try:
        has_totp = user.totp_device.confirmed
    except Exception:
        has_totp = False
    data["two_factor_methods"] = {
        "totp": has_totp,
        "passkey": user.webauthn_credentials.exists(),
    }

    # Org 2FA enforcement info
    if (
        current_org
        and getattr(current_org, "require_2fa", False)
        and not user.has_2fa_enabled
    ):
        data["org_2fa_required"] = True
        if current_org.require_2fa_enforced_at:
            from datetime import timedelta

            grace_end = current_org.require_2fa_enforced_at + timedelta(
                days=current_org.require_2fa_grace_period_days
            )
            data["org_2fa_grace_ends_at"] = grace_end.isoformat()

    return Response(data)


def get_user_checks(user):
    # Use exists() instead of count() > 0 for better performance
    return {
        "keys": ApiKey.objects.filter(user=user).exists(),
        "dataset": Dataset.objects.filter(user=user).exists(),
        "evaluation": UserEvalMetric.objects.filter(user=user).exists(),
        "experiment": ExperimentsTable.objects.filter(user=user).exists(),
        "observe": Project.objects.filter(trace_type="observe", user=user).exists(),
        "invite": User.objects.filter(invited_by=user).exists(),
    }


class FirstChecksView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            user = request.user
            # Invited users (who joined an existing org) should skip onboarding
            # since the org is already set up with datasets, keys, etc.
            if user.invited_by is not None:
                result = {
                    "keys": True,
                    "dataset": True,
                    "evaluation": True,
                    "experiment": True,
                    "observe": True,
                    "invite": True,
                }
            else:
                result = get_user_checks(user)
            return self._gm.success_response(result)
        except Exception as e:
            logger.exception(f"Error in get started api: {e}")
            return self._gm.internal_server_error_response(
                get_error_message("UNABLE_TO_FETCH_CHECKS")
            )


@api_view(["POST", "GET"])
@permission_classes([IsAuthenticated])
def user_onboarding(request):
    """
    Handle user onboarding data (role and goals)
    POST: Save user role and goals
    GET: Retrieve user role and goals
    """
    _gm = GeneralMethods()

    if request.method == "GET":
        try:
            user = User.objects.get(id=request.user.id)

            # Check if data exists in model fields (new way) or config (legacy)
            role = user.role
            goals = user.goals if user.goals is not None else []
            # Onboarding requires both role and at least one goal.
            completed = bool(role) and bool(goals)

            # Fallback to config if model fields are empty (for backward compatibility)
            if not role:
                onboarding_data = user.config.get("onboarding", {})
                role = role or onboarding_data.get("role", "")
                goals = goals if goals else onboarding_data.get("goals", [])
                completed = onboarding_data.get("completed", False)

            return _gm.success_response(
                {
                    "role": role or "",
                    "goals": goals or [],
                    "completed": completed,
                }
            )
        except User.DoesNotExist:
            return _gm.bad_request("User not found")
        except Exception as e:
            logger.exception(f"Error fetching onboarding data: {e}")
            return _gm.internal_server_error_response("Failed to fetch onboarding data")

    elif request.method == "POST":
        try:
            # Validate the incoming data
            serializer = UserOnboardingSerializer(data=request.data)
            if not serializer.is_valid():
                return _gm.bad_request(serializer.errors)

            validated_data = serializer.validated_data

            # Get the user
            user = User.objects.get(id=request.user.id)

            # Save to model fields
            user.role = validated_data.get("role")
            user.goals = validated_data.get("goals", [])

            # Also update config for tracking completion timestamp
            if not user.config:
                user.config = {}

            user.config["onboarding_completed_at"] = timezone.now().isoformat()

            user.save(update_fields=["role", "goals", "config"])

            # Track event
            try:
                properties = get_mixpanel_properties(user=user)
                properties["role"] = user.role
                properties["goals"] = user.goals
                track_mixpanel_event("Onboarding Completed", properties)
            except Exception as e:
                logger.warning(f"Failed to track onboarding in Mixpanel: {e}")

            return _gm.success_response(
                {
                    "message": "Onboarding data saved successfully",
                    "data": {
                        "role": user.role,
                        "goals": user.goals,
                    },
                }
            )

        except User.DoesNotExist:
            return _gm.bad_request("User not found")
        except Exception as e:
            logger.exception(f"Error saving onboarding data: {e}")
            return _gm.internal_server_error_response("Failed to save onboarding data")
