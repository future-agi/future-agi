import datetime
import json
import os
import re
from functools import partial
from urllib.parse import urlsplit

import structlog
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Q
from django.utils import timezone
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.status import (
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    HTTP_502_BAD_GATEWAY,
)
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
    CapActivationConflictSerializer,
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
    AgentccProviderCredentialCreateSerializer,
    ProviderBaseURLField,
    unsupported_credential_keys,
    unsupported_credential_keys_message,
)
from agentcc.services import mcp_admin, mcp_credentials
from agentcc.services.cap_ledger_status import AdmissionCapLedgerUnavailable
from agentcc.services.cap_revision import (
    CAP_FIELDS,
    CAP_LEVEL_REJECTION,
    CapPolicyRevisionConflict,
)
from agentcc.services.config_push import (
    _RULE_TO_REGISTRY,
    MCPCredentialPlacementRefused,
    OrgConfigRejected,
    # Compatibility patch seam only; a request never pushes other orgs.
    push_all_org_configs,  # noqa: F401
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
    remove_in_checks_list,
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
from agentcc.services.provider_public_projection import public_provider_models
from agentcc.services.provider_url_auth import (
    provider_url_auth_values,
    public_provider_base_url,
)
from agentcc.services.provider_validation import ensure_provider_configuration
from agentcc.services.routing_key_rules import with_rejection_param
from agentcc.services.url_safety import ensure_provider_base_url_allowed
from agentcc.views.event_stream import EventStreamResponse
from tfc.utils.api_contracts import validated_request
from tfc.utils.general_methods import GeneralMethods

logger = structlog.get_logger(__name__)


def _allows_keyless_system_one(provider_name, api_format, base_url):
    """Only an explicit Generic System One endpoint may omit its API key.

    Ported unchanged from the dev serializer (allows_keyless_system_one): the IR
    serializer does not define it. Match the gateway's vendor IDs and host
    suffixes, not the display name; URL safety (including private-network
    policy) is still checked by the view.
    """
    if api_format not in ("systemone", "typesafe") or not base_url:
        return False
    try:
        url = ProviderBaseURLField().run_validation(base_url)
    except DRFValidationError:
        return False
    host = urlsplit(url).hostname.lower().rstrip(".")
    name = provider_name.lower()
    if name.startswith(("typesafe", "perplexity", "openai")):
        return False
    return not any(
        host == vendor or host.endswith("." + vendor)
        for vendor in ("typesafe.ai", "perplexity.ai", "openai.com")
    )


def _gateway_error_code(error):
    """Categorize errors without forwarding upstream-controlled diagnostic text."""
    if isinstance(error, GatewayClientError):
        status = error.status_code
        if type(status) is int:
            if 400 <= status < 500:
                return "upstream_4xx"
            if 500 <= status < 600:
                return "upstream_5xx"
        return "gateway_error"
    return "internal_error"


def _gateway_error_message(error):
    return f"Gateway request failed. ({_gateway_error_code(error)})"


# Fixed, value-free texts the IR gateway client raises for a misconfigured
# client, proxy, TLS or request (services/gateway_client.py _request). The
# client already drops the original chain; only an exact match is shown.
_GATEWAY_CONFIGURATION_MESSAGES = frozenset(
    {
        "Invalid gateway client configuration",
        "Invalid gateway request configuration",
        "Invalid gateway proxy configuration",
        "Gateway TLS certificate verification failed",
        "Invalid gateway TLS configuration",
        "Gateway transport failure",
    }
)


def _gateway_read_error_message(error):
    """The IR read endpoints' answer: the HTTP status alone, never the body, or
    one of the client's fixed configuration texts; anything else stays
    categorical."""
    if isinstance(error, GatewayClientError):
        status = error.status_code
        if type(status) is int and 100 <= status < 600:
            return f"Gateway returned {status}"
        return _gateway_error_message(error)
    # ssl.SSLError(msg) renders str() as the args tuple, so also match args[0].
    candidates = [str(error)]
    args = getattr(error, "args", ())
    if args and isinstance(args[0], str):
        candidates.append(args[0])
    for message in candidates:
        if message in _GATEWAY_CONFIGURATION_MESSAGES:
            return message
    return _gateway_error_message(error)


def _log_gateway_error(event, error):
    # A traceback or exc_info would put the original diagnostic back in the log.
    logger.warning(
        event, exception_type=type(error).__name__, code=_gateway_error_code(error)
    )


def _mcp_access_default_denial(result):
    """The reason for a Try a tool the organization's MCP access default refused
    (the gateway's tool_denied with rule access.default), in Django's own
    words; None for any other result. The gateway's text is never used."""
    if (
        isinstance(result, dict)
        and result.get("code") == "tool_denied"
        and result.get("rule") == "access.default"
    ):
        return {
            "error": {
                "message": mcp_admin.MCP_ACCESS_DEFAULT_DENY_MESSAGE,
                "code": mcp_admin.MCP_ACCESS_DEFAULT_DENY,
            }
        }
    return None


def _mcp_error_result(result):
    """Sanitize semantic errors before forwarding helper results to the caller."""
    candidates = result if isinstance(result, list) else [result]
    if isinstance(result, dict) and isinstance(result.get("servers"), list):
        candidates = [result, *result["servers"]]
    if any(
        isinstance(item, dict)
        and (
            item.get("error")
            or item.get("is_error") is True
            or item.get("isError") is True
        )
        for item in candidates
    ):
        return {
            "error": {"message": "Gateway request failed.", "code": "gateway_error"}
        }
    return None


_GATEWAY_SYNC_WARNING = GATEWAY_SYNC_FAILED_MESSAGE

# Refusal text for a credential placement the gateway cannot apply.
# Render the field only, never the exception's internal reason.
_MCP_CREDENTIAL_PLACEMENT_REFUSED_MESSAGE = (
    "The MCP credential configuration at {param} cannot be applied. No changes were "
    "saved. Correct the credential, the server URL or the catalog reference, then "
    "retry."
)


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
    # Set after the health merge so no gateway field can stand in for it. The
    # console reads it to say up front that Try it and Guardrail test are off.
    data["delegated_playground_available"] = delegated_enabled()
    # And that Submit Batch Job is off (submit-batch refuses with 409 then).
    data["delegated_batch_available"] = delegated_enabled(batch=True)
    return data


# NG-30: when the org last ran a health check, and what it found. Kept per org:
# the check is an org member's click, and another org's clicks are not theirs.
_LAST_HEALTH_CHECK_KEY = "agentcc:gateway_last_health_check:{org_id}"
# Only an Owner or Admin may re-push, and only this caller's org is synced
# or throttled. Health-history entries remain independent of this throttle.
_REPUSH_THROTTLE_KEY = "agentcc:gateway_config_synced:{org_id}"
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
    """The blocks above from the gateway's GET /-/config, or None when the read
    failed or answered something other than an object."""
    try:
        reported = client.get_config()
    except (GatewayClientError, ValueError) as e:
        _log_gateway_error("gateway_config_read_failed", e)
        return None
    if not isinstance(reported, dict):
        return None
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


_NOT_TEXT_REASON = (
    "not pushed: the saved credential holds a value that is not text; "
    "replace the credential"
)


def _not_pushed_reason(credential):
    """Why the config push leaves this provider out (NG-58), or None. The
    push skips a credential it cannot decrypt, one with a key outside the
    provider contract, or one its model-ID projection refuses (an api_key
    that is not text): config_push._assemble_providers. The reason never
    carries a credential value."""
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
    if "code" in public_provider_models(credential, credentials=decrypted):
        return _NOT_TEXT_REASON
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
            "remove_guardrail",
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

    def _gateway_scope_refusal(self, request, *, orgless_read=False):
        """Refuse before any tenant read or gateway call.

        IR authentication resolves the organization from an active membership
        (an account without one never reaches this view) and the workspace
        inside it; organization-level operations may run with no workspace.
        So an organization is required, and a workspace, when present, must
        belong to it. ``orgless_read`` lets the virtual-gateway reads answer
        with the gateway's own state alone: they make no tenant read without
        an organization.
        """
        organization = getattr(request, "organization", None)
        workspace = getattr(request, "workspace", None)
        if organization is None:
            if orgless_read:
                return None
        elif workspace is None or str(workspace.organization_id) == str(
            organization.id
        ):
            return None
        return self._gm.bad_request("Organization and workspace context are required")

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
        refusal = self._gateway_scope_refusal(request, orgless_read=True)
        if refusal is not None:
            return refusal

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
            _log_gateway_error("gateway_list_error", error=e)
            return self._gm.bad_request(_gateway_read_error_message(e))

    @swagger_auto_schema(
        responses={
            200: GatewayDetailResponseSerializer,
            **GATEWAY_NOT_FOUND_RESPONSES,
        }
    )
    def retrieve(self, request, *args, **kwargs):
        """Accept any pk and ignore it — there is only one gateway."""
        refusal = self._gateway_scope_refusal(request, orgless_read=True)
        if refusal is not None:
            return refusal

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
            _log_gateway_error("gateway_retrieve_error", error=e)
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
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

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
                            "models": public_provider_models(p)["models"],
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

                # Preserve IR's admin-only recovery and health metadata, but
                # never sync or throttle any organization except the caller's.
                org_configs_pushed = (
                    self._repush_org_configs(client, org)
                    if is_gateway_admin(request)
                    else False
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
                _log_gateway_error("gateway_health_unreachable", e)
                _record_health_check(org, "unreachable")
                return self._gm.bad_request(
                    {
                        "status": "unreachable",
                        "error": _gateway_error_message(e),
                    }
                )
        except Exception as e:
            _log_gateway_error("health_check_error", error=e)
            return self._gm.bad_request(_gateway_error_message(e))

    def _repush_org_configs(self, client, org):
        """Recover only the caller's config; failed syncs never set the throttle."""
        key = _REPUSH_THROTTLE_KEY.format(org_id=org.id)
        if cache.get(key):
            return False
        try:
            if not self._ensure_org_config_pushed(client, str(org.id)):
                return False
        except Exception as e:
            _log_gateway_error("gateway_auto_sync_failed", e)
            return False
        cache.set(key, True, _REPUSH_THROTTLE_SECONDS)
        logger.info("gateway_auto_sync")
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
        refusal = self._gateway_scope_refusal(request, orgless_read=True)
        if refusal is not None:
            return refusal

        try:
            org = self._current_org(request)

            org_config = None
            providers = []
            # Without an organization there is no tenant to read: never query
            # organization=None rows; answer with the gateway's own state only.
            if org is not None:
                org_config = AgentccOrgConfig.no_workspace_objects.filter(
                    organization=org, is_active=True, deleted=False
                ).first()
                providers = list(
                    AgentccProviderCredential.no_workspace_objects.filter(
                        organization=org,
                        deleted=False,
                        is_active=True,
                    )
                )
            org_data = {"cost_tracking": default_cost_tracking_config()}
            if org_config:
                from agentcc.serializers.org_config import AgentccOrgConfigSerializer

                org_data = AgentccOrgConfigSerializer(org_config).data

            providers_map = {}
            for p in providers:
                extra_config = (
                    p.extra_config if isinstance(p.extra_config, dict) else {}
                )
                projected = public_provider_models(p)
                providers_map[p.provider_name] = {
                    "id": str(p.id),
                    "name": p.provider_name,
                    "display_name": p.display_name or p.provider_name,
                    "base_url": public_provider_base_url(p.base_url),
                    "api_format": p.api_format,
                    "api_path_prefix": extra_config.get("api_path_prefix", "/v1"),
                    "models": projected["models"],
                    # How many saved model IDs the read left out because they
                    # match the provider's credential (a count, never the IDs).
                    "dropped_model_count": projected["dropped_model_count"],
                    "is_active": p.is_active,
                    "default_timeout": p.default_timeout_seconds,
                    "max_concurrent": p.max_concurrent,
                    "conn_pool_size": p.conn_pool_size,
                }

            gateway_status = "unreachable"
            gateway_blocks = {}
            # Whether the gateway's blocks were read: without them the console
            # cannot tell a failed read from a gateway that does not report.
            config_read = "failed"
            try:
                client = get_gateway_client()
                client.health_check()
                gateway_status = "healthy"
                blocks = _gateway_config_blocks(client)
                if blocks is not None:
                    gateway_blocks, config_read = blocks, "ok"
            except GatewayClientError as e:
                if e.status_code is not None:
                    raise GatewayClientError(
                        f"Gateway returned {e.status_code}", status_code=e.status_code
                    ) from None

            result = {**org_data, **gateway_blocks}
            result["providers"] = providers_map
            # Providers the push leaves out (see providers): a count only.
            result["refused_provider_count"] = sum(
                1 for p in providers if _not_pushed_reason(p) is not None
            )
            result["gateway"] = {"status": gateway_status, "config_read": config_read}
            _with_last_health_check(result, org)
            return self._gm.success_response(result)
        except Exception as e:
            _log_gateway_error("config_error", error=e)
            return self._gm.bad_request(_gateway_read_error_message(e))

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
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

        try:
            org = self._current_org(request)
            synced = self._push_current_config(org)
            data = {"status": "ok", "gateway_synced": synced}
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except MCPCredentialPlacementRefused as e:
            log_config_rejected("reload_rejected", org, e)
            return self._mcp_credential_placement_refused_response(e)
        except Exception as e:
            _log_gateway_error("reload_error", error=e)
            return self._gm.bad_request(_gateway_error_message(e))

    @validated_request(
        request_serializer=GatewayConfigPatchRequestSerializer,
        responses={
            200: GatewayConfigMutationResponseSerializer,
            409: CapActivationConflictSerializer,
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
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

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
        except (CapPolicyRevisionConflict, AdmissionCapLedgerUnavailable) as e:
            return e.response()
        except OrgConfigRejected as e:
            # The exception's own code (e.g. secret_in_config) goes back to
            # the client; a rejection without one keeps the generic 400 code.
            log_config_rejected("update_config_rejected", org, e)
            return with_rejection_param(self._gm.bad_request(e), e)
        except Exception as e:
            _log_gateway_error("update_config_error", error=e)
            return self._gm.bad_request(_gateway_error_message(e))

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
        """Add or update a provider credential for the org, then push config.

        A complete nonblank AWS key pair replaces the credential family: an
        omitted/blank session token removes the previous STS token. Blank-only
        or partial credential edits keep the existing merge behavior.
        """
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

        try:
            org = self._current_org(request)
            provider_name = request.validated_data.get("name")
            provider_config = request.validated_data.get("config")
            if not provider_name or not provider_config:
                return self._gm.bad_request("name and config are required")
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
            # Every non-api_key blank is ABSENT before any family parser.
            blank_credential_fields = {
                k
                for k, v in provider_config.items()
                if k in credential_fields and isinstance(v, str) and not v.strip()
            }
            provider_config = {
                k: v
                for k, v in provider_config.items()
                if k not in blank_credential_fields
            }
            if provider_name == "vertex":
                provider_config = _prepare_vertex_provider_config(provider_config)
            elif "service_account_json" in provider_config:
                return self._gm.bad_request(
                    "Google service-account credentials can only be used with Vertex AI."
                )
            try:
                provider_config["base_url"] = ProviderBaseURLField(
                    max_length=500, allow_blank=True
                ).run_validation(provider_config.get("base_url", ""))
            except DRFValidationError as exc:
                # This field emits fixed texts only, never URL/parser values.
                return self._gm.bad_request(exc.detail)
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
                # Both validators raise only fixed texts (url_safety constants,
                # the format list) or echo the request's own provider name;
                # no upstream body or stored value reaches this message.
                _log_gateway_error("provider_url_rejected", e)
                return self._gm.bad_request(str(e))

            from agentcc.services.provider_model_safety import (
                InvalidProviderCredentialContext,
                credential_context_strings,
                credential_text_leaves,
                filter_provider_model_ids,
                retires_credential_text,
            )
            from agentcc.services.provider_secret_policy import (
                extra_config_secret_values,
            )
            from integrations.services.credentials import CredentialManager

            api_key = provider_config.pop("api_key", None)
            if api_key is not None and not isinstance(api_key, str):
                return self._gm.bad_request("API key must be a string")
            if isinstance(api_key, str):
                api_key = api_key.strip()
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
                # Serialize before locking provider rows, as remove_provider and
                # the config writer do. A refused push must undo the credential
                # upsert as well as normalization or first-config creation.
                lock_org(org.pk)
                try:
                    cred = AgentccProviderCredential.no_workspace_objects.select_for_update().get(
                        **lookup
                    )
                except AgentccProviderCredential.DoesNotExist:
                    cred = None

                # Omitted keys keep the stored credentials; an explicit empty
                # key must still pass the same rules as a new provider.
                try:
                    credentials = (
                        CredentialManager.decrypt(bytes(cred.encrypted_credentials))
                        if cred is not None
                        else {}
                    )
                    if not isinstance(credentials, dict):
                        raise InvalidProviderCredentialContext
                    credential_context_strings(
                        (
                            new_cred_values,
                            extra_config_secret_values(
                                cred.extra_config if cred else {}
                            ),
                            extra_config_secret_values(defaults["extra_config"]),
                            provider_url_auth_values(cred.base_url if cred else ""),
                            provider_url_auth_values(defaults["base_url"]),
                        )
                    )
                except Exception:
                    return self._gm.custom_error_response(
                        400,
                        str(InvalidProviderCredentialContext()),
                        code=InvalidProviderCredentialContext.code,
                    )
                # The model list the console offered for the saved row (none
                # when the saved credential holds a value that is not text).
                offered = (
                    public_provider_models(cred, credentials=credentials)["models"]
                    if cred is not None
                    else []
                )
                # A saved credential that decrypts but holds a value that is not
                # text ("not pushed: ... replace the credential") is replaced by
                # a complete new credential; anything less is refused as before.
                try:
                    credential_context_strings(credentials)
                    stored_non_text = False
                except InvalidProviderCredentialContext:
                    stored_non_text = True
                if stored_non_text:
                    complete = bool(new_cred_values) and all(
                        isinstance(value, str) and value.strip()
                        for value in new_cred_values.values()
                    )
                    if complete:
                        try:
                            AgentccProviderCredentialCreateSerializer(
                                data={"provider_name": provider_name}
                            ).fields["credentials"].run_validation(
                                dict(new_cred_values)
                            )
                        except DRFValidationError:
                            complete = False
                    if not complete:
                        return self._gm.custom_error_response(
                            400,
                            "This provider's saved credential holds a value that is "
                            "not text. Enter a complete new credential to replace it.",
                            code=InvalidProviderCredentialContext.code,
                        )
                    try:
                        saved_credentials = credential_text_leaves(credentials)
                    except InvalidProviderCredentialContext:
                        return self._gm.custom_error_response(
                            400,
                            str(InvalidProviderCredentialContext()),
                            code=InvalidProviderCredentialContext.code,
                        )
                    credentials = {}
                else:
                    saved_credentials = dict(credentials)
                stored_api_key = credentials.get("api_key")
                if (
                    cred is not None
                    and api_key == ""
                    and isinstance(stored_api_key, str)
                    and stored_api_key.strip()
                    and _allows_keyless_system_one(
                        provider_name, defaults["api_format"], defaults["base_url"]
                    )
                ):
                    return self._gm.bad_request(
                        "API key or provider credentials are required"
                    )
                if (
                    (provider_name == "bedrock" or defaults["api_format"] == "bedrock")
                    and all(
                        isinstance(new_cred_values.get(field), str)
                        and new_cred_values[field].strip()
                        for field in ("aws_access_key_id", "aws_secret_access_key")
                    )
                    and "aws_session_token" not in new_cred_values
                ):
                    # A new IAM pair must not inherit a token from an old STS
                    # session. saved_credentials still screens retired secrets
                    # out of model IDs below, and nonempty key fields ensure the
                    # token removal is re-encrypted and pushed atomically.
                    credentials.pop("aws_session_token", None)
                credentials.update(new_cred_values)
                has_credentials = isinstance(credentials.get("api_key"), str) and bool(
                    credentials["api_key"].strip()
                )
                if (
                    provider_name in ("bedrock", "vertex")
                    or defaults["api_format"] == "bedrock"
                ):
                    has_credentials = any(
                        isinstance(value, str) and value.strip()
                        for value in credentials.values()
                    )
                if not has_credentials and not _allows_keyless_system_one(
                    provider_name, defaults["api_format"], defaults["base_url"]
                ):
                    return self._gm.bad_request(
                        "API key or provider credentials are required"
                    )

                # A list sent back as the console offered it (any order), or
                # none when it offered none, is no change: the saved IDs stay,
                # the ones the reads hold back as well. A replaced non-text
                # credential keeps them too, screened against the new values,
                # as does a save that drops a secret value an ID could spell.
                submitted_models = provider_config.get("models")
                if cred is None:
                    unchanged = False
                elif submitted_models is None:
                    unchanged = not offered
                else:
                    unchanged = (
                        isinstance(submitted_models, list)
                        and all(isinstance(model, str) for model in submitted_models)
                        and set(submitted_models) == set(offered)
                    )
                if unchanged:
                    defaults["models_list"] = cred.models_list
                try:
                    projected = filter_provider_model_ids(
                        defaults["models_list"],
                        (
                            credentials,
                            saved_credentials,
                            extra_config_secret_values(
                                cred.extra_config if cred else {}
                            ),
                            extra_config_secret_values(defaults["extra_config"]),
                            provider_url_auth_values(cred.base_url if cred else ""),
                            provider_url_auth_values(defaults["base_url"]),
                        ),
                        provider_name=provider_name,
                        api_format=defaults["api_format"],
                        enforce_discovery_grammar=False,
                    )
                    # An ID held back only by a secret value this save drops
                    # would be offered once it is gone: screen the list then.
                    keep_saved = (
                        unchanged
                        and not stored_non_text
                        and not retires_credential_text(
                            (
                                saved_credentials,
                                extra_config_secret_values(cred.extra_config),
                                provider_url_auth_values(cred.base_url),
                            ),
                            (
                                credentials,
                                extra_config_secret_values(defaults["extra_config"]),
                                provider_url_auth_values(defaults["base_url"]),
                            ),
                        )
                    )
                except InvalidProviderCredentialContext:
                    return self._gm.custom_error_response(
                        400,
                        str(InvalidProviderCredentialContext()),
                        code=InvalidProviderCredentialContext.code,
                    )
                # A kept list keeps its held-back IDs, so the answer counts only
                # IDs this save removed.
                removed = 0
                if not keep_saved:
                    defaults["models_list"] = projected["models"]
                    removed = projected["dropped_model_count"]
                if (
                    cred is not None
                    and not projected["dropped_model_count"]
                    and blank_credential_fields
                    and not new_cred_values
                    and all(getattr(cred, k) == v for k, v in defaults.items())
                ):
                    # Full-replace metadata still applies: omitted nondefault
                    # fields reset and push; only an identical resave is a no-op.
                    return self._gm.success_response(
                        {
                            "provider": provider_name,
                            "action": "unchanged",
                            "gateway_synced": False,
                        }
                    )
                if cred is not None:
                    for k, v in defaults.items():
                        setattr(cred, k, v)
                    if new_cred_values:
                        cred.encrypted_credentials = CredentialManager.encrypt(
                            credentials
                        )
                    cred.save()
                else:
                    # Encrypt even an empty dict: config push decrypts every
                    # provider row, including a keyless Generic endpoint.
                    defaults["encrypted_credentials"] = CredentialManager.encrypt(
                        credentials
                    )
                    cred = AgentccProviderCredential(**lookup, **defaults)
                    cred.disable_auto_workspace_assignment = True
                    cred.save(force_insert=True)

                synced = self._push_current_config(org)

            data = {
                "provider": provider_name,
                "action": "updated",
                "gateway_synced": synced,
            }
            if removed:
                data["dropped_model_count"] = removed
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except MCPCredentialPlacementRefused as e:
            log_config_rejected("update_provider_rejected", org, e)
            return self._mcp_credential_placement_refused_response(e)
        except Exception as e:
            _log_gateway_error("update_provider_error", error=e)
            return self._gm.bad_request(_gateway_error_message(e))

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
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

        try:
            org = self._current_org(request)
            provider_name = request.validated_data.get("name")
            if not provider_name:
                return self._gm.bad_request("name is required")

            with transaction.atomic():
                # Match the config writer's lock order before changing credentials.
                # A refused push rolls back the soft-delete as well as any config
                # write; a False result keeps the saved change and sync warning.
                lock_org(org.pk)
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
        except MCPCredentialPlacementRefused as e:
            log_config_rejected("remove_provider_rejected", org, e)
            return self._mcp_credential_placement_refused_response(e)
        except Exception as e:
            _log_gateway_error("remove_provider_error", error=e)
            return self._gm.bad_request(_gateway_error_message(e))

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
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

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
            _log_gateway_error("toggle_guardrail_error", error=e)
            return self._gm.bad_request(_gateway_error_message(e))

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
            _log_gateway_error("protect_templates_error", error=e)
            return self._gm.bad_request(_gateway_error_message(e))

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
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

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
            _log_gateway_error("update_guardrail_error", error=e)
            return self._gm.bad_request(_gateway_error_message(e))

    @update_guardrail.mapping.delete
    @validated_request(
        request_serializer=GatewayNameRequestSerializer,
        responses={
            200: GatewayMutationResponseSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        },
        reject_unknown_fields=True,
    )
    def remove_guardrail(self, request, pk=None):
        """Remove a checks-list guardrail from its durable policy sources."""
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal
        try:
            org = self._current_org(request)
            name = request.validated_data["name"]

            def updater(cfg):
                if not remove_in_checks_list(org, cfg, name):
                    raise OrgConfigRejected(
                        "Guardrails are no longer stored as a checks list. Refresh and retry."
                    )

            new_config, synced = self._update_org_config(
                org, updater, user=request.user, desc=f"Remove guardrail {name}"
            )
            data = {
                "guardrail": name,
                "action": "removed",
                "version": new_config.version,
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except OrgConfigRejected as e:
            log_config_rejected("remove_guardrail_rejected", org, e)
            return with_rejection_param(self._gm.bad_request(e), e)
        except Exception as e:
            _log_gateway_error("remove_guardrail_error", error=e)
            return self._gm.bad_request(_gateway_error_message(e))

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
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

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

            # Preserve IR's body-typed guardrail outcome as well as accepted
            # gateway header/446 blocks; ordinary upstream failure bodies stay private.
            outcome = playground_outcome(status_code, body, headers)
            triggered = any(
                key.lower() == "x-agentcc-guardrail-triggered"
                and str(value).lower() == "true"
                for key, value in headers.items()
            )
            guardrail_block = (
                outcome["blocked"]
                or status_code == 446
                or (status_code == 403 and triggered)
            )
            if not (200 <= status_code < 300) and not guardrail_block:
                code = (
                    "upstream_5xx"
                    if status_code >= 500
                    else "upstream_4xx"
                    if status_code >= 400
                    else "upstream_other"
                )
                logger.warning("test_playground_upstream_error", code=code)
                body = {"error": {"message": "Gateway request failed.", "code": code}}
                # Diagnostics in failure headers are not completion metadata.
                headers = {}
                outcome = playground_outcome(status_code, body, headers)

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
                    **outcome,
                }
            )
        except GatewayClientError as e:
            _log_gateway_error("test_playground_gateway_error", e)
            return self._gm.bad_request(_gateway_error_message(e))
        except Exception as e:
            _log_gateway_error("test_playground_error", error=e)
            return self._gm.bad_request(_gateway_error_message(e))

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
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

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
            _log_gateway_error("test_playground_gateway_error", e)
            return self._gm.bad_request(_gateway_error_message(e))
        except Exception as e:
            _log_gateway_error("test_playground_stream_error", e)
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
                logger.warning("playground_config_push_failed", code="sync_failed")
                return False

            client.get_org_config(org_id)
            return True
        except Exception as e:
            _log_gateway_error("playground_config_push_failed", e)
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
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

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
        except MCPCredentialPlacementRefused as e:
            log_config_rejected("set_budget_rejected", org, e)
            return self._mcp_credential_placement_refused_response(e)
        except OrgConfigRejected as e:
            log_config_rejected("set_budget_rejected", org, e)
            return with_rejection_param(self._gm.bad_request(e), e)
        except Exception as e:
            _log_gateway_error("set_budget_error", error=e)
            return self._gm.bad_request(_gateway_error_message(e))

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
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

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
        except MCPCredentialPlacementRefused as e:
            log_config_rejected("remove_budget_rejected", org, e)
            return self._mcp_credential_placement_refused_response(e)
        except OrgConfigRejected as e:
            log_config_rejected("remove_budget_rejected", org, e)
            return with_rejection_param(self._gm.bad_request(e), e)
        except Exception as e:
            _log_gateway_error("remove_budget_error", error=e)
            return self._gm.bad_request(_gateway_error_message(e))

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
        """Submit as the session person, using a registered delegation.

        IR batches never use the admin bearer: the gateway runs them under a
        person grant (services/delegated_batches.py), so the scope guard, not
        a categorical refusal, is the P1 boundary here."""
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

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
        except (GatewayClientError, EnvelopeRejected) as e:
            _log_gateway_error("submit_batch_gateway_error", e)
            return self._gm.bad_request("Delegated batch submission unavailable")
        except Exception as e:
            _log_gateway_error("submit_batch_error", e)
            return self._gm.bad_request("Batch submission unavailable")

    @swagger_auto_schema(
        responses={
            200: GatewayBatchDetailResponseSerializer,
            **GATEWAY_BAD_REQUEST_OR_NOT_FOUND_RESPONSES,
        }
    )
    @action(detail=True, methods=["get"], url_path="get-batch")
    def get_batch(self, request, pk=None):
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

        from agentcc.services import batch_jobs
        from agentcc.services.batch_access import authorized_job

        try:
            batch_id = request.query_params.get("batch_id")
            if not batch_id:
                return self._gm.bad_request("batch_id query param is required")
            # Person and workspace authorization first; the gateway client is
            # acquired only for a batch this caller may read.
            job = authorized_job(request, batch_id)
            if job is None:
                return self._gm.not_found("Batch not found")
            client = get_gateway_client()
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
            except Exception as e:
                _log_gateway_error("batch_status_record_failed", e)
            return self._gm.success_response(result)
        except Exception as e:
            _log_gateway_error("get_batch_error", e)
            return self._gm.bad_request(_gateway_error_message(e))

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
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

        from agentcc.services.batch_access import authorized_job

        try:
            batch_id = request.validated_data.get("batch_id")
            if not batch_id:
                return self._gm.bad_request("batch_id is required")
            job = authorized_job(request, batch_id, cancel=True)
            if job is None:
                return self._gm.not_found("Batch not found")
            client = get_gateway_client()
            result = client.cancel_batch(batch_id, org_id=str(job.organization_id))
            return self._gm.success_response(result)
        except Exception as e:
            _log_gateway_error("cancel_batch_error", e)
            return self._gm.bad_request(_gateway_error_message(e))

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
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

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
                projected = public_provider_models(cred)
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
                        "base_url": public_provider_base_url(cred.base_url),
                        "api_format": cred.api_format,
                        "models": projected["models"],
                        # Saved model IDs left out because they match the
                        # provider's credential: a count, never the IDs.
                        "dropped_model_count": projected["dropped_model_count"],
                        "request_count": request_count,
                        "avg_latency": pstats.get("avg_latency_ms", 0),
                        "error_rate": round(
                            error_count / request_count * 100 if request_count else 0.0,
                            2,
                        ),
                    }
                )

            return self._gm.success_response(
                {
                    "providers": merged,
                    # Providers the push leaves out: a count, shown so a
                    # provider is never unpublished silently.
                    "refused_provider_count": sum(
                        1 for item in merged if not item["pushed"]
                    ),
                }
            )
        except Exception as e:
            _log_gateway_error("providers_error", error=e)
            return self._gm.bad_request(_gateway_error_message(e))

    # --- MCP ---

    @swagger_auto_schema(
        responses={
            200: GatewayMCPStatusResponseSerializer,
            **GATEWAY_BAD_REQUEST_RESPONSES,
        }
    )
    @action(detail=True, methods=["get"], url_path="mcp-status")
    def mcp_status(self, request, pk=None):
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

        try:
            result = mcp_admin.org_status(self._current_org(request))
            error_result = _mcp_error_result(result)
            if error_result is not None:
                return self._gm.success_response(error_result)
            return self._gm.success_response(
                {**result, "features": mcp_features.capabilities()}
            )
        except Exception as e:
            _log_gateway_error("mcp_status_error", e)
            return self._gm.bad_request(_gateway_error_message(e))

    @swagger_auto_schema(
        responses={
            200: AgentccListResultResponseSerializer,
        }
    )
    @action(detail=True, methods=["get"], url_path="mcp-tools")
    def mcp_tools(self, request, pk=None):
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

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
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

        vs = request.validated_query_data.get("vs", "")
        key_id = request.validated_query_data.get("key_id", "")
        server_id = request.validated_query_data.get("server_id", "")
        try:
            result = mcp_admin.org_served_tools(
                self._current_org(request), vs, key_id, server_id
            )
            if _mcp_error_result(result) is not None:
                return self._mcp_unreachable_response()
            return self._gm.success_response(result)
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
            _log_gateway_error("mcp_served_gateway_error", e)
            return self._mcp_unreachable_response()
        except Exception as e:
            _log_gateway_error("mcp_served_gateway_error", e)
            return self._mcp_unreachable_response()

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
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

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
            _log_gateway_error("update_mcp_server_error", e)
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
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

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
            except MCPCredentialPlacementRefused as e:
                # This refusal is also a ValueError, but not a missing server.
                log_config_rejected("remove_mcp_server_rejected", org, e)
                return self._mcp_credential_placement_refused_response(e)
            except OrgConfigRejected:
                # Local config rejections subclass ValueError; do not turn them into 404.
                raise
            except ValueError:
                return self._gm.not_found("MCP server not found.")
            data = {
                "server": server_id,
                "action": "removed",
                "version": new_config.version,
                "gateway_synced": synced,
            }
            if not synced:
                data["gateway_warning"] = _GATEWAY_SYNC_WARNING
            return self._gm.success_response(data)
        except OrgConfigRejected as e:
            log_config_rejected("remove_mcp_server_rejected", org, e)
            return with_rejection_param(self._gm.bad_request(e), e)
        except Exception as e:
            _log_gateway_error("remove_mcp_server_error", error=e)
            return self._gm.bad_request(_gateway_error_message(e))

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
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

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
        except MCPCredentialPlacementRefused as e:
            log_config_rejected("update_mcp_guardrails_rejected", org, e)
            return self._mcp_credential_placement_refused_response(e)
        except OrgConfigRejected as e:
            log_config_rejected("update_mcp_guardrails_rejected", org, e)
            return with_rejection_param(self._gm.bad_request(e), e)
        except Exception as e:
            _log_gateway_error("update_mcp_guardrails_error", error=e)
            return self._gm.bad_request(_gateway_error_message(e))

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
        """Run one of the org's own tools. mcp_admin.run_tool_test attributes
        the tool to the caller's servers on an org-scoped gateway before any
        run, and names only the caller's org to the gateway."""
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

        try:
            result = mcp_admin.run_tool_test(
                self._current_org(request),
                request.validated_data["name"],
                request.validated_data.get("arguments", {}),
            )
        except mcp_admin.NoMCPServers:
            return self._gm.bad_request(mcp_admin.NO_MCP_SERVERS_MESSAGE)
        except mcp_admin.MCPToolNotFound:
            return self._gm.custom_error_response(
                HTTP_404_NOT_FOUND,
                mcp_admin.MCP_TOOL_NOT_FOUND_MESSAGE,
                code=mcp_admin.MCP_TOOL_NOT_FOUND,
            )
        except mcp_admin.MCPDisabled as e:
            return self._mcp_disabled_response(e)
        except GatewayClientError as e:
            _log_gateway_error("test_mcp_tool_gateway_error", e)
            return self._gm.bad_request(_gateway_error_message(e))
        # The caller's own run is shown; a reported failure is categorized,
        # as for the MCP reads and the playground's failure bodies. A refusal
        # by the organization's MCP access default says why.
        denial = _mcp_access_default_denial(result)
        if denial is not None:
            return self._gm.success_response(denial)
        error_result = _mcp_error_result(result)
        if error_result is not None:
            return self._gm.success_response(error_result)
        return self._gm.success_response(result)

    @swagger_auto_schema(
        responses={
            200: AgentccListResultResponseSerializer,
        }
    )
    @action(detail=True, methods=["get"], url_path="mcp-resources")
    def mcp_resources(self, request, pk=None):
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

        return self._mcp_list_response(mcp_admin.org_resources, request)

    @swagger_auto_schema(
        responses={
            200: AgentccListResultResponseSerializer,
        }
    )
    @action(detail=True, methods=["get"], url_path="mcp-prompts")
    def mcp_prompts(self, request, pk=None):
        refusal = self._gateway_scope_refusal(request)
        if refusal is not None:
            return refusal

        return self._mcp_list_response(mcp_admin.org_prompts, request)

    def _mcp_list_response(self, read, request):
        """An MCP list read: 404 ``gateway_mcp_disabled`` when the deployment
        runs with MCP off, 502 ``gateway_unreachable`` when the gateway fails."""
        try:
            result = read(self._current_org(request))
            if _mcp_error_result(result) is not None:
                return self._mcp_unreachable_response()
            return self._gm.success_response(result)
        except mcp_admin.MCPDisabled as e:
            return self._mcp_disabled_response(e)
        except Exception as e:
            _log_gateway_error("mcp_list_gateway_error", e)
            return self._mcp_unreachable_response()

    def _mcp_unreachable_response(self):
        return self._gm.custom_error_response(
            HTTP_502_BAD_GATEWAY,
            mcp_admin.GATEWAY_UNREACHABLE_MESSAGE,
            code=mcp_admin.GATEWAY_UNREACHABLE,
        )

    def _mcp_disabled_response(self, error):
        return self._gm.custom_error_response(
            HTTP_404_NOT_FOUND,
            mcp_admin.GATEWAY_MCP_DISABLED_MESSAGE,
            code=mcp_admin.GATEWAY_MCP_DISABLED,
        )

    def _mcp_credential_placement_refused_response(self, error):
        return with_rejection_param(
            self._gm.custom_error_response(
                HTTP_400_BAD_REQUEST,
                _MCP_CREDENTIAL_PLACEMENT_REFUSED_MESSAGE.format(param=error.param),
                code=error.code,
            ),
            error,
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
            config = AgentccOrgConfig(
                organization=org,
                version=1,
                is_active=True,
                deleted=False,
                cost_tracking=default_cost_tracking_config(),
                managed_models=initial_managed_models(org),
            )
            config.disable_auto_workspace_assignment = True
            # Keep get_or_create's savepoint and competing-insert recovery,
            # while setting the per-instance opt-out before the first save.
            try:
                with transaction.atomic():
                    config.save(force_insert=True)
            except IntegrityError as exc:
                try:
                    config = AgentccOrgConfig.no_workspace_objects.get(
                        organization=org, version=1, is_active=True, deleted=False
                    )
                except AgentccOrgConfig.DoesNotExist:
                    raise exc from None
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
            _log_gateway_error("config_push_failed", e)
            return False
