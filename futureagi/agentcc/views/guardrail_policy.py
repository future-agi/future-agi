from collections.abc import Callable

import structlog
from django.db import transaction
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.serializers.guardrail_policy import AgentccGuardrailPolicySerializer
from agentcc.services.config_push import (
    OrgConfigRejected,
    push_org_config,
    validate_org_config,
)
from agentcc.services.guardrail_sync import sync_guardrail_policies
from agentcc.services.org_config_service import get_or_create_org_config
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

_GATEWAY_SYNC_WARNING = (
    "Config saved but gateway sync failed. Changes will apply on next gateway restart."
)


class AgentccGuardrailPolicyViewSet(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    """CRUD for reusable guardrail policies. Org-scoped."""

    permission_classes = [IsAuthenticated]
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

    def _save_and_push(self, org, save, old_scope=None, new_scope=None):
        with transaction.atomic():
            policy = save()
            config = get_or_create_org_config(org)
            if "global" in (old_scope, new_scope):
                # Never push from inside the transaction. The global sync only
                # updates the active config; validation may still reject it.
                sync_guardrail_policies(org, push=False)
                config.refresh_from_db()
            validate_org_config(config)
        return policy, push_org_config(str(org.id), config)

    def _response(self, policy, synced):
        data = AgentccGuardrailPolicySerializer(policy).data
        data["gateway_synced"] = synced
        if not synced:
            data["gateway_warning"] = _GATEWAY_SYNC_WARNING
        return self._gm.success_response(data)

    def create(self, request, *args, **kwargs):
        try:
            org = getattr(request, "organization", None)
            if org is None:
                return self._gm.bad_request("Organization context is required")
            serializer = AgentccGuardrailPolicySerializer(
                data=request.data, context={"request": request, "organization": org}
            )
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            policy, synced = self._save_and_push(
                org,
                lambda: serializer.save(organization=org, runs_on_gateway=True),
                new_scope=serializer.validated_data.get("scope", "global"),
            )
            return self._response(policy, synced)
        except (OrgConfigRejected, ValidationError) as e:
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.exception("guardrail_policy_create_error", error=str(e))
            return self._gm.bad_request(str(e))

    def update(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            org = getattr(request, "organization", None)
            serializer = AgentccGuardrailPolicySerializer(
                instance,
                data=request.data,
                partial=kwargs.get("partial", False),
                context={"request": request, "organization": org},
            )
            if not serializer.is_valid():
                return self._gm.bad_request(serializer.errors)
            policy, synced = self._save_and_push(
                org,
                lambda: serializer.save(runs_on_gateway=True),
                old_scope=instance.scope,
                new_scope=serializer.validated_data.get("scope", instance.scope),
            )
            return self._response(policy, synced)
        except (OrgConfigRejected, ValidationError) as e:
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.exception("guardrail_policy_update_error", error=str(e))
            return self._gm.bad_request(str(e))

    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        return self.update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()

            def remove():
                instance.deleted = True
                instance.deleted_at = timezone.now()
                instance.save(update_fields=["deleted", "deleted_at", "updated_at"])
                return instance

            _, synced = self._save_and_push(
                instance.organization, remove, old_scope=instance.scope
            )
            data = {"deleted": True, "gateway_synced": synced}
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except (OrgConfigRejected, ValidationError) as e:
            return self._gm.bad_request(str(e))
        except Exception as e:
            logger.exception("guardrail_policy_delete_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"])
    def apply(self, request, pk=None):
        """Apply to dashboard key UUIDs, or persist a project-only assignment."""
        try:
            instance = self.get_object()
            org = getattr(request, "organization", None)
            if org is None:
                return self._gm.bad_request("Organization context is required")
            key_ids = request.data.get("key_ids")
            project_ids = request.data.get("project_ids")
            if (key_ids is None) == (project_ids is None):
                return self._gm.bad_request(
                    "Provide either key_ids or project_ids, not both"
                )
            save: Callable[[], AgentccGuardrailPolicy]
            if key_ids is not None:
                serializer = AgentccGuardrailPolicySerializer(
                    instance,
                    data={"scope": "key", "applied_keys": key_ids},
                    partial=True,
                    context={"request": request, "organization": org},
                )
                if not serializer.is_valid():
                    return self._gm.bad_request(serializer.errors)

                def save_keys():
                    return serializer.save(runs_on_gateway=True)

                save = save_keys
                new_scope = "key"
            else:
                if not isinstance(project_ids, list):
                    return self._gm.bad_request({"project_ids": ["Must be a list"]})
                from agentcc.models.project import AgentccProject

                valid_projects = {
                    str(p)
                    for p in AgentccProject.no_workspace_objects.filter(
                        id__in=project_ids, organization=org, deleted=False
                    ).values_list("id", flat=True)
                }
                invalid = [str(p) for p in project_ids if str(p) not in valid_projects]
                if invalid:
                    return self._gm.bad_request(
                        {
                            "project_ids": [
                                f"Invalid or unauthorized project IDs: {invalid}"
                            ]
                        }
                    )

                def save_projects():
                    instance.applied_projects = project_ids
                    instance.applied_keys = []
                    instance.scope = "project"
                    instance.save()
                    return instance

                save = save_projects
                new_scope = "project"
            policy, synced = self._save_and_push(
                org, save, old_scope=instance.scope, new_scope=new_scope
            )
            return self._response(policy, synced)
        except (OrgConfigRejected, ValidationError) as e:
            return self._gm.bad_request(str(e))
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
