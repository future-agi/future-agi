import copy

import structlog
from django.db import transaction
from django.db.models import Max
from django.http import Http404
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agentcc.models import AgentccOrgConfig
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.org_config_defaults import default_cost_tracking_config
from agentcc.serializers.guardrail_policy import AgentccGuardrailPolicySerializer
from agentcc.serializers.org_config import (
    AgentccOrgConfigSerializer,
    AgentccOrgConfigWriteSerializer,
)
from agentcc.services.config_push import (
    _inject_guardrail_credentials,
    validate_guardrail_representations,
    validate_org_config,
)
from tfc.ee_gating import FeatureUnavailable
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

_GATEWAY_SYNC_WARNING = (
    "Config saved but gateway sync failed. Changes will apply on next gateway restart."
)


class AgentccOrgConfigViewSet(BaseModelViewSetMixinWithUserOrg, ModelViewSet):
    """Per-org gateway configuration management. Configs are versioned and immutable."""

    permission_classes = [IsAuthenticated]
    serializer_class = AgentccOrgConfigSerializer
    queryset = AgentccOrgConfig.no_workspace_objects.all()
    _gm = GeneralMethods()

    def get_queryset(self):
        return super().get_queryset().order_by("-version")

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.get_queryset()
            serializer = AgentccOrgConfigSerializer(queryset, many=True)
            return self._gm.success_response(serializer.data)
        except Exception as e:
            logger.exception("org_config_list_error", error=str(e))
            return self._gm.bad_request(str(e))

    def retrieve(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            return self._gm.success_response(AgentccOrgConfigSerializer(instance).data)
        except Exception as e:
            logger.exception("org_config_retrieve_error", error=str(e))
            return self._gm.not_found("Org config not found")

    def create(self, request, *args, **kwargs):
        """Create a new config version. Auto-increments version and activates it."""
        try:
            write_serializer = AgentccOrgConfigWriteSerializer(data=request.data)
            if not write_serializer.is_valid():
                return self._gm.bad_request(write_serializer.errors)

            data = write_serializer.validated_data
            org = getattr(request, "organization", None)
            if org is None:
                return self._gm.bad_request("Organization context is required")

            if data.get("privacy"):
                from tfc.ee_gating import EEFeature, check_ee_feature

                check_ee_feature(EEFeature.DATA_MASKING, org_id=str(org.id))

            # Validate every retained representation before selecting or extracting
            # any of them. A checks map must not hide a legacy rules sibling.
            guardrails = copy.deepcopy(data.get("guardrails", {}))
            validate_guardrail_representations(guardrails)
            selected = "checks" if guardrails.get("checks") else "rules"

            with transaction.atomic():
                # Resolve both collections before updating either policy, since
                # both may refer to the same previous version's encrypted values.
                for field in ("checks", "rules"):
                    if guardrails.get(field):
                        _inject_guardrail_credentials(guardrails[field], str(org.id))
                for field in ("checks", "rules"):
                    collection = guardrails.get(field)
                    if not collection:
                        continue
                    checks_list = (
                        [{**check, "name": name} for name, check in collection.items()]
                        if isinstance(collection, dict)
                        else collection
                    )
                    # Retained siblings need independent encrypted storage: the
                    # same check name may have different credentials in each.
                    # Only the selected representation participates in policy sync.
                    is_selected = field == selected
                    policy_name = (
                        "__ui_guardrails__"
                        if is_selected
                        else f"__ui_guardrails_{field}__"
                    )
                    policy, _ = (
                        AgentccGuardrailPolicy.no_workspace_objects.get_or_create(
                            organization=org,
                            name=policy_name,
                            deleted=False,
                            defaults={
                                "checks": [],
                                "scope": AgentccGuardrailPolicy.SCOPE_GLOBAL,
                                "is_active": is_selected,
                            },
                        )
                    )
                    policy_serializer = AgentccGuardrailPolicySerializer(
                        policy,
                        data={"checks": checks_list, "is_active": is_selected},
                        partial=True,
                    )
                    policy_serializer.is_valid(raise_exception=True)
                    policy_serializer.save()
                    sanitized = [
                        {**check, "_policy": str(policy.id)} for check in policy.checks
                    ]
                    if isinstance(collection, dict):
                        guardrails[field] = {
                            check["name"]: {
                                key: value
                                for key, value in check.items()
                                if key != "name"
                            }
                            for check in sanitized
                        }
                    else:
                        guardrails[field] = sanitized
                data["guardrails"] = guardrails

                # Lock active config to prevent concurrent version creation
                AgentccOrgConfig.no_workspace_objects.select_for_update().filter(
                    organization=org, is_active=True, deleted=False
                ).first()

                # Compute next version number
                max_version = (
                    AgentccOrgConfig.no_workspace_objects.filter(
                        organization=org, deleted=False
                    ).aggregate(Max("version"))["version__max"]
                    or 0
                )
                next_version = max_version + 1

                # Deactivate any currently active config for this org
                AgentccOrgConfig.no_workspace_objects.filter(
                    organization=org, is_active=True, deleted=False
                ).update(is_active=False)

                # Create the new version
                config = AgentccOrgConfig.no_workspace_objects.create(
                    organization=org,
                    version=next_version,
                    guardrails=data.get("guardrails", {}),
                    routing=data.get("routing", {}),
                    cache=data.get("cache", {}),
                    rate_limiting=data.get("rate_limiting", {}),
                    budgets=data.get("budgets", {}),
                    cost_tracking=data.get(
                        "cost_tracking", default_cost_tracking_config()
                    ),
                    ip_acl=data.get("ip_acl", {}),
                    alerting=data.get("alerting", {}),
                    privacy=data.get("privacy", {}),
                    tool_policy=data.get("tool_policy", {}),
                    mcp=data.get("mcp", {}),
                    a2a=data.get("a2a", {}),
                    audit=data.get("audit", {}),
                    model_database=data.get("model_database", {}),
                    model_map=data.get("model_map", {}),
                    is_active=True,
                    created_by=request.user,
                    change_description=data.get("change_description", ""),
                )
                # Fail closed: a version the gateway contract rejects must not
                # become active. Raising here rolls the whole save back.
                validate_org_config(config)

            # Push to gateway
            synced = self._push_config_to_gateway(org.id, config)

            data = AgentccOrgConfigSerializer(config).data
            data["gateway_synced"] = synced
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except FeatureUnavailable:
            raise
        except Exception as e:
            logger.exception("org_config_create_error", error=str(e))
            return self._gm.bad_request(str(e))

    def update(self, request, *args, **kwargs):
        """Disabled — configs are immutable versions. Create a new one instead."""
        return self._gm.bad_request(
            "Org configs are immutable. Create a new version instead."
        )

    def destroy(self, request, *args, **kwargs):
        """Soft-delete a config version. Cannot delete the active version."""
        try:
            instance = self.get_object()
            if instance.is_active:
                return self._gm.bad_request(
                    "Cannot delete the active config version. Activate another version first."
                )
            instance.deleted = True
            instance.deleted_at = timezone.now()
            instance.save(update_fields=["deleted", "deleted_at", "updated_at"])
            return self._gm.success_response({"deleted": True})
        except Exception as e:
            logger.exception("org_config_delete_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["post"])
    def activate(self, request, pk=None):
        """Roll back to a specific config version by activating it."""
        try:
            instance = self.get_object()
            if instance.is_active:
                return self._gm.success_response(
                    AgentccOrgConfigSerializer(instance).data
                )

            org = instance.organization
            # Fail closed: an older version can hold keys the gateway contract
            # no longer accepts.
            validate_org_config(instance)

            with transaction.atomic():
                # Deactivate current active config
                AgentccOrgConfig.no_workspace_objects.filter(
                    organization=org, is_active=True, deleted=False
                ).update(is_active=False)

                # Activate the selected version
                instance.is_active = True
                instance.save(update_fields=["is_active", "updated_at"])

            # Push to gateway
            synced = self._push_config_to_gateway(org.id, instance)

            data = AgentccOrgConfigSerializer(instance).data
            data["gateway_synced"] = synced
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Http404:
            return self._gm.not_found("Org config not found")
        except Exception as e:
            logger.exception("org_config_activate_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=False, methods=["get"])
    def active(self, request):
        """Get the currently active config for the requesting user's org."""
        try:
            org = getattr(request, "organization", None)
            if org is None:
                return self._gm.bad_request("Organization context is required")
            config = AgentccOrgConfig.no_workspace_objects.filter(
                organization=org, is_active=True, deleted=False
            ).first()
            if not config:
                return self._gm.success_response(None)
            return self._gm.success_response(AgentccOrgConfigSerializer(config).data)
        except Exception as e:
            logger.exception("org_config_active_error", error=str(e))
            return self._gm.bad_request(str(e))

    @action(detail=True, methods=["get"])
    def diff(self, request, pk=None):
        """Compare this config version with another. Pass ?compare_to=<uuid>."""
        try:
            instance = self.get_object()
            compare_to_id = request.query_params.get("compare_to")
            if not compare_to_id:
                return self._gm.bad_request("compare_to query param is required")

            other = AgentccOrgConfig.no_workspace_objects.filter(
                id=compare_to_id,
                organization=instance.organization,
                deleted=False,
            ).first()
            if not other:
                return self._gm.not_found("Comparison config not found")

            diff = {
                "from_version": other.version,
                "to_version": instance.version,
                "guardrails": _json_diff(other.guardrails, instance.guardrails),
                "routing": _json_diff(other.routing, instance.routing),
                "cache": _json_diff(other.cache or {}, instance.cache or {}),
                "rate_limiting": _json_diff(
                    other.rate_limiting or {}, instance.rate_limiting or {}
                ),
                "budgets": _json_diff(other.budgets or {}, instance.budgets or {}),
                "cost_tracking": _json_diff(
                    other.cost_tracking or {}, instance.cost_tracking or {}
                ),
                "ip_acl": _json_diff(other.ip_acl or {}, instance.ip_acl or {}),
                "alerting": _json_diff(other.alerting or {}, instance.alerting or {}),
                "privacy": _json_diff(other.privacy or {}, instance.privacy or {}),
                "tool_policy": _json_diff(
                    other.tool_policy or {}, instance.tool_policy or {}
                ),
                "mcp": _json_diff(other.mcp or {}, instance.mcp or {}),
                "a2a": _json_diff(other.a2a or {}, instance.a2a or {}),
                "audit": _json_diff(other.audit or {}, instance.audit or {}),
                "model_database": _json_diff(
                    other.model_database or {}, instance.model_database or {}
                ),
                "model_map": _json_diff(
                    other.model_map or {}, instance.model_map or {}
                ),
            }
            return self._gm.success_response(diff)
        except Exception as e:
            logger.exception("org_config_diff_error", error=str(e))
            return self._gm.bad_request(str(e))

    def _push_config_to_gateway(self, org_id, config):
        """Push to gateway. Returns True on success, False on failure."""
        try:
            from agentcc.services.config_push import push_org_config

            return push_org_config(str(org_id), config)
        except Exception as e:
            logger.warning(
                "org_config_push_failed",
                org_id=str(org_id),
                error=str(e),
            )
            return False


def _json_diff(old, new):
    """Simple diff between two JSON-serializable dicts."""
    added = {k: v for k, v in new.items() if k not in old}
    removed = {k: v for k, v in old.items() if k not in new}
    changed = {}
    for k in set(old) & set(new):
        if old[k] != new[k]:
            changed[k] = {"old": old[k], "new": new[k]}
    return {"added": added, "removed": removed, "changed": changed}
