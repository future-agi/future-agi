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
from agentcc.permissions import GatewayAdminActionsMixin
from agentcc.serializers.guardrail_policy import AgentccGuardrailPolicySerializer
from agentcc.serializers.org_config import (
    AgentccOrgConfigSerializer,
    AgentccOrgConfigWriteSerializer,
)
from agentcc.services import guardrail_urls
from agentcc.services.config_push import validate_org_config
from agentcc.services.guardrail_policy_scope import (
    GuardrailPolicyNotInOrg,
    log_policy_refused,
    reject_unowned_guardrail_policies,
)
from agentcc.services.org_config_secrets import (
    SECRET_SECTIONS,
    OrgConfigSecretRejected,
    encrypt_section_secrets,
    masked_section,
)
from tfc.ee_gating import FeatureUnavailable
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

_GATEWAY_SYNC_WARNING = (
    "Config saved but gateway sync failed. Changes will apply on next gateway restart."
)


class AgentccOrgConfigViewSet(
    GatewayAdminActionsMixin, BaseModelViewSetMixinWithUserOrg, ModelViewSet
):
    """Per-org gateway configuration management. Configs are versioned and immutable."""

    permission_classes = [IsAuthenticated]
    # Writes are an org Owner's or Admin's; every member reads.
    gateway_admin_actions = frozenset(
        {"create", "update", "partial_update", "destroy", "activate"}
    )
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
            org = getattr(request, "organization", None)
            # Every create is a whole new version. When its guardrails are the
            # active version's, as stored (the Fallbacks page posts the active
            # config back to save routing), this save does not set them, and
            # their "${" check is not this save's to make. Looked up only when
            # needed, after the checks that need no org config.
            stored = []

            def stored_guardrails():
                if not stored:
                    stored.append(
                        AgentccOrgConfig.no_workspace_objects.filter(
                            organization=org, is_active=True, deleted=False
                        )
                        .values_list("guardrails", flat=True)
                        .first()
                        or {}
                    )
                return stored[0]

            def stored_alerting():
                return (
                    AgentccOrgConfig.no_workspace_objects.filter(
                        organization=org, is_active=True, deleted=False
                    )
                    .values_list("alerting", flat=True)
                    .first()
                    or {}
                )

            write_serializer = AgentccOrgConfigWriteSerializer(
                data=request.data,
                context={
                    "stored_guardrails": stored_guardrails,
                    "stored_alerting": stored_alerting,
                },
            )
            if not write_serializer.is_valid():
                return self._gm.bad_request(write_serializer.errors)

            data = write_serializer.validated_data
            if org is None:
                return self._gm.bad_request("Organization context is required")

            if data.get("privacy"):
                from tfc.ee_gating import EEFeature, check_ee_feature

                check_ee_feature(EEFeature.DATA_MASKING, org_id=str(org.id))

            guardrails_changed = data.get("guardrails", {}) != stored_guardrails()

            # Route guardrail credentials through AgentccGuardrailPolicy for encryption
            guardrails = data.get("guardrails", {})
            # A check may name only a policy the org may name, checked as sent
            # and before anything is written, the console policy upsert below
            # included.
            reject_unowned_guardrail_policies(org, guardrails, stored_guardrails())
            raw_checks = guardrails.get("checks")
            raw_rules = guardrails.get("rules")

            # Determine the checks list — frontend may send as dict or rules array
            checks_list = None
            source_format = None
            if raw_checks and isinstance(raw_checks, dict):
                source_format = "checks_dict"
                checks_list = [
                    {"name": name, **copy.deepcopy(cd)}
                    for name, cd in raw_checks.items()
                ]
            elif raw_rules and isinstance(raw_rules, list):
                source_format = "rules_list"
                checks_list = copy.deepcopy(raw_rules)

            with transaction.atomic():
                if checks_list:
                    # Upsert a UI guardrails policy for this org
                    policy, _ = (
                        AgentccGuardrailPolicy.no_workspace_objects.get_or_create(
                            organization=org,
                            name="__ui_guardrails__",
                            deleted=False,
                            defaults={
                                "checks": [],
                                "scope": AgentccGuardrailPolicy.SCOPE_GLOBAL,
                                "is_active": True,
                            },
                        )
                    )
                    policy_serializer = AgentccGuardrailPolicySerializer(
                        policy,
                        data={"checks": checks_list},
                        partial=True,
                        # The write serializer has checked this save's
                        # callback URLs against the active version.
                        context={
                            "check_env_references": guardrails_changed,
                            "check_callback_urls": False,
                        },
                    )
                    policy_serializer.is_valid(raise_exception=True)
                    policy_serializer.save()
                    policy_id = str(policy.id)

                    if source_format == "checks_dict":
                        sanitized = {}
                        for check in policy.checks:
                            check_copy = dict(check)
                            name = check_copy.pop("name", None)
                            if name:
                                sanitized[name] = {**check_copy, "_policy": policy_id}
                        data["guardrails"] = {**guardrails, "checks": sanitized}
                    else:
                        # rules list — inject _policy metadata into each rule
                        sanitized_rules = []
                        for check in policy.checks:
                            sanitized_rules.append({**check, "_policy": policy_id})
                        data["guardrails"] = {**guardrails, "rules": sanitized_rules}

                # Lock active config to prevent concurrent version creation
                active = (
                    AgentccOrgConfig.no_workspace_objects.select_for_update()
                    .filter(organization=org, is_active=True, deleted=False)
                    .first()
                )

                # Every infrastructure secret this version holds is stored
                # encrypted. The console posts each section it read
                # back, masks included: a blank or a read-back mask keeps the
                # active version's secret (never across an origin change), and
                # a new one, MCP ones included, is encrypted. Inside the
                # transaction, so a refusal also rolls back the policy upsert.
                encrypt_section_secrets(
                    {name: data[name] for name in SECRET_SECTIONS if name in data},
                    active,
                )
                # An alert channel URL sent as a read-back mask is checked
                # once resolved to the value it keeps (the write serializer
                # skips masks): only a URL this save introduces or changes,
                # read as its decrypted value.
                if "alerting" in data:
                    guardrail_urls.reject_changed_bad_alerting_urls(
                        data["alerting"], (active.alerting if active else None) or {}
                    )

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
                validate_org_config(config, check_guardrails=guardrails_changed)

            # Push to gateway
            synced = self._push_config_to_gateway(org.id, config)

            data = AgentccOrgConfigSerializer(config).data
            data["gateway_synced"] = synced
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except FeatureUnavailable:
            raise
        except GuardrailPolicyNotInOrg as e:
            log_policy_refused("org_config_create_rejected", org, e)
            return self._gm.bad_request(e)
        except OrgConfigSecretRejected as e:
            # The refusal's code (secret_not_kept, secret_origin_changed) goes
            # back to the client; the log names the field, never a value.
            logger.warning(
                "org_config_create_rejected",
                org_id=str(org.id) if org is not None else None,
                code=e.code,
                path=e.pointer,
            )
            return self._gm.bad_request(e)
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
            # Fail closed: activation refuses a version the gateway contract
            # does not accept or whose switched-on guardrail checks hold a
            # "${" where the gateway refuses one; the 400 names the field and
            # nothing is pushed.
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

            # The secret sections (cache, a2a, audit, alerting, mcp) are
            # masked on both sides before the diff, so a rotated secret shows
            # as a change (the masks differ) without revealing either value,
            # and an unchanged one is not a change.
            diff = {
                "from_version": other.version,
                "to_version": instance.version,
                "guardrails": _json_diff(other.guardrails, instance.guardrails),
                "routing": _json_diff(other.routing, instance.routing),
                "cache": _json_diff(
                    masked_section("cache", other.cache or {}),
                    masked_section("cache", instance.cache or {}),
                ),
                "rate_limiting": _json_diff(
                    other.rate_limiting or {}, instance.rate_limiting or {}
                ),
                "budgets": _json_diff(other.budgets or {}, instance.budgets or {}),
                "cost_tracking": _json_diff(
                    other.cost_tracking or {}, instance.cost_tracking or {}
                ),
                "ip_acl": _json_diff(other.ip_acl or {}, instance.ip_acl or {}),
                "alerting": _json_diff(
                    masked_section("alerting", other.alerting or {}),
                    masked_section("alerting", instance.alerting or {}),
                ),
                "privacy": _json_diff(other.privacy or {}, instance.privacy or {}),
                "tool_policy": _json_diff(
                    other.tool_policy or {}, instance.tool_policy or {}
                ),
                "mcp": _json_diff(
                    masked_section("mcp", other.mcp or {}),
                    masked_section("mcp", instance.mcp or {}),
                ),
                "a2a": _json_diff(
                    masked_section("a2a", other.a2a or {}),
                    masked_section("a2a", instance.a2a or {}),
                ),
                "audit": _json_diff(
                    masked_section("audit", other.audit or {}),
                    masked_section("audit", instance.audit or {}),
                ),
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
