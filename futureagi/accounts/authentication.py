import base64
import hashlib
import json
import time
import traceback
from datetime import datetime, timedelta
from typing import Any

import structlog
from cryptography.fernet import Fernet
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError
from django.http import HttpResponseForbidden
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.response import Response

from accounts.models import OrgApiKey, User
from accounts.models.auth_token import (
    AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES,
    AuthToken,
    AuthTokenType,
)
from accounts.models.organization import Organization
from accounts.models.workspace import Workspace, WorkspaceMembership
from tfc.constants.roles import OrganizationRoles

logger = structlog.get_logger(__name__)

# Rate limiting settings with defaults
MAX_LOGIN_ATTEMPTS_PER_HOUR: int = getattr(settings, "MAX_LOGIN_ATTEMPTS_PER_HOUR", 10)
IP_BLOCK_DURATION: int = getattr(settings, "IP_BLOCK_DURATION", 3600)


class APIKeyAuthentication(BaseAuthentication):
    def _bind_user_context(self, user):
        """Bind user context to structlog for all subsequent logs in this request."""
        structlog.contextvars.bind_contextvars(user_id=str(user.id))
        if hasattr(user, "email") and user.email:
            structlog.contextvars.bind_contextvars(user_email=user.email)
        if hasattr(user, "organization") and user.organization:
            structlog.contextvars.bind_contextvars(
                organization_id=str(user.organization.id)
            )

    def authenticate(self, request):
        # Check for JWT token first
        auth_token = (
            request.META.get("HTTP_AUTHORIZATION", "")
            .replace("Bearer", "")
            .replace(" ", "")
        )

        if auth_token:
            try:
                user, token = decode_token(auth_token)
                # Allow org-less users to authenticate (they were removed
                # from their org).  View layer handles access control via
                # request.organization being None.
                # Validate that user is active
                if not user.is_active:
                    raise AuthenticationFailed("User account is inactive")
                # Validate that organization is active

                # Bind user context for structured logging
                self._bind_user_context(user)

                # Set workspace context after JWT authentication
                self._set_workspace_context(request, user)
                return user, token
            except PermissionDenied:
                raise  # Let 403 propagate — don't wrap as 401
            except Exception as e:
                traceback.print_exc()
                raise AuthenticationFailed(f"Invalid Token parsed: {e}") from e

        # Fallback to API key authentication
        api_key = request.headers.get("X-Api-Key")
        secret_key = request.headers.get("X-Secret-Key")

        if not api_key or not secret_key:
            return None

        try:
            org_api_key = OrgApiKey.objects.select_related(
                "organization", "workspace"
            ).get(api_key=api_key, secret_key=secret_key, enabled=True)

            # Validate that the API key has a valid organization
            if not org_api_key.organization:
                raise AuthenticationFailed("API key has no organization")

            # Validate that the API key is enabled
            if not org_api_key.enabled:
                raise AuthenticationFailed("API key is disabled")

            # Get or create a system user for this organization
            if org_api_key.type == "system":
                user = (
                    User.objects.select_related("organization")
                    .filter(organization=org_api_key.organization)
                    .order_by("created_at")
                    .first()
                )
            else:
                user = org_api_key.user

            # Store the API key info in request for workspace context
            request.org_api_key = org_api_key

            # Bind user context for structured logging
            self._bind_user_context(user)

            # Set workspace context after API key authentication
            self._set_workspace_context(request, user)
            return user, None
        except OrgApiKey.DoesNotExist as e:
            raise AuthenticationFailed("Invalid API key or secret key") from e

    def _set_workspace_context(self, request, user):
        """Set workspace context after successful authentication.

        Stores context in three places:
        1. ``request.workspace`` / ``request.organization`` — per-request, safe
        2. Thread-local via ``set_workspace_context()`` — for model-layer code
           (managers, signals, fields) that has no access to the request object.
        """
        from tfc.middleware.workspace_context import set_workspace_context

        # --- Step 1: Resolve current organization ---
        organization = self._resolve_organization(request, user)

        if not organization:
            request.workspace = None
            request.organization = None
            return

        # --- Step 2: Resolve workspace within that org ---
        workspace = self._get_requested_workspace(request, user, organization)

        if not workspace:
            workspace = self._get_user_default_workspace(user, organization)

        # Check workspace access permissions
        if workspace and not self._user_has_workspace_access(user, workspace):
            raise PermissionDenied("Access denied to this workspace")

        # Check write permissions for write operations
        # These paths are excluded from workspace write checks because they:
        # - Don't modify workspace data (switch operations, profile updates)
        # - Or need to be accessible regardless of workspace role
        excluded_paths = [
            "workspace/switch/",
            "organizations/switch/",
            "get-eval-templates",
            "update-user-full-name",  # Users can always update their own profile
        ]

        should_skip_write_check = any(
            excluded_path in request.path for excluded_path in excluded_paths
        )

        if (
            request.method in ["POST", "PUT", "PATCH", "DELETE"]
            and workspace
            and not should_skip_write_check
        ):
            if not self._can_write_to_workspace(user, workspace):
                raise PermissionDenied("Write access denied to this workspace")

        # Per-request attributes (safe, no race condition)
        request.workspace = workspace
        request.organization = organization

        # Thread-local for model-layer code
        set_workspace_context(
            workspace=workspace,
            organization=organization,
            user=user,
        )

    def _resolve_organization(self, request, user):
        """Resolve the current organization for this request.

        Priority:
        1. API key's organization (for API key auth)
        2. X-Organization-Id header
        3. user.config["currentOrganizationId"] (last switched org)
        4. user.organization (legacy FK fallback)
        5. First active OrganizationMembership
        """
        from accounts.models.organization_membership import OrganizationMembership

        logger = structlog.get_logger("auth.resolve_org")

        # 1. API key auth — org comes from the key itself
        if hasattr(request, "org_api_key") and request.org_api_key:
            logger.debug(
                "org_resolved",
                source="api_key",
                org_id=str(request.org_api_key.organization_id),
            )
            return request.org_api_key.organization

        # 2. Explicit header
        org_id = request.headers.get("X-Organization-Id")
        if not org_id:
            org_id = request.GET.get("organization_id")

        if org_id:
            try:
                org = Organization.objects.get(id=org_id)
                if user.can_access_organization(org):
                    logger.debug("org_resolved", source="header", org_id=str(org.id))
                    return org
                else:
                    # Log detailed info about why access was denied
                    from accounts.models.organization_membership import (
                        OrganizationMembership,
                    )

                    membership_count = (
                        OrganizationMembership.no_workspace_objects.filter(
                            user=user, organization=org
                        ).count()
                    )
                    logger.warning(
                        "org_header_access_denied",
                        org_id=org_id,
                        user_id=str(user.id),
                        membership_count=membership_count,
                        has_legacy_org_fk=bool(user.organization_id),
                    )
            except (Organization.DoesNotExist, ValueError):
                logger.warning("org_header_invalid", org_id=org_id)
            # Invalid org header — don't fail, fall through to defaults

        # 3. Last-switched org from user config
        # Read config fresh from DB since the cached user object (from token
        # cache) may have stale config after an org switch.
        from accounts.models import User as UserModel

        fresh_config = (
            UserModel.objects.filter(pk=user.pk)
            .values_list("config", flat=True)
            .first()
        ) or {}
        config_org_id = fresh_config.get("currentOrganizationId") or fresh_config.get(
            "selected_organization_id"
        )
        if config_org_id:
            try:
                org = Organization.objects.get(id=config_org_id)
                if user.can_access_organization(org):
                    logger.debug("org_resolved", source="config", org_id=str(org.id))
                    return org
            except (Organization.DoesNotExist, ValueError):
                pass
            # Stale config — fall through

        # 4. First active membership (source of truth)
        first_membership = (
            OrganizationMembership.no_workspace_objects.filter(
                user=user, is_active=True
            )
            .select_related("organization")
            .first()
        )
        if first_membership:
            logger.debug(
                "org_resolved",
                source="membership",
                org_id=str(first_membership.organization_id),
            )
            return first_membership.organization

        # 5. Legacy fallback: user.organization FK (for old accounts without membership records)
        # Only use if user has no memberships at all (truly legacy account)
        if (
            user.organization
            and not OrganizationMembership.no_workspace_objects.filter(
                user=user
            ).exists()
        ):
            logger.debug(
                "org_resolved", source="user_fk", org_id=str(user.organization_id)
            )
            return user.organization

        logger.warning("org_resolved", source="none", user_id=str(user.id))
        return None

    def _get_requested_workspace(self, request, user, organization):
        """Get the requested workspace from headers, query params, or API key."""

        # First, check if this is an API key request and get workspace from API key
        if hasattr(request, "org_api_key") and request.org_api_key:
            org_api_key = request.org_api_key
            if org_api_key.workspace and org_api_key.workspace.is_active:
                if self._user_has_workspace_access(user, org_api_key.workspace):
                    return org_api_key.workspace

        # Check for workspace ID in header
        workspace_id = request.headers.get("X-Workspace-Id")

        # Fallback to query parameter
        if not workspace_id:
            workspace_id = request.GET.get("workspace_id")

        if workspace_id:
            try:
                workspace = Workspace.objects.get(
                    id=workspace_id, organization=organization, is_active=True
                )
                if self._user_has_workspace_access(user, workspace):
                    return workspace
                # User explicitly requested a workspace they can't access.
                # Only reject if they already have workspace memberships in
                # this org (meaning they were assigned to specific workspaces).
                # Users with zero memberships need the fallback auto-assignment.
                has_any_ws = WorkspaceMembership.no_workspace_objects.filter(
                    user=user,
                    workspace__organization=organization,
                    is_active=True,
                ).exists()
                if has_any_ws:
                    raise PermissionDenied("Access denied to this workspace")
            except (Workspace.DoesNotExist, ValueError):
                pass

        return None

    def _user_has_workspace_access(self, user, workspace):
        """Check if user has access to the workspace"""
        return user.can_access_workspace(workspace)

    def _can_write_to_workspace(self, user, workspace):
        """Check if user has write access to the workspace"""
        return user.can_write_to_workspace(workspace)

    def _get_user_default_workspace(self, user, organization):
        """Get user's preferred workspace within the given organization."""
        if not organization:
            return None

        # Check org-specific workspace preference
        org_workspace_map = user.config.get("orgWorkspaceMap", {})
        workspace_id = org_workspace_map.get(str(organization.id))

        # Fallback: legacy currentWorkspaceId (only if it belongs to this org)
        if not workspace_id:
            workspace_id = user.config.get("currentWorkspaceId") or user.config.get(
                "defaultWorkspaceId"
            )

        if workspace_id:
            try:
                workspace = Workspace.objects.get(
                    id=workspace_id,
                    organization=organization,
                    is_active=True,
                )
                if user.can_access_workspace(workspace):
                    return workspace
            except (Workspace.DoesNotExist, ValueError):
                pass

        # Fallback: use first existing workspace membership in this org
        existing_ws_membership = (
            WorkspaceMembership.no_workspace_objects.filter(
                user=user,
                workspace__organization=organization,
                workspace__is_active=True,
                is_active=True,
            )
            .select_related("workspace")
            .first()
        )
        if existing_ws_membership:
            return existing_ws_membership.workspace

        # Last resort: get or create default workspace (only creates membership
        # when user has NO workspace memberships in this org)
        return self._get_or_create_default_workspace(user, organization)

    def _get_or_create_default_workspace(self, user, organization):
        """Get or create the default workspace for an organization.

        Also ensures the user has a WorkspaceMembership for the workspace,
        creating one (as workspace_admin) if missing.  Without a membership
        the next request's ``_user_has_workspace_access`` check would fail.
        """
        try:
            default_workspace = Workspace.objects.get(
                organization=organization, is_default=True, is_active=True
            )
        except Workspace.DoesNotExist:
            try:
                default_workspace = Workspace.objects.create(
                    name="Default Workspace",
                    organization=organization,
                    is_default=True,
                    is_active=True,
                    created_by=user,
                )
            except IntegrityError as e:
                if "unique_default_workspace_per_org" in str(e):
                    try:
                        default_workspace = Workspace.objects.get(
                            organization=organization,
                            is_default=True,
                            is_active=True,
                        )
                    except Workspace.DoesNotExist:
                        logger.error(
                            f"Failed to get default workspace after creation conflict: {e}"
                        )
                        return None
                else:
                    logger.error(f"Failed to create default workspace: {e}")
                    return None
            except Exception as e:
                logger.error(f"Failed to create default workspace: {e}")
                return None

        # Ensure the user has a workspace membership so that subsequent
        # _user_has_workspace_access checks pass.
        if not WorkspaceMembership.no_workspace_objects.filter(
            user=user, workspace=default_workspace, is_active=True
        ).exists():
            try:
                WorkspaceMembership.no_workspace_objects.create(
                    workspace=default_workspace,
                    user=user,
                    role=OrganizationRoles.WORKSPACE_ADMIN,
                    invited_by=user,
                )
            except IntegrityError:
                # Race condition: another request created it concurrently.
                pass

        return default_workspace


class LangfuseBasicAuthentication(APIKeyAuthentication):
    """Authenticate Langfuse SDK requests that use HTTP Basic Auth.

    The Langfuse Python SDK (v3+) sends::

        Authorization: Basic base64(LANGFUSE_PUBLIC_KEY:LANGFUSE_SECRET_KEY)
        x-langfuse-sdk-name: python
        x-langfuse-public-key: <public_key>

    This class decodes the Basic auth header and maps:
        LANGFUSE_PUBLIC_KEY → OrgApiKey.api_key
        LANGFUSE_SECRET_KEY → OrgApiKey.secret_key

    Must be listed *before* ``APIKeyAuthentication`` in
    ``authentication_classes`` so it intercepts the Basic header before
    ``APIKeyAuthentication`` tries (and fails) to JWT-decode it.
    """

    def authenticate(self, request):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")

        if not auth_header[:6].lower() == "basic ":
            return None  # Not Basic auth — let next authenticator try

        # Decode Basic auth credentials
        try:
            encoded = auth_header[6:]  # Strip "Basic " (case-insensitive)
            decoded = base64.b64decode(encoded).decode("utf-8")
        except Exception as e:
            raise AuthenticationFailed("Invalid Basic auth encoding") from e

        if ":" not in decoded:
            raise AuthenticationFailed("Invalid Basic auth format")

        public_key, secret_key = decoded.split(":", 1)

        if not public_key or not secret_key:
            raise AuthenticationFailed("Empty credentials in Basic auth")

        # Look up OrgApiKey using the Langfuse credentials
        try:
            org_api_key = OrgApiKey.objects.select_related(
                "organization", "workspace"
            ).get(api_key=public_key, secret_key=secret_key, enabled=True)
        except OrgApiKey.DoesNotExist as e:
            raise AuthenticationFailed("Invalid API key or secret key") from e

        if not org_api_key.organization:
            raise AuthenticationFailed("API key has no organization")

        # Resolve user (same logic as parent class)
        if org_api_key.type == "system":
            user = (
                User.objects.select_related("organization")
                .filter(organization=org_api_key.organization)
                .order_by("created_at")
                .first()
            )
        else:
            user = org_api_key.user

        if not user:
            raise AuthenticationFailed("No user found for API key")

        request.org_api_key = org_api_key
        self._bind_user_context(user)
        self._set_workspace_context(request, user)
        return user, None


def get_client_ip(request):
    """
    Get client IP address from request object.
    Returns tuple of (ip_address, is_routable).
    """
    # Try to get IP from X-Forwarded-For header first
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")

    if x_forwarded_for:
        # X-Forwarded-For header can contain multiple IPs
        # First IP is the client's, followed by proxy IPs
        ip = x_forwarded_for.split(",")[0].strip()
    else:
        # If no X-Forwarded-For header, use REMOTE_ADDR
        ip = request.META.get("REMOTE_ADDR")

    # Check if IP is private/routable
    is_routable = True

    # Private IP ranges
    private_ips = [
        ("10.0.0.0", "10.255.255.255"),
        ("172.16.0.0", "172.31.255.255"),
        ("192.168.0.0", "192.168.255.255"),
    ]

    # Convert IP string to integer for range comparison
    ip_parts = ip.split(".")
    if len(ip_parts) == 4:
        ip_int = (
            (int(ip_parts[0]) << 24)
            + (int(ip_parts[1]) << 16)
            + (int(ip_parts[2]) << 8)
            + int(ip_parts[3])
        )

        for start, end in private_ips:
            start_parts = start.split(".")
            end_parts = end.split(".")

            start_int = (
                (int(start_parts[0]) << 24)
                + (int(start_parts[1]) << 16)
                + (int(start_parts[2]) << 8)
                + int(start_parts[3])
            )
            end_int = (
                (int(end_parts[0]) << 24)
                + (int(end_parts[1]) << 16)
                + (int(end_parts[2]) << 8)
                + int(end_parts[3])
            )

            if start_int <= ip_int <= end_int:
                is_routable = False
                break

    return ip, is_routable


class AuthMonitoringMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        client_ip, _ = get_client_ip(request)

        if request.path.endswith("password-reset-initiate/"):
            # Check if IP is blocked
            if cache.get(f"rate_limit_{client_ip}"):
                return HttpResponseForbidden(
                    "Rate limit exceeded. Too many password reset requests."
                )

            # Check rate limiting
            requests = cache.get(f"rate_limit_requests_{client_ip}", [])
            now = time.time()

            # Remove requests older than 1 hour
            requests = [req for req in requests if now - req < 1000]

            if len(requests) >= MAX_LOGIN_ATTEMPTS_PER_HOUR:
                cache.set(f"rate_limit_{client_ip}", True, IP_BLOCK_DURATION)
                return HttpResponseForbidden(
                    "Rate limit exceeded. Too many password reset requests."
                )

            requests.append(now)
            cache.set(f"rate_limit_requests_{client_ip}", requests, 1200)

        if (
            request.path.endswith("login/")
            or request.path.endswith("token/")
            or request.path.endswith("signup/")
        ):
            # Check if IP is blocked
            if cache.get(f"blocked_ip_{client_ip}"):
                return HttpResponseForbidden(
                    "IP address temporarily blocked due to multiple failed attempts"
                )

            # Check rate limiting
            requests = cache.get(f"ip_requests_{client_ip}", [])
            now = time.time()

            # Remove requests older than 1 hour
            requests = [req for req in requests if now - req < 1000]

            if len(requests) >= MAX_LOGIN_ATTEMPTS_PER_HOUR:
                cache.set(f"blocked_ip_{client_ip}", True, IP_BLOCK_DURATION)
                return HttpResponseForbidden("Too many login attempts")

            requests.append(now)
            cache.set(f"ip_requests_{client_ip}", requests, 1200)

        return self.get_response(request)


def generate_encrypted_message(key_value_pairs: dict) -> str:
    """
    Generate a deterministic encrypted message from key-value pairs.
    Returns same encrypted value for same input key-value pairs.

    Args:
        key_value_pairs (dict): Dictionary of key-value pairs to encrypt

    Returns:
        str: Encrypted message string
    """
    # Convert datetime objects to ISO format strings
    processed_pairs = {}
    for key, value in key_value_pairs.items():
        if isinstance(value, datetime):
            processed_pairs[key] = value.isoformat()
        else:
            processed_pairs[key] = value

    # Sort the dictionary by keys to ensure consistent ordering
    sorted_pairs = dict(sorted(processed_pairs.items()))

    # Convert dict to JSON string
    message = json.dumps(sorted_pairs, sort_keys=True)

    # Generate key from settings secret key
    secret_key = settings.SECRET_KEY
    if isinstance(secret_key, bytes):
        secret_key_bytes = secret_key
    else:
        secret_key_bytes = secret_key.encode()
    key = base64.b64encode(hashlib.sha256(secret_key_bytes).digest())
    f = Fernet(key)

    # Encrypt the message
    encrypted = f.encrypt(message.encode())

    return encrypted.decode()


def decrypt_message(encrypted_message: str) -> dict[str, Any]:
    """
    Decrypt an encrypted message back to the original key-value pairs.

    Args:
        encrypted_message (str): The encrypted message to decrypt

    Returns:
        dict: Original key-value pairs
    """
    try:
        # Generate key from settings secret key
        secret_key = settings.SECRET_KEY
        if isinstance(secret_key, bytes):
            secret_key_bytes = secret_key
        else:
            secret_key_bytes = secret_key.encode()
        key = base64.b64encode(hashlib.sha256(secret_key_bytes).digest())
        f = Fernet(key)

        # Decrypt the message
        try:
            decrypted = f.decrypt(encrypted_message.encode())
        except Exception as e:
            raise AuthenticationFailed("Invalid token encryption") from e

        # Parse JSON
        try:
            key_value_pairs = json.loads(decrypted)
        except json.JSONDecodeError as e:
            raise AuthenticationFailed("Malformed token data") from e

        # Convert ISO format strings back to datetime objects
        processed_pairs: dict[str, Any] = {}
        for key, value in key_value_pairs.items():
            if isinstance(value, str) and key == "expires_at":
                try:
                    processed_pairs[key] = datetime.fromisoformat(value)
                except ValueError:
                    processed_pairs[key] = value
            else:
                processed_pairs[key] = value

        return processed_pairs

    except AuthenticationFailed as e:
        raise e
    except Exception as ex:
        raise AuthenticationFailed("Invalid token format") from ex


def decode_token(token: str):
    try:
        if not token:
            raise AuthenticationFailed("empty token")

        decrypted_token_obj = decrypt_message(token)
        user_id = decrypted_token_obj.get("user_id")
        token_id = decrypted_token_obj.get("id")
        cache_data = cache.get(f"access_token_{token_id}")

        if cache_data:
            user = cache_data.get("user")
            token = cache_data.get("token")
            # Ensure organization is loaded to prevent sync-in-async errors later.
            if "organization" not in user._state.fields_cache:
                user = User.objects.select_related("organization").get(pk=user.pk)
            cache.set(
                f"access_token_{token_id}",
                {"token": token, "user": user},
                timeout=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES * 60,
            )
            AuthToken.objects.filter(id=token_id).update(last_used_at=timezone.now())

            return user, token

        if not user_id:
            raise AuthenticationFailed("Invalid user id")
        user = User.objects.select_related("organization").get(
            id=user_id, is_active=True
        )

        try:
            auth_token_obj = AuthToken.objects.get(
                id=token_id, auth_type=AuthTokenType.ACCESS.value
            )
        except AuthToken.DoesNotExist as e:
            raise AuthenticationFailed("Invalid auth token") from e

        if not auth_token_obj.is_active:
            raise AuthenticationFailed("Access token expired")

        # Check if token has expired due to inactivity
        expiration_threshold = timezone.now() - timedelta(
            minutes=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES
        )
        if (
            auth_token_obj.last_used_at is None
            or auth_token_obj.last_used_at < expiration_threshold
        ):
            auth_token_obj.is_active = False
            auth_token_obj.save()
            raise AuthenticationFailed(
                f"Access token expired due to inactivity since {AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES} minutes"
            )

        auth_token_obj.last_used_at = timezone.now()
        auth_token_obj.save()

        cache.set(
            f"access_token_{auth_token_obj.id}",
            {"token": token, "user": user},
            timeout=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES * 60,
        )

        return user, token

    except Exception as e:
        raise AuthenticationFailed(f"Invalid Token parsed: {e}") from e


def custom_exception_handler(exc, context):
    """
    Global DRF exception handler.

    Handles:
    - AuthenticationFailed → 401
    - Pydantic ValidationError → 400 with structured error response
    - Everything else → default DRF handler

    Error format:
    {
        "type": "validation_error",
        "code": "invalid_input",
        "detail": "filters.tags: Input should be a valid list",
        "attr": "filters.tags"
    }
    """
    from rest_framework.views import exception_handler

    from tfc.ee_gating import FeatureUnavailable

    response = exception_handler(exc, context)

    if isinstance(exc, AuthenticationFailed):
        return Response({"detail": str(exc)}, status=status.HTTP_401_UNAUTHORIZED)

    if isinstance(exc, FeatureUnavailable):
        detail = {"feature": exc.feature}
        detail.update(getattr(exc, "metadata", {}) or {})
        body = {
            "status": False,
            "error": {
                "code": getattr(exc, "error_code", exc.default_code),
                "message": str(exc.detail),
                "detail": detail,
            },
            "upgrade_required": True,
        }
        if exc.upgrade_cta is not None:
            body["upgrade_cta"] = exc.upgrade_cta
        return Response(body, status=status.HTTP_402_PAYMENT_REQUIRED)

    # Handle Pydantic ValidationError (not caught by DRF's default handler)
    if response is None:
        try:
            from pydantic import ValidationError as PydanticValidationError

            if isinstance(exc, PydanticValidationError):
                errors = exc.errors()
                if len(errors) == 1:
                    err = errors[0]
                    attr = ".".join(str(loc) for loc in err.get("loc", []))
                    return Response(
                        {
                            "type": "validation_error",
                            "code": err.get("type", "invalid_input"),
                            "detail": err.get("msg", str(exc)),
                            "attr": attr or None,
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                else:
                    items = []
                    for err in errors[:5]:
                        attr = ".".join(str(loc) for loc in err.get("loc", []))
                        items.append(
                            {
                                "type": "validation_error",
                                "code": err.get("type", "invalid_input"),
                                "detail": err.get("msg", ""),
                                "attr": attr or None,
                            }
                        )
                    return Response(
                        {
                            "type": "validation_error",
                            "code": "invalid_input",
                            "detail": f"{len(errors)} validation error(s)",
                            "errors": items,
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
        except ImportError:
            pass

    return response
