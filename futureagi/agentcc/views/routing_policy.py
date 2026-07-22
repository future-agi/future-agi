import structlog
from django.db import transaction
from django.db.models import Max
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agentcc.models.routing_policy import AgentccRoutingPolicy
from agentcc.serializers.routing_policy import AgentccRoutingPolicySerializer
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

_GATEWAY_SYNC_WARNING = (
    "Config saved but gateway sync failed. Changes will apply on next gateway restart."
)


class AgentccRoutingPolicyViewSet(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    """Routing policy management with version history. Org-scoped."""

    permission_classes = [IsAuthenticated]
    serializer_class = AgentccRoutingPolicySerializer
    queryset = AgentccRoutingPolicy.no_workspace_objects.all()
    _gm = GeneralMethods()

    def list(self, request, *args, **kwargs):
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
        try:
            instance = self.get_object()
            return self._gm.success_response(
                AgentccRoutingPolicySerializer(instance).data
            )
        except Exception as e:
            logger.exception("routing_policy_retrieve_error", error=str(e))
            return self._gm.not_found("Routing policy not found")

    def create(self, request, *args, **kwargs):
        """Create a new routing policy. Auto-increments version for same name."""
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

            # Sync to gateway
            synced = self._sync_routing_to_gateway(org)

            data = AgentccRoutingPolicySerializer(policy).data
            data["gateway_synced"] = synced
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("routing_policy_create_error", error=str(e))
            return self._gm.bad_request(str(e))

    def update(self, request, *args, **kwargs):
        """Updates are disabled — create a new version instead."""
        return self._gm.bad_request(
            "Routing policies are versioned. Create a new version instead."
        )

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            instance.delete()
            synced = self._sync_routing_to_gateway(instance.organization)
            data = {"deleted": True, "gateway_synced": synced}
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("routing_policy_delete_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        """Activate a specific version (rollback)."""
        try:
            instance = self.get_object()
            org = getattr(request, "organization", None)
            if org is None:
                return self._gm.bad_request("Organization context is required")

            with transaction.atomic():
                AgentccRoutingPolicy.no_workspace_objects.filter(
                    organization=org, name=instance.name, is_active=True, deleted=False
                ).update(is_active=False)
                instance.is_active = True
                instance.save(update_fields=["is_active", "updated_at"])

            synced = self._sync_routing_to_gateway(org)

            data = AgentccRoutingPolicySerializer(instance).data
            data["gateway_synced"] = synced
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("routing_policy_activate_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["post"])
    def sync(self, request):
        """Manual sync all active routing policies to gateway."""
        try:
            org = getattr(request, "organization", None)
            if org is None:
                return self._gm.bad_request("Organization context is required")
            synced = self._sync_routing_to_gateway(org)
            data = {"synced": True, "gateway_synced": synced}
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("routing_policy_sync_error", error=str(e))
            return self._gm.bad_request(str(e))

    def _sync_routing_to_gateway(self, org):
        """Merge active routing policies into org config and push to gateway.

        Returns True on successful gateway push, False on failure or skip.
        """
        try:
            from agentcc.models import AgentccOrgConfig
            from agentcc.services.config_push import push_org_config

            active_policies = AgentccRoutingPolicy.no_workspace_objects.filter(
                organization=org, is_active=True, deleted=False
            ).order_by("name")

            merged_routing = {}
            for policy in active_policies:
                merged_routing[policy.name] = policy.config

            with transaction.atomic():
                config = (
                    AgentccOrgConfig.no_workspace_objects.select_for_update()
                    .filter(organization=org, is_active=True, deleted=False)
                    .first()
                )

                if not config:
                    return False

                max_version = (
                    AgentccOrgConfig.no_workspace_objects.filter(
                        organization=org, deleted=False
                    ).aggregate(Max("version"))["version__max"]
                    or 0
                )

                AgentccOrgConfig.no_workspace_objects.filter(
                    organization=org, is_active=True, deleted=False
                ).update(is_active=False)

                new_config = AgentccOrgConfig.no_workspace_objects.create(
                    organization=org,
                    version=max_version + 1,
                    guardrails=config.guardrails,
                    routing={"policies": merged_routing},
                    cache=config.cache,
                    rate_limiting=config.rate_limiting,
                    budgets=config.budgets,
                    cost_tracking=config.cost_tracking,
                    ip_acl=config.ip_acl,
                    alerting=config.alerting,
                    privacy=config.privacy,
                    tool_policy=config.tool_policy,
                    mcp=config.mcp,
                    a2a=config.a2a,
                    audit=config.audit,
                    model_database=config.model_database,
                    model_map=config.model_map,
                    is_active=True,
                    change_description="Routing policy sync",
                )

            return push_org_config(str(org.id), new_config)
        except Exception as e:
            logger.warning(
                "routing_sync_push_failed",
                org_id=str(org.id),
                error=str(e),
            )
            return False
