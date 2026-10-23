"""GET/PATCH current workspace escalation settings."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from accounts.models.workspace import Workspace
from tfc.permissions.utils import get_effective_workspace_level
from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import ApiErrorResponseSerializer
from tfc.utils.general_methods import GeneralMethods
from tracer.serializers.feed_escalation import (
    FeedEscalationSettingsApiResponseSerializer,
    FeedEscalationSettingsBodySerializer,
    FeedEscalationSettingsSerializer,
)
from tracer.services.feed_escalation_settings import (
    PolicyConflict,
    can_edit_policy,
    settings_response,
    update_policy,
)
from tracer.views.feed._permissions import ErrorFeedLicenseRequired

RESPONSES = {
    200: FeedEscalationSettingsApiResponseSerializer,
    400: ApiErrorResponseSerializer,
    403: ApiErrorResponseSerializer,
    409: ApiErrorResponseSerializer,
}


class FeedEscalationSettingsView(ErrorFeedLicenseRequired, APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    def _workspace(self, request):
        workspace = getattr(request, "workspace", None)
        organization = getattr(request, "organization", None)
        if workspace is None or organization is None:
            return None
        resolved = (
            Workspace.no_workspace_objects.filter(
                pk=workspace.pk,
                organization_id=organization.pk,
                is_active=True,
            )
            .select_related("organization")
            .first()
        )
        if (
            resolved is None
            or get_effective_workspace_level(
                request.user, resolved.id, organization=resolved.organization
            )
            is None
        ):
            return None
        return resolved

    @validated_request(responses=RESPONSES)
    def get(self, request):
        workspace = self._workspace(request)
        if workspace is None:
            return self._gm.forbidden_response("Access denied to this workspace")
        return self._response(request, workspace)

    @validated_request(
        request_serializer=FeedEscalationSettingsBodySerializer, responses=RESPONSES
    )
    def patch(self, request):
        workspace = self._workspace(request)
        if workspace is None or not can_edit_policy(request.user, workspace):
            return self._gm.forbidden_response(
                "Workspace administrator access required"
            )
        body = dict(request.validated_data)
        revision = body.pop("revision")
        try:
            update_policy(
                workspace=workspace,
                actor=request.user,
                expected_revision=revision,
                changes=body,
            )
        except PolicyConflict as exc:
            return self._gm.custom_error_response(409, str(exc))
        except ValueError as exc:
            return self._gm.bad_request(str(exc))
        return self._response(request, workspace)

    def _response(self, request, workspace):
        result = settings_response(
            workspace, can_edit=can_edit_policy(request.user, workspace)
        )
        return self._gm.success_response(FeedEscalationSettingsSerializer(result).data)
