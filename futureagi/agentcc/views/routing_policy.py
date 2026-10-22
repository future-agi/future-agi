import structlog
from django.db import transaction
from django.db.models import Max
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agentcc.models.routing_policy import AgentccRoutingPolicy
from agentcc.permissions import GatewayAdminActionsMixin
from agentcc.serializers.routing_policy import AgentccRoutingPolicySerializer
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

ROUTING_POLICIES_RETIRED = "routing_policies_retired"
_RETIRED_MESSAGE = (
    "Routing policies are retired and are not pushed to the gateway. "
    "Routing is versioned in the org config: change it with "
    "POST /agentcc/gateways/{id}/update-config/ and roll back with "
    "POST /agentcc/org-configs/{id}/activate/."
)


class AgentccRoutingPolicyViewSet(
    GatewayAdminActionsMixin, BaseModelViewSetMixinWithUserOrg, ModelViewSet
):
    """Deprecated: routing policies are retired and never reach the gateway.

    Routing is versioned in the org config (`/agentcc/org-configs/`). Stored
    policies stay readable; `sync` and `activate` return 410
    `routing_policies_retired`. Every response carries `Deprecation: true`.
    """

    permission_classes = [IsAuthenticated]
    gateway_admin_actions = frozenset(
        {"create", "update", "partial_update", "destroy", "activate", "sync"}
    )
    serializer_class = AgentccRoutingPolicySerializer
    queryset = AgentccRoutingPolicy.no_workspace_objects.all()
    _gm = GeneralMethods()

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Deprecation"] = "true"
        return response

    def list(self, request, *args, **kwargs):
        """Deprecated: list stored routing policies."""
        try:
            queryset = self.get_queryset()
            # Optionally filter to active only
            active_only = request.query_params.get("active_only")
            if active_only == "true":
                queryset = queryset.filter(is_active=True)
            serializer = AgentccRoutingPolicySerializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception("routing_policy_list_error", error=str(e))
            return self._gm.bad_request(str(e))

    def retrieve(self, request, *args, **kwargs):
        """Deprecated: read one stored routing policy."""
        try:
            instance = self.get_object()
            return self._gm.success_response(
                AgentccRoutingPolicySerializer(instance).data
            )
        except Exception as e:
            logger.exception("routing_policy_retrieve_error", error=str(e))
            return self._gm.not_found("Routing policy not found")

    def create(self, request, *args, **kwargs):
        """Deprecated: store a new policy version. It is never pushed to the
        gateway and does not change the org config."""
        try:
            serializer = AgentccRoutingPolicySerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            org = getattr(request, "organization", None)
            if org is None:
                return self._gm.bad_request("Organization context is required")
            name = serializer.validated_data["name"]

            with transaction.atomic():
                max_version = (
                    AgentccRoutingPolicy.no_workspace_objects.filter(
                        organization=org, name=name, deleted=False
                    ).aggregate(Max("version"))["version__max"]
                    or 0
                )
                next_version = max_version + 1

                # Deactivate previous versions of this policy
                AgentccRoutingPolicy.no_workspace_objects.filter(
                    organization=org, name=name, is_active=True, deleted=False
                ).update(is_active=False)

                policy = serializer.save(
                    organization=org,
                    version=next_version,
                    created_by=request.user,
                )

            return self._gm.success_response(
                AgentccRoutingPolicySerializer(policy).data
            )
        except Exception as e:
            logger.exception("routing_policy_create_error", error=str(e))
            return self._gm.bad_request(str(e))

    def update(self, request, *args, **kwargs):
        """Updates are disabled — create a new version instead."""
        return self._gm.bad_request(
            "Routing policies are versioned. Create a new version instead."
        )

    def destroy(self, request, *args, **kwargs):
        """Deprecated: soft-delete a stored policy. Nothing is pushed."""
        try:
            instance = self.get_object()
            instance.delete()
            return self._gm.success_response({"deleted": True})
        except Exception as e:
            logger.exception("routing_policy_delete_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        """Retired: returns 410 routing_policies_retired. Roll routing back by
        activating an org-config version instead."""
        return self._retired()

    @action(detail=False, methods=["post"])
    def sync(self, request):
        """Retired: returns 410 routing_policies_retired. Routing reaches the
        gateway through org-config versions."""
        return self._retired()

    def _retired(self):
        return self._gm.custom_error_response(
            status.HTTP_410_GONE, _RETIRED_MESSAGE, code=ROUTING_POLICIES_RETIRED
        )
