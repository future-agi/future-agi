import structlog
from django.db import transaction
from django.db.models import Q
from django.db.models.functions import Now
from django.http import Http404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.viewsets import ModelViewSet

from accounts.authentication import allows_no_workspace
from accounts.models.audit_log import AuditAction
from agentcc.key_permissions import PersonalGatewayKeyAccess, can_manage_gateway_keys
from agentcc.models import AgentccAPIKey, AgentccProject
from agentcc.permissions import GatewayAdminActionsMixin
from agentcc.serializers.api_key import (
    KEY_METADATA_FIELDS,
    RESERVED_METADATA_KEYS,
    AgentccAPIKeyCreateSerializer,
    AgentccAPIKeySerializer,
    AgentccAPIKeyUpdateSerializer,
)
from agentcc.services import auth_bridge, ee_agent_key_policy
from agentcc.services.gateway_client import GatewayClientError
from agentcc.services.managed_models_default import ensure_first_org_config
from agentcc.services.key_audit import KeyAuditUnavailable, record_key_event
from agentcc.services.key_rotation_mcp import lock_rotation_config, stage_mcp_rotation
from agentcc.services.key_transactions import (
    KeyTransactionUnavailable,
    key_creation_transaction,
    require_key_transaction_boundary,
    revoke_key_with_audit,
)
from agentcc.views._errors import exception_response
from model_hub.utils.workspace_scope import request_workspace_filter
from tfc.middleware.workspace_context import (
    get_current_workspace,
    set_workspace_context,
)
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

    A raw ``metadata`` dict replaces the caller's own keys, as before. The
    reserved keys stored there (the key settings, a key limit stored before
    the limits were refused, and ``org_id``) survive it unless the settings'
    own fields change them; the serializer refuses them in a raw dict, so no
    update can move a key into another org's config.
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
    permission_classes = [IsAuthenticated, PersonalGatewayKeyAccess]
    # Org-scoped credentials: defer workspace ROLE checks to the permission.
    # The empty-landing guard is independent: only the three method-marked
    # self-service actions below can reach that permission without a workspace.
    # It requires personal-session provenance; admin/config gates stay intact.
    workspace_write_exempt = True
    # 733: active members create their own key from a session and may remove
    # their own keys. Editing access/configuration and bulk sync stay admin-only.
    gateway_admin_actions = frozenset({"update", "partial_update", "sync"})
    serializer_class = AgentccAPIKeySerializer
    queryset = AgentccAPIKey.no_workspace_objects.all()
    _gm = GeneralMethods()

    @classmethod
    def as_view(cls, actions=None, **initkwargs):
        # DRF's router calls as_view and Django inspects THIS returned callback,
        # not the action method. Creation/rotation and standalone revoke
        # must own their durable transaction rather than ATOMIC_REQUESTS.
        view = super().as_view(actions=actions, **initkwargs)
        if actions and actions.get("post") in {"create", "revoke"}:
            using = AgentccAPIKey.no_workspace_objects.select_for_update().db
            view = transaction.non_atomic_requests(using=using)(view)
        return view

    def get_queryset(self):
        """
        API keys are org-scoped, not workspace-scoped.  Keys created via
        provision_key() intentionally have workspace=NULL so they're
        visible across all workspaces in the org.  Override the default
        workspace filter to include workspace-NULL keys when browsing
        from a non-default workspace.

        A request with no organization (a user removed from every org still
        authenticates) gets no keys, never every org's keys.
        """
        # The no-workspace manager also denies empty authenticated landings.
        # Only own-key removal needs raw access here; keep reads and other
        # actions unchanged, with explicit org/owner boundaries on every path.
        manager = AgentccAPIKey.no_workspace_objects
        if self.action in {"revoke", "destroy"}:
            manager = AgentccAPIKey.unscoped_objects
        qs = manager.filter(deleted=False)
        qs = scope_to_request_organization(qs, self.request)
        if not can_manage_gateway_keys(self.request):
            qs = qs.filter(user=self.request.user)
        return qs.select_related("project", "user")

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            serializer = AgentccAPIKeySerializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            return exception_response(
                self._gm, e, logger=logger, event="api_key_list_error"
            )

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

    @allows_no_workspace("POST")
    def create(self, request, *args, **kwargs):
        from accounts.api_key_policy import require_personal_key_session

        require_personal_key_session(request)
        # Keep the no-workspace marker so this action can return its typed remedy.
        # Reads intentionally hide keys without a landing; never mint one there.
        if getattr(request, "workspace", None) is None:
            return self._gm.custom_error_response(
                status.HTTP_403_FORBIDDEN,
                "Choose or join a workspace to create API keys.",
                code="personal_key_workspace_required",
            )
        try:
            org = getattr(request, "organization", None)
            serializer = AgentccAPIKeyCreateSerializer(
                data=request.data,
                context={"key_organization_id": org.id if org else None},
            )
            if not serializer.is_valid():
                return self._invalid(serializer)

            data = serializer.validated_data
            revoke_rotated_key = data.pop("revoke_rotated_key", False)
            if revoke_rotated_key and "rotates_key_id" not in data:
                return self._gm.custom_error_response(
                    status.HTTP_403_FORBIDDEN, "Key rotation is not permitted"
                )
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

            # The explicit replacement command creates and revokes in the same
            # atomic block. A marker without that command never revokes its source.
            # An absent, foreign, ownerless or unavailable source has one refusal.
            revocation = {"gateway_failed": True}
            with key_creation_transaction() as issuance:
                if revoke_rotated_key:
                    lock_rotation_config(org, using=issuance.using)
                previous_key = None
                if "rotates_key_id" in data:
                    previous_key = (
                        AgentccAPIKey.unscoped_objects.using(issuance.using)
                        .select_for_update()
                        .filter(
                            pk=data["rotates_key_id"],
                            organization=org,
                            user=request.user,
                            deleted=False,
                            status=AgentccAPIKey.ACTIVE,
                        )
                        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=Now()))
                        .first()
                    )
                    # Recheck time after the lock: waiting for another writer
                    # can outlive the expiry checked by the SELECT statement.
                    if previous_key is None or (
                        previous_key.expires_at is not None
                        and previous_key.expires_at <= timezone.now()
                    ):
                        return self._gm.custom_error_response(
                            status.HTTP_403_FORBIDDEN, "Key rotation is not permitted"
                        )

                # Personal gateway keys are org-wide (F2). The shared pre/post-save
                # signals otherwise replace provision_key's explicit workspace=None
                # with the browsing workspace. Clear only this request's ContextVar
                # during issuance, then restore it for the remainder of the request.
                browsing_workspace = get_current_workspace()
                try:
                    set_workspace_context(workspace=None)
                    api_key, raw_key = issuance.provision_key(
                        name=data["name"],
                        owner=request.user.email,
                        user=request.user,
                        project=project,
                        models=data.get("allowed_models"),
                        providers=data.get("allowed_providers"),
                        metadata=_key_metadata(data, data.get("metadata")),
                        organization=org,
                    )
                finally:
                    set_workspace_context(workspace=browsing_workspace)

                # The request's owner/user fields never establish authority.
                record_key_event(api_key, request.user, AuditAction.GATEWAY_KEY_CREATED)
                if previous_key is not None:
                    record_key_event(
                        api_key,
                        request.user,
                        AuditAction.GATEWAY_KEY_ROTATED,
                        previous_key=previous_key,
                    )
                if revoke_rotated_key:
                    # Copy the locked source's expiry, not a stale client value.
                    # Any failure before revoke rolls back creation and its audit.
                    api_key.expires_at = previous_key.expires_at
                    api_key.save(
                        using=issuance.using, update_fields=["expires_at", "updated_at"]
                    )
                    issuance.before_import = stage_mcp_rotation(
                        org, previous_key.gateway_key_id, api_key.gateway_key_id,
                        user=request.user, using=issuance.using,
                    )
                    previous_key, _ = revoke_key_with_audit(
                        previous_key, request.user, outcome=revocation, locked_source=True
                    )

            if not issuance.provisioned:
                # No new prose: the console uses activated KEYS-ROTATE-10.
                # Never serialize the pending/failed key or deliver its secret.
                return Response(
                    {"code": "KEY_PROVISIONING_FAILED"},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

            # A new org's first key works without a switch where the
            # deployment offers Future AGI models: its first org config turns
            # them on (best effort; never fails the key).
            ensure_first_org_config(org, user=request.user)

            result = AgentccAPIKeySerializer(api_key).data
            result["key"] = raw_key  # Only returned on creation.
            if revoke_rotated_key:
                result["rotation"] = {
                    "status": previous_key.status,
                    "expiry_copied": True,
                    "gateway_revocation_confirmed": not revocation["gateway_failed"],
                }
                if revocation["gateway_failed"]:
                    result["rotation"]["warning"] = (
                        "Revoked. Any gateway replica that has not received "
                        "the change yet stops accepting this key at its next key sync."
                    )
            return self._gm.create_response(result)

        except (KeyAuditUnavailable, KeyTransactionUnavailable):
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Key change could not be recorded. Please retry.",
            )
        except AgentccProject.DoesNotExist:
            return self._gm.not_found("Project not found")
        except GatewayClientError as e:
            return exception_response(
                self._gm, e, logger=logger, event="api_key_create_gateway_error"
            )
        except Exception as e:
            return exception_response(
                self._gm,
                e,
                logger=logger,
                event="api_key_create_error",
                log_detail=False,
            )

    def update(self, request, *args, **kwargs):
        return self.partial_update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        try:
            api_key = self.get_object()
            if api_key.status == AgentccAPIKey.REVOKED:
                return self._gm.bad_request("Cannot update a revoked key")
            if api_key.status in {AgentccAPIKey.PENDING, AgentccAPIKey.FAILED}:
                return Response(
                    {"code": "KEY_PROVISIONING_FAILED"},
                    status=status.HTTP_409_CONFLICT,
                )

            serializer = AgentccAPIKeyUpdateSerializer(
                data=request.data,
                context={"key_organization_id": api_key.organization_id},
            )
            if not serializer.is_valid():
                return self._invalid(serializer)

            data = dict(serializer.validated_data)
            has_expiry = "expires_at" in data
            expires_at = data.pop("expires_at", None)
            if has_expiry and ee_agent_key_policy.extends_agent_key(
                api_key, expires_at
            ):
                return self._gm.bad_request(ee_agent_key_policy.EXPIRY_FIXED)
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
            return exception_response(
                self._gm, e, logger=logger, event="api_key_update_gateway_error"
            )
        except Exception as e:
            return exception_response(
                self._gm, e, logger=logger, event="api_key_update_error"
            )

    @allows_no_workspace("DELETE")
    def destroy(self, request, *args, **kwargs):
        """Delete a key: revoke it on the gateway first, so it stops working
        now rather than at the gateway's next key sync."""
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
            return exception_response(
                self._gm, e, logger=logger, event="api_key_delete_error"
            )

    @action(detail=True, methods=["post"])
    @allows_no_workspace("POST")
    def revoke(self, request, pk=None):
        try:
            require_key_transaction_boundary()
            api_key = self.get_object()
            api_key, gateway_failed = revoke_key_with_audit(api_key, request.user)
            data = AgentccAPIKeySerializer(api_key).data
            data["gateway_revocation_confirmed"] = not gateway_failed
            if gateway_failed:
                data["warning"] = (
                    "Revoked. Any gateway replica that has not received "
                    "the change yet stops accepting this key at its next key sync."
                )
            return self._gm.success_response(data)
        except KeyAuditUnavailable:
            return self._gm.custom_error_response(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Key change could not be recorded. Please retry.",
            )
        except Http404:
            return self._gm.not_found("API key not found")
        except Exception as e:
            return exception_response(
                self._gm,
                e,
                logger=logger,
                event="api_key_revoke_error",
                log_detail=False,
            )

    @action(detail=False, methods=["post"])
    def sync(self, request):
        try:
            org = getattr(request, "organization", None)
            if org is None:
                return self._gm.bad_request("Organization context is required")
            count = auth_bridge.sync_keys(org=org)
            return self._gm.success_response({"synced": count})
        except GatewayClientError as e:
            return exception_response(
                self._gm, e, logger=logger, event="api_key_sync_error"
            )
        except Exception as e:
            return exception_response(
                self._gm, e, logger=logger, event="api_key_sync_error"
            )
