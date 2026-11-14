import structlog
from drf_yasg.utils import swagger_auto_schema
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from agentcc.permissions import IsAdminToken
from agentcc.serializers.contracts import (
    AgentccErrorResponseSerializer,
    APIKeyBulkResponseSerializer,
)
from agentcc.services.auth_bridge import gateway_key_payload, gateway_loadable_keys
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class APIKeyBulkView(APIView):
    """
    Bulk endpoint for gateway startup key sync.
    Returns all active keys with their hashes so the gateway can restore
    its in-memory KeyStore on restart.

    Authenticated by admin token (not user JWT).
    """

    authentication_classes = []
    permission_classes = [IsAdminToken]
    renderer_classes = [JSONRenderer]  # bypass camelCase — Go expects snake_case
    _gm = GeneralMethods()

    @swagger_auto_schema(
        responses={
            200: APIKeyBulkResponseSerializer,
            400: AgentccErrorResponseSerializer,
        }
    )
    def get(self, request):
        try:
            result = [gateway_key_payload(key) for key in gateway_loadable_keys()]
            return self._gm.success_response(result)
        except Exception as e:
            logger.exception("api_key_bulk_error", error=str(e))
            return self._gm.internal_server_error_response("Internal server error")
