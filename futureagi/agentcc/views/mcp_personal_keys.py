"""The personal-key toggle (M25x): ``POST /agentcc/mcp/keys/{key_id}/personal/
{personal: bool}``.

Only the key's own user may change it: anyone else, org Owners and Admins
included, gets 403. A key of another org answers 404. The gateway learns the
change at its next key sync (``mcp_personal_keys``).
"""

from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from agentcc.models.api_key import AgentccAPIKey
from agentcc.serializers.contracts import AgentccErrorResponseSerializer
from agentcc.serializers.mcp_connect import (
    MCPPersonalKeyRequestSerializer,
    MCPPersonalKeyResponseSerializer,
)
from agentcc.services import mcp_personal_keys
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

_ERRORS = {
    400: AgentccErrorResponseSerializer,
    403: AgentccErrorResponseSerializer,
    404: AgentccErrorResponseSerializer,
}


class MCPPersonalKeyView(APIView):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @validated_request(
        request_serializer=MCPPersonalKeyRequestSerializer,
        responses={200: MCPPersonalKeyResponseSerializer, **_ERRORS},
        request_methods=["POST"],
        reject_unknown_fields=True,
    )
    def post(self, request, key_id):
        org = getattr(request, "organization", None)
        if org is None:
            return self._gm.bad_request("Organization context is required")
        key = AgentccAPIKey.no_workspace_objects.filter(
            pk=key_id, organization=org, deleted=False
        ).first()
        if key is None:
            return self._gm.not_found("API key not found.")
        try:
            personal = mcp_personal_keys.set_personal(
                key, request.user, request.validated_data["personal"]
            )
        except mcp_personal_keys.NotKeyOwner as refused:
            return self._gm.custom_error_response(
                403, str(refused), code="not_key_owner"
            )
        return self._gm.success_response({"key_id": str(key.pk), "personal": personal})
