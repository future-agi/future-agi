import structlog
from django.db import connection
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from agentcc.models import AgentccAPIKey
from agentcc.permissions import IsAdminToken
from agentcc.serializers.contracts import (
    AgentccErrorResponseSerializer,
    APIKeyBulkResponseSerializer,
)
from agentcc.services.auth_bridge import gateway_key_payload, gateway_loadable_keys
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

# The error code of the 503 answer while the database has no key table yet.
# agentcc-gateway internal/auth/sync.go (MigrationsPendingCode) reads it.
MIGRATIONS_PENDING_CODE = "migrations_pending"


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
        # A new install's database has no key table until the bootstrap job has
        # migrated it. With bundled datastores the Helm chart runs that job as a
        # post-install hook, which `helm install --wait` starts only once the
        # gateway reports ready, and the gateway reports ready only once it
        # knows the app's keys. So say that the app has made no key, in a form
        # the gateway acts on (503 migrations_pending), before any query: the
        # SQL error of a missing table is never sent.
        if AgentccAPIKey._meta.db_table not in connection.introspection.table_names():
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "The database has no gateway key table yet: its migrations have not run.",
                code=MIGRATIONS_PENDING_CODE,
            )
        try:
            result = [gateway_key_payload(key) for key in gateway_loadable_keys()]
            return self._gm.success_response(result)
        except Exception as e:
            logger.exception("api_key_bulk_error", error=str(e))
            return self._gm.internal_server_error_response("Internal server error")
