"""Issue a one-request Omega assertion from current FutureAGI membership authority.

The caller is a browser access token or a person's API key (SB W2-KEYS); the assertion names
which (`auth_method`) and the credential it binds to (`sid` or `key_id`), so EE can keep
human-session-only verbs from API keys (W2-ACCESS) and bind a data token to it (D2).
"""

import re
from uuid import UUID

from drf_yasg import openapi
from rest_framework.exceptions import (
    AuthenticationFailed,
    PermissionDenied,
    Throttled,
    ValidationError,
)
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models.auth_token import AuthToken, AuthTokenType
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import OrgApiKey
from accounts.models.workspace import Workspace, WorkspaceMembership
from accounts.omega_session import (
    DelegationUnavailable,
    HostAssertionSigner,
    delegation_role,
    emergency_authority,
)
from accounts.omega_session_authentication import (
    API_KEY,
    SESSION,
    OmegaSessionAuthentication,
)
from accounts.omega_throttles import (
    OmegaApiKeyOwnerThrottle,
    OmegaSessionExchangeThrottle,
)
from accounts.serializers.contracts import ACCOUNTS_ERROR_RESPONSES
from accounts.serializers.omega_session import (
    OmegaSessionExchangeRequestSerializer,
    OmegaSessionExchangeResponseSerializer,
)
from omega_rollout.claims import rollout_claims_for
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.api_serializers import EmptyRequestSerializer
from tfc.utils.error_codes import get_error_message


def _scope_display_name(scope):
    # Legacy names may contain line breaks. Display metadata must not deny authentication.
    name = scope.display_name or scope.name
    return re.sub(r"[\x00-\x1f\x7f]", " ", name).strip() or None


def _assert_selected_context(request, header, actual_id):
    selected = request.headers.get(header)
    if selected is None:
        return
    try:
        if UUID(selected) == actual_id:
            return
    except (ValueError, AttributeError):
        pass
    # The legacy authenticator may fall back on invalid selections. This endpoint
    # must never mint a credential for a different context from the one selected.
    raise PermissionDenied("Selected organization or workspace is unavailable")


class OmegaSessionExchangeView(APIView):
    authentication_classes = [OmegaSessionAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]
    throttle_classes = [OmegaSessionExchangeThrottle]

    def get_throttles(self):
        # A key exchange was already counted against its key in the authenticator, and counts
        # against its owner here; the browser budget (120/min per user) is the browser's own.
        if getattr(self.request, "omega_auth_method", None) == API_KEY:
            return [OmegaApiKeyOwnerThrottle()]
        return super().get_throttles()

    def throttled(self, request, wait):
        raise Throttled(wait=wait, detail=get_error_message("OMEGA_SESSION_THROTTLED"))

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        response["Pragma"] = "no-cache"
        return response

    @validated_request(
        request_serializer=OmegaSessionExchangeRequestSerializer,
        query_serializer=EmptyRequestSerializer,
        responses={
            200: OmegaSessionExchangeResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
            429: ACCOUNTS_ERROR_RESPONSES[500],
            503: ACCOUNTS_ERROR_RESPONSES[500],
        },
        reject_unknown_fields=True,
        strict_response_validation=True,
        # The host currently defines only API-key Swagger schemes. This route rejects those keys;
        # publish its required opaque browser bearer explicitly instead of inheriting that default.
        security=[],
        manual_parameters=[
            openapi.Parameter(
                "Authorization",
                openapi.IN_HEADER,
                required=True,
                type=openapi.TYPE_STRING,
                description="Bearer <opaque FutureAGI browser access token>, or send X-Api-Key and X-Secret-Key instead (never both). Cookies are not accepted.",
            ),
            openapi.Parameter(
                "X-Api-Key",
                openapi.IN_HEADER,
                type=openapi.TYPE_STRING,
                description="Instead of Authorization: the public id of a personal API key.",
            ),
            openapi.Parameter(
                "X-Secret-Key",
                openapi.IN_HEADER,
                type=openapi.TYPE_STRING,
                description="With X-Api-Key: that key's secret.",
            ),
            openapi.Parameter(
                "X-Organization-Id",
                openapi.IN_HEADER,
                type=openapi.TYPE_STRING,
                format="uuid",
                description="Selected host organization; current membership is rechecked.",
            ),
            openapi.Parameter(
                "X-Workspace-Id",
                openapi.IN_HEADER,
                type=openapi.TYPE_STRING,
                format="uuid",
                description="Selected host workspace; current access is rechecked.",
            ),
        ],
        operation_description=(
            "Exchange an active FutureAGI browser access token, or a personal API key (X-Api-Key and "
            "X-Secret-Key), for one-use, 45-second Omega Squad assertions: one by default, or `count` "
            "(at most 8, browser tokens only) in `accessTokens`, each with its own replay ids. Each "
            "assertion names how the caller signed in (`auth_method`: `session` or `api_key`) and "
            "the credential (`sid` or `key_id`). A key acts with its owner's current role in the "
            "workspace it is pinned to, else the one selected. No native Omega account or session is "
            "created. Viewers are refused with the code `omega_viewer_not_admitted`, organization "
            "system and MCP keys with `omega_key_not_admitted`. More than 120 calls a minute per "
            "browser user, 1,800 a minute per API key from one client address or per key owner, or "
            "18,000 a minute per API key in all, answer 429 with Retry-After."
        ),
    )
    def post(self, request):
        organization = getattr(request, "organization", None)
        workspace = getattr(request, "workspace", None)
        if organization is None or workspace is None:
            raise PermissionDenied("Organization and workspace context are required")
        _assert_selected_context(request, "X-Organization-Id", organization.pk)
        _assert_selected_context(request, "X-Workspace-Id", workspace.pk)
        auth_method = getattr(request, "omega_auth_method", None)
        if auth_method == API_KEY:
            # Keys are minted one per request at the gateway, so a revoked key stops at once;
            # a batch would outlive the key by up to the assertion lifetime.
            if request.validated_data.get("count") is not None:
                raise ValidationError(
                    {"count": "An API key exchanges for one assertion per request."}
                )
            key = OrgApiKey.no_workspace_objects.filter(
                pk=getattr(request, "omega_api_key_id", None),
                user=request.user,
                organization=organization,
                type="user",
                enabled=True,
                deleted=False,
            ).first()
            from accounts.api_key_policy import admit_api_key

            admit_api_key(key)
            # A key pinned to a workspace acts there or nowhere: never in a fallback workspace
            # the native resolution chose because the pinned one is no longer open to its owner.
            if key.workspace_id is not None and key.workspace_id != workspace.pk:
                raise PermissionDenied(
                    "This API key's workspace is unavailable to its owner"
                )
        elif (
            auth_method != SESSION
            or not AuthToken.no_workspace_objects.filter(
                pk=getattr(request, "omega_browser_token_id", None),
                user=request.user,
                user__is_active=True,
                auth_type=AuthTokenType.ACCESS.value,
                is_active=True,
            ).exists()
        ):
            raise AuthenticationFailed("Invalid browser access token")
        # Explicit no_workspace managers prevent stale thread context filtering.
        membership = (
            OrganizationMembership.no_workspace_objects.select_related("organization")
            .filter(user=request.user, organization=organization, is_active=True)
            .first()
        )
        current_workspace = Workspace.no_workspace_objects.filter(
            pk=workspace.pk, organization=organization, is_active=True
        ).first()
        if membership is None or current_workspace is None:
            raise PermissionDenied(
                "Current organization and workspace access is required"
            )
        workspace_membership = WorkspaceMembership.no_workspace_objects.filter(
            user=request.user, workspace=workspace, is_active=True
        ).first()
        # Organization owner/admin roles grant global workspace access in host policy.
        try:
            role = delegation_role(
                membership.level_or_legacy,
                workspace_membership.level_or_legacy if workspace_membership else None,
            )
        except ValueError as exc:
            # A viewer is told why (CP P10, D6), so the host can say "Ask an admin for access".
            raise PermissionDenied(
                get_error_message("OMEGA_SESSION_VIEWER"),
                code="omega_viewer_not_admitted",
            ) from exc
        # Controlled rollout (READINESS 3.13): every key of a batch names the products this
        # workspace is admitted to; none admitted is 404, an unreadable rollout store is 503
        # and mints nothing.
        products, rollout_epoch = rollout_claims_for(organization.pk, workspace.pk)
        count = request.validated_data.get("count")
        try:
            signer = HostAssertionSigner.from_environment()
            issued = [
                signer.issue(
                    subject=str(request.user.pk),
                    organization_id=str(organization.pk),
                    workspace_id=str(workspace.pk),
                    organization_name=_scope_display_name(membership.organization),
                    workspace_name=_scope_display_name(current_workspace),
                    role=role,
                    audience=request.validated_data["audience"],
                    auth_method=auth_method,
                    credential_id=request.omega_credential_id,
                    products=products,
                    rollout_epoch=rollout_epoch,
                    # This grant changes only workspace tool policy; the native role stays capped.
                    policy_workspace_admin=(
                        auth_method == SESSION
                        and membership.level_or_legacy == 3
                        and workspace_membership is not None
                        and workspace_membership.level_or_legacy == 8
                    ),
                    emergency_control=emergency_authority(
                        membership.level_or_legacy,
                        workspace_membership.level_or_legacy
                        if workspace_membership
                        else None,
                        auth_method,
                    ),
                )
                for _ in range(count or 1)
            ]
        except DelegationUnavailable:
            return Response(
                build_error_envelope(
                    "Omega delegation is unavailable", status_code=503
                ),
                status=503,
            )
        response = dict(issued[0])
        if count is not None:
            response["accessTokens"] = [item["accessToken"] for item in issued]
        return Response(response)
