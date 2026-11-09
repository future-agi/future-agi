import datetime
import json
import os
import re
from functools import partial

import structlog
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.cache import cache
from django.db import transaction
from django.db.models import Avg, Count, Q
from django.utils import timezone
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.status import HTTP_404_NOT_FOUND, HTTP_502_BAD_GATEWAY
from rest_framework.viewsets import ViewSet

from agentcc import mcp_features
from agentcc.gateway_configuration import hosted_gateway_public_url_missing
from agentcc.models.org_config import AgentccOrgConfig
from agentcc.models.provider_credential import AgentccProviderCredential
from agentcc.models.request_log import AgentccRequestLog
from agentcc.org_config_defaults import (
    default_cost_tracking_config,
    normalize_cost_tracking_config,
)
from agentcc.permissions import GatewayAdminActionsMixin, is_gateway_admin
from agentcc.serializers.contracts import (
    AgentccEmptyRequestSerializer,
    AgentccErrorResponseSerializer,
    AgentccListResultResponseSerializer,
    CapPolicyRevisionConflictSerializer,
    GatewayBatchCancelResponseSerializer,
    GatewayBatchDetailResponseSerializer,
    GatewayBatchRequestSerializer,
    GatewayBatchSubmitRequestSerializer,
    GatewayBatchSubmitResponseSerializer,
    GatewayBudgetRemoveRequestSerializer,
    GatewayBudgetSetRequestSerializer,
    GatewayConfigMutationResponseSerializer,
    GatewayConfigPatchRequestSerializer,
    GatewayConfigResponseSerializer,
    GatewayDetailResponseSerializer,
    GatewayHealthResponseSerializer,
    GatewayListResponseSerializer,
    GatewayMCPGuardrailsUpdateRequestSerializer,
    GatewayMCPServerRemoveRequestSerializer,
    GatewayMCPServerUpdateRequestSerializer,
    GatewayMCPStatusResponseSerializer,
    GatewayMCPToolTestRequestSerializer,
    GatewayMCPToolTestResponseSerializer,
    GatewayMutationResponseSerializer,
    GatewayNamedConfigRequestSerializer,
    GatewayNameRequestSerializer,
    GatewayPlaygroundTestRequestSerializer,
    GatewayPlaygroundTestResponseSerializer,
    GatewayProvidersResponseSerializer,
    GatewayProviderUpdateRequestSerializer,
    GatewayToggleGuardrailRequestSerializer,
)
from agentcc.serializers.mcp_served import (
    MCPServedQuerySerializer,
    MCPServedResponseSerializer,
)
from agentcc.serializers.provider_credential import (
    unsupported_credential_keys,
    unsupported_credential_keys_message,
)
from agentcc.services import mcp_admin, mcp_credentials
from agentcc.services.cap_revision import (
    CAP_FIELDS,
    CAP_LEVEL_REJECTION,
    CapPolicyRevisionConflict,
)
from agentcc.services.config_push import (
    _RULE_TO_REGISTRY,
    MCPCredentialPlacementRefused,
    OrgConfigRejected,
    push_all_org_configs,
    push_org_config,
)
from agentcc.services.delegated_availability import (
    delegated_enabled,
    unavailable_response,
)
from agentcc.services.delegated_calls import for_chat
from agentcc.services.gateway_client import (
    AGENTCC_GATEWAY_URL,
    PLAYGROUND_PARAMS,
    GatewayClientError,
    get_gateway_client,
)
from agentcc.services.guardrail_credentials import (
    _encrypt_new_guardrail_credentials,
    _prepare_guardrails,
    _reject_unowned_guardrail_policies,
    log_config_rejected,
)
from agentcc.services.guardrail_env_refs import reject_rule_env_references
from agentcc.services.guardrail_names import unique_check_index
from agentcc.services.guardrail_policy_checks import (
    edit_in_checks_list,
    switch_in_checks_list,
)
from agentcc.services.managed_models_default import initial_managed_models
from agentcc.services.org_config_writer import (
    GATEWAY_SYNC_FAILED,
    GATEWAY_SYNC_FAILED_MESSAGE,
    MERGE,
    is_gateway_synced,
    lock_org,
    write_config_version,
    write_config_version_with,
)
from agentcc.services.playground_outcome import playground_outcome
from agentcc.services.playground_stream import playground_events
from agentcc.services.provider_label import MANAGED
from agentcc.services.provider_validation import ensure_provider_configuration
from agentcc.services.routing_key_rules import with_rejection_param
from agentcc.services.url_safety import ensure_provider_base_url_allowed
from agentcc.views.event_stream import EventStreamResponse
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)

_GATEWAY_SYNC_WARNING = GATEWAY_SYNC_FAILED_MESSAGE


def _prepare_vertex_provider_config(provider_config):
    """Build a Vertex endpoint from validated GCP fields, never from a supplied URL."""
    config = dict(provider_config)
    raw_key = config.get("service_account_json")
    if raw_key is not None:
        if not isinstance(raw_key, str) or len(raw_key) > 65536:
            raise ValueError("Paste valid Google service-account JSON (64 KB maximum).")
        try:
            key = json.loads(raw_key)
        except (TypeError, ValueError) as exc:
            raise ValueError("Paste valid Google service-account JSON.") from exc
        if (
            not isinstance(key, dict)
            or key.get("type") != "service_account"
            or not all(
                isinstance(key.get(field), str) and key[field]
                for field in ("client_email", "private_key", "project_id")
            )
            or key.get("token_uri") != "https://oauth2.googleapis.com/token"
        ):
            raise ValueError(
                "The JSON must be a Google service-account key with a project ID."
            )
        try:
            private_key = serialization.load_pem_private_key(
                key["private_key"].encode(), password=None
            )
        except (TypeError, ValueError, UnsupportedAlgorithm) as exc:
            raise ValueError("The service-account private key is invalid.") from exc
        if not isinstance(private_key, rsa.RSAPrivateKey):
            raise ValueError("The service-account private key must be RSA.")
        config["service_account_json"] = json.dumps(key, separators=(",", ":"))

    project = str(config.pop("gcp_project", "") or "").strip()
    location = str(config.pop("gcp_location", "") or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,62}", project):
        raise ValueError("Enter a valid Google Cloud project ID.")
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,62}", location):
        raise ValueError("Enter a valid Vertex AI location.")
    host = (
        "aiplatform.googleapis.com"
        if location == "global"
        else f"{location}-aiplatform.googleapis.com"
    )
    config["base_url"] = (
        f"https://{host}/v1beta1/projects/{project}/locations/{location}"
    )
    config["api_format"] = "gemini"
    config.pop("api_key", None)
    return config


# Public-facing URL shown to users in the dashboard (e.g. https://gateway.futureagi.com).
# Only local/self-hosted installations retain the legacy fallback when unset.
AGENTCC_GATEWAY_PUBLIC_URL = (
    ""
    if hosted_gateway_public_url_missing()
    else os.environ.get("AGENTCC_GATEWAY_PUBLIC_URL", "") or AGENTCC_GATEWAY_URL
)

GATEWAY_BAD_REQUEST_RESPONSES = {
    400: AgentccErrorResponseSerializer,
}
# test-playground-stream answers text/event-stream (services/playground_stream.py
# lists the events); the schema says only that the body is text.
PLAYGROUND_STREAM_RESPONSE = openapi.Response(
    "Server-sent events: meta, delta..., done; or one result (a refusal); or error.",
    schema=openapi.Schema(type=openapi.TYPE_STRING),
)
GATEWAY_NOT_FOUND_RESPONSES = {
    404: AgentccErrorResponseSerializer,
}
GATEWAY_BAD_REQUEST_OR_NOT_FOUND_RESPONSES = {
    **GATEWAY_BAD_REQUEST_RESPONSES,
    **GATEWAY_NOT_FOUND_RESPONSES,
}

_BUDGET_LEVEL_KEY_ALIASES = {
    "admissionCapsEnabled": "admission_caps_enabled",
    "keyIds": "key_ids",
    "policyRevision": "policy_revision",
    "orgLimit": "org_limit",
    "hardLimit": "hard_limit",
    "perKey": "per_key",
    "perUser": "per_user",
    "perModel": "per_model",
}


def _normalize_budget_level_key(level):
    if not isinstance(level, str):
        return level
    return _BUDGET_LEVEL_KEY_ALIASES.get(level, level)


def _set_budget_level_config(budgets, level, budget_config):
    if not isinstance(budgets, dict):
        budgets = {}
    else:
        budgets = {**budgets}

    normalized_level = _normalize_budget_level_key(level)
    if normalized_level != level:
        budgets.pop(level, None)
    budgets[normalized_level] = budget_config
    return budgets


def _remove_budget_level_config(budgets, level):
    if not isinstance(budgets, dict):
        return budgets

    updated = {**budgets}
    normalized_level = _normalize_budget_level_key(level)
    updated.pop(level, None)
    updated.pop(normalized_level, None)
    return updated


# The keys the gateway contract takes on a check of a checks map
# (agentcc/contracts/gateway_admin.py GuardrailCheck, extra="forbid").
_CHECK_KEYS = ("enabled", "action", "confidence_threshold", "config")


def _stored_checks_map(guardrails):
    """The section's checks map when the section is stored as a non-empty
    one, else None. The config push forwards such a map to the gateway as
    stored, so the gateway contract refuses any `rules` beside it: a toggle
    or an edit of such a section writes the map."""
    checks = guardrails.get("checks") if isinstance(guardrails, dict) else None
    return checks if isinstance(checks, dict) and checks else None


def _checks_map_key(checks, name):
    """The key of the map's check that `name` names: the key itself, the
    catalog name a rule name maps to, a rule name that maps to the key, or a
    key whose check keeps `name` as its `_originalName`. None when the map
    has no such check."""
    if name in checks:
        return name
    registry = _RULE_TO_REGISTRY.get(name)
    if registry in checks:
        return registry
    for key, check in checks.items():
        if _RULE_TO_REGISTRY.get(key) == name or (
            isinstance(check, dict) and check.get("_originalName") == name
        ):
            return key
    return None


def _as_checks_map_entry(stored, sent):
    """A check of a checks map after an edit: the stored check, with every key
    it is stored with kept, and the contract's keys the edit sent. A rule's
    `threshold` is the map's `confidence_threshold`, and it wins over a
    `confidence_threshold` sent beside it: the rules-style edit dialog sends
    the check as listed, stored threshold included, with its edit as
    `threshold`. A camelCase `confidenceThreshold` counts only when neither is
    sent. A rule's other keys (its name, stage or execution mode) are not the
    contract's and are dropped."""
    entry = dict(stored) if isinstance(stored, dict) else {}
    sent = dict(sent)
    for alias in ("threshold", "confidence_threshold", "confidenceThreshold"):
        if alias in sent:
            sent["confidence_threshold"] = sent[alias]
            break
    for key in _CHECK_KEYS:
        if key in sent:
            entry[key] = sent[key]
    return entry


def _build_virtual_gateway(status="healthy", health=None):
    """Build the virtual gateway dict returned by list/retrieve."""
    data = {
        "id": "default",
        "name": "Agent Command Center Gateway",
        "base_url": AGENTCC_GATEWAY_PUBLIC_URL.rstrip("/") + "/v1",
        "status": status,
    }
    if health:
        # Don't let the gateway's raw status (e.g. "ok") overwrite ours
        health.pop("status", None)
        data.update(health)
    if hosted_gateway_public_url_missing():
        # Internal reachability cannot supply or substitute a public endpoint.
        data["base_url"] = ""
        data["status"] = "unconfigured"
    return data


# NG-30: when the org last ran a health check, and what it found. Kept per org:
# the check is an org member's click, and another org's clicks are not theirs.
_LAST_HEALTH_CHECK_KEY = "agentcc:gateway_last_health_check:{org_id}"
# The health check's fleet re-push (every org's config) runs at most once
# per 5 minutes, and only for an Owner or Admin of the org.
_REPUSH_THROTTLE_KEY = "agentcc:gateway_config_synced"
_REPUSH_THROTTLE_SECONDS = 300


def _record_health_check(org, outcome):
    if org is None:
        return
    cache.set(
        _LAST_HEALTH_CHECK_KEY.format(org_id=org.id),
        {"at": timezone.now().isoformat(), "status": outcome},
        timeout=None,
    )


def _with_last_health_check(data, org):
    """Adds last_health_check (ISO 8601, or None when no check is recorded)
    and last_health_check_status (healthy or unreachable) to `data`."""
    record = cache.get(_LAST_HEALTH_CHECK_KEY.format(org_id=org.id)) if org else None
    if not isinstance(record, dict):
        record = {}
    data["last_health_check"] = record.get("at")
    data["last_health_check_status"] = record.get("status")
    return data


# NG-30 (K8) and K2: the blocks of the gateway's GET /-/config the console
# shows, passed through as the gateway reports them. Anything else /-/config
# returns stays out; a block the gateway does not report stays absent, so the
# console says "not reported" rather than showing a default.
_GATEWAY_CONFIG_BLOCKS = {
    "server": lambda v: isinstance(v, dict),
    "logging": lambda v: isinstance(v, dict),
    "batch": lambda v: isinstance(v, dict),
    "org_sections_enforced": lambda v: (
        isinstance(v, list) and all(isinstance(s, str) for s in v)
    ),
    # How the node runs an org's guardrail checks (enabled, fail_open, mode);
    # the console shows it in place of an org's own Fail Open and Mode.
    "guardrail_pipeline": lambda v: (
        isinstance(v, dict)
        and isinstance(v.get("enabled"), bool)
        and isinstance(v.get("fail_open"), bool)
        and isinstance(v.get("mode"), str)
    ),
    # section -> why this node does not enforce it (ip_acl without a trusted
    # proxy: "client IP unknown (trusted_proxies not set)").
    "org_sections_not_enforced": lambda v: (
        isinstance(v, dict)
        and all(isinstance(k, str) and isinstance(r, str) for k, r in v.items())
    ),
}


def _gateway_config_blocks(client):
    try:
        reported = client.get_config()
    except (GatewayClientError, ValueError) as e:
        logger.warning("gateway_config_read_failed", error=str(e))
        return {}
    if not isinstance(reported, dict):
        return {}
    return {
        key: reported[key]
        for key, valid in _GATEWAY_CONFIG_BLOCKS.items()
        if key in reported and valid(reported[key])
    }


# NG-31 (contract K6): an org provider's health, from the org's own request
# logs of the last 24 hours. The gateway's /-/health/providers is the
# operator's monitor, keyed by the bare provider id, so it is never read for
# an org provider.
_HEALTH_WINDOW = datetime.timedelta(hours=24)
_HEALTH_MIN_REQUESTS = 5
_UNHEALTHY_ERROR_RATE = 50.0
_DEGRADED_ERROR_RATE = 10.0


def _org_provider_stats(org):
    """Per provider name: requests, average latency and errors in the org's
    logs of the last 24 h. Neither counts, in any of the three: a row the
    operator's managed models served (not the org's provider), and a
    guardrail block (403 with a triggered check: the org's own policy, which
    says nothing of the provider). A managed row is left out by its
    credential type and by its billing mode, so a managed call on a provider
    id the org also holds a key for (an own OpenRouter key) never counts on
    the key's row. Excluded rather than byok required: a call recorded
    before billing modes has none and stays on its row."""
    rows = (
        AgentccRequestLog.no_workspace_objects.filter(
            organization=org,
            started_at__gte=timezone.now() - _HEALTH_WINDOW,
        )
        .exclude(credential_type="managed")
        .exclude(billing_mode=MANAGED)
        .exclude(guardrail_triggered=True, status_code=403)
        .values("provider")
        .annotate(
            request_count=Count("id"),
            avg_latency_ms=Avg("latency_ms"),
            error_count=Count("id", filter=Q(is_error=True)),
        )
    )
    return {
        row["provider"]: {
            "request_count": row["request_count"],
            "error_count": row["error_count"],
            "avg_latency_ms": round(row["avg_latency_ms"] or 0, 2),
        }
        for row in rows
    }


def _health_from_logs(request_count, error_count):
    """(status, status_reason) by K6's thresholds."""
    if request_count < _HEALTH_MIN_REQUESTS:
        return "healthy", f"fewer than {_HEALTH_MIN_REQUESTS} requests in 24 h"
    rate = error_count / request_count * 100
    failed = f"{rate:.1f}% of {request_count} requests failed in 24 h"
    if rate >= _UNHEALTHY_ERROR_RATE:
        return "unhealthy", f"{failed} (unhealthy at 50% or more)"
    if rate >= _DEGRADED_ERROR_RATE:
        return "degraded", f"{failed} (degraded at 10% or more)"
    return "healthy", f"{failed} (healthy under 10%)"


def _not_pushed_reason(credential):
    """Why the config push leaves this provider out (NG-58), or None. The
    push skips a credential it cannot decrypt or one with a key outside the
    provider contract (config_push._assemble_providers)."""
    from integrations.services.credentials import CredentialManager

    try:
        decrypted = CredentialManager.decrypt(bytes(credential.encrypted_credentials))
    except Exception:
        return "not pushed: the stored credential cannot be decrypted"
    if not isinstance(decrypted, dict):
        return "not pushed: the stored credential cannot be decrypted"
    keys = unsupported_credential_keys(
        credential.provider_name, decrypted, for_save=False
    )
    if keys:
        noun = "key" if len(keys) == 1 else "keys"
        return f"not pushed: unsupported credential {noun} {', '.join(keys)}"
    return None


class AgentccGatewayViewSet(GatewayAdminActionsMixin, ViewSet):
    """
    Stateless proxy to the Go gateway (configured via env vars).
    No DB model — returns a virtual singleton gateway with live health.
    """

    # Keep action contracts explicit: each endpoint owns its success serializer,
    # while shared error serializers live at module level. If this file grows
    # further, split by gateway surface instead of hiding contracts in factories.
    permission_classes = [IsAuthenticated]
    # Every action that changes what the gateway routes, spends or enforces.
    # A console batch spends the org's credits on its providers, so submitting
    # or cancelling one is an admin's; reading a batch stays open to members.
    gateway_admin_actions = frozenset(
        {
            "reload",
            "update_config",
            "update_provider",
            "remove_provider",
            "toggle_guardrail",
            "update_guardrail",
            "test_playground",
            "test_playground_stream",
            "set_budget",
            "remove_budget",
            "update_mcp_server",
            "remove_mcp_server",
            "update_mcp_guardrails",
            "test_mcp_tool",
            "mcp_served_tools",
            "submit_batch",
        }
    )
    _gm = GeneralMethods()

    def _current_org(self, request):
        return getattr(request, "organization", None)

    def _current_org_id(self, request):
        organization = self._current_org(request)
        return str(organization.id) if organization else ""

    # ------------------------------------------------------------------
    # list / retrieve — virtual gateway with live health
    # ------------------------------------------------------------------

    @swagger_auto_schema(
        responses={
            200: GatewayListResponseSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        }
    )
    def list(self, request, *args, **kwargs):
        try:
            status = "healthy"
            health = {}
            try:
                client = get_gateway_client()
                health = client.health_check()
            except GatewayClientError as e:
                if e.status_code is not None:
                    # An HTTP response is not an outage. Keep its body private
                    # when passing it to this endpoint's existing error path.
                    raise GatewayClientError(
                        f"Gateway returned {e.status_code}", status_code=e.status_code
                    ) from None
                status = "unreachable"

            data = _build_virtual_gateway(status=status, health=health)

            org = self._current_org(request)
            if org:
                self._enrich_with_org_provider_counts(data, org)
            _with_last_health_check(data, org)
            return self._gm.success_response([data])
        except Exception as e:
            logger.exception("gateway_list_error", error=str(e))
            return self._gm.bad_request(str(e))

    @swagger_auto_schema(
        responses={
            200: GatewayDetailResponseSerializer,
            **GATEWAY_NOT_FOUND_RESPONSES,
        }
    )
    def retrieve(self, request, *args, **kwargs):
        """Accept any pk and ignore it — there is only one gateway."""
        try:
            status = "healthy"
            health = {}
            try:
                client = get_gateway_client()
                health = client.health_check()
            except GatewayClientError as e:
                if e.status_code is not None:
                    # An HTTP response is not an outage. Keep its body private
                    # when passing it to this endpoint's existing error path.
                    raise GatewayClientError(
                        f"Gateway returned {e.status_code}", status_code=e.status_code
                    ) from None
                status = "unreachable"

            data = _build_virtual_gateway(status=status, health=health)

            org = self._current_org(request)
            if org:
                self._enrich_with_org_provider_counts(data, org)
            _with_last_health_check(data, org)
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("gateway_retrieve_error", error=str(e))
            return self._gm.not_found("Gateway not found")

    def _enrich_with_org_provider_counts(self, data, org):
        providers = AgentccProviderCredential.no_workspace_objects.filter(
            organization=org,
            is_active=True,
            deleted=False,
        )
        provider_count = providers.count()
        model_count = sum(len(p.models_list or []) for p in providers)
        if isinstance(data, list):
            for item in data:
                item["provider_count"] = provider_count
                item["model_count"] = model_count
        else:
            data["provider_count"] = provider_count
            data["model_count"] = model_count
        return data

    # ------------------------------------------------------------------
    # health_check
    # ------------------------------------------------------------------

    @validated_request(
        request_serializer=AgentccEmptyRequestSerializer,
        responses={
            200: GatewayHealthResponseSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def health_check(self, request, pk=None):
        try:
            client = get_gateway_client()
            org = self._current_org(request)

            try:
                health = client.health_check()

                db_providers = AgentccProviderCredential.no_workspace_objects.filter(
                    organization=org, is_active=True, deleted=False
                )
                provider_health = {
                    "providers": [
                        {
                            "name": p.provider_name,
                            "display_name": p.display_name or p.provider_name,
                            "models": p.models_list or [],
                            "status": "configured",
                        }
                        for p in db_providers
                    ]
                }

                providers = provider_health.get("providers", [])
                p_count = len(providers)
                m_count = sum(
                    len(p.get("models", [])) for p in providers if isinstance(p, dict)
                )

                # Every member may check health; only an Owner or Admin's
                # check re-pushes every org's config (e.g. after a gateway
                # restart), at most once per 5 minutes.
                org_configs_pushed = (
                    self._repush_org_configs() if is_gateway_admin(request) else False
                )
                _record_health_check(org, "healthy")

                data = {
                    "status": "healthy",
                    "health": health,
                    "providers": provider_health,
                    "provider_count": p_count,
                    "model_count": m_count,
                    "org_configs_pushed": org_configs_pushed,
                }
                _with_last_health_check(data, org)
                return self._gm.success_response(data)
            except GatewayClientError as e:
                _record_health_check(org, "unreachable")
                return self._gm.bad_request(
                    {
                        "status": "unreachable",
                        "error": str(e),
                    }
                )
        except Exception as e:
            logger.exception("health_check_error", error=str(e))
            return self._gm.bad_request(str(e))

    def _repush_org_configs(self):
        """Push every org's active config to the gateway, unless that ran in
        the last 5 minutes. True when this call pushed."""
        if cache.get(_REPUSH_THROTTLE_KEY):
            return False
        try:
            push_all_org_configs()
        except Exception:
            logger.warning("gateway_auto_sync_failed", exc_info=True)
            return False
        cache.set(_REPUSH_THROTTLE_KEY, True, _REPUSH_THROTTLE_SECONDS)
        logger.info("gateway_auto_sync", reason="health_check")
        return True

    # ------------------------------------------------------------------
    # config / reload / update-config
    # ------------------------------------------------------------------

    @swagger_auto_schema(
        responses={
            200: GatewayConfigResponseSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        }
    )
    @action(detail=True, methods=["get"])
    def config(self, request, pk=None):
        try:
            org = self._current_org(request)

            org_config = AgentccOrgConfig.no_workspace_objects.filter(
                organization=org, is_active=True, deleted=False
            ).first()
            org_data = {"cost_tracking": default_cost_tracking_config()}
            if org_config:
                from agentcc.serializers.org_config import AgentccOrgConfigSerializer

                org_data = AgentccOrgConfigSerializer(org_config).data

            providers = list(
                AgentccProviderCredential.no_workspace_objects.filter(
                    organization=org,
                    deleted=False,
                    is_active=True,
                ).values(
                    "id",
                    "provider_name",
                    "display_name",
                    "base_url",
                    "api_format",
                    "models_list",
                    "extra_config",
                    "is_active",
                    "default_timeout_seconds",
                    "max_concurrent",
                    "conn_pool_size",
                    "created_at",
                )
            )
            providers_map = {}
            for p in providers:
                extra_config = p.get("extra_config") or {}
                providers_map[p["provider_name"]] = {
                    "id": str(p["id"]),
                    "name": p["provider_name"],
                    "display_name": p["display_name"] or p["provider_name"],
                    "base_url": p["base_url"],
                    "api_format": p["api_format"],
                    "api_path_prefix": extra_config.get("api_path_prefix", "/v1"),
                    "models": p["models_list"] or [],
                    "is_active": p["is_active"],
                    "default_timeout": p["default_timeout_seconds"],
                    "max_concurrent": p["max_concurrent"],
                    "conn_pool_size": p["conn_pool_size"],
                }

            gateway_status = "unreachable"
            gateway_blocks = {}
            try:
                client = get_gateway_client()
                client.health_check()
                gateway_status = "healthy"
                gateway_blocks = _gateway_config_blocks(client)
            except GatewayClientError as e:
                if e.status_code is not None:
                    raise GatewayClientError(
                        f"Gateway returned {e.status_code}", status_code=e.status_code
                    ) from None

            result = {**org_data, **gateway_blocks}
            result["providers"] = providers_map
            result["gateway"] = {"status": gateway_status}
            _with_last_health_check(result, org)
            return self._gm.success_response(result)
        except Exception as e:
            logger.exception("config_error", error=str(e))
            return self._gm.bad_request(str(e))

    @validated_request(
        request_serializer=AgentccEmptyRequestSerializer,
        responses={
            200: GatewayMutationResponseSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"])
    def reload(self, request, pk=None):
        """Re-push this org's config to the gateway."""
        try:
            org = self._current_org(request)
            synced = self._push_current_config(org)
            data = {"status": "ok", "gateway_synced": synced}
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("reload_error", error=str(e))
            return self._gm.bad_request(str(e))

    @validated_request(
        request_serializer=GatewayConfigPatchRequestSerializer,
        responses={
            200: GatewayConfigMutationResponseSerializer,
            409: CapPolicyRevisionConflictSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="update-config")
    def update_config(self, request, pk=None):
        """Patch the org's active config and push it.

        Cap edits require budgets.policy_revision from the last config read.
        Omit it or send 0 only when no policy has ever been saved. Disabling
        keeps a positive revision; it does not reset the precondition. Cap
        maps, admission_caps_enabled and an enabled policy's prerequisites
        are checked atomically under the org lock before changing any config.
        A stale revision (including on enable) returns 409 with only code
        cap_policy_revision_conflict and the current policy_revision. Refresh
        the policy before retrying; do not blindly overwrite the winning edit.
        A current enable still requires budgets.enabled=true, fail_mode=closed
        and no_max_tokens=clamp, plus a valid complete cap envelope (400).
        While any cap policy exists (even disabled), routing.agents.key_bindings
        and routing.agents.max_run_usd edits or clears also require the revision.
        Unrelated patches need no revision, but every config write after the
        first policy advances it. The 200 result.policy_revision is the saved
        revision (0 before any policy), ready for the next edit without a read.
        Rollbacks advance the revision without resetting activation or spend.
        """
        try:
            org = self._current_org(request)
            config_patch = request.validated_data
            if not config_patch or not isinstance(config_patch, dict):
                return self._gm.bad_request("Config patch must be a JSON object")

            prepare = None
            if "guardrails" in config_patch:
                prepare = partial(_prepare_guardrails, org, config_patch["guardrails"])
            new_config, warnings = write_config_version(
                org,
                config_patch,
                mode=MERGE,
                description="Config update",
                user=request.user,
                push=partial(self._push_current_config, org),
                prepare=prepare,
            )
            synced = is_gateway_synced(warnings)
            data = {
                "version": new_config.version,
                "policy_revision": new_config.budgets.get("policy_revision", 0),
                "gateway_synced": synced,
                # The writer's warnings (managed-models change, routing keys
                # the gateway does not run); a failed sync is reported in
                # gateway_synced and gateway_warning instead.
                "warnings": [
                    w for w in warnings if w.get("code") != GATEWAY_SYNC_FAILED
                ],
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except CapPolicyRevisionConflict as e:
            return e.response()
        except OrgConfigRejected as e:
            # The exception's own code (e.g. secret_in_config) goes back to
            # the client; a rejection without one keeps the generic 400 code.
            log_config_rejected("update_config_rejected", org, e)
            return with_rejection_param(self._gm.bad_request(e), e)
        except Exception as e:
            logger.exception("update_config_error", error=str(e))
            return self._gm.bad_request(str(e))

    # ------------------------------------------------------------------
    # update-provider / remove-provider
    # ------------------------------------------------------------------

    @validated_request(
        request_serializer=GatewayProviderUpdateRequestSerializer,
        responses={
            200: GatewayMutationResponseSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="update-provider")
    def update_provider(self, request, pk=None):
        """Add or update a provider credential for the org, then push config."""
        try:
            org = self._current_org(request)
            provider_name = request.validated_data.get("name")
            provider_config = request.validated_data.get("config")
            if not provider_name or not provider_config:
                return self._gm.bad_request("name and config are required")
            if provider_name == "vertex":
                provider_config = _prepare_vertex_provider_config(provider_config)
            elif "service_account_json" in provider_config:
                return self._gm.bad_request(
                    "Google service-account credentials can only be used with Vertex AI."
                )
            saved_base_url = (
                AgentccProviderCredential.no_workspace_objects.filter(
                    organization=org, provider_name=provider_name, deleted=False
                )
                .values_list("base_url", flat=True)
                .first()
            )
            try:
                ensure_provider_configuration(
                    provider_name,
                    provider_config.get("api_format", "openai"),
                    provider_config.get("base_url", ""),
                )
                ensure_provider_base_url_allowed(
                    provider_config.get("base_url", ""), saved_base_url=saved_base_url
                )
            except ValueError as e:
                return self._gm.bad_request(str(e))

            from integrations.services.credentials import CredentialManager

            api_key = provider_config.pop("api_key", None)
            credential_fields = (
                "api_secret",
                "access_key",
                "secret_key",
                "region",
                "aws_access_key_id",
                "aws_secret_access_key",
                "aws_region",
                "aws_session_token",
                "service_account_json",
            )
            new_cred_values = {
                k: provider_config.pop(k)
                for k in list(provider_config)
                if k in credential_fields
            }
            if api_key is not None:
                new_cred_values["api_key"] = api_key
            # NG-58: a key the provider contract does not take would make the
            # config push leave the provider out; refuse it, naming the key.
            unsupported = unsupported_credential_keys(provider_name, new_cred_values)
            if unsupported:
                return self._gm.bad_request(
                    unsupported_credential_keys_message(provider_name, unsupported)
                )

            defaults = {
                "base_url": provider_config.get("base_url", ""),
                "api_format": provider_config.get("api_format", "openai"),
                "models_list": provider_config.get("models", []),
                "default_timeout_seconds": provider_config.get(
                    "default_timeout_seconds",
                    provider_config.get("default_timeout", 60),
                ),
                "max_concurrent": provider_config.get("max_concurrent", 100),
                "conn_pool_size": provider_config.get("conn_pool_size", 100),
                "extra_config": {
                    k: v
                    for k, v in provider_config.items()
                    if k
                    not in (
                        "api_key",
                        "api_secret",
                        "access_key",
                        "secret_key",
                        "region",
                        "aws_access_key_id",
                        "aws_secret_access_key",
                        "aws_region",
                        "aws_session_token",
                        "service_account_json",
                        "base_url",
                        "api_format",
                        "models",
                        "display_name",
                        "default_timeout",
                        "default_timeout_seconds",
                        "max_concurrent",
                        "conn_pool_size",
                    )
                },
                "is_active": True,
                "display_name": provider_config.get("display_name", "")
                or provider_name,
            }
            lookup = {
                "organization": org,
                "provider_name": provider_name,
                "deleted": False,
            }
            with transaction.atomic():
                try:
                    cred = AgentccProviderCredential.no_workspace_objects.select_for_update().get(
                        **lookup
                    )
                    for k, v in defaults.items():
                        setattr(cred, k, v)
                    # Merge new credential values into existing encrypted credentials.
                    if new_cred_values:
                        existing = CredentialManager.decrypt(cred.encrypted_credentials)
                        existing.update(new_cred_values)
                        cred.encrypted_credentials = CredentialManager.encrypt(existing)
                    cred.save()
                except AgentccProviderCredential.DoesNotExist:
                    if not new_cred_values:
                        return self._gm.bad_request(
                            "credentials are required for new providers"
                        )
                    defaults["encrypted_credentials"] = CredentialManager.encrypt(
                        new_cred_values
                    )
                    cred = AgentccProviderCredential.no_workspace_objects.create(
                        **lookup, **defaults
                    )

            synced = self._push_current_config(org)

            data = {
                "provider": provider_name,
                "action": "updated",
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("update_provider_error", error=str(e))
            return self._gm.bad_request(str(e))

    @validated_request(
        request_serializer=GatewayNameRequestSerializer,
        responses={
            200: GatewayMutationResponseSerializer,
            **GATEWAY_BAD_REQUEST_OR_NOT_FOUND_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="remove-provider")
    def remove_provider(self, request, pk=None):
        """Soft-delete a provider credential and push config."""
        try:
            org = self._current_org(request)
            provider_name = request.validated_data.get("name")
            if not provider_name:
                return self._gm.bad_request("name is required")

            deleted_count = AgentccProviderCredential.no_workspace_objects.filter(
                organization=org,
                provider_name=provider_name,
                deleted=False,
            ).update(deleted=True, deleted_at=timezone.now())

            if not deleted_count:
                return self._gm.not_found(f"Provider '{provider_name}' not found")

            synced = self._push_current_config(org)

            data = {
                "provider": provider_name,
                "action": "removed",
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("remove_provider_error", error=str(e))
            return self._gm.bad_request(str(e))

    # ------------------------------------------------------------------
    # guardrails
    # ------------------------------------------------------------------

    @validated_request(
        request_serializer=GatewayToggleGuardrailRequestSerializer,
        responses={
            200: GatewayMutationResponseSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="toggle-guardrail")
    def toggle_guardrail(self, request, pk=None):
        try:
            org = self._current_org(request)
            guardrail_name = request.validated_data.get("name")
            enabled = request.validated_data.get("enabled")
            if not guardrail_name or enabled is None:
                return self._gm.bad_request("name and enabled are required")

            def updater(cfg):
                # A section stored as a checks list (a policy sync writes one)
                # is switched where the next sync reads the check: in its
                # policy (release round 6B, fix (b)).
                if switch_in_checks_list(org, cfg, guardrail_name, enabled):
                    return
                guardrails = cfg.guardrails or {}
                checks = _stored_checks_map(guardrails)
                if checks is not None:
                    # A section stored as a checks map is toggled in the map.
                    # A check the map lacks is added with the defaults a new
                    # rule gets below (block, 0.8), written out: the gateway
                    # runs a map check without them as log, at 0.
                    key = _checks_map_key(checks, guardrail_name)
                    if key is None:
                        checks[guardrail_name] = {
                            "enabled": enabled,
                            "action": "block",
                            "confidence_threshold": 0.8,
                            "config": {},
                        }
                    else:
                        stored = checks[key] if isinstance(checks[key], dict) else {}
                        checks[key] = {**stored, "enabled": enabled}
                    cfg.guardrails = guardrails
                    return
                rules = guardrails.get("rules", [])

                index = unique_check_index(
                    rules,
                    guardrail_name,
                    repair="Replace the rules list via update-config to resolve duplicates.",
                )
                if index is not None:
                    rules[index]["enabled"] = enabled
                else:
                    # Upsert: create the rule with sensible defaults
                    rules.append(
                        {
                            "name": guardrail_name,
                            "enabled": enabled,
                            "action": "block",
                            "threshold": 0.8,
                            "stage": "pre",
                            "mode": "sync",
                            "config": {},
                        }
                    )

                # Also update checks dict if it exists
                checks = guardrails.get("checks", {})
                if guardrail_name in checks:
                    checks[guardrail_name]["enabled"] = enabled

                guardrails["rules"] = rules
                guardrails["checks"] = checks
                cfg.guardrails = guardrails

            new_config, synced = self._update_org_config(
                org,
                updater,
                user=request.user,
                desc=f"Toggle guardrail {guardrail_name} {'on' if enabled else 'off'}",
            )
            data = {
                "guardrail": guardrail_name,
                "enabled": enabled,
                "version": new_config.version,
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except OrgConfigRejected as e:
            # Switching on a check whose stored setting holds "${" where the
            # gateway expands it (guardrail_env_reference), among others.
            log_config_rejected("toggle_guardrail_rejected", org, e)
            return with_rejection_param(self._gm.bad_request(e), e)
        except Exception as e:
            logger.exception("toggle_guardrail_error", error=str(e))
            return self._gm.bad_request(str(e))

    @swagger_auto_schema(
        responses={
            200: AgentccListResultResponseSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        }
    )
    @action(detail=False, methods=["get"], url_path="protect-templates")
    def protect_templates(self, request):
        """Return eval templates compatible with the FI protect guardrail."""
        try:
            from model_hub.models.evals_metric import EvalTemplate

            # Protect-compatible eval templates.
            protect_metrics = [
                "toxicity",
                "bias_detection",
                "prompt_injection",
                "data_privacy_compliance",
                "protect_flash",
            ]

            # System eval templates have organization_id=None, workspace_id=None,
            # so WorkspaceContextMiddleware filters them out. Use no_workspace_objects
            # to bypass workspace filtering.
            templates = EvalTemplate.no_workspace_objects.filter(
                owner="system",
                deleted=False,
                name__in=protect_metrics,
            ).values("eval_id", "name", "description")

            result = [
                {
                    "eval_id": t["eval_id"],
                    "name": t["name"],
                    "description": t["description"] or "",
                }
                for t in templates
            ]

            return self._gm.success_response(result)
        except Exception as e:
            logger.exception("protect_templates_error", error=str(e))
            return self._gm.bad_request(str(e))

    @validated_request(
        request_serializer=GatewayNamedConfigRequestSerializer,
        responses={
            200: GatewayMutationResponseSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="update-guardrail")
    def update_guardrail(self, request, pk=None):
        try:
            org = self._current_org(request)
            guardrail_name = request.validated_data.get("name")
            guardrail_config = request.validated_data.get("config")
            if not guardrail_name or not guardrail_config:
                return self._gm.bad_request("name and config are required")

            def updater(cfg):
                # A section stored as a checks list (a policy sync writes one)
                # is edited where the next sync reads the check: in its policy
                # (release round 6B, fix (b)).
                if edit_in_checks_list(org, cfg, guardrail_name, guardrail_config):
                    return
                guardrails = cfg.guardrails or {}
                checks = _stored_checks_map(guardrails)
                if checks is not None:
                    # A section stored as a checks map is edited in the map:
                    # the check's entry takes the contract's keys the edit
                    # sent, and keeps the rest of what it is stored with. As
                    # for a rule, a `_policy` it names must be the org's, and a
                    # clear-text credential is moved into the console policy.
                    # A check the map lacks is added enabled unless the edit
                    # says otherwise, as a new rule is (the push runs a rule
                    # without `enabled` as enabled).
                    key = _checks_map_key(checks, guardrail_name)
                    stored = {"enabled": True} if key is None else checks[key]
                    key = guardrail_name if key is None else key
                    entry = _as_checks_map_entry(stored, guardrail_config)
                    _reject_unowned_guardrail_policies(org, {"checks": {key: entry}})
                    checks[key] = entry
                    cfg.guardrails = guardrails
                    _encrypt_new_guardrail_credentials(org, cfg)
                    return
                # The rule is stored as sent, so it may name only a policy the
                # org may name, may hold no "${" in a string the gateway could
                # expand (named as the request's field, config.config.<key>),
                # and a clear-text credential in it is moved into the console
                # policy before the version is validated or saved. The "${"
                # check runs on the raw rule, before any credential becomes the
                # sentinel.
                updated = {**guardrail_config, "name": guardrail_name}
                reject_rule_env_references(guardrail_name, guardrail_config)
                _reject_unowned_guardrail_policies(org, {"rules": [updated]})
                rules = guardrails.get("rules", [])
                index = unique_check_index(
                    rules,
                    guardrail_name,
                    repair="Replace the rules list via update-config to resolve duplicates.",
                )
                if index is None:
                    rules.append(updated)
                else:
                    rules[index] = updated
                guardrails["rules"] = rules
                cfg.guardrails = guardrails
                _encrypt_new_guardrail_credentials(org, cfg)

            new_config, synced = self._update_org_config(
                org,
                updater,
                user=request.user,
                desc=f"Update guardrail {guardrail_name}",
            )
            data = {
                "guardrail": guardrail_name,
                "action": "updated",
                "version": new_config.version,
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except OrgConfigRejected as e:
            # Names the field for a rejection that carries one, e.g. a
            # malformed callback URL (guardrail_bad_url, RF13).
            log_config_rejected("update_guardrail_rejected", org, e)
            return with_rejection_param(self._gm.bad_request(e), e)
        except Exception as e:
            logger.exception("update_guardrail_error", error=str(e))
            return self._gm.bad_request(str(e))

    # ------------------------------------------------------------------
    # test-playground
    # ------------------------------------------------------------------

    def _playground_request(self, request):
        """What a playground test sends: the prompt, the model (may be empty),
        an optional system prompt and the sampling parameters given."""
        data = request.validated_data
        return {
            "prompt": data.get("prompt", "").strip(),
            "model": data.get("model", ""),
            "system_prompt": data.get("system_prompt", "") or None,
            "params": {
                name: data[name]
                for name in PLAYGROUND_PARAMS
                if data.get(name) is not None
            },
        }

    def _playground_open(self, request, client, test, *, streaming=False):
        """Bind a live session principal, sync config, and select the model."""
        from accounts.api_key_policy import live_membership
        from tfc.service_envelope import EnvelopeRejected

        if not test["prompt"]:
            return None, None, self._gm.bad_request("prompt is required")
        org = self._current_org(request)
        if org is None or live_membership(request.user.pk, org.pk) is None:
            return (
                None,
                None,
                self._gm.bad_request("An active organization membership is required"),
            )
        request.organization = org
        try:
            kind = request.headers.get(
                "X-FA-Console-Surface", "tryit" if streaming else "playground"
            )
            if kind not in {"playground", "tryit", "guardrail-test"}:
                raise EnvelopeRejected()
            delegation = for_chat(request, kind=kind, streaming=streaming)
        except EnvelopeRejected:
            return (
                None,
                None,
                self._gm.bad_request(
                    "Delegated calls require an active person and workspace"
                ),
            )
        if not self._ensure_org_config_pushed(client, str(org.pk)):
            return (
                None,
                None,
                self._gm.bad_request(
                    "Could not sync the active organization config to the gateway"
                ),
            )
        model = test["model"] or self._pick_default_model(client, org_id=str(org.pk))
        if not model:
            return (
                None,
                None,
                self._gm.bad_request(
                    "No model specified and no providers configured. Please specify a model or configure a provider first."
                ),
            )
        test["delegation"] = delegation
        return delegation, model, None

    @validated_request(
        request_serializer=GatewayPlaygroundTestRequestSerializer,
        responses={
            200: GatewayPlaygroundTestResponseSerializer,
            409: AgentccErrorResponseSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="test-playground")
    def test_playground(self, request, pk=None):
        """Send a real chat completion through the gateway to test guardrails."""
        if not delegated_enabled():
            return unavailable_response()
        try:
            client = get_gateway_client()
            test = self._playground_request(request)
            delegation, model, error = self._playground_open(request, client, test)
            if error is not None:
                return error

            status_code, body, headers = client.send_delegated_chat_completion(
                delegation,
                prompt=test["prompt"],
                model=model,
                system_prompt=test["system_prompt"],
                params=test["params"],
            )

            # Extract guardrail info from response
            guardrail_info = {}
            for key, val in headers.items():
                lk = key.lower()
                if "guardrail" in lk or "x-agentcc" in lk:
                    guardrail_info[key] = val

            return self._gm.success_response(
                {
                    "status_code": status_code,
                    "body": body,
                    "guardrail_headers": guardrail_info,
                    "model": model,
                    **playground_outcome(status_code, body, headers),
                }
            )
        except GatewayClientError as e:
            return self._gm.bad_request(f"Gateway error: {e}")
        except Exception as e:
            logger.exception("test_playground_error", error=str(e))
            return self._gm.bad_request(str(e))

    @validated_request(
        request_serializer=GatewayPlaygroundTestRequestSerializer,
        responses={
            200: PLAYGROUND_STREAM_RESPONSE,
            409: AgentccErrorResponseSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="test-playground-stream")
    def test_playground_stream(self, request, pk=None):
        """The test-playground request with the answer streamed as server-sent
        events while it arrives (services/playground_stream.py lists them).
        Errors before the stream starts are 400s, as on test-playground."""
        if not delegated_enabled():
            return unavailable_response()
        try:
            client = get_gateway_client()
            test = self._playground_request(request)
            _, model, error = self._playground_open(
                request, client, test, streaming=True
            )
            if error is not None:
                return error
        except GatewayClientError as e:
            return self._gm.bad_request(f"Gateway error: {e}")
        except Exception:
            logger.exception("test_playground_stream_error")
            return self._gm.bad_request("Delegated stream unavailable")
        return EventStreamResponse(playground_events(client, model=model, request=test))

    def _ensure_org_config_pushed(self, client, org_id):
        """Push the org's active config to the gateway (idempotent)."""
        try:
            config = AgentccOrgConfig.no_workspace_objects.filter(
                organization_id=org_id, is_active=True, deleted=False
            ).first()
            if not config:
                return True

            if not push_org_config(org_id, config):
                logger.warning("playground_config_push_failed", org_id=org_id)
                return False

            client.get_org_config(org_id)
            return True
        except Exception as e:
            logger.warning("playground_config_push_failed", org_id=org_id, error=str(e))
            return False

    def _pick_default_model(self, client, org_id=None):
        if not org_id:
            return None
        try:
            cred = AgentccProviderCredential.no_workspace_objects.filter(
                organization_id=org_id,
                is_active=True,
                deleted=False,
            ).first()
            if cred and cred.models_list:
                first = cred.models_list[0]
                return first if isinstance(first, str) else first.get("name", "")
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # budgets
    # ------------------------------------------------------------------

    @validated_request(
        request_serializer=GatewayBudgetSetRequestSerializer,
        responses={
            200: GatewayMutationResponseSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="set-budget")
    def set_budget(self, request, pk=None):
        try:
            org = self._current_org(request)
            level = request.validated_data.get("level")
            if _normalize_budget_level_key(level) in CAP_FIELDS:
                return self._gm.bad_request(CAP_LEVEL_REJECTION)
            budget_config = request.validated_data.get("config")
            if not level or not budget_config:
                return self._gm.bad_request("level and config are required")

            def updater(cfg):
                cfg.budgets = _set_budget_level_config(
                    cfg.budgets,
                    level,
                    budget_config,
                )

            new_config, synced = self._update_org_config(
                org,
                updater,
                user=request.user,
                desc=f"Set budget {level}",
            )
            data = {
                "budget": level,
                "action": "set",
                "version": new_config.version,
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("set_budget_error", error=str(e))
            return self._gm.bad_request(str(e))

    @validated_request(
        request_serializer=GatewayBudgetRemoveRequestSerializer,
        responses={
            200: GatewayMutationResponseSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="remove-budget")
    def remove_budget(self, request, pk=None):
        try:
            org = self._current_org(request)
            level = request.validated_data.get("level")
            if _normalize_budget_level_key(level) in CAP_FIELDS:
                return self._gm.bad_request(CAP_LEVEL_REJECTION)
            if not level:
                return self._gm.bad_request("level is required")

            def updater(cfg):
                cfg.budgets = _remove_budget_level_config(cfg.budgets, level)

            new_config, synced = self._update_org_config(
                org,
                updater,
                user=request.user,
                desc=f"Remove budget {level}",
            )
            data = {
                "budget": level,
                "action": "removed",
                "version": new_config.version,
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("remove_budget_error", error=str(e))
            return self._gm.bad_request(str(e))

    # --- Batch API proxy ---

    @validated_request(
        request_serializer=GatewayBatchSubmitRequestSerializer,
        responses={
            200: GatewayBatchSubmitResponseSerializer,
            409: AgentccErrorResponseSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="submit-batch")
    def submit_batch(self, request, pk=None):
        """Submit as the session person, using a registered delegation."""
        if not delegated_enabled(batch=True):
            return unavailable_response(batch=True)
        from agentcc.services import delegated_batches
        from tfc.service_envelope import EnvelopeRejected

        try:
            requests_list = request.validated_data.get("requests", [])
            if not requests_list:
                return self._gm.bad_request("requests array is required")
            organization = self._current_org(request)
            if organization is None:
                return self._gm.bad_request(
                    "An active organization is required to submit a batch"
                )
            result = delegated_batches.submit(
                get_gateway_client(),
                organization=organization,
                user=request.user,
                workspace=getattr(request, "workspace", None),
                requests=requests_list,
                max_concurrency=request.validated_data.get("max_concurrency", 5),
            )
            return self._gm.success_response(result)
        except (GatewayClientError, EnvelopeRejected):
            return self._gm.bad_request("Delegated batch submission unavailable")
        except Exception:
            logger.exception("submit_batch_error")
            return self._gm.bad_request("Batch submission unavailable")

    @swagger_auto_schema(
        responses={
            200: GatewayBatchDetailResponseSerializer,
            **GATEWAY_BAD_REQUEST_OR_NOT_FOUND_RESPONSES,
        }
    )
    @action(detail=True, methods=["get"], url_path="get-batch")
    def get_batch(self, request, pk=None):
        from agentcc.services import batch_jobs
        from agentcc.services.batch_access import authorized_job

        try:
            client = get_gateway_client()
            batch_id = request.query_params.get("batch_id")
            if not batch_id:
                return self._gm.bad_request("batch_id query param is required")
            job = authorized_job(request, batch_id)
            if job is None:
                return self._gm.not_found("Batch not found")
            try:
                result = client.get_batch(batch_id, org_id=str(job.organization_id))
            except GatewayClientError as e:
                # A missing open batch is immediately failed with an unknown
                # outcome. A finished one answers its recorded status. Other
                # gateway errors keep their existing error response.
                recorded = batch_jobs.answer_for_missing_batch(job, e, client=client)
                if recorded is None:
                    raise
                return self._gm.success_response(recorded)
            try:
                # The first terminal status sends batch.completed and revokes
                # the batch's key (NG-20); a failure there never hides the
                # batch's status from the console.
                batch_jobs.record_status(job, result, client=client)
            except Exception:
                logger.exception("batch_status_record_failed", batch_id=batch_id)
            return self._gm.success_response(result)
        except GatewayClientError as e:
            return self._gm.bad_request(f"Gateway error: {e}")
        except Exception as e:
            logger.exception("get_batch_error", error=str(e))
            return self._gm.bad_request(str(e))

    @validated_request(
        request_serializer=GatewayBatchRequestSerializer,
        responses={
            200: GatewayBatchCancelResponseSerializer,
            **GATEWAY_BAD_REQUEST_OR_NOT_FOUND_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="cancel-batch")
    def cancel_batch(self, request, pk=None):
        from agentcc.services.batch_access import authorized_job

        try:
            client = get_gateway_client()
            batch_id = request.validated_data.get("batch_id")
            if not batch_id:
                return self._gm.bad_request("batch_id is required")
            job = authorized_job(request, batch_id, cancel=True)
            if job is None:
                return self._gm.not_found("Batch not found")
            result = client.cancel_batch(batch_id, org_id=str(job.organization_id))
            return self._gm.success_response(result)
        except GatewayClientError as e:
            return self._gm.bad_request(f"Gateway error: {e}")
        except Exception as e:
            logger.exception("cancel_batch_error", error=str(e))
            return self._gm.bad_request(str(e))

    @swagger_auto_schema(
        responses={
            200: GatewayProvidersResponseSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        }
    )
    @action(detail=True, methods=["get"])
    def providers(self, request, pk=None):
        """The org's providers, each with its health from the org's own
        request logs of the last 24 h (contract K6), and whether the config
        push sends it to the gateway at all (NG-58)."""
        try:
            org = getattr(request, "organization", None)
            credentials = {}
            if org:
                for cred in AgentccProviderCredential.no_workspace_objects.filter(
                    organization=org, is_active=True, deleted=False
                ):
                    credentials[cred.provider_name] = cred
            provider_stats = _org_provider_stats(org) if org else {}

            merged = []
            for pid, cred in credentials.items():
                pstats = provider_stats.get(pid, {})
                request_count = pstats.get("request_count", 0)
                error_count = pstats.get("error_count", 0)
                not_pushed = _not_pushed_reason(cred)
                if not_pushed:
                    status, status_reason = "unhealthy", not_pushed
                else:
                    status, status_reason = _health_from_logs(
                        request_count, error_count
                    )
                merged.append(
                    {
                        "id": pid,
                        "name": pid,
                        "status": status,
                        "status_reason": status_reason,
                        "healthy": status != "unhealthy",
                        # The operator's breaker is never an org provider's.
                        "circuit_state": "n/a",
                        "pushed": not_pushed is None,
                        "display_name": cred.display_name,
                        "base_url": cred.base_url,
                        "api_format": cred.api_format,
                        "models": cred.models_list or [],
                        "request_count": request_count,
                        "avg_latency": pstats.get("avg_latency_ms", 0),
                        "error_rate": round(
                            error_count / request_count * 100 if request_count else 0.0,
                            2,
                        ),
                    }
                )

            return self._gm.success_response({"providers": merged})
        except Exception as e:
            logger.exception("providers_error", error=str(e))
            return self._gm.bad_request(str(e))

    # --- MCP ---

    @swagger_auto_schema(
        responses={
            200: GatewayMCPStatusResponseSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        }
    )
    @action(detail=True, methods=["get"], url_path="mcp-status")
    def mcp_status(self, request, pk=None):
        return self._gm.success_response(
            {
                **mcp_admin.org_status(self._current_org(request)),
                "features": mcp_features.capabilities(),
            }
        )

    @swagger_auto_schema(
        responses={
            200: AgentccListResultResponseSerializer,
        }
    )
    @action(detail=True, methods=["get"], url_path="mcp-tools")
    def mcp_tools(self, request, pk=None):
        return self._mcp_list_response(mcp_admin.org_tools, request)

    @validated_request(
        query_serializer=MCPServedQuerySerializer,
        responses={
            200: MCPServedResponseSerializer,
            400: AgentccErrorResponseSerializer,
            403: AgentccErrorResponseSerializer,
            404: AgentccErrorResponseSerializer,
            502: AgentccErrorResponseSerializer,
        },
    )
    @action(detail=True, methods=["get"], url_path="mcp-served-tools")
    def mcp_served_tools(self, request, pk=None):
        """Org-admin-only runtime inventory; an optional key narrows the view."""
        vs = request.validated_query_data.get("vs", "")
        key_id = request.validated_query_data.get("key_id", "")
        server_id = request.validated_query_data.get("server_id", "")
        try:
            return self._gm.success_response(
                mcp_admin.org_served_tools(
                    self._current_org(request), vs, key_id, server_id
                )
            )
        except mcp_admin.MCPDisabled as e:
            return self._mcp_disabled_response(e)
        except GatewayClientError as e:
            if e.status_code == 404 and e.error_code in {
                "org_not_found",
                "virtual_server_not_found",
                "key_not_found",
                "server_not_found",
            }:
                return self._gm.custom_error_response(
                    HTTP_404_NOT_FOUND,
                    "The requested MCP context was not found.",
                    code=e.error_code,
                )
            return self._gm.custom_error_response(
                HTTP_502_BAD_GATEWAY,
                mcp_admin.GATEWAY_UNREACHABLE_MESSAGE,
                code=mcp_admin.GATEWAY_UNREACHABLE,
            )

    @validated_request(
        request_serializer=GatewayMCPServerUpdateRequestSerializer,
        responses={
            200: GatewayMutationResponseSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="update-mcp-server")
    def update_mcp_server(self, request, pk=None):
        """Add or replace one MCP server. A new ``auth.token`` or ``auth.key``
        goes into the MCP credential vault and only its reference is written
        to the config (M14); with no new value the stored reference and
        transport are kept."""
        org = None
        try:
            org = self._current_org(request)
            server_id = request.validated_data.get("server_id")
            server_config = request.validated_data.get("config")
            if not server_id or not server_config:
                return self._gm.bad_request("server_id and config are required")

            def updater(cfg):
                mcp_cfg = cfg.mcp or {}
                servers = mcp_cfg.get("servers", {})
                # The credential row is written inside this config write, so
                # a rejected version rolls it back too.
                servers[server_id] = mcp_credentials.console_server_entry(
                    org,
                    server_id,
                    server_config,
                    servers.get(server_id),
                    actor=request.user,
                )
                mcp_cfg["servers"] = servers
                cfg.mcp = mcp_cfg

            # The vault writes this server's credential reference; a client
            # credential_id or state is refused by console_server_entry.
            with mcp_credentials.vault_write():
                new_config, synced = self._update_org_config(
                    org,
                    updater,
                    user=request.user,
                    desc=f"Update MCP server {server_id}",
                )
            data = {
                "server": server_id,
                "action": "updated",
                "version": new_config.version,
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except mcp_credentials.MCPCredentialError as e:
            # The message names the field, never the secret.
            return self._gm.bad_request(e)
        except OrgConfigRejected as e:
            log_config_rejected("update_mcp_server_rejected", org, e)
            return with_rejection_param(self._gm.bad_request(e), e)
        except Exception as e:
            logger.exception("update_mcp_server_error", error_type=type(e).__name__)
            return self._gm.bad_request("The MCP server could not be saved.")

    @validated_request(
        request_serializer=GatewayMCPServerRemoveRequestSerializer,
        responses={
            200: GatewayMutationResponseSerializer,
            **GATEWAY_BAD_REQUEST_OR_NOT_FOUND_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="remove-mcp-server")
    def remove_mcp_server(self, request, pk=None):
        try:
            org = self._current_org(request)
            server_id = request.validated_data.get("server_id")
            if not server_id:
                return self._gm.bad_request("server_id is required")

            def updater(cfg):
                mcp_cfg = cfg.mcp or {}
                servers = mcp_cfg.get("servers", {})
                if server_id not in servers:
                    raise ValueError(f"MCP server '{server_id}' not found")
                del servers[server_id]
                mcp_cfg["servers"] = servers
                cfg.mcp = mcp_cfg
                # A removed server's credentials go with it (M14), in the
                # same transaction as the config write.
                mcp_credentials.revoke_server_credentials(org, server_id)

            try:
                new_config, synced = self._update_org_config(
                    org,
                    updater,
                    user=request.user,
                    desc=f"Remove MCP server {server_id}",
                )
            except ValueError as ve:
                return self._gm.not_found(str(ve))
            data = {
                "server": server_id,
                "action": "removed",
                "version": new_config.version,
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("remove_mcp_server_error", error=str(e))
            return self._gm.bad_request(str(e))

    @validated_request(
        request_serializer=GatewayMCPGuardrailsUpdateRequestSerializer,
        responses={
            200: GatewayMutationResponseSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="update-mcp-guardrails")
    def update_mcp_guardrails(self, request, pk=None):
        try:
            org = self._current_org(request)
            guardrail_config = request.validated_data.get("config")
            if not guardrail_config:
                return self._gm.bad_request("config is required")

            def updater(cfg):
                mcp_cfg = cfg.mcp or {}
                mcp_cfg["guardrails"] = guardrail_config
                cfg.mcp = mcp_cfg

            new_config, synced = self._update_org_config(
                org,
                updater,
                user=request.user,
                desc="Update MCP guardrails",
            )
            data = {
                "action": "updated",
                "version": new_config.version,
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except Exception as e:
            logger.exception("update_mcp_guardrails_error", error=str(e))
            return self._gm.bad_request(str(e))

    @validated_request(
        request_serializer=GatewayMCPToolTestRequestSerializer,
        responses={
            200: GatewayMCPToolTestResponseSerializer,
            **GATEWAY_BAD_REQUEST_OR_NOT_FOUND_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    @action(detail=True, methods=["post"], url_path="test-mcp-tool")
    def test_mcp_tool(self, request, pk=None):
        try:
            result = mcp_admin.run_tool_test(
                self._current_org(request),
                request.validated_data["name"],
                request.validated_data.get("arguments", {}),
            )
        except mcp_admin.NoMCPServers as e:
            return self._gm.bad_request(str(e))
        except mcp_admin.MCPToolNotFound as e:
            return self._gm.custom_error_response(
                HTTP_404_NOT_FOUND, str(e), code=mcp_admin.MCP_TOOL_NOT_FOUND
            )
        except mcp_admin.MCPDisabled as e:
            return self._mcp_disabled_response(e)
        except GatewayClientError as e:
            return self._gm.bad_request(f"Gateway error: {e}")
        return self._gm.success_response(result)

    @swagger_auto_schema(
        responses={
            200: AgentccListResultResponseSerializer,
        }
    )
    @action(detail=True, methods=["get"], url_path="mcp-resources")
    def mcp_resources(self, request, pk=None):
        return self._mcp_list_response(mcp_admin.org_resources, request)

    @swagger_auto_schema(
        responses={
            200: AgentccListResultResponseSerializer,
        }
    )
    @action(detail=True, methods=["get"], url_path="mcp-prompts")
    def mcp_prompts(self, request, pk=None):
        return self._mcp_list_response(mcp_admin.org_prompts, request)

    def _mcp_list_response(self, read, request):
        """An MCP list read: 404 ``gateway_mcp_disabled`` when the deployment
        runs with MCP off, 502 ``gateway_unreachable`` when the gateway fails."""
        try:
            return self._gm.success_response(read(self._current_org(request)))
        except mcp_admin.MCPDisabled as e:
            return self._mcp_disabled_response(e)
        except GatewayClientError as e:
            logger.warning("mcp_list_gateway_error", read=read.__name__, error=str(e))
            return self._gm.custom_error_response(
                HTTP_502_BAD_GATEWAY,
                mcp_admin.GATEWAY_UNREACHABLE_MESSAGE,
                code=mcp_admin.GATEWAY_UNREACHABLE,
            )

    def _mcp_disabled_response(self, error):
        return self._gm.custom_error_response(
            HTTP_404_NOT_FOUND, str(error), code=mcp_admin.GATEWAY_MCP_DISABLED
        )

    # --- Per-org config helpers ---

    def _get_or_create_org_config(self, org):
        with transaction.atomic():
            # Org lock first, as every config write: this may create version 1
            # or rewrite the active row.
            lock_org(org.pk)
            config = (
                AgentccOrgConfig.no_workspace_objects.select_for_update()
                .filter(
                    organization=org,
                    is_active=True,
                    deleted=False,
                )
                .first()
            )
            if config:
                normalized = normalize_cost_tracking_config(config.cost_tracking)
                if normalized != config.cost_tracking:
                    config.cost_tracking = normalized
                    config.save(update_fields=["cost_tracking", "updated_at"])
                return config
            # A new org's first version: Future AGI models on where the
            # deployment offers them, as every first version
            # (managed_models_default).
            config, _ = AgentccOrgConfig.no_workspace_objects.get_or_create(
                organization=org,
                version=1,
                is_active=True,
                deleted=False,
                defaults={
                    "cost_tracking": default_cost_tracking_config(),
                    "managed_models": initial_managed_models(org),
                },
            )
            normalized = normalize_cost_tracking_config(config.cost_tracking)
            if normalized != config.cost_tracking:
                config.cost_tracking = normalized
                config.save(update_fields=["cost_tracking", "updated_at"])
            return config

    def _update_org_config(self, org, updater_fn, user=None, desc=""):
        new_config, warnings = write_config_version_with(
            org,
            updater_fn,
            description=desc,
            user=user,
            push=partial(self._push_current_config, org),
        )
        return new_config, is_gateway_synced(warnings)

    def _push_current_config(self, org, config=None):
        """Returns True if gateway was updated, False if push failed.

        A config passed in comes from the writer, which already holds the
        org lock. Without one, the active config is read and pushed under
        the org lock, so a writer cannot push a newer version in between.
        """
        try:
            if config is not None:
                return push_org_config(str(org.id), config)
            with transaction.atomic():
                # _get_or_create_org_config takes the org lock in a savepoint;
                # the lock is held until this outer transaction ends.
                config = self._get_or_create_org_config(org)
                return push_org_config(str(org.id), config)
        except MCPCredentialPlacementRefused:
            # A refusal, not a failed push (AM-C20): the write that pushes an
            # MCP secret the gateway cannot apply answers 400 (update-mcp-server).
            raise
        except Exception as e:
            logger.warning("config_push_failed", org_id=str(org.id), error=str(e))
            return False
