import structlog
from django.http import Http404
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.status import HTTP_400_BAD_REQUEST
from rest_framework.viewsets import ModelViewSet

from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.permissions import GatewayAdminActionsMixin
from agentcc.serializers.guardrail_policy import (
    SCOPED_POLICY_NOT_SUPPORTED,
    SCOPED_POLICY_NOT_SUPPORTED_MESSAGE,
    AgentccGuardrailPolicyReadSerializer,
    AgentccGuardrailPolicySerializer,
)
from agentcc.services.guardrail_sync import sync_guardrail_policies
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

_GATEWAY_SYNC_WARNING = (
    "Config saved but gateway sync failed. Changes will apply on next gateway restart."
)
# Another organization's policy, a deleted one and an unknown id answer the
# same 404 on every action, as retrieve always did. update, destroy and apply
# used to catch get_object's Http404 in their broad except and answer 400
# with the model's name (release round 6B, fix (d)).
_NOT_FOUND = "Guardrail policy not found"


class AgentccGuardrailPolicyViewSet(
    GatewayAdminActionsMixin, BaseModelViewSetMixinWithUserOrg, ModelViewSet
):
    """CRUD for reusable guardrail policies. Org-scoped.

    A policy's checks are merged into the org config and pushed to the
    gateway (``sync_guardrail_policies``), so every write is an org Owner's
    or Admin's, as every other gateway config write is; every member keeps
    read access. Every policy is global: it runs for every key of the
    organization. Key and project scope are not supported.
    """

    permission_classes = [IsAuthenticated]
    gateway_admin_actions = frozenset(
        {"create", "update", "partial_update", "destroy", "apply", "sync"}
    )
    serializer_class = AgentccGuardrailPolicySerializer
    queryset = AgentccGuardrailPolicy.no_workspace_objects.all()
    _gm = GeneralMethods()

    def get_serializer_class(self):
        # DRF and schema generation use the same serializer per action. Reads
        # declare every stored scope (a policy an earlier release left at key
        # or project scope reads back as stored); writes accept global only,
        # and their responses declare the read representation (below).
        if self.action in ("list", "retrieve"):
            return AgentccGuardrailPolicyReadSerializer
        return AgentccGuardrailPolicySerializer

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            serializer = AgentccGuardrailPolicyReadSerializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception("guardrail_policy_list_error", error=str(e))
            return self._gm.bad_request(str(e))

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            return self._gm.success_response(
                AgentccGuardrailPolicyReadSerializer(instance).data
            )
        except Exception as e:
            logger.exception("guardrail_policy_retrieve_error", error=str(e))
            return self._gm.not_found(_NOT_FOUND)

    @swagger_auto_schema(responses={201: AgentccGuardrailPolicyReadSerializer})
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

            data = AgentccGuardrailPolicyReadSerializer(policy).data
            data["gateway_synced"] = synced
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("guardrail_policy_create_error", error=str(e))
            return self._gm.bad_request(str(e))

    @swagger_auto_schema(responses={200: AgentccGuardrailPolicyReadSerializer})
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

            data = AgentccGuardrailPolicyReadSerializer(policy).data
            data["gateway_synced"] = synced
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Http404:
            return self._gm.not_found(_NOT_FOUND)
        except Exception as e:
            logger.exception("guardrail_policy_update_error", error=str(e))
            return self._gm.bad_request(str(e))

    @swagger_auto_schema(responses={200: AgentccGuardrailPolicyReadSerializer})
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
        except Http404:
            return self._gm.not_found(_NOT_FOUND)
        except Exception as e:
            logger.exception("guardrail_policy_delete_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"])
    def apply(self, request, pk=None):
        """Not supported: answers 400 ``scoped_policy_not_supported``.

        A policy runs for every key of its organization. The gateway receives
        only the org's global policies (``guardrail_sync``), so this action
        used to remove the policy from every key, the targeted ones included,
        when it set the policy's scope to its keys or projects. It now leaves
        the policy unchanged and pushes nothing.
        """
        try:
            self.get_object()
            return self._gm.custom_error_response(
                HTTP_400_BAD_REQUEST,
                SCOPED_POLICY_NOT_SUPPORTED_MESSAGE,
                code=SCOPED_POLICY_NOT_SUPPORTED,
            )
        except Http404:
            return self._gm.not_found(_NOT_FOUND)

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
