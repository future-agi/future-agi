"""FutureAGI-managed models: the gateway's managed providers' catalog, and an
org's opt-in and credit balance. The org opts in through its org config
(managed_models.enabled, via the gateway's update-config action); requests
the managed providers serve are billed at upstream cost plus the gateway's
managed markup, drawn from the org's balance."""

import structlog
from django.core.cache import cache
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ViewSet

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.serializers.contracts import AgentccErrorResponseSerializer
from agentcc.serializers.managed_models import (
    ManagedCatalogResponseSerializer,
    ManagedModelsStatusResponseSerializer,
)
from agentcc.services.gateway_client import GatewayClientError, get_gateway_client
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

# The catalog changes when the gateway re-fetches it (every few hours); the
# console reads it on every visit to the models page.
CATALOG_CACHE_KEY = "agentcc:managed-models:catalog"
CATALOG_CACHE_SECONDS = 300


class AgentccManagedModelsViewSet(ViewSet):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        responses={
            200: ManagedModelsStatusResponseSerializer,
            400: AgentccErrorResponseSerializer,
        }
    )
    def list(self, request):
        """Whether managed models are offered, enabled for this org, and its balance."""
        org = getattr(request, "organization", None)
        if org is None:
            return self._gm.bad_request("No organization on the request")

        active = AgentccOrgConfig.no_workspace_objects.filter(
            organization=org, is_active=True, deleted=False
        ).first()
        enabled = bool(active and (active.managed_models or {}).get("enabled"))
        result = {
            "available": False,
            "enabled": enabled,
            "balance_usd": None,
            "markup_percent": None,
        }
        try:
            credits = get_gateway_client().get_org_credits(str(org.id))
            result.update(
                available=True,
                balance_usd=credits.get("balance_usd"),
                markup_percent=credits.get("markup_percent"),
            )
        except GatewayClientError as e:
            # 404: the gateway has no managed providers; anything else means
            # it is unreachable, which reads the same to the console.
            logger.info("managed_models_unavailable", error=str(e))
        return self._gm.success_response(result)

    @swagger_auto_schema(
        responses={
            200: ManagedCatalogResponseSerializer,
            400: AgentccErrorResponseSerializer,
        }
    )
    @action(detail=False, methods=["get"])
    def catalog(self, request):
        """Every managed model, with upstream and billed (marked-up) prices."""
        data = cache.get(CATALOG_CACHE_KEY)
        if data is None:
            try:
                data = get_gateway_client().get_catalog()
            except GatewayClientError as e:
                return self._gm.bad_request(f"Gateway error: {e}")
            cache.set(CATALOG_CACHE_KEY, data, CATALOG_CACHE_SECONDS)
        return self._gm.success_response(data)
