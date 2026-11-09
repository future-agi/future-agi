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
        try:
            data = request.validated_data
            message = data["message"]
            org_id = (
                getattr(request, "organization", None) or request.user.organization
            ).id
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
        except Exception as exc:
            logger.error(
                "websocket_relay_delivery_failed", error_type=type(exc).__name__
            )
            return self._gm.bad_request("Unable to send message to Websocket")
