import structlog
from django.http import Http404
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from agentcc.models import AgentccAPIKey, AgentccProject
from agentcc.serializers.api_key import (
    AgentccAPIKeyCreateSerializer,
    AgentccAPIKeySerializer,
    AgentccAPIKeyUpdateSerializer,
)
from agentcc.services import auth_bridge
from agentcc.services.gateway_client import GatewayClientError
from model_hub.utils.workspace_scope import request_workspace_filter
from tfc.utils.base_viewset import (
    BaseModelViewSetMixinWithUserOrg,
    scope_to_request_organization,
)
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class AgentccAPIKeyViewSet(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = AgentccAPIKeySerializer
    queryset = AgentccAPIKey.no_workspace_objects.all()
    _gm = GeneralMethods()

    def get_queryset(self):
        """
        API keys are org-scoped, not workspace-scoped.  Keys created via
        provision_key() intentionally have workspace=NULL so they're
        visible across all workspaces in the org.  Override the default
        workspace filter to include workspace-NULL keys when browsing
        from a non-default workspace.

        A request with no organization gets no keys.
        """
        qs = AgentccAPIKey.no_workspace_objects.filter(deleted=False)
        qs = scope_to_request_organization(qs, self.request)
        return qs.select_related("project", "user")

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            serializer = AgentccAPIKeySerializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception("api_key_list_error", error=str(e))
            return self._gm.bad_request(str(e))

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            return self._gm.success_response(AgentccAPIKeySerializer(instance).data)
        except Exception as e:
            logger.exception("api_key_retrieve_error", error=str(e))
            return self._gm.not_found("API key not found")

    def _format_errors(self, errors):
        """Flatten serializer errors dict to a human-readable string."""
        messages = []
        for field, errs in errors.items():
            field_errors = errs if isinstance(errs, list) else [errs]
            messages.append(f"{field}: {', '.join(str(e) for e in field_errors)}")
        return "; ".join(messages)

    def create(self, request, *args, **kwargs):
        try:
            org = getattr(request, "organization", None)
            serializer = AgentccAPIKeyCreateSerializer(
                data=request.data,
                context={"key_organization_id": org.id if org else None},
            )
            if not serializer.is_valid():
                return self._gm.bad_request(self._format_errors(serializer.errors))

            data = serializer.validated_data
            if org is None:
                return self._gm.bad_request("Organization context is required")

            project = None
            if data.get("project_id"):
                project = AgentccProject.no_workspace_objects.filter(
                    request_workspace_filter(request),
                    deleted=False,
                    organization=org,
                ).get(
                    id=data["project_id"],
                )

            api_key, raw_key = auth_bridge.provision_key(
                name=data["name"],
                owner=data.get("owner", ""),
                user=request.user,
                project=project,
                models=data.get("allowed_models"),
                providers=data.get("allowed_providers"),
                metadata=data.get("metadata"),
                organization=org,
            )

            result = AgentccAPIKeySerializer(api_key).data
            result["key"] = raw_key  # Only returned on creation.
            return self._gm.create_response(result)

        except AgentccProject.DoesNotExist:
            return self._gm.not_found("Project not found")
        except GatewayClientError as e:
            logger.exception("api_key_create_gateway_error", error=str(e))
            return self._gm.bad_request(f"Gateway error: {e}")
        except Exception as e:
            logger.exception("api_key_create_error", error=str(e))
            return self._gm.bad_request(str(e))

    def update(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        try:
            api_key = self.get_object()
            if api_key.status == AgentccAPIKey.REVOKED:
                return self._gm.bad_request("Cannot update a revoked key")

            serializer = AgentccAPIKeyUpdateSerializer(
                data=request.data,
                context={"key_organization_id": api_key.organization_id},
            )
            if not serializer.is_valid():
                return self._gm.bad_request(self._format_errors(serializer.errors))

            api_key = auth_bridge.update_key(api_key, **serializer.validated_data)
            return self._gm.success_response(AgentccAPIKeySerializer(api_key).data)

        except Http404:
            return self._gm.not_found("API key not found")
        except GatewayClientError as e:
            logger.exception("api_key_update_gateway_error", error=str(e))
            return self._gm.bad_request(f"Gateway error: {e}")
        except Exception as e:
            logger.exception("api_key_update_error", error=str(e))
            return self._gm.bad_request(str(e))

    def destroy(self, request, *args, **kwargs):
        """Delete a key: it is revoked on the gateway before the row is
        deleted, so it stops working at once. When the gateway cannot revoke
        it, the delete answers 503, the row is kept and the delete can be
        retried."""
        try:
            api_key = self.get_object()
            auth_bridge.delete_key(api_key)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Http404:
            return self._gm.not_found("API key not found")
        except GatewayClientError as e:
            logger.exception("api_key_delete_gateway_error", error=str(e))
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                get_error_message("AGENTCC_KEY_DELETE_GATEWAY_UNREACHABLE"),
            )
        except Exception as e:
            logger.exception("api_key_delete_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        try:
            api_key = self.get_object()
            api_key, gateway_failed = auth_bridge.revoke_key(api_key)
            data = AgentccAPIKeySerializer(api_key).data
            if gateway_failed:
                data["warning"] = (
                    "Key revoked locally but the gateway could not be reached. "
                    "The gateway will be updated on next sync."
                )
            return self._gm.success_response(data)
        except Http404:
            return self._gm.not_found("API key not found")
        except Exception as e:
            logger.exception("api_key_revoke_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["post"])
    def sync(self, request):
        try:
            org = getattr(request, "organization", None)
            if org is None:
                return self._gm.bad_request("Organization context is required")
            count = auth_bridge.sync_keys(org=org)
            return self._gm.success_response({"synced": count})
        except GatewayClientError as e:
            logger.exception("api_key_sync_error", error=str(e))
            return self._gm.bad_request(f"Gateway error: {e}")
        except Exception as e:
            logger.exception("api_key_sync_error", error=str(e))
            return self._gm.bad_request(str(e))
