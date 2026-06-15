import structlog
from drf_yasg.utils import swagger_auto_schema
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from agentcc.models import AgentccAPIKey
from agentcc.permissions import IsAdminToken
from agentcc.serializers.contracts import (
    AgentccErrorResponseSerializer,
    APIKeyBulkResponseSerializer,
)
from agentcc.services.gateway_client import _stringify_metadata
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
            keys = AgentccAPIKey.no_workspace_objects.filter(
                status=AgentccAPIKey.ACTIVE,
                deleted=False,
            )

            result = []
            for key in keys:
                if not key.key_hash:
                    continue
                metadata = _stringify_metadata(key.metadata or {})
                metadata.setdefault("org_id", str(key.organization_id))
                result.append(
                    {
                        "id": key.gateway_key_id,
                        "name": key.name,
                        "owner": key.owner,
                        "key_hash": key.key_hash,
                        "models": key.allowed_models or [],
                        "providers": key.allowed_providers or [],
                        "metadata": metadata,
                    }
                )

            return self._gm.success_response(result)
        except Exception as e:
            logger.exception("api_key_bulk_error", error=str(e))
            return self._gm.bad_request(str(e))
