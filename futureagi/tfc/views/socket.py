import traceback

import structlog
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from model_hub.utils.utils import send_message_to_channel, send_message_to_uuid
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
            if data.get("send_to_uuid"):
                send_message_to_uuid(data.get("uuid"), message)
            else:
                send_message_to_channel(org_id, message)

            return self._gm.success_response("Message sent to websocket")
        except Exception:
            logger.exception("Unable to send message to Websocket")
            traceback.print_exc()
            return self._gm.bad_request("Unable to send message to Websocket")
