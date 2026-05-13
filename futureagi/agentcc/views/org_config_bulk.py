import structlog
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from agentcc.db_routing import DATABASE_FOR_ORG_CONFIG_BULK
from agentcc.models import AgentccOrgConfig
from agentcc.permissions import IsAdminToken
from agentcc.services.config_push import _build_payload
from tfc.routers import uses_db
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class OrgConfigBulkView(APIView):
    """
    Bulk endpoint for gateway startup sync.
    Returns all active org configs keyed by org ID.
    Authenticated by admin token (not user JWT).
    """

    authentication_classes = []
    permission_classes = [IsAdminToken]
    renderer_classes = [JSONRenderer]  # bypass camelCase — Go expects snake_case
    _gm = GeneralMethods()

    @uses_db(DATABASE_FOR_ORG_CONFIG_BULK, feature_key="feature:org_config_bulk")
    def get(self, request):
        try:
            # Pure routing: same query as before, just on the replica alias
            # when "feature:org_config_bulk" is opted in.
            configs = AgentccOrgConfig.no_workspace_objects.db_manager(
                DATABASE_FOR_ORG_CONFIG_BULK
            ).filter(is_active=True, deleted=False).select_related("organization")

            result = {}
            for cfg in configs:
                org_id = str(cfg.organization_id)
                result[org_id] = _build_payload(org_id, cfg)

            return self._gm.success_response(result)
        except Exception as e:
            logger.exception("org_config_bulk_error", error=str(e))
            return self._gm.bad_request(str(e))
