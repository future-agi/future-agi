"""Read-only discovery of an exact Observe target for a workspace-pinned API key."""

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotFound,
    PermissionDenied,
    Throttled,
)
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.api_key_policy import authenticate_api_key
from accounts.models import OrgApiKey
from accounts.omega_throttles import OmegaObserveTargetThrottle
from accounts.serializers.contracts import ACCOUNTS_ERROR_RESPONSES
from accounts.serializers.omega_observe_target import (
    OmegaObserveTargetQuerySerializer,
    OmegaObserveTargetResponseSerializer,
)
from omega_rollout.enforce import require_platform
from tfc.utils.api_contracts import validated_request
from tfc.utils.error_codes import get_error_message


def _project_model():
    from tracer.models.project import Project

    return Project


class OmegaObserveKeyAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return "ApiKey"

    def authenticate(self, request):
        # No ambient browser session, cached principal, or fallback workspace may authorize export.
        if request.headers.get("Authorization") is not None or request.COOKIES:
            raise AuthenticationFailed("An API key pair is required.")
        api_key = request.headers.get("X-Api-Key", "")
        secret_key = request.headers.get("X-Secret-Key", "")
        if not api_key or not secret_key or len(api_key) > 255 or len(secret_key) > 255:
            raise AuthenticationFailed("An API key pair is required.")
        # Every attempt counts against the key it names, before the secret is checked, so
        # guessing a secret is limited too (READINESS L03).
        throttle = OmegaObserveTargetThrottle()
        if not throttle.allow_request(request, None):
            raise Throttled(
                wait=throttle.wait(),
                detail=get_error_message("OMEGA_OBSERVE_TARGET_THROTTLED"),
            )
        # Look the key up by its public id, then check the secret against its keyed
        # hash in constant time; a miss costs the same as a wrong secret (L03).
        key = authenticate_api_key(api_key, secret_key)
        workspace = key.workspace
        if (
            workspace is None
            or workspace.deleted
            or not workspace.is_active
            or workspace.organization_id != key.organization_id
        ):
            raise PermissionDenied("An active workspace-pinned API key is required.")
        for header, expected in (
            ("X-Organization-Id", str(key.organization_id)),
            ("X-Workspace-Id", str(workspace.pk)),
        ):
            if request.headers.get(header, expected) != expected:
                raise PermissionDenied(
                    "The requested scope does not match the API key."
                )
        return None, key


class OmegaObserveKeyPermission(BasePermission):
    def has_permission(self, request, view):
        return isinstance(request.auth, OrgApiKey)


class OmegaObserveTargetView(APIView):
    authentication_classes = [OmegaObserveKeyAuthentication]
    permission_classes = [OmegaObserveKeyPermission]
    http_method_names = ["get", "head", "options"]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        response["Pragma"] = "no-cache"
        return response

    @validated_request(
        query_serializer=OmegaObserveTargetQuerySerializer,
        responses={
            200: OmegaObserveTargetResponseSerializer,
            **ACCOUNTS_ERROR_RESPONSES,
        },
        security=[{"X-Api-Key": [], "X-Secret-Key": []}],
        reject_unknown_fields=True,
        strict_response_validation=True,
        operation_description=(
            "Resolve one exact Observe project using a fresh enabled API key and secret. "
            "The key must be pinned to the project's active workspace. Browser tokens and "
            "ambiguous same-name Observe/experiment projects are refused."
        ),
    )
    def get(self, request):
        key = request.auth
        # A route every Omega runtime needs (product `platform`, READINESS 3.13): refused for a
        # workspace admitted to no hosted Omega product.
        require_platform(key.organization_id, key.workspace_id, route="observe_target")
        # The collector currently resolves by project name without trace_type. Refuse a collision
        # instead of authorizing an Observe id which could cause ingestion into an experiment.
        projects = _project_model().no_workspace_objects.filter(
            organization_id=key.organization_id,
            workspace_id=key.workspace_id,
            deleted=False,
        )
        project = projects.filter(
            pk=request.validated_query_data["project_id"], trace_type="observe"
        ).first()
        if (
            project is None
            or projects.filter(name=project.name).exclude(pk=project.pk).exists()
        ):
            raise NotFound("The requested Observe target is unavailable.")
        return Response(
            {
                "organizationId": str(key.organization_id),
                "workspaceId": str(key.workspace_id),
                "projectId": str(project.pk),
                "projectName": project.name,
            }
        )
