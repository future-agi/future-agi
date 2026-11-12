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
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from agentcc.models.org_config import AgentccOrgConfig
from agentcc.serializers.contracts import AgentccErrorResponseSerializer
from agentcc.serializers.managed_models import (
    ManagedCatalogResponseSerializer,
    ManagedModelsStatusResponseSerializer,
)
from agentcc.services.gateway_client import GatewayClientError, get_gateway_client
from tfc.utils.api_errors import build_error_envelope
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

# The catalog changes when the gateway re-fetches it (every few hours); the
# console reads it on every visit to the models page.
CATALOG_CACHE_KEY = "agentcc:managed-models:catalog"
CATALOG_CACHE_SECONDS = 300


def disabled_catalog():
    """The catalog of a gateway that offers no managed models."""
    return {
        "available": False,
        "markup_percent": None,
        "providers": [],
        "models": [],
    }


def _gateway_error_response(error):
    # Internal admin auth is not the browser's session. Never turn upstream
    # 401/403 or an untyped 404 into a browser logout or a missing feature.
    status_code = 503 if error.status_code in (None, 503) else 502
    body = build_error_envelope(
        error.public_message,
        status_code=status_code,
        code=error.code,
        error_type="server_error" if status_code == 502 else None,
        extra={"metadata": error.metadata},
    )
    return Response(body, status=status_code, headers=error.headers)


class AgentccManagedModelsViewSet(ViewSet):
    permission_classes = [IsAuthenticated]
    _gm = GeneralMethods()

    @swagger_auto_schema(
        responses={
            200: ManagedModelsStatusResponseSerializer,
            400: AgentccErrorResponseSerializer,
            502: AgentccErrorResponseSerializer,
            503: AgentccErrorResponseSerializer,
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
            logger.info(
                "managed_models_unavailable", code=e.code, status_code=e.status_code
            )
            if not (e.status_code == 404 and e.code == "managed_models_disabled"):
                return _gateway_error_response(e)
        return self._gm.success_response(result)

    @swagger_auto_schema(
        responses={
            200: ManagedCatalogResponseSerializer,
            400: AgentccErrorResponseSerializer,
            502: AgentccErrorResponseSerializer,
            503: AgentccErrorResponseSerializer,
        }
    )
    @action(detail=False, methods=["get"])
    def catalog(self, request):
        """Every managed model, with upstream and billed (marked-up) prices.

        On a gateway that offers no managed models the catalog is
        `available: false`, with a null markup and no providers or models, as
        GET /agentcc/managed-models/ reports."""
        data = cache.get(CATALOG_CACHE_KEY)
        if data is None:
            try:
                data = get_gateway_client().get_catalog()
            except GatewayClientError as e:
                # The gateway answers 404 (managed_models_disabled) when it has
                # no managed providers. That answer is not cached, so the
                # catalog comes back with the status; an unreachable gateway
                # stays an error the console retries.
                if e.status_code == 404 and e.code == "managed_models_disabled":
                    return self._gm.success_response(disabled_catalog())
                return _gateway_error_response(e)
            cache.set(CATALOG_CACHE_KEY, data, CATALOG_CACHE_SECONDS)
        return self._gm.success_response(data)
