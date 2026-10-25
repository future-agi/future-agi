import copy
from collections.abc import Mapping
from functools import partial

import structlog
from django.db import transaction
from django.http import Http404
from django.utils import timezone
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.viewsets import ModelViewSet

from agentcc.models import AgentccOrgConfig
from agentcc.models.guardrail_policy import AgentccGuardrailPolicy
from agentcc.permissions import GatewayAdminActionsMixin
from agentcc.serializers.guardrail_policy import AgentccGuardrailPolicySerializer
from agentcc.serializers.org_config import (
    AgentccOrgConfigSerializer,
    AgentccOrgConfigWriteSerializer,
)
from agentcc.services.config_push import OrgConfigRejected
from agentcc.services.guardrail_credentials import (
    CONSOLE_GUARDRAIL_POLICY,
    _prepare_guardrails,
    log_config_rejected,
)
from agentcc.services.org_config_writer import (
    CONFIG_SECTIONS,
    GATEWAY_SYNC_FAILED_MESSAGE,
    REPLACE_SECTIONS,
    activate_config_version,
    is_gateway_synced,
    lock_org,
    write_config_version,
)
from agentcc.services.routing_key_rules import with_rejection_param
from tfc.ee_gating import FeatureUnavailable
from tfc.utils.base_viewset import BaseModelViewSetMixinWithUserOrg
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


class AgentccOrgConfigViewSet(
    GatewayAdminActionsMixin, BaseModelViewSetMixinWithUserOrg, ModelViewSet
):
    """Per-org gateway configuration management. Configs are versioned and immutable."""

    permission_classes = [IsAuthenticated]
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
        """Create a new config version from the active one and activate it.

        Each section the payload names replaces the active one whole; an
        explicit `{}` or `null` clears it. Sections it leaves out, including
        `managed_models`, are copied from the active version.

        A guardrails section may name only policies the org may name (400
        `guardrail_policy_not_in_org`), and every clear-text credential in it
        is stored encrypted in the console policy, whichever shape it has.
        """
        try:
            named, cleared = _named_sections(request.data)
            org = getattr(request, "organization", None)
            # validate_mcp reads the org's active config (vs ids, stored
            # servers) from the org passed here, not from the request context
            # var; the writer reruns its rules under the org lock.
            write_serializer = AgentccOrgConfigWriteSerializer(
                data=_without_sections(request.data, cleared),
                context={"organization": org, "request": request},
            )
            if not write_serializer.is_valid():
                return self._gm.bad_request(write_serializer.errors)

            data = write_serializer.validated_data
            if org is None:
                return self._gm.bad_request("Organization context is required")

            # POST org-configs/ imports a whole version; an MCP server secret
            # belongs in the MCP credential path (update-mcp-server), not here.
            # A new clear MCP secret is refused; a kept one
            # (blank or the read-back mask) and every other write path encrypt
            # it. Runs only on this endpoint, so the serializer and the
            # contract tests that call it directly are unaffected.
            if "mcp" in data:
                from agentcc.services.org_config_secrets import (
                    SecretInConfig,
                    new_clear_mcp_secret_paths,
                )

                active_mcp = (
                    AgentccOrgConfig.no_workspace_objects.filter(
                        organization=org, is_active=True, deleted=False
                    )
                    .values_list("mcp", flat=True)
                    .first()
                )
                new_secrets = new_clear_mcp_secret_paths(data["mcp"], active_mcp)
                if new_secrets:
                    raise SecretInConfig(new_secrets)

            if data.get("privacy"):
                from tfc.ee_gating import EEFeature, check_ee_feature

                check_ee_feature(EEFeature.DATA_MASKING, org_id=str(org.id))

            # Route guardrail credentials through AgentccGuardrailPolicy for encryption
            guardrails = data.get("guardrails", {})
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
                # The writer's org lock comes before the policy upsert's row
                # lock, as in every config write, so this cannot deadlock
                # with update-config, which locks the org and then the policy.
                lock_org(org.pk)
                if checks_list:
                    # Upsert a UI guardrails policy for this org
                    policy, _ = (
                        AgentccGuardrailPolicy.no_workspace_objects.get_or_create(
                            organization=org,
                            name=CONSOLE_GUARDRAIL_POLICY,
                            deleted=False,
                            defaults={
                                "checks": [],
                                "scope": AgentccGuardrailPolicy.SCOPE_GLOBAL,
                                "is_active": True,
                            },
                        )
                    )
                    policy_serializer = AgentccGuardrailPolicySerializer(
                        policy, data={"checks": checks_list}, partial=True
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

                patch = {
                    section: None if section in cleared else data[section]
                    for section in named
                }
                # The parts of the section the upsert above leaves as sent
                # (rules beside a checks map, a checks list) go through the
                # same checks as update-config; the ids are checked as sent.
                prepare = (
                    partial(_prepare_guardrails, org, guardrails)
                    if "guardrails" in patch and patch["guardrails"] is not None
                    else None
                )
                # A version the gateway contract rejects raises here, which
                # also rolls back the guardrail policy upsert above.
                config, warnings = write_config_version(
                    org,
                    patch,
                    mode=REPLACE_SECTIONS,
                    description=data.get("change_description", ""),
                    user=request.user,
                    push=lambda saved: self._push_config_to_gateway(org.id, saved),
                    prepare=prepare,
                )

            return self._gm.success_response(_with_sync_status(config, warnings))
        except FeatureUnavailable:
            raise
        except OrgConfigRejected as e:
            log_config_rejected("org_config_create_rejected", org, e)
            return with_rejection_param(self._gm.bad_request(e), e)
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
            with transaction.atomic():
                # Under the org lock an activation of this version cannot be
                # in flight, so the active flag read here is current.
                lock_org(instance.organization_id)
                instance = (
                    AgentccOrgConfig.no_workspace_objects.select_for_update()
                    .filter(pk=instance.pk, deleted=False)
                    .first()
                )
                if instance is None:
                    return self._gm.not_found("Org config not found")
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
        """Roll back to a specific config version by activating it.

        `warnings` carries `managed_models_change` when the rollback turns
        managed models on or off.
        """
        try:
            instance = self.get_object()
            if instance.is_active:
                data = AgentccOrgConfigSerializer(instance).data
                data["warnings"] = []
                return self._gm.success_response(data)

            # The writer refuses to activate a version holding keys the gateway
            # contract does not accept.
            config, warnings = activate_config_version(
                instance,
                push=lambda saved: self._push_config_to_gateway(
                    saved.organization_id, saved
                ),
            )
            return self._gm.success_response(_with_sync_status(config, warnings))
        except Http404:
            return self._gm.not_found("Org config not found")
        except OrgConfigRejected as e:
            # A version the contract does not accept, or one that would
            # switch on a check holding a "${" reference
            # (guardrail_env_reference, with param naming the field).
            log_config_rejected(
                "org_config_activate_rejected", instance.organization, e
            )
            return with_rejection_param(self._gm.bad_request(e), e)
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

            from agentcc.services.org_config_secrets import masked_section

            diff = {
                "from_version": other.version,
                "to_version": instance.version,
                # Secret sections are masked on both sides before the diff, so
                # a rotated secret shows as a change (the masks differ) without
                # revealing either value, and an unchanged one is not a change.
                **{
                    section: _json_diff(
                        masked_section(section, getattr(other, section) or {}),
                        masked_section(section, getattr(instance, section) or {}),
                    )
                    for section in CONFIG_SECTIONS
                },
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


def _named_sections(raw):
    """The config sections a POST names, and those it sets to null.

    Read from the raw payload: the write serializer fills a default for every
    absent section, so its validated data cannot tell absent from empty.
    """
    if not isinstance(raw, Mapping):
        return [], set()
    named = [section for section in CONFIG_SECTIONS if section in raw]
    return named, {section for section in named if raw[section] is None}


def _without_sections(raw, sections):
    # The write serializer rejects null, so null (clear) sections skip it.
    if not sections:
        return raw
    return {key: value for key, value in raw.items() if key not in sections}


def _with_sync_status(config, warnings):
    data = AgentccOrgConfigSerializer(config).data
    data["gateway_synced"] = is_gateway_synced(warnings)
    if not data["gateway_synced"]:
        data["gateway_warning"] = GATEWAY_SYNC_FAILED_MESSAGE
    data["warnings"] = warnings
    return data


def _json_diff(old, new):
    """Simple diff between two JSON-serializable dicts."""
    added = {k: v for k, v in new.items() if k not in old}
    removed = {k: v for k, v in old.items() if k not in new}
    changed = {}
    for k in set(old) & set(new):
        if old[k] != new[k]:
            changed[k] = {"old": old[k], "new": new[k]}
    return {"added": added, "removed": removed, "changed": changed}
