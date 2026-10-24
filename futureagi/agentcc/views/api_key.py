import structlog
from django.http import Http404
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.viewsets import ModelViewSet

from agentcc.models import AgentccAPIKey, AgentccProject
from agentcc.permissions import GatewayAdminActionsMixin
from agentcc.serializers.api_key import (
    KEY_METADATA_FIELDS,
    RESERVED_METADATA_KEYS,
    AgentccAPIKeyCreateSerializer,
    AgentccAPIKeySerializer,
    AgentccAPIKeyUpdateSerializer,
)
from agentcc.services import auth_bridge
from agentcc.services.gateway_client import GatewayClientError
from model_hub.utils.workspace_scope import request_workspace_filter
from tfc.utils.api_errors import ApiErrorCode
from tfc.utils.base_viewset import (
    BaseModelViewSetMixinWithUserOrg,
    scope_to_request_organization,
)
from tfc.utils.error_codes import get_error_message
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


def _key_metadata(data, base):
    """``base`` with the key settings in ``data`` (popped from it) applied; a
    ``None`` setting removes the key."""
    metadata = dict(base or {})
    for field in KEY_METADATA_FIELDS:
        if field not in data:
            continue
        value = data.pop(field)
        if value is None:
            metadata.pop(field, None)
        else:
            metadata[field] = value
    return metadata


def _updated_metadata(api_key, data):
    """The key's metadata after an update, or None when it does not change.

    A raw ``metadata`` dict replaces the caller's own keys. The reserved keys
    stored there (the key settings, a stored key limit and ``org_id``) survive
    it unless the settings' own fields change them; the serializer refuses them
    in a raw dict, so no update can move a key into another org's config.
    """
    if "metadata" not in data and not any(f in data for f in KEY_METADATA_FIELDS):
        return None
    current = api_key.metadata or {}
    base = dict(data.pop("metadata", current))
    base.update({k: v for k, v in current.items() if k in RESERVED_METADATA_KEYS})
    return _key_metadata(data, base)


class AgentccAPIKeyViewSet(
    GatewayAdminActionsMixin, BaseModelViewSetMixinWithUserOrg, ModelViewSet
):
    permission_classes = [IsAuthenticated]
    # Every write changes what a key may spend, reach or log.
    gateway_admin_actions = frozenset(
        {"create", "update", "partial_update", "destroy", "revoke", "sync"}
    )
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

    def _invalid(self, serializer):
        """The 400 for a key write the serializer refused. A key limit the
        gateway does not enforce gets its own code, so a client can tell it
        from a bad value; every other error keeps the generic one."""
        for error in serializer.errors.get(api_settings.NON_FIELD_ERRORS_KEY, []):
            if getattr(error, "code", None) == ApiErrorCode.KEY_LIMIT_NOT_ENFORCED:
                return self._gm.custom_error_response(
                    status.HTTP_400_BAD_REQUEST,
                    str(error),
                    code=ApiErrorCode.KEY_LIMIT_NOT_ENFORCED,
                )
        return self._gm.bad_request(self._format_errors(serializer.errors))

    def create(self, request, *args, **kwargs):
        try:
            org = getattr(request, "organization", None)
            serializer = AgentccAPIKeyCreateSerializer(
                data=request.data,
                context={"key_organization_id": org.id if org else None},
            )
            if not serializer.is_valid():
                return self._invalid(serializer)

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
                metadata=_key_metadata(data, data.get("metadata")),
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
                return self._invalid(serializer)

            data = dict(serializer.validated_data)
            has_expiry = "expires_at" in data
            expires_at = data.pop("expires_at", None)
            metadata = _updated_metadata(api_key, data)
            if metadata is not None:
                data["metadata"] = metadata

            api_key = auth_bridge.update_key(api_key, **data)
            if has_expiry:
                api_key.expires_at = expires_at
                api_key.save(update_fields=["expires_at", "updated_at"])
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
        """Delete a key: revoke it on the gateway first, so it stops working
        at once."""
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
