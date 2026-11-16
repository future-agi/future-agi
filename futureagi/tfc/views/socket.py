import structlog
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.http.request import RawPostDataException
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import (
    CallWebsocketErrorResponseSerializer,
    CallWebsocketRequestSerializer,
    CallWebsocketResponseSerializer,
)
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.safe_traceback import sanitized_exception_traceback
from tfc.utils.websocket_relay_auth import (
    RELAY_SIGNATURE_HEADER,
    RELAY_TIMESTAMP_HEADER,
    relay_signature_is_valid,
)

logger = structlog.get_logger(__name__)


class IsInternalRelayCaller(BasePermission):
    """Allow only the server-side ``call_websocket`` caller.

    It authenticates with its organization's system API key and signs the
    request with a credential derived from this install's settings. Member
    sessions and user or MCP API keys cannot publish to the relay.
    """

    message = "Only internal callers may use this endpoint."

    def has_permission(self, request, view):
        org_api_key = getattr(request, "org_api_key", None)
        organization = getattr(request, "organization", None)
        if org_api_key is None or getattr(org_api_key, "type", None) != "system":
            return False
        if organization is None or str(org_api_key.organization_id) != str(
            organization.id
        ):
            return False
        try:
            body = request.body
        except RawPostDataException:
            return False
        return relay_signature_is_valid(
            organization.id,
            body,
            request.headers.get(RELAY_SIGNATURE_HEADER),
            request.headers.get(RELAY_TIMESTAMP_HEADER),
        )


class CallWebsocketView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated, IsInternalRelayCaller]

    # Sending message to websocket
    @validated_request(
        request_serializer=CallWebsocketRequestSerializer,
        responses={
            200: CallWebsocketResponseSerializer,
            400: CallWebsocketErrorResponseSerializer,
        },
    )
    def post(self, request, *args, **kwargs):
        # Authentication is authoritative; a removed member can retain a legacy
        # user.organization FK without access to that organization.
        organization = getattr(request, "organization", None)
        if organization is None:
            return self._gm.forbidden_response(
                "An authenticated organization is required."
            )

        try:
            data = request.validated_data
            message = data["message"]
            org_id = organization.id
            # Never publish the authenticated relay to the legacy unscoped group.
            group = (
                f"uuid_org_{org_id}_{data['uuid']}"
                if data.get("send_to_uuid")
                else f"org_{org_id}"
            )
            # Let enqueue failures reach the caller, rather than using the
            # best-effort helpers that swallow channel-layer exceptions.
            async_to_sync(get_channel_layer().group_send)(
                group, {"type": "send_data", "data": message}
            )
            return self._gm.success_response("Message sent to websocket")
        except Exception as exc:  # noqa: BLE001 - relay boundary; logs a sanitized traceback
            logger.error(
                "websocket_relay_delivery_failed",
                error_type=type(exc).__name__,
                exception_traceback=sanitized_exception_traceback(exc),
            )
            return self._gm.bad_request("Unable to send message to Websocket")
