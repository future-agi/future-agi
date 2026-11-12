import structlog
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from tfc.utils.api_contracts import validated_request
from tfc.utils.api_serializers import (
    CallWebsocketErrorResponseSerializer,
    CallWebsocketRequestSerializer,
    CallWebsocketResponseSerializer,
)
from tfc.utils.general_methods import GeneralMethods
from tfc.utils.safe_traceback import sanitized_exception_traceback

logger = structlog.get_logger(__name__)


class CallWebsocketView(APIView):
    _gm = GeneralMethods()
    permission_classes = [IsAuthenticated]

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
