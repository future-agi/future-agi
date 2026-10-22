import structlog
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.permissions import GatewayAdminActionsMixin
from agentcc.serializers.guardrail_policy import AgentccGuardrailPolicySerializer
from agentcc.services.guardrail_sync import sync_guardrail_policies
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

_GATEWAY_SYNC_WARNING = (
    "Config saved but gateway sync failed. Changes will apply on next gateway restart."
)


class AgentccGuardrailPolicyViewSet(
    GatewayAdminActionsMixin, BaseModelViewSetMixinWithUserOrg, ModelViewSet
):
    """CRUD for reusable guardrail policies. Org-scoped.

    A policy's checks are merged into the org config and pushed to the
    gateway (``sync_guardrail_policies``); every write is an org Owner's or
    Admin's, and every member can read.
    """

    permission_classes = [IsAuthenticated]
    gateway_admin_actions = frozenset(
        {"create", "update", "partial_update", "destroy", "apply", "sync"}
    )
    serializer_class = AgentccGuardrailPolicySerializer
    queryset = AgentccGuardrailPolicy.no_workspace_objects.all()
    _gm = GeneralMethods()

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            serializer = AgentccGuardrailPolicySerializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception("guardrail_policy_list_error", error=str(e))
            return self._gm.bad_request(str(e))

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            return self._gm.success_response(
                AgentccGuardrailPolicySerializer(instance).data
            )
        except Exception as e:
            logger.exception("guardrail_policy_retrieve_error", error=str(e))
            return self._gm.not_found("Guardrail policy not found")

    def create(self, request, *args, **kwargs):
        try:
            serializer = AgentccGuardrailPolicySerializer(data=request.data)
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            org = getattr(request, "organization", None)
            if org is None:
                return self._gm.bad_request("Organization context is required")

            policy = serializer.save(organization=org)

            # Sync to gateway
            synced = self._sync(org)

            data = AgentccGuardrailPolicySerializer(policy).data
            data["gateway_synced"] = synced
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("guardrail_policy_create_error", error=str(e))
            return self._gm.bad_request(str(e))

    def update(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            serializer = AgentccGuardrailPolicySerializer(
                instance, data=request.data, partial=kwargs.get("partial", False)
            )
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)

            policy = serializer.save()

            # Sync to gateway
            synced = self._sync(instance.organization)

            data = AgentccGuardrailPolicySerializer(policy).data
            data["gateway_synced"] = synced
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("guardrail_policy_update_error", error=str(e))
            return self._gm.bad_request(str(e))

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            instance.deleted = True
            instance.deleted_at = timezone.now()
            instance.save(update_fields=["deleted", "deleted_at", "updated_at"])

            # Sync to gateway (policy removed from merged config)
            synced = self._sync(instance.organization)

            data = {"deleted": True, "gateway_synced": synced}
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("guardrail_policy_delete_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"])
    def apply(self, request, pk=None):
        """Apply this policy to specific keys or projects."""
        try:
            instance = self.get_object()
            org = getattr(request, "organization", None)
            if org is None:
                return self._gm.bad_request("Organization context is required")
            key_ids = request.data.get("key_ids")
            project_ids = request.data.get("project_ids")

            if key_ids is not None and project_ids is not None:
                return self._gm.bad_request(
                    "Cannot apply to both keys and projects simultaneously. "
                    "Provide either key_ids or project_ids, not both."
                )

            if key_ids is None and project_ids is None:
                return self._gm.bad_request("Provide either key_ids or project_ids")

            if key_ids is not None:
                if not isinstance(key_ids, list):
                    return self._gm.bad_request("key_ids must be a list")
                # Validate key IDs belong to the same org
                from agentcc.models import AgentccAPIKey

                valid_keys = set(
                    AgentccAPIKey.no_workspace_objects.filter(
                        id__in=key_ids,
                        organization=org,
                        deleted=False,
                    ).values_list("id", flat=True)
                )
                invalid_keys = {str(k) for k in key_ids} - {str(k) for k in valid_keys}
                if invalid_keys:
                    return self._gm.bad_request(
                        f"Invalid or unauthorized key IDs: {list(invalid_keys)}"
                    )
                instance.applied_keys = key_ids
                instance.scope = AgentccGuardrailPolicy.SCOPE_KEY

            if project_ids is not None:
                if not isinstance(project_ids, list):
                    return self._gm.bad_request("project_ids must be a list")
                # Validate project IDs belong to the same org
                from agentcc.models.project import AgentccProject

                valid_projects = set(
                    AgentccProject.no_workspace_objects.filter(
                        id__in=project_ids,
                        organization=org,
                        deleted=False,
                    ).values_list("id", flat=True)
                )
                invalid_projects = {str(p) for p in project_ids} - {
                    str(p) for p in valid_projects
                }
                if invalid_projects:
                    return self._gm.bad_request(
                        f"Invalid or unauthorized project IDs: {list(invalid_projects)}"
                    )
                instance.applied_projects = project_ids
                instance.scope = AgentccGuardrailPolicy.SCOPE_PROJECT

            instance.save()

            # Sync to gateway
            synced = self._sync(org)

            data = AgentccGuardrailPolicySerializer(instance).data
            data["gateway_synced"] = synced
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("guardrail_policy_apply_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["post"])
    def sync(self, request):
        """Manual trigger to resync all policies to gateway."""
        try:
            org = getattr(request, "organization", None)
            if org is None:
                return self._gm.bad_request("Organization context is required")
            synced = self._sync(org)
            data = {"synced": True, "gateway_synced": synced}
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("guardrail_policy_sync_error", error=str(e))
            return self._gm.bad_request(str(e))

    def _sync(self, org, user=None):
        """Sync policies to gateway. Returns True on success, False on failure."""
        try:
            return sync_guardrail_policies(org, user=user)
        except Exception as e:
            logger.warning(
                "guardrail_policy_sync_failed",
                org_id=str(org.id),
                error=str(e),
            )
            return False
