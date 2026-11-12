import base64
import hashlib
import json
import re
import time
import traceback
from datetime import datetime, timedelta
from typing import Any

import structlog
from cryptography.fernet import Fernet
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import DatabaseError, InterfaceError, OperationalError
from django.http import JsonResponse
from django.utils import timezone
from django_redis.exceptions import ConnectionInterrupted
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from redis.exceptions import TimeoutError as RedisTimeoutError
from rest_framework import status
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import (
    APIException,
    AuthenticationFailed,
    PermissionDenied,
)
from rest_framework.response import Response

from accounts.services.auth_cache import (
    cache_access_token,
    get_cached_access_token,
    matches,
    revoke_cached_access_token,
)
from accounts.api_key_lookup import find_api_key
from accounts.api_key_policy import authenticate_api_key
from accounts.models import OrgApiKey, User
from accounts.models.auth_token import (
    AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES,
    AuthToken,
    AuthTokenOrigin,
    AuthTokenType,
)
from accounts.models.organization import Organization
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.saml_credential import SamlCredentialInvalid, validate_saml_credential
from accounts.services.workspace_landing import resolve_user_workspace
from accounts.services.workspace_selection import accessible_workspace_selections
from tfc.ee_gating import is_oss
from tfc.utils.api_errors import (
    build_error_envelope,
    error_details,
    exception_code,
)

logger = structlog.get_logger(__name__)
security_logger = structlog.get_logger("saml2_auth.security")

# Postgres or Redis being slow or down says nothing about a credential.
INFRASTRUCTURE_ERRORS = (
    DatabaseError,
    InterfaceError,
    ConnectionInterrupted,
    RedisError,
)
# The cache could not be reached; any other Redis error is a bug and stays a 500.
_CACHE_UNAVAILABLE_ERRORS = (
    ConnectionInterrupted,
    RedisConnectionError,
    RedisTimeoutError,
)

# Rate limiting settings with defaults
MAX_LOGIN_ATTEMPTS_PER_HOUR: int = getattr(settings, "MAX_LOGIN_ATTEMPTS_PER_HOUR", 10)
IP_BLOCK_DURATION: int = getattr(settings, "IP_BLOCK_DURATION", 3600)
RATE_LIMIT_WINDOW_SECONDS: int = 3600

ANNOTATION_QUEUE_ROLE_SCOPED_WRITE_PATHS = (
    re.compile(
        r"/model-hub/annotation-queues/[^/]+/items/" r"(?:assign|bulk-review)/?$"
    ),
    re.compile(
        r"/model-hub/annotation-queues/[^/]+/items/[^/]+/"
        r"(?:annotations/submit|complete|skip|release|review)/?$"
    ),
    re.compile(r"/model-hub/annotation-queues/[^/]+/items/[^/]+/" r"discussion/?$"),
    re.compile(
        r"/model-hub/annotation-queues/[^/]+/items/[^/]+/"
        r"discussion/comments/[^/]+(?:/reaction)?/?$"
    ),
    re.compile(
        r"/model-hub/annotation-queues/[^/]+/items/[^/]+/"
        r"discussion/[^/]+/(?:resolve|reopen)/?$"
    ),
)


def _is_annotation_queue_role_scoped_write_path(path):
    """Allow queue-role checks in the annotation views to own these writes."""
    return any(
        pattern.search(path or "")
        for pattern in ANNOTATION_QUEUE_ROLE_SCOPED_WRITE_PATHS
    )


def workspace_read_only(view_cls):
    """Mark a view whose write-method requests perform NO workspace writes.

    Some read-only endpoints use a POST body only to carry filters or a
    search query — they read, they don't mutate. Decorate those views with
    ``@workspace_read_only`` so the workspace write-permission check skips
    them and read-only roles (viewers) can reach them. A POST declared on the
    decorated class also skips the landing requirement; inherited handlers
    are not marked by this decorator.

    Prefer this over adding the path to a string allow-list: the intent
    lives at the view definition, so it cannot drift out of sync when the
    route changes, and it is impossible to add a read-only POST endpoint
    without the marker travelling with it.
    """
    view_cls.workspace_write_exempt = True
    post = vars(view_cls).get("post")
    if callable(post):
        allows_no_workspace("POST")(post)
    return view_cls


NO_ACTIVE_ORGANIZATION = "no_active_organization"


def allows_no_workspace(*methods):
    """Exempt only named methods on an account/recovery handler, never a path.

    Place above @api_view for function views; on APIViews/ViewSets decorate the
    concrete method/action. Its existing permission and ownership gates still run.
    """

    def decorate(handler):
        view_cls = getattr(handler, "cls", None)
        for method in methods:
            target = getattr(view_cls, method.lower(), None) if view_cls else handler
            if callable(target):
                target._no_workspace_methods = frozenset(
                    getattr(target, "_no_workspace_methods", ())
                ) | {method}
        return handler

    return decorate


def _view_allows_no_workspace(request):
    """Resolve the exact HTTP handler/action, independent of legacy role skips."""
    view_cls = _resolve_view_class(request)
    if view_cls is None:
        return False
    method = request.method
    callback = request.resolver_match.func
    actions = getattr(callback, "actions", None)
    if actions is not None:
        if not isinstance(actions, dict):
            return False
        name = actions.get(method.lower())
    else:
        name = method.lower()
    if not isinstance(name, str):
        return False
    handler = getattr(view_cls, name, None)
    return method in getattr(handler, "_no_workspace_methods", ()) or (
        method == "POST" and getattr(handler, "_read_query_post", False) is True
    )


def allows_no_organization(view):
    """Mark a view that serves an account with no organization.

    Authentication answers 403 ``no_active_organization`` when it resolves no
    organization for a signed-in account (an account whose membership rows
    are all inactive or removed: e-mail, OAuth and SSO sign-ups all create the
    account's first organization), unless the view handling the request
    carries this marker. Mark only the views such an account needs: signing
    in and out, its own profile, and creating, choosing or joining an
    organization. Works on a view class and on a function view built by
    ``@api_view`` (place it above ``@api_view``).

    A view that only a signed-out visitor reaches needs no marker: a request
    without credentials never reaches this check. The AWS and GCP Marketplace
    sign-up endpoints are such views; the register page that calls them is
    shown only to signed-out visitors (``GuestGuard``).
    """
    view_cls = getattr(view, "cls", view)
    view_cls.allows_no_organization = True
    # Account recovery also works without a landing, but only for the concrete
    # handlers declared now. A future write/action does not inherit a wildcard.
    for method in ("post", "put", "patch", "delete"):
        handler = vars(view_cls).get(method)
        if callable(handler):
            allows_no_workspace(method.upper())(handler)
    return view


def _view_allows_no_organization(request):
    """Fail-closed: a view that cannot be resolved does not carry the marker."""
    return getattr(_resolve_view_class(request), "allows_no_organization", False)


def _require_an_organization(request):
    """Refuse a signed-in request that resolved no organization.

    The organization context comes only from an active membership. Views that
    serve an account without one carry ``@allows_no_organization``.
    """
    if getattr(request, "organization", None) is not None:
        return
    if _view_allows_no_organization(request):
        return
    raise PermissionDenied(
        "This account has no active organization membership.",
        code=NO_ACTIVE_ORGANIZATION,
    )


def _resolve_view_class(request):
    """Return the class-based view handling this request, if resolvable.

    Django attaches ``.cls`` to the function produced by ``View.as_view()``;
    by the time authentication runs the URL is already resolved, so
    ``request.resolver_match`` is populated.
    """
    match = getattr(request, "resolver_match", None)
    return getattr(getattr(match, "func", None), "cls", None)


def _is_workspace_write_exempt_view(request):
    """Honor legacy read-only views or the exact resolved read-POST handler.

    Fail-closed: if the view cannot be resolved, returns ``False`` so the
    write check still runs — a resolution failure can never grant write
    access.
    """
    view_cls = _resolve_view_class(request)
    return bool(
        getattr(view_cls, "workspace_write_exempt", False)
    ) or _is_read_query_post(request)


def _is_read_query_post(request):
    """Only the concrete, resolved read-query POST is a read, not every exemption."""
    view_cls = _resolve_view_class(request)
    if view_cls is None or getattr(request, "method", None) != "POST":
        return False
    callback = request.resolver_match.func
    if hasattr(callback, "actions"):
        if not isinstance(callback.actions, dict):
            return False
        handler_name = callback.actions.get("post")
    else:
        handler_name = "post"
    if not isinstance(handler_name, str):
        return False
    return (
        getattr(getattr(view_cls, handler_name, None), "_read_query_post", False)
        is True
    )


WORKSPACE_ACCOUNT_ROUTES = frozenset(
    {
        "switch_workspace",
        "switch_organization",
        "update-user-full-name",
        "user-onboarding",
        "user-logout",
        "2fa_status",
        "2fa_totp_setup",
        "2fa_totp_confirm",
        "2fa_totp_disable",
        "2fa_verify_totp",
        "2fa_verify_recovery",
        "2fa_verify_passkey_options",
        "2fa_verify_passkey",
        "2fa_recovery_codes",
        "2fa_recovery_regenerate",
        "passkey_register_options",
        "passkey_register_verify",
        "passkey_list",
        "passkey_detail",
        "passkey_auth_options",
        "passkey_auth_verify",
    }
)


def _is_workspace_write(request):
    return request.method in {
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    } and not _is_read_query_post(request)


def _workspace_selection_optional(request):
    # No-workspace markers alone are NOT enough: key creation can carry that
    # marker but still consumes request.workspace when a landing exists.
    return _view_allows_no_organization(request) or _is_workspace_account_route(request)


def _is_workspace_account_route(request):
    match = getattr(request, "resolver_match", None)
    route = getattr(match, "url_name", None)
    # Listing accessible destinations is navigation, not a read of the stale
    # selected workspace. Keep this exemption exact to the registered read.
    return route in WORKSPACE_ACCOUNT_ROUTES or (
        route == "workspace_list" and request.method in {"GET", "HEAD"}
    )


def _refuse_unavailable_workspace(request):
    return _is_workspace_write(request) and not _workspace_selection_optional(request)


class ServiceEnvelopeEvalAuthentication(BaseAuthentication):
    """The gateway service credential is valid only on standalone eval."""

    def authenticate_header(self, request):
        return "ServiceEnvelope"

    def authenticate(self, request):
        if "X-FA-Envelope" not in request.headers:
            return None
        from accounts.services.eval_envelope import authenticate_eval
        from tfc.service_envelope import EnvelopeRejected

        try:
            return authenticate_eval(request)
        except (EnvelopeRejected, PermissionDenied) as exc:
            raise AuthenticationFailed("service_envelope_rejected") from exc


def is_coding_agent_key(request) -> bool:
    """Whether ``request`` authenticated with a coding agent's key."""
    org_api_key = getattr(request, "org_api_key", None)
    return org_api_key is not None and org_api_key.type == "coding_agent"


class APIKeyAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return "ApiKey"

    def _bind_user_context(self, user, auth_scope=None):
        """Bind user context to structlog for all subsequent logs in this request."""
        structlog.contextvars.bind_contextvars(user_id=str(user.id))
        if auth_scope is not None:
            structlog.contextvars.bind_contextvars(
                organization_id=str(auth_scope.org_id)
            )
            return
        if hasattr(user, "email") and user.email:
            structlog.contextvars.bind_contextvars(user_email=user.email)
        if hasattr(user, "organization") and user.organization:
            structlog.contextvars.bind_contextvars(
                organization_id=str(user.organization.id)
            )

    def _decode_bearer(self, token):
        return decode_token(token)

    def _find_api_key(self, queryset, api_key, secret_key):
        return find_api_key(queryset, api_key, secret_key)

    def authenticate(self, request):
        # Check for JWT token first
        auth_token = (
            request.META.get("HTTP_AUTHORIZATION", "")
            .replace("Bearer", "")
            .replace(" ", "")
        )

        if auth_token:
            try:
                user, token, auth_scope = self._decode_bearer(auth_token)
                # An account with no active membership still authenticates,
                # but only views marked @allows_no_organization serve it
                # (_require_an_organization below).
                # Validate that user is active
                if not user.is_active:
                    raise AuthenticationFailed("User account is inactive")
                # Validate that organization is active

                # Bind user context for structured logging
                request.auth_scope = auth_scope
                # decode_token validates a live browser access-session row; API
                # pairs, OAuth tool tokens and service identities never set this.
                request.credential_provenance = "interactive_session"
                self._bind_user_context(user, auth_scope)

                # Set workspace context after JWT authentication
                self._set_workspace_context(request, user)
                _require_an_organization(request)
                return user, token
            except SamlCredentialInvalid as exc:
                security_logger.warning(
                    "saml_credential_denied", reason=exc.reason, transport="http"
                )
                raise
            except (PermissionDenied, *INFRASTRUCTURE_ERRORS):
                raise  # Authorization denials and database or cache failures are not bad tokens.
            except Exception as e:
                traceback.print_exc()
                raise AuthenticationFailed(f"Invalid Token parsed: {e}") from e

        # Fallback to API key authentication
        api_key = request.headers.get("X-Api-Key")
        secret_key = request.headers.get("X-Secret-Key")

        if not api_key or not secret_key:
            return None

        try:
            # Look the key up by its public id; the secret is checked against its
            # keyed hash in constant time (accounts/api_key_lookup.py, L03).
            org_api_key = authenticate_api_key(
                api_key,
                secret_key,
                workspace_unavailable_on_write=_refuse_unavailable_workspace(request),
                coding_agent_request=request,
            )
            user = (
                org_api_key.minted_by
                if org_api_key.type == "coding_agent"
                else org_api_key.user
            )

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
        from tfc.utils.tenant_scope import (
            acts_in_organization,
            align_legacy_organization,
        )

        # --- Step 1: Resolve current organization ---
        organization = self._resolve_organization(request, user)

        # The organization context comes only from an active membership: the
        # in-memory user record must not name an organization the account
        # can no longer act in.
        align_legacy_organization(user, organization)

        if not organization:
            request.workspace = None
            request.organization = None
            set_workspace_context(
                workspace=None, organization=None, user=user, require_workspace=True
            )
            return

        # An API key names its organization (every other source above already
        # requires an active membership); the account acting for the key must
        # still act in it. Refused here, before the workspace lookup runs.
        if getattr(request, "org_api_key", None) is not None and not (
            acts_in_organization(user, organization)
        ):
            raise PermissionDenied("Access denied to this workspace")

        # --- Step 2: Resolve workspace within that org ---
        workspace = self._get_requested_workspace(request, user, organization)

        if not workspace:
            if getattr(request, "auth_scope", None) is not None:
                workspace = self._get_scoped_default_workspace(user, organization)
            else:
                workspace = self._get_user_default_workspace(user, organization)

        # A bound session must resolve an accessible workspace (742s/T-048).
        # A global org role is not permission to run a workspace-less request.
        if workspace is None and getattr(request, "auth_scope", None) is not None:
            request.workspace = None
            request.organization = organization
            set_workspace_context(
                workspace=None,
                organization=organization,
                user=user,
                require_workspace=True,
            )
            raise PermissionDenied(
                "This session has no accessible workspace in its organization.",
                code="workspace_required",
            )

        # Check workspace access permissions
        if workspace and not self._user_has_workspace_access(user, workspace):
            raise PermissionDenied("Access denied to this workspace")

        is_write = _is_workspace_write(request)
        # Empty landing is not permission to insert NULL-workspace data, even
        # for global org roles. Exemptions require a resolved handler/method
        # marker (including account/recovery), a read-query POST marker, or a
        # workspace_read_only POST marker. Each handler owns its authorization.
        # Legacy role-check path skips below must never waive this boundary.
        if is_write and workspace is None and not _view_allows_no_workspace(request):
            raise PermissionDenied(
                "This request needs an accessible workspace.",
                code="workspace_required",
            )

        # Check write permissions for write operations
        # These resolved account routes skip workspace write checks because they:
        # - Don't modify workspace data (switch operations, profile updates)
        # - Or need to be accessible regardless of workspace role
        should_skip_write_check = (
            _is_workspace_write_exempt_view(request)
            or _is_workspace_account_route(request)
            or _is_annotation_queue_role_scoped_write_path(request.path)
        )

        if is_write and not should_skip_write_check and workspace is not None:
            if not self._can_write_to_workspace(user, workspace):
                raise PermissionDenied("Write access denied to this workspace")

        # Per-request attributes (safe, no race condition)
        request.workspace = workspace
        request.organization = organization

        # None means no accessible workspace, never an unscoped data query.
        # Organization-level operations remain available without a landing.
        set_workspace_context(
            workspace=workspace,
            organization=organization,
            user=user,
            require_workspace=True,
        )

    def _resolve_organization(self, request, user):
        """Resolve the current organization for this request.

        Explicit header/query selectors must be accessible and agree with any
        credential binding. Only an absent selector permits config, active
        membership, then truly legacy organization fallback.
        """
        from accounts.models.organization_membership import OrganizationMembership

        logger = structlog.get_logger("auth.resolve_org")
        requested_org_ids = [
            value
            for value in (
                request.headers.get("X-Organization-Id"),
                request.GET.get("organization_id"),
            )
            if value is not None
        ]

        auth_scope = getattr(request, "auth_scope", None)
        if auth_scope is not None:
            if any(
                str(org_id) != str(auth_scope.org_id) for org_id in requested_org_ids
            ):
                raise PermissionDenied(
                    "This session is bound to the organization that signed you in",
                    code="saml_scope_conflict",
                )
            return Organization.objects.get(id=auth_scope.org_id)

        # Do not distinguish absent rows, malformed IDs or missing membership
        # in the response, and never substitute a different write destination.
        unavailable = PermissionDenied(
            "The selected organization is unavailable. Select an accessible organization and retry.",
            code="organization_unavailable",
        )
        try:
            requested_org_ids = [
                Organization._meta.pk.to_python(value) for value in requested_org_ids
            ]
        except (ValidationError, ValueError, TypeError):
            raise unavailable from None
        if len(set(requested_org_ids)) > 1:
            raise unavailable

        # A key stays pinned to its own org, even if its owner can access others.
        if hasattr(request, "org_api_key") and request.org_api_key:
            org = request.org_api_key.organization
            if any(org_id != org.pk for org_id in requested_org_ids):
                raise unavailable
            logger.debug("org_resolved", source="api_key", org_id=str(org.pk))
            return org

        if requested_org_ids:
            try:
                org = Organization.objects.get(id=requested_org_ids[0])
            except Organization.DoesNotExist:
                raise unavailable from None
            if not user.can_access_organization(org):
                raise unavailable
            logger.debug("org_resolved", source="explicit", org_id=str(org.pk))
            return org

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
        # Only use if user has no membership rows at all, in any state (truly
        # legacy account): an inactive or removed row grants nothing.
        from tfc.utils.tenant_scope import has_membership_rows

        if user.organization and not has_membership_rows(user):
            logger.debug(
                "org_resolved", source="user_fk", org_id=str(user.organization_id)
            )
            return user.organization

        logger.warning("org_resolved", source="none", user_id=str(user.id))
        return None

    def _get_requested_workspace(self, request, user, organization):
        """Get the requested workspace from headers, query params, or API key."""

        auth_scope = getattr(request, "auth_scope", None)
        if auth_scope is not None:
            workspace_id = request.headers.get("X-Workspace-Id") or request.GET.get(
                "workspace_id"
            )
            if not workspace_id:
                return None
            workspace = Workspace.objects.filter(
                id=workspace_id, organization_id=auth_scope.org_id, is_active=True
            ).first()
            if workspace is None or not self._user_has_workspace_access(
                user, workspace
            ):
                raise PermissionDenied(
                    "This session is bound to the organization that signed you in",
                    code="saml_scope_conflict",
                )
            return workspace

        is_write = _is_workspace_write(request)
        selection_optional = _workspace_selection_optional(request)
        refuse_unavailable = is_write and not selection_optional

        # A pinned credential is an explicit destination, not a landing hint.
        # Re-read its workspace: cached credential relations can be stale.
        if hasattr(request, "org_api_key") and request.org_api_key:
            org_api_key = request.org_api_key
            if refuse_unavailable and org_api_key.workspace_id is not None:
                workspace = (
                    accessible_workspace_selections(user)
                    .filter(pk=org_api_key.workspace_id, organization=organization)
                    .first()
                )
                if workspace is None:
                    raise PermissionDenied(
                        "The selected workspace is unavailable. Select an accessible workspace and retry.",
                        code="workspace_unavailable",
                    )
                return workspace
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
                if refuse_unavailable:
                    # A write destination is never an existence probe. Inaccessible
                    # and missing IDs both miss this already-authorized lookup.
                    return accessible_workspace_selections(user).get(
                        id=workspace_id, organization=organization
                    )
                workspace = Workspace.objects.get(
                    id=workspace_id, organization=organization, is_active=True
                )
                if self._user_has_workspace_access(user, workspace):
                    return workspace
                if selection_optional:
                    return (
                        None  # Account/navigation operations do not use this selector.
                    )
                # User explicitly requested a workspace they can't access.
                # Only reject if they already have workspace memberships in
                # this org (meaning they were assigned to specific workspaces).
                # With no memberships, the access-checked landing may be None;
                # authentication does not auto-assign a workspace.
                has_any_ws = WorkspaceMembership.no_workspace_objects.filter(
                    user=user,
                    workspace__organization=organization,
                    is_active=True,
                ).exists()
                if has_any_ws:
                    raise PermissionDenied(
                        "The selected workspace is unavailable. Select an accessible workspace and retry.",
                        code="workspace_unavailable",
                    )
            except (Workspace.DoesNotExist, ValueError):
                pass
            except ValidationError:
                if not is_write:
                    raise  # Preserve the existing read behavior for malformed IDs.
            if refuse_unavailable:
                raise PermissionDenied(
                    "The selected workspace is unavailable. Select an accessible workspace and retry.",
                    code="workspace_unavailable",
                )

        return None

    def _user_has_workspace_access(self, user, workspace):
        """Check if user has access to the workspace"""
        return user.can_access_workspace(workspace)

    def _can_write_to_workspace(self, user, workspace):
        """Check if user has write access to the workspace"""
        return user.can_write_to_workspace(workspace)

    def _get_user_default_workspace(self, user, organization):
        """Select existing access only; authentication never provisions access.

        Workspace creation and membership grants belong to the explicit
        onboarding, organization creation and invitation flows. A denied
        landing must remain None, including for a first-time org member.
        """
        return resolve_user_workspace(user, organization)

    def _get_scoped_default_workspace(self, user, organization):
        """Apply the same landing policy inside the immutable signed-in org."""
        return resolve_user_workspace(user, organization)


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
        org_api_key = authenticate_api_key(
            public_key,
            secret_key,
            workspace_unavailable_on_write=_refuse_unavailable_workspace(request),
            coding_agent_request=request,
        )
        user = (
            org_api_key.minted_by
            if org_api_key.type == "coding_agent"
            else org_api_key.user
        )

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

    @staticmethod
    def _json_forbidden(error_code, error_message, **extra):
        """Return a JSON 403 response with a structured error_code."""
        body = {
            "status": False,
            "result": {
                "error": error_message,
                "error_code": error_code,
                **extra,
            },
        }
        return JsonResponse(body, status=403)

    def __call__(self, request):
        if is_oss():
            return self.get_response(request)

        client_ip, _ = get_client_ip(request)

        if request.path.endswith("password-reset-initiate/"):
            # Check if IP is blocked
            if cache.get(f"rate_limit_{client_ip}"):
                return self._json_forbidden(
                    "LOGIN_PASSWORD_RESET_RATE_LIMITED",
                    "Rate limit exceeded. Too many password reset requests.",
                )

            # Check rate limiting
            requests = cache.get(f"rate_limit_requests_{client_ip}", [])
            now = time.time()

            # Remove requests older than 1 hour
            requests = [
                req for req in requests if now - req < RATE_LIMIT_WINDOW_SECONDS
            ]

            if len(requests) >= MAX_LOGIN_ATTEMPTS_PER_HOUR:
                cache.set(f"rate_limit_{client_ip}", True, IP_BLOCK_DURATION)
                return self._json_forbidden(
                    "LOGIN_PASSWORD_RESET_RATE_LIMITED",
                    "Rate limit exceeded. Too many password reset requests.",
                )

            requests.append(now)
            cache.set(
                f"rate_limit_requests_{client_ip}", requests, RATE_LIMIT_WINDOW_SECONDS
            )

        if (
            request.path.endswith("login/")
            or request.path.endswith("token/")
            or request.path.endswith("signup/")
        ):
            # Check if IP is blocked
            if cache.get(f"blocked_ip_{client_ip}"):
                return self._json_forbidden(
                    "LOGIN_IP_BLOCKED",
                    "IP address temporarily blocked due to multiple failed attempts",
                    blocked=True,
                )

            # Check rate limiting
            requests = cache.get(f"ip_requests_{client_ip}", [])
            now = time.time()

            # Remove requests older than 1 hour
            requests = [
                req for req in requests if now - req < RATE_LIMIT_WINDOW_SECONDS
            ]

            if len(requests) >= MAX_LOGIN_ATTEMPTS_PER_HOUR:
                cache.set(f"blocked_ip_{client_ip}", True, IP_BLOCK_DURATION)
                return self._json_forbidden(
                    "LOGIN_IP_RATE_LIMITED",
                    "Too many login attempts",
                    blocked=True,
                )

            requests.append(now)
            cache.set(f"ip_requests_{client_ip}", requests, RATE_LIMIT_WINDOW_SECONDS)

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


def _touch_non_saml_access_token(token_id, user_id, now, *, user):
    """Validate cached authority on the primary in the existing liveness write.

    A bulk update or a racing cache refill can bypass signal eviction. A changed
    authority bit makes the cache hit miss, so decode_token reloads the User
    before any permission sees it. Ordinary hits still need only one query.
    """
    return (
        AuthToken.objects.using("default").filter(
            id=token_id,
            user_id=user_id,
            user__is_active=True,
            user__is_staff=user.is_staff,
            user__is_superuser=user.is_superuser,
            auth_type=AuthTokenType.ACCESS.value,
            is_active=True,
            last_used_at__gte=now
            - timedelta(minutes=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES),
        )
        .exclude(auth_origin=AuthTokenOrigin.SAML)
        .update(last_used_at=now)
        == 1
    )


def decode_token(token: str, *, transport: str = "http", touch: bool = True):
    """Validate a bearer; optional read-only mode never reads or writes auth cache.

    Invitation previews/refusals must not extend token lifetimes or mutate cache
    state before the handler runs. That mode validates authoritative rows with
    all the same liveness, identity and SAML checks, but without bookkeeping.
    Ordinary authentication retains its existing sliding expiry/cache behavior.
    """
    try:
        if not token:
            raise AuthenticationFailed("empty token")

        decrypted_token_obj = decrypt_message(token)
        user_id = decrypted_token_obj.get("user_id")
        token_id = decrypted_token_obj.get("id")
        if not user_id:
            raise AuthenticationFailed("Invalid user id")

        now = timezone.now()
        # Even a cache read may evict an expired entry, so bypass it entirely
        # in no-touch mode rather than just suppressing set/delete calls.
        cache_data = get_cached_access_token(token_id, cache_backend=cache) if touch else None
        # Older pods may cache SAML as ordinary. Only this code writes the
        # versioned marker, after authoritative classification and validation.
        # Keep the existing cache key so all revocation paths still evict it.
        if cache_data and cache_data.get("non_saml_v1") is True:
            user = cache_data.get("user")
            if (
                user is not None
                and str(user.pk) == str(user_id)
                and user.is_active
                and matches(cache_data, token)
                and _touch_non_saml_access_token(token_id, user_id, now, user=user)
            ):
                if "organization" not in user._state.fields_cache:
                    user = (
                        User.objects.using("default")
                        .select_related("organization")
                        .get(pk=user.pk)
                    )
                cache_access_token(
                    token_id,
                    token,
                    user,
                    timeout=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES * 60,
                    non_saml=True,
                    cache_backend=cache,
                )
                return user, token, None

        # Legacy and failed cache hits must be reclassified, not promoted.
        if cache_data:
            revoke_cached_access_token(token_id, cache_backend=cache)
        token_row = (
            AuthToken.objects.using("default").filter(id=token_id)
            .values(
                "id",
                "user_id",
                "auth_type",
                "is_active",
                "last_used_at",
                "auth_origin",
                "scoped_organization_id",
                "origin_idp_id",
                "origin_idp_generation",
            )
            .first()
        )
        if token_row is None:
            raise AuthenticationFailed("Invalid auth token")
        if token_row["auth_origin"] == AuthTokenOrigin.SAML:
            credential = validate_saml_credential(
                token_row, user_id, now=now, transport=transport
            )
            user = (
                User.objects.using("default")
                .select_related("organization")
                .get(id=user_id, is_active=True)
            )
            if touch and (
                AuthToken.objects.filter(id=token_id, is_active=True).update(
                    last_used_at=now
                )
                != 1
            ):
                raise SamlCredentialInvalid("token_inactive")
            return user, token, credential
        if (
            str(token_row["user_id"]) != str(user_id)
            or token_row["auth_type"] != AuthTokenType.ACCESS.value
        ):
            raise AuthenticationFailed("Invalid auth token")
        if not token_row["is_active"]:
            raise AuthenticationFailed("Access token expired")

        expiration_threshold = now - timedelta(
            minutes=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES
        )
        if (
            token_row["last_used_at"] is None
            or token_row["last_used_at"] < expiration_threshold
        ):
            # Do not deactivate a token concurrently touched by a live request,
            # or perform any cleanup from a read-only invitation request.
            if touch:
                AuthToken.objects.filter(
                    id=token_id, last_used_at=token_row["last_used_at"]
                ).update(is_active=False)
            raise AuthenticationFailed(
                f"Access token expired due to inactivity since {AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES} minutes"
            )

        user = (
            User.objects.using("default")
            .select_related("organization")
            .get(id=user_id, is_active=True)
        )
        if touch:
            if not _touch_non_saml_access_token(token_id, user_id, now, user=user):
                raise AuthenticationFailed("Invalid auth token")

            cache_access_token(
                token_id,
                token,
                user,
                timeout=AUTH_TOKEN_EXPIRATION_TIME_IN_MINUTES * 60,
                non_saml=True,
                cache_backend=cache,
            )

        return user, token, None

    except (*INFRASTRUCTURE_ERRORS, SamlCredentialInvalid):
        raise
    except Exception as e:
        raise AuthenticationFailed(f"Invalid Token parsed: {e}") from e


def _pydantic_error_response(exc):
    errors = exc.errors()
    details = {}
    for err in errors[:10]:
        attr = ".".join(str(loc) for loc in err.get("loc", []))
        key = attr or "non_field_errors"
        details.setdefault(key, []).append(err.get("msg", str(exc)))
    code = errors[0].get("type", "invalid_input") if errors else "invalid_input"
    return build_error_envelope(
        details or str(exc),
        status_code=status.HTTP_400_BAD_REQUEST,
        code=code,
        error_type="validation_error",
        details=details,
    )


class DatabaseUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Database temporarily unavailable."
    default_code = "service_unavailable"


class CacheUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Cache temporarily unavailable."
    default_code = "service_unavailable"


def _database_unavailable_metadata(exc):
    """Allowlisted attribution only: never render exceptions or inspect locals."""

    def bounded_name(value):
        if isinstance(value, str) and re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_.]*|<[A-Za-z_]+>", value
        ):
            return value[:128]
        return None

    cause = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    metadata = {
        "error_class": bounded_name(type(exc).__name__),
        "cause_class": (
            bounded_name(type(cause).__name__) if cause is not None else None
        ),
        "sqlstate": None,
        "source_module": None,
        "source_function": None,
        "source_line": None,
    }
    for error in (cause, exc):
        for attribute in ("sqlstate", "pgcode"):
            value = getattr(error, attribute, None)
            if isinstance(value, str) and re.fullmatch(r"[A-Z0-9]{5}", value):
                metadata["sqlstate"] = value
                break
        if metadata["sqlstate"] is not None:
            break

    tb = exc.__traceback__
    if tb is not None:
        while tb.tb_next is not None:
            tb = tb.tb_next
        metadata.update(
            source_module=bounded_name(tb.tb_frame.f_globals.get("__name__")),
            source_function=bounded_name(tb.tb_frame.f_code.co_name),
            source_line=tb.tb_lineno,
        )
    return metadata


def custom_exception_handler(exc, context):
    """
    Global DRF exception handler.

    API errors return the same public envelope:
    {status: false, type, code, detail, message, result, attr?, details?}.
    """
    from rest_framework.views import exception_handler

    from tfc.ee_gating import FeatureUnavailable

    if isinstance(exc, (OperationalError, InterfaceError)):
        logger.warning(
            "api_database_unavailable", **_database_unavailable_metadata(exc)
        )
        # Keep raw database details private and use DRF's normal rollback path.
        exc = DatabaseUnavailable()
    elif isinstance(exc, _CACHE_UNAVAILABLE_ERRORS):
        logger.warning("api_cache_unavailable", error_class=type(exc).__name__)
        exc = CacheUnavailable()
    response = exception_handler(exc, context)

    if isinstance(exc, FeatureUnavailable):
        detail = {"feature": exc.feature}
        detail.update(getattr(exc, "metadata", {}) or {})
        code = getattr(exc, "error_code", exc.default_code)
        message = str(exc.detail)
        body = {
            **build_error_envelope(
                message,
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                error_type="entitlement_error",
                code=code,
                details={"feature": [exc.feature], **error_details(detail)},
            ),
            "error": {
                "code": code,
                "message": message,
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
                return Response(
                    _pydantic_error_response(exc),
                    status=status.HTTP_400_BAD_REQUEST,
                )
        except ImportError:
            pass

    if response is not None:
        response.data = build_error_envelope(
            response.data,
            status_code=response.status_code,
            code=exception_code(exc),
            details=error_details(response.data),
        )

    if response is not None:
        from accounts.api_key_policy import APIKeyRetired

        if isinstance(exc, APIKeyRetired):
            response.data["create_url"] = exc.create_url
    return response
