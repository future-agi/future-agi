import structlog
from drf_yasg.utils import swagger_auto_schema
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView

from agentcc.db_routing import DATABASE_FOR_ORG_CONFIG_BULK
from agentcc.models import AgentccOrgConfig
from agentcc.permissions import IsAdminToken
from agentcc.serializers.contracts import (
    AgentccErrorResponseSerializer,
    OrgConfigBulkResponseSerializer,
)
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
    @swagger_auto_schema(
        responses={
            200: OrgConfigBulkResponseSerializer,
            400: AgentccErrorResponseSerializer,
        }
    )
    def get(self, request):
        try:
            # Pure routing: same query as before, just on the replica alias
            # when "feature:org_config_bulk" is opted in.
            configs = (
                AgentccOrgConfig.no_workspace_objects.db_manager(
                    DATABASE_FOR_ORG_CONFIG_BULK
                )
                .filter(is_active=True, deleted=False)
                .select_related("organization")
            )

            result = {}
            skipped = []
            for cfg in configs:
                org_id = str(cfg.organization_id)
                try:
                    result[org_id] = _build_payload(org_id, cfg)
                except Exception as e:
                    # One org's payload must not fail the response for every
                    # org. The gateway drops an org that is absent from this
                    # response, so the skipped org loses its config there too.
                    # Saves are validated before a version becomes active, so
                    # this path is for a payload that stops building for
                    # another reason.
                    skipped.append(org_id)
                    logger.warning(
                        "org_config_bulk_org_skipped",
                        org_id=org_id,
                        version=cfg.version,
                        error=str(e),
                    )

            if skipped:
                logger.error(
                    "org_config_bulk_partial",
                    skipped_count=len(skipped),
                    served_count=len(result),
                )

            return self._gm.success_response(result)
        except Exception as e:
            logger.exception("org_config_bulk_error", error=str(e))
            return self._gm.bad_request(str(e))
