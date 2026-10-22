"""
Config Push Service — pushes per-org configs from Django to the Go gateway.
Called when org configs are created, activated, or deleted.
"""

import copy
import uuid

import structlog
from django.conf import settings as django_settings
from django.db import transaction
from django.utils import timezone
from pydantic import ValidationError

from agentcc.contracts.gateway_admin import (
    AlertChannelConfig as GatewayAlertChannelConfig,
)
from agentcc.contracts.gateway_admin import AlertRuleConfig as GatewayAlertRuleConfig
from agentcc.contracts.gateway_admin import (
    DelegatedGrant as GatewayDelegatedGrant,
)
from agentcc.contracts.gateway_admin import IdentityConfig as GatewayIdentityConfig
from agentcc.contracts.gateway_admin import (
    IdentityIdPConfig as GatewayIdentityIdPConfig,
)
from agentcc.contracts.gateway_admin import (
    ManagedModelsConfig as GatewayManagedModelsConfig,
)
from agentcc.contracts.gateway_admin import (
    MCPCredentialSecret as GatewayMCPCredentialSecret,
)
from agentcc.contracts.gateway_admin import OrgConfig as GatewayOrgConfig
from agentcc.contracts.gateway_admin import ProviderConfig as GatewayProviderConfig
from agentcc.contracts.gateway_admin import RolloutConfig as GatewayRolloutConfig
from agentcc.contracts.gateway_admin import RoutingConfig as GatewayRoutingConfig
from agentcc.models import AgentccOrgConfig
from agentcc.org_config_defaults import normalize_cache_config
from agentcc.services.gateway_client import GatewayClientError, get_gateway_client

logger = structlog.get_logger(__name__)

# Map org-config rule names → gateway registry names
_RULE_TO_REGISTRY = {
    "pii-detector": "pii-detection",
    "injection-detector": "prompt-injection",
    "secrets-detector": "secret-detection",
    # These are the same in both:
    "content-moderation": "content-moderation",
    "keyword-blocklist": "keyword-blocklist",
    "topic-restriction": "topic-restriction",
    "language-detection": "language-detection",
    "system-prompt-protection": "system-prompt-protection",
    "hallucination-detection": "hallucination-detection",
    "data-leakage-prevention": "data-leakage-prevention",
}

# External guardrail rules that need a "provider" key in their config
# for the gateway's dynamic factory to recognize them.
_RULE_PROVIDER_DEFAULTS = {
    "futureagi-eval": "futureagi",
    "llama-guard": "llama_guard",
    "azure-content-safety": "azure_content_safety",
    "presidio-pii": "presidio",
    "lakera-guard": "lakera",
    "bedrock-guardrails": "bedrock_guardrails",
    "hiddenlayer-guard": "hiddenlayer",
    "aporia-guard": "aporia",
    "pangea-guard": "pangea",
    "dynamoai-guard": "dynamoai",
    "enkrypt-guard": "enkrypt",
    "ibm-ai-detector": "ibm_ai",
    "grayswan-guard": "grayswan",
    "lasso-guard": "lasso",
    "crowdstrike-aidr": "crowdstrike",
    "zscaler-guard": "zscaler",
    "tool-permissions": "tool_permissions",
    "mcp-security": "mcp_security",
}

_PROVIDER_CREDENTIAL_ALIASES = {
    "access_key": "aws_access_key_id",
    "secret_key": "aws_secret_access_key",
    "region": "aws_region",
}


def field_input_names(contract_model, field_name):
    """Every spelling the contract accepts for one field (snake and camel)."""
    field = contract_model.model_fields[field_name]
    names = {field_name}
    if field.alias:
        names.add(field.alias)
    validation_alias = field.validation_alias
    if validation_alias is not None:
        choices = getattr(validation_alias, "choices", (validation_alias,))
        names.update(choice for choice in choices if isinstance(choice, str))
    return frozenset(names)


def _contract_input_names(contract_model):
    return frozenset().union(
        *(
            field_input_names(contract_model, name)
            for name in contract_model.model_fields
        )
    )


_PROVIDER_INPUT_FIELDS = _contract_input_names(GatewayProviderConfig)
_ALERT_RULE_INPUT_FIELDS = _contract_input_names(GatewayAlertRuleConfig)
_ALERT_CHANNEL_INPUT_FIELDS = _contract_input_names(GatewayAlertChannelConfig)

# Per-org managed upstream keys exist only in a pushed payload (contract
# managed-upstream-keys-push-v1): never in a saved config, under any spelling,
# including a rollout candidate's managed_models overlay.
_UPSTREAM_KEYS_INPUTS = field_input_names(GatewayManagedModelsConfig, "upstream_keys")
_ROLLOUT_INPUTS = field_input_names(GatewayRoutingConfig, "rollout")
# The identity section's push-only parts: never stored.
_IDENTITY_GRANTS_INPUTS = field_input_names(GatewayIdentityConfig, "delegated_grants")
_IDENTITY_IDP_INPUTS = field_input_names(GatewayIdentityConfig, "idp")
_IDP_SECRET_INPUTS = field_input_names(GatewayIdentityIdPConfig, "client_secret")
_ROLLOUT_MANAGED_INPUTS = field_input_names(GatewayRolloutConfig, "managed_models")

# Keys the Monitoring UI stores on alert rules/channels for its own rendering
# (the Disabled status, a channel's severity_filter). The gateway has no
# equivalent, so they are projected out here, unlogged as they are expected.
# A rule's severity is pushed (contract AlertRuleConfig.severity).
_UI_ONLY_ALERTING_KEYS = frozenset({"enabled", "severity_filter"})


class UnsupportedProviderCredentialFields(ValueError):
    def __init__(self, provider_name, fields):
        self.provider_name = provider_name
        self.fields = tuple(sorted(fields))
        super().__init__(
            "Unsupported gateway credential fields for provider "
            f"{provider_name}: {', '.join(self.fields)}"
        )


def _normalize_eval_ids(cfg):
    """
    Normalize futureagi eval id config so the gateway receives both shapes:
    - eval_ids: ["76", "15", "22"] — array, what the new gateway iterates
    - eval_id: "76" — first id, fallback for older gateway builds

    Accepts either input shape. No-op if neither is present.
    """
    if not isinstance(cfg, dict):
        return
    raw = cfg.get("eval_ids")
    if raw is None and "eval_id" in cfg:
        raw = [cfg.get("eval_id")]
    if raw is None:
        return
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    eval_ids = [str(i) for i in raw if i not in (None, "")]
    if eval_ids:
        cfg["eval_ids"] = eval_ids
        cfg["eval_id"] = eval_ids[0]
    else:
        cfg.pop("eval_ids", None)
        cfg.pop("eval_id", None)


def guardrail_check_items(checks):
    """(name, check) pairs of a guardrail checks list, or of a map keyed by
    name. A list entry is named by its "name" key; anything else yields none."""
    if isinstance(checks, list):
        return [
            (check.get("name"), check) for check in checks if isinstance(check, dict)
        ]
    if isinstance(checks, dict):
        return [
            (name, check) for name, check in checks.items() if isinstance(check, dict)
        ]
    return []


def guardrail_policy_id(check):
    """The canonical id of the policy a check's `_policy` names, or None when
    it names none. A value that is not a UUID names no policy."""
    try:
        return str(uuid.UUID(str(check.get("_policy"))))
    except ValueError:
        return None


_POLICY_REF_PREFIX_CHARS = 8


def guardrail_policy_ref(check):
    """How a log or a message names the policy a check's `_policy` names: its
    canonical id. A string that is not a UUID is named by its first 8
    characters and its length, any other value by its type, so a value
    pasted there by mistake never reaches a log whole."""
    pid = guardrail_policy_id(check)
    if pid is not None:
        return pid
    value = check.get("_policy")
    if isinstance(value, str):
        return f"{value[:_POLICY_REF_PREFIX_CHARS]}... ({len(value)} chars)"
    return f"<{type(value).__name__}>"


def org_guardrail_policies(org_id, policy_ids):
    """The org's own live guardrail policies among `policy_ids`.

    `_policy` comes from a stored config a client can write, so a policy is
    read only when it belongs to the org being pushed. With no org, none is.
    """
    from agentcc.models.guardrail_policy import AgentccGuardrailPolicy

    if org_id is None or not policy_ids:
        return AgentccGuardrailPolicy.no_workspace_objects.none()
    return AgentccGuardrailPolicy.no_workspace_objects.filter(
        id__in=policy_ids, organization_id=org_id, deleted=False
    )


def _inject_guardrail_credentials(checks, org_id):
    """
    Decrypt and inject real credentials into guardrail checks, replacing
    "__encrypted__" sentinel values with actual secrets from the
    encrypted_check_configs blob of the org's own policy that the check names.

    A `_policy` that is not one of `org_id`'s live policies (another org's, a
    deleted or unknown one, or not a UUID at all) is never loaded: its check
    gets no credential, and the log names only the policy id. A check without
    `_policy` is left as it is.
    """
    from agentcc.serializers.guardrail_policy import ENCRYPTED_SENTINEL
    from integrations.services.credentials import CredentialManager

    items = [
        (name, check)
        for name, check in guardrail_check_items(checks)
        if check.get("_policy")
    ]
    if not items:
        return

    policy_ids = {pid for _, check in items if (pid := guardrail_policy_id(check))}
    policies = list(
        org_guardrail_policies(org_id, policy_ids).only("id", "encrypted_check_configs")
    )
    decrypted_map = {}  # {policy_id: {check_name: {key: value}}}
    for policy in policies:
        if policy.encrypted_check_configs:
            try:
                decrypted_map[str(policy.id)] = CredentialManager.decrypt(
                    bytes(policy.encrypted_check_configs)
                )
            except Exception:
                logger.warning(
                    "guardrail_credential_decrypt_failed",
                    policy_id=str(policy.id),
                )
    for pid in sorted(policy_ids - {str(policy.id) for policy in policies}):
        logger.warning("guardrail_policy_not_in_org", policy_id=pid)
    not_ids = {
        guardrail_policy_ref(check)
        for _, check in items
        if guardrail_policy_id(check) is None
    }
    for ref in sorted(not_ids):
        logger.warning(
            "guardrail_policy_not_in_org", policy_id=ref, reason="not_a_uuid"
        )

    for check_name, check in items:
        cfg = check.get("config")
        if not check_name or not isinstance(cfg, dict):
            continue
        policy_secrets = decrypted_map.get(guardrail_policy_id(check), {})
        check_secrets = policy_secrets.get(check_name, {})
        for key, value in list(cfg.items()):
            if value == ENCRYPTED_SENTINEL:
                if key in check_secrets:
                    cfg[key] = check_secrets[key]
                else:
                    # No decrypted value available — remove sentinel
                    del cfg[key]


def _transform_guardrails(guardrails_data, org_id=None):
    """
    Transform Django org-config guardrails (rules array) into gateway tenant
    format (checks object map with registry names).

    Django stores: {"rules": [{"name": "pii-detector", "action": "block", ...}], "enabled": true}
    Gateway expects: {"checks": {"pii-detection": {"enabled": true, "action": "block", ...}}}

    Deep-copies the input because downstream helpers (_inject_guardrail_credentials,
    _inject_fi_credentials) mutate sub-dicts in place — without the copy those
    mutations would leak back into the caller's Django model instance and the
    next AgentccOrgConfigSerializer(config).data would render decrypted secrets.
    """
    if not guardrails_data:
        return guardrails_data

    guardrails_data = copy.deepcopy(guardrails_data)
    raw_checks = guardrails_data.get("checks")
    rules = guardrails_data.get("rules")

    # Checks as list (from guardrail_sync — merged policies)
    if isinstance(raw_checks, list) and raw_checks:
        # Inject decrypted guardrail credentials (replaces __encrypted__ sentinels)
        _inject_guardrail_credentials(raw_checks, org_id)

        checks = {}
        for rule in raw_checks:
            rule_name = rule.get("name", "")
            registry_name = _RULE_TO_REGISTRY.get(rule_name, rule_name)
            cfg = dict(rule.get("config") or {})
            if rule_name in _RULE_PROVIDER_DEFAULTS and "provider" not in cfg:
                cfg["provider"] = _RULE_PROVIDER_DEFAULTS[rule_name]
            _normalize_eval_ids(cfg)
            checks[registry_name] = {
                "enabled": rule.get("enabled", True),
                "action": rule.get("action", "block"),
                "confidence_threshold": rule.get("threshold", 0.8),
                "config": cfg,
            }
        if org_id:
            _inject_fi_credentials(checks, org_id)
        return _rebuilt_guardrails(guardrails_data, checks)

    # Checks as dict (from frontend saves via OrgConfig)
    if isinstance(raw_checks, dict) and raw_checks:
        # Inject decrypted guardrail credentials (replaces __encrypted__ sentinels)
        _inject_guardrail_credentials(raw_checks, org_id)

        mapped = {}
        for name, cfg in raw_checks.items():
            registry_name = _RULE_TO_REGISTRY.get(name, name)
            # Strip internal metadata keys before forwarding to gateway
            clean_cfg = (
                {k: v for k, v in cfg.items() if not k.startswith("_")}
                if isinstance(cfg, dict)
                else cfg
            )
            inner = clean_cfg.get("config") if isinstance(clean_cfg, dict) else None
            _normalize_eval_ids(inner)
            mapped[registry_name] = clean_cfg
        if org_id:
            _inject_fi_credentials(mapped, org_id)
        return {**guardrails_data, "checks": mapped}

    # Convert rules array → checks map
    if not isinstance(rules, list) or not rules:
        return _section_without_checks(guardrails_data)

    # Inject decrypted guardrail credentials before transformation
    _inject_guardrail_credentials(rules, org_id)

    checks = {}
    for rule in rules:
        rule_name = rule.get("name", "")
        registry_name = _RULE_TO_REGISTRY.get(rule_name, rule_name)
        cfg = dict(rule.get("config") or {})
        if rule_name in _RULE_PROVIDER_DEFAULTS and "provider" not in cfg:
            cfg["provider"] = _RULE_PROVIDER_DEFAULTS[rule_name]
        _normalize_eval_ids(cfg)
        checks[registry_name] = {
            "enabled": rule.get("enabled", True),
            "action": rule.get("action", "block"),
            "confidence_threshold": rule.get("threshold", 0.8),
            "config": cfg,
        }

    # Auto-inject org's FI platform credentials for futureagi-eval
    if org_id:
        _inject_fi_credentials(checks, org_id)

    return _rebuilt_guardrails(guardrails_data, checks)


# The keys a stored section may hold that the gateway contract does not take,
# when the section holds no check: an "enabled" flag next to an empty checks
# list, and an emptied rules list (the rules editor's save once its last rule
# is deleted).
_EMPTY_SECTION_EXTRA_KEYS = ("enabled", "rules")


def _section_without_checks(guardrails_data):
    """The gateway section for a stored section that holds no check: no rule
    in its rules list and no check in its checks list or map.

    Such a section is sent as an empty checks map beside its section-level
    settings, which the contract accepts and the gateway reads as "no org
    check", so the gateway stops running the checks it had. A section with a
    key the contract would refuse (an "enabled" flag, an emptied rules list, an
    empty checks list) is sent this way too. A section that is already valid is
    sent as stored, and one whose rules or checks are not a list or a map is
    left for the contract to refuse.
    """
    rules = guardrails_data.get("rules")
    checks = guardrails_data.get("checks")
    if rules is not None and rules != []:
        return guardrails_data
    if checks is not None and not (isinstance(checks, (list, dict)) and not checks):
        return guardrails_data
    if not any(key in guardrails_data for key in _EMPTY_SECTION_EXTRA_KEYS) and (
        not isinstance(checks, list)
    ):
        return guardrails_data
    section = {
        key: value
        for key, value in guardrails_data.items()
        if key not in _EMPTY_SECTION_EXTRA_KEYS
    }
    section["checks"] = {}
    return section


def _rebuilt_guardrails(guardrails_data, checks):
    """The gateway guardrail section for a rebuilt checks map, keeping the
    section-level settings (including tool_calls) that sit beside the checks."""
    result = {
        "checks": checks,
        "fail_open": guardrails_data.get(
            "failOpen", guardrails_data.get("fail_open", False)
        ),
        "pipeline_mode": guardrails_data.get("pipeline_mode", "parallel"),
    }
    if guardrails_data.get("timeout_ms"):
        result["timeout_ms"] = guardrails_data["timeout_ms"]
    for key in ("tool_calls", "toolCalls"):
        if key in guardrails_data:
            result[key] = guardrails_data[key]
    return result


def _normalize_url(url):
    """Strip trailing slash and lowercase scheme+host for comparison."""
    if not url or not isinstance(url, str):
        return ""
    return url.strip().rstrip("/").lower()


def _inject_fi_credentials(checks, org_id):
    """
    Inject the org's FI platform credentials into futureagi-eval config.

    Behavior:
    - base_url missing → fill with django_settings.BASE_URL, force-inject the
      org's OrgApiKey for api_key/secret_key. This is the common case: the
      guardrail is calling its own platform, and the user shouldn't have to
      know their own platform's API key.
    - base_url present and matches this platform's BASE_URL → same as above,
      force-inject (heals user-typed wrong keys for the local platform).
    - base_url points at a *different* environment (e.g. local gateway
      explicitly targeting https://dev.api.futureagi.com) → respect whatever
      api_key/secret_key the user provided. Local keys would 401 against the
      remote env, so auto-injecting would break this case.
    """
    fi_check = checks.get("futureagi-eval")
    if fi_check is None:
        return

    if not fi_check.get("enabled", False):
        return

    cfg = fi_check.get("config") or {}

    cfg.setdefault("call_type", "protect")
    cfg.setdefault("base_url", django_settings.BASE_URL)
    fi_check["config"] = cfg

    targets_local_platform = _normalize_url(cfg.get("base_url")) == _normalize_url(
        django_settings.BASE_URL
    )
    if not targets_local_platform:
        logger.info(
            "fi_credentials_skipped_cross_env",
            org_id=str(org_id),
            base_url=cfg.get("base_url"),
        )
        return

    try:
        from accounts.models.user import OrgApiKey

        org_key = OrgApiKey.no_workspace_objects.filter(
            organization_id=org_id,
            type="system",
            enabled=True,
            deleted=False,
        ).first()
        if org_key:
            cfg["api_key"] = org_key.api_key
            cfg["secret_key"] = org_key.secret_key
            logger.info(
                "fi_credentials_injected",
                org_id=str(org_id),
            )
        else:
            logger.warning(
                "fi_credentials_not_found",
                org_id=str(org_id),
            )
    except Exception:
        logger.warning(
            "fi_credentials_inject_failed",
            org_id=str(org_id),
            exc_info=True,
        )


def _normalize_provider_credentials(provider_name, decrypted):
    normalized = {}
    unsupported = []
    for key, value in decrypted.items():
        if value in (None, ""):
            continue
        canonical_key = _PROVIDER_CREDENTIAL_ALIASES.get(key, key)
        if canonical_key in GatewayProviderConfig.model_fields:
            normalized[canonical_key] = value
        else:
            unsupported.append(key)

    if unsupported:
        raise UnsupportedProviderCredentialFields(provider_name, unsupported)
    return normalized


def _normalize_provider_extra_config(provider_name, extra_config):
    if not isinstance(extra_config, dict):
        return {}

    normalized = {}
    ignored = []
    for key, value in extra_config.items():
        if key in _PROVIDER_INPUT_FIELDS:
            normalized[key] = value
        elif value not in (None, "", [], {}):
            ignored.append(key)

    if ignored:
        logger.warning(
            "provider_extra_config_ignored",
            provider=provider_name,
            keys=sorted(ignored),
        )
    return normalized


def _assemble_providers(org_id):
    """Build providers dict from AgentccProviderCredential rows for an org."""
    from agentcc.models.provider_credential import AgentccProviderCredential
    from integrations.services.credentials import CredentialManager

    credentials = AgentccProviderCredential.no_workspace_objects.filter(
        organization_id=org_id,
        is_active=True,
        deleted=False,
    )
    providers = {}
    for cred in credentials:
        try:
            decrypted = CredentialManager.decrypt(bytes(cred.encrypted_credentials))
        except Exception:
            logger.warning(
                "provider_credential_decrypt_failed",
                org_id=str(org_id),
                provider=cred.provider_name,
            )
            continue

        try:
            raw = {
                **_normalize_provider_extra_config(
                    cred.provider_name, cred.extra_config
                ),
                **_normalize_provider_credentials(cred.provider_name, decrypted),
                "base_url": cred.base_url,
                "api_format": cred.api_format,
                "models": cred.models_list,
                "enabled": True,
                "timeout": cred.default_timeout_seconds,
                "max_concurrent": cred.max_concurrent,
                "conn_pool_size": cred.conn_pool_size,
            }
            providers[cred.provider_name] = GatewayProviderConfig.model_validate(
                raw
            ).model_dump(by_alias=True, exclude_none=True)
        except UnsupportedProviderCredentialFields as e:
            logger.warning(
                "provider_credential_unsupported_fields",
                org_id=str(org_id),
                provider=cred.provider_name,
                fields=list(e.fields),
            )
            continue
    return providers


def _as_named_entries(value):
    """
    Coerce an alerting rules/channels collection into a list of dicts.

    Two writers produce two shapes: Settings → Alerting saves an array, while
    Monitoring → Create Rule saves a name-keyed object so its patch can add one
    entry without clobbering the rest. Returns None when the value is neither.
    """
    if isinstance(value, dict):
        return [
            {"name": name, **entry} if isinstance(entry, dict) else {"name": name}
            for name, entry in value.items()
        ]
    if isinstance(value, list):
        return [entry for entry in value if isinstance(entry, dict)]
    return None


def _literal_values(annotation):
    """The values of every Literal inside a contract field's annotation."""
    from typing import Literal, get_args, get_origin

    if get_origin(annotation) is Literal:
        return set(get_args(annotation))
    values = set()
    for arg in get_args(annotation):
        values |= _literal_values(arg)
    return values


# The severities the gateway contract takes on an alert rule.
_ALERT_RULE_SEVERITIES = frozenset(
    _literal_values(GatewayAlertRuleConfig.model_fields["severity"].annotation)
)


def _pushable_severity(rule):
    """
    Put a rule's stored severity in the contract's form, or leave it out.

    The UI writes info, warning or critical; a row written through the API may
    hold any string. One the contract cannot take would fail the org's whole
    config, so it is left out (and logged): the gateway then sends its default,
    warning.
    """
    severity = rule.get("severity")
    if severity is None:
        rule.pop("severity", None)
        return rule
    if isinstance(severity, str) and severity.lower() in _ALERT_RULE_SEVERITIES:
        rule["severity"] = severity.lower()
        return rule
    logger.warning(
        "alerting_severity_ignored",
        rule=str(rule.get("name", ""))[:128],
        severity=str(severity)[:32],
    )
    del rule["severity"]
    return rule


def _project_alerting_entries(entries, allowed_fields, kind):
    """
    Drop disabled entries and any key the gateway's alerting contract forbids.

    The gateway evaluates every rule it is given — it has no per-rule enable
    switch — so a rule stored with `enabled: false` is omitted rather than
    forwarded with the flag stripped. A rule's severity is pushed in the
    contract's form (_pushable_severity).
    """
    projected = []
    unknown = set()
    for entry in entries:
        if entry.get("enabled") is False:
            continue
        item = {k: v for k, v in entry.items() if k in allowed_fields}
        projected.append(_pushable_severity(item) if kind == "rules" else item)
        unknown.update(
            k
            for k in entry
            if k not in allowed_fields and k not in _UI_ONLY_ALERTING_KEYS
        )

    if unknown:
        logger.warning("alerting_fields_ignored", kind=kind, keys=sorted(unknown))
    return projected


def _normalize_alerting(alerting):
    """
    Project stored alerting config onto the gateway admin contract.

    The `alerting` column is a free-form JSONField that the UI writes directly,
    but `AlertingConfig` is `extra="forbid"`. Without this projection any key
    the UI adds for its own use fails validation, and because the bulk sync
    endpoint validates every org in one pass, a single such rule takes the whole
    fleet's config sync down.
    """
    if not alerting or not isinstance(alerting, dict):
        return alerting

    result = {**alerting}
    for key, allowed_fields in (
        ("rules", _ALERT_RULE_INPUT_FIELDS),
        ("channels", _ALERT_CHANNEL_INPUT_FIELDS),
    ):
        entries = _as_named_entries(result.get(key))
        if entries is None:
            continue
        result[key] = _project_alerting_entries(entries, allowed_fields, key)
    return result


def _extract_budget_action(entry):
    if not isinstance(entry, dict):
        return None

    action = (
        entry.get("action")
        or entry.get("action_mode")
        or entry.get("actionMode")
        or entry.get("on_exceed")
        or entry.get("onExceed")
    )
    if isinstance(action, str) and action:
        return action.lower()
    return None


def _normalize_budget_action(entry, hard_key):
    if not isinstance(entry, dict):
        return entry

    result = {**entry}
    normalized_action = _extract_budget_action(result)
    if normalized_action:
        result["action"] = normalized_action
        result[hard_key] = normalized_action == "block"
    return result


def _budget_alert_fraction(entry):
    """Set Budget's Alert Threshold (%) as the gateway's fraction of the
    limit: a number in (0, 100] divided by 100, else None (not sent)."""
    if not isinstance(entry, dict):
        return None
    pct = entry.get("alert_threshold", entry.get("alertThreshold"))
    if isinstance(pct, bool) or not isinstance(pct, (int, float)):
        return None
    if not 0 < pct <= 100:
        return None
    return pct / 100


def _budget_throttle_rpm(entry):
    """A level's throttle_rpm when it is a whole number from 1, else None."""
    if not isinstance(entry, dict):
        return None
    rpm = entry.get("throttle_rpm", entry.get("throttleRpm"))
    if isinstance(rpm, bool) or not isinstance(rpm, int) or rpm < 1:
        return None
    return rpm


def _budget_saved_at(entry):
    """When a Set Budget save was made (its saved_at, UTC), or None for an
    unstamped entry or one with an unreadable stamp."""
    from datetime import UTC, datetime

    value = entry.get("saved_at") if isinstance(entry, dict) else None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


_BUDGET_DIALOG_ORG_KEYS = ("org_limit", "hard_limit")


def _latest_stamped_org_budget_entry(budgets):
    """Of the Budgets page's two org-level entries (Organization = org_limit,
    Hard Limit = hard_limit), the one saved last, when either is stamped; a
    stamped save beats an unstamped one. None when neither is stamped."""
    latest = None
    for key in _BUDGET_DIALOG_ORG_KEYS:
        saved_at = _budget_saved_at(budgets.get(key))
        if saved_at is not None and (latest is None or saved_at > latest[1]):
            latest = (key, saved_at)
    return latest[0] if latest else None


def _transform_budget_level(entry):
    """One level (team, user, key, tag, organization) as the gateway's
    contract takes it: the action as action + hard, Alert Threshold (%) as
    alert_thresholds, throttle_rpm only when valid, no saved_at."""
    if not isinstance(entry, dict):
        return entry

    result = _normalize_budget_action(entry, "hard")
    fraction = _budget_alert_fraction(result)
    rpm = _budget_throttle_rpm(result)
    for key in ("alert_threshold", "alertThreshold", "saved_at", "throttleRpm"):
        result.pop(key, None)
    if fraction is not None and not result.get("alert_thresholds"):
        result["alert_thresholds"] = [fraction]
    if rpm is None:
        result.pop("throttle_rpm", None)
    else:
        result["throttle_rpm"] = rpm
    return result


def _transform_budget_levels(levels):
    if not isinstance(levels, dict):
        return levels

    return {name: _transform_budget_level(entry) for name, entry in levels.items()}


def _apply_org_budget_entry(result, key, entry):
    """Push one org-level dialog entry as the flat org budget, whole: its
    limit, period, action, alert threshold and throttle rate (a Hard Limit
    entry with neither an action nor a hard flag blocks)."""
    if entry.get("limit") is not None:
        result["org_limit"] = entry["limit"]
    if entry.get("period"):
        result["org_period"] = entry["period"]

    normalized_action = _extract_budget_action(entry)
    if normalized_action:
        result["action"] = normalized_action
        result["hard_limit"] = normalized_action == "block"
    elif "hard" in entry:
        result["hard_limit"] = bool(entry["hard"])
    elif key == "hard_limit":
        result["hard_limit"] = True

    fraction = _budget_alert_fraction(entry)
    if fraction is not None:
        result["warn_threshold"] = fraction
    rpm = _budget_throttle_rpm(entry)
    if rpm is not None:
        result["throttle_rpm"] = rpm


def _composite_org_budget(budgets):
    """The org budget for unstamped entries: the Organization entry's limit
    over Hard Limit's, the action of the last entry read, with the alert
    threshold and throttle rate of those entries."""
    result = {**budgets}
    limit_entry = action_entry = None

    flat_org_budget = result.get("org_limit")
    if isinstance(flat_org_budget, dict):
        limit_entry = flat_org_budget
        if flat_org_budget.get("limit") is not None:
            result["org_limit"] = flat_org_budget["limit"]
        if flat_org_budget.get("period"):
            result["org_period"] = flat_org_budget["period"]

        normalized_action = _extract_budget_action(flat_org_budget)
        if normalized_action:
            action_entry = flat_org_budget
            result["action"] = normalized_action
            result["hard_limit"] = normalized_action == "block"
        elif "hard" in flat_org_budget and "hard_limit" not in result:
            result["hard_limit"] = bool(flat_org_budget["hard"])

    flat_hard_budget = result.get("hard_limit")
    if isinstance(flat_hard_budget, dict):
        if "org_limit" not in result and flat_hard_budget.get("limit") is not None:
            limit_entry = flat_hard_budget
            result["org_limit"] = flat_hard_budget["limit"]
        if "org_period" not in result and flat_hard_budget.get("period"):
            result["org_period"] = flat_hard_budget["period"]

        normalized_action = _extract_budget_action(flat_hard_budget)
        if normalized_action:
            action_entry = flat_hard_budget
            result["action"] = normalized_action
            result["hard_limit"] = normalized_action == "block"
        elif "hard" in flat_hard_budget:
            result["hard_limit"] = bool(flat_hard_budget["hard"])
        if isinstance(result.get("hard_limit"), dict):
            # A Hard Limit entry without an action or hard flag blocks.
            result["hard_limit"] = True

    for org_key in ("organization", "org"):
        org_budget = result.get(org_key)
        if not isinstance(org_budget, dict):
            continue

        if "org_limit" not in result and org_budget.get("limit") is not None:
            limit_entry = org_budget
            result["org_limit"] = org_budget["limit"]
        if "org_period" not in result and org_budget.get("period"):
            result["org_period"] = org_budget["period"]

        normalized_action = _extract_budget_action(org_budget)
        if normalized_action:
            action_entry = org_budget
            result["action"] = normalized_action
            result["hard_limit"] = normalized_action == "block"
        elif "hard" in org_budget and "hard_limit" not in result:
            result["hard_limit"] = bool(org_budget["hard"])
        break

    fraction = _budget_alert_fraction(limit_entry)
    if fraction is not None:
        result["warn_threshold"] = fraction
    rpm = _budget_throttle_rpm(action_entry)
    if rpm is not None:
        result["throttle_rpm"] = rpm
    return result


def _budget_levels_configured(result):
    """Whether any level of a pushed budgets section has a limit above 0."""

    def positive(value):
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value > 0
        )

    if positive(result.get("org_limit")):
        return True
    for org_key in ("organization", "org"):
        entry = result.get(org_key)
        if isinstance(entry, dict) and positive(entry.get("limit")):
            return True
    for level_key in ("teams", "users", "keys", "tags"):
        levels = result.get(level_key)
        if isinstance(levels, dict) and any(
            isinstance(entry, dict) and positive(entry.get("limit"))
            for entry in levels.values()
        ):
            return True
    return False


def _transform_budgets(budgets):
    """The stored budgets section as the gateway's budget plugin reads it.

    - The Budgets page's Set Budget stores Organization (org_limit) and Hard
      Limit (hard_limit) as dialog entries; the one saved last is pushed as the
      org budget, whole (unstamped entries are pushed as the composite of
      both).
    - When Limit Exceeded becomes action + hard_limit (hard on a level);
      Throttle also sends its throttle_rpm; Alert Threshold (%) becomes
      warn_threshold (alert_thresholds on a level).
    - enabled: as stored when the org chose (an explicit off stays off), else
      on exactly when some level has a limit above 0.
    """
    if not budgets or not isinstance(budgets, dict):
        return budgets

    stamped = _latest_stamped_org_budget_entry(budgets)
    if stamped is not None:
        result = {
            key: value
            for key, value in budgets.items()
            if key not in _BUDGET_DIALOG_ORG_KEYS or not isinstance(value, dict)
        }
        _apply_org_budget_entry(result, stamped, budgets[stamped])
    else:
        result = _composite_org_budget(budgets)

    top_level = _normalize_budget_action(result, "hard_limit")
    if isinstance(top_level, dict):
        result = top_level
    if _budget_throttle_rpm(result) is None:
        result.pop("throttle_rpm", None)
    result.pop("throttleRpm", None)

    for org_key in ("organization", "org"):
        if isinstance(result.get(org_key), dict):
            result[org_key] = _transform_budget_level(result[org_key])
    for level_key in ("teams", "users", "keys", "tags"):
        result[level_key] = _transform_budget_levels(result.get(level_key))

    if budgets.get("enabled") is None:
        result["enabled"] = _budget_levels_configured(result)
    return result


def _section_present(section, names):
    return sorted(names & section.keys()) if isinstance(section, dict) else []


def push_only_secret_paths(*, managed_models=None, routing=None, identity=None):
    """Paths in saved config sections that hold push-only secrets: managed
    upstream keys, and the identity section's IdP client secret and delegated
    grants."""
    paths = [
        f"managed_models.{key}"
        for key in _section_present(managed_models, _UPSTREAM_KEYS_INPUTS)
    ]
    paths += [
        f"identity.{key}" for key in _section_present(identity, _IDENTITY_GRANTS_INPUTS)
    ]
    for idp_key in _section_present(identity, _IDENTITY_IDP_INPUTS):
        paths += [
            f"identity.{idp_key}.{key}"
            for key in _section_present(identity[idp_key], _IDP_SECRET_INPUTS)
        ]
    for rollout_key in _section_present(routing, _ROLLOUT_INPUTS):
        rollout = routing[rollout_key]
        for managed_key in _section_present(rollout, _ROLLOUT_MANAGED_INPUTS):
            paths += [
                f"routing.{rollout_key}.{managed_key}.{key}"
                for key in _section_present(rollout[managed_key], _UPSTREAM_KEYS_INPUTS)
            ]
    return paths


def _managed_models_enabled(config):
    """True when the org, or its rollout candidate, opts in to managed models."""
    managed_models = config.managed_models
    if isinstance(managed_models, dict) and managed_models.get("enabled") is True:
        return True
    routing = config.routing if isinstance(config.routing, dict) else {}
    for rollout_key in _section_present(routing, _ROLLOUT_INPUTS):
        for managed_key in _section_present(
            routing[rollout_key], _ROLLOUT_MANAGED_INPUTS
        ):
            overlay = routing[rollout_key][managed_key]
            if isinstance(overlay, dict) and overlay.get("enabled") is True:
                return True
    return False


_UPSTREAM_KEY_REQUIRED = ("api_key", "key_hash")
_UPSTREAM_KEY_FIELDS = frozenset({*_UPSTREAM_KEY_REQUIRED, "label"})


def _upstream_key_problem(provider_id, entry):
    """Why one upstream key entry breaks managed-upstream-keys-push-v1, or
    None. The reason names fields, never their values."""
    if not isinstance(provider_id, str) or not provider_id:
        return "provider id is not a non-empty string"
    if not isinstance(entry, dict):
        return "entry is not an object"
    if not _UPSTREAM_KEY_FIELDS.issuperset(entry):
        return "entry has fields other than api_key, key_hash and label"
    for field in _UPSTREAM_KEY_REQUIRED:
        value = entry.get(field)
        if not isinstance(value, str) or not value:
            return f"{field} is not a non-empty string"
    if entry.get("label") is not None and not isinstance(entry["label"], str):
        return "label is not a string"
    return None


def _valid_upstream_keys(org_id, upstream_keys):
    """The entries that match the push contract. Each invalid one
    is dropped and logged by provider id and reason, so a malformed key can
    neither reach a log nor fail the org's whole config in the bulk sync."""
    if not isinstance(upstream_keys, dict):
        logger.warning(
            "managed_upstream_keys_invalid",
            org_id=str(org_id),
            reason="result is not an object keyed by provider id",
        )
        return {}
    valid = {}
    for provider_id, entry in upstream_keys.items():
        problem = _upstream_key_problem(provider_id, entry)
        if problem is None:
            valid[provider_id] = entry
            continue
        logger.warning(
            "managed_upstream_key_dropped",
            org_id=str(org_id),
            provider_id=(
                provider_id
                if isinstance(provider_id, str)
                else type(provider_id).__name__
            ),
            reason=problem,
        )
    return valid


def _assemble_managed_upstream_keys(org_id):
    """
    Per-org managed upstream keys for the push payload, from the managed
    upstream key service, keyed by managed provider id.

    Returns {} when that service cannot be imported. A failure is logged and
    pushes no keys rather than failing the push: the gateway then fails closed
    in per_org mode (503 managed_upstream_key_missing), and a raising service
    must not stop every opted-in org's config from syncing. The failure is
    logged by its type only, because the message could carry a key.
    """
    try:
        from agentcc.services.managed_upstream_keys import assemble_for_push
    except ImportError:
        return {}
    try:
        upstream_keys = assemble_for_push(org_id) or {}
    except Exception as e:
        logger.warning(
            "managed_upstream_keys_assemble_failed",
            org_id=str(org_id),
            error_type=type(e).__name__,
        )
        return {}
    return _valid_upstream_keys(org_id, upstream_keys)


# The contract's minimum for managed_models.default_max_tokens_estimate, the
# output a managed request that sets no max_tokens is held for. The gateway
# holds the same floor (billing.MinOrgDefaultMaxOut);
# test_managed_estimate_floor.py checks all three agree.
MANAGED_ESTIMATE_FLOOR = 1024
_ESTIMATE_INPUTS = field_input_names(
    GatewayManagedModelsConfig, "default_max_tokens_estimate"
)


def _floored_estimate(managed_models):
    """managed_models as pushed: a stored default_max_tokens_estimate below the
    floor is pushed at MANAGED_ESTIMATE_FLOOR, so the contract accepts the
    org's config and the gateway's bulk sync keeps the org. A new dict when one
    changes; the stored one is untouched."""
    if not isinstance(managed_models, dict):
        return managed_models
    floored = None
    for key in _section_present(managed_models, _ESTIMATE_INPUTS):
        value = managed_models[key]
        if (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value < MANAGED_ESTIMATE_FLOOR
        ):
            floored = floored if floored is not None else dict(managed_models)
            floored[key] = MANAGED_ESTIMATE_FLOOR
    return floored if floored is not None else managed_models


def _floored_rollout_estimate(routing):
    """routing as pushed, with its rollout candidate's managed_models through
    _floored_estimate. New dicts only on the changed path."""
    if not isinstance(routing, dict):
        return routing
    out = routing
    for rollout_key in _section_present(routing, _ROLLOUT_INPUTS):
        rollout = routing[rollout_key]
        for managed_key in _section_present(rollout, _ROLLOUT_MANAGED_INPUTS):
            floored = _floored_estimate(rollout[managed_key])
            if floored is not rollout[managed_key]:
                if out is routing:
                    out = dict(routing)
                out[rollout_key] = {**out[rollout_key], managed_key: floored}
    return out


def _managed_models_payload(org_id, config, *, with_upstream_keys=True):
    managed_models = config.managed_models
    if not with_upstream_keys or not _managed_models_enabled(config):
        return managed_models
    upstream_keys = _assemble_managed_upstream_keys(org_id)
    if not upstream_keys:
        return managed_models
    # A new dict: the keys must never reach the model instance that is saved.
    return {**(managed_models or {}), "upstream_keys": upstream_keys}


def _assemble_mcp_credentials(org_id):
    """
    Push-only MCP secrets for the push payload, from the MCP credential vault:
    {server_id: {type, header, value}} for the org's active shared credentials,
    keyed by an mcp.servers id or a managed_tools.credentials id.

    Returns {} when that service is not installed. Like the managed upstream
    keys, a failure is logged by its type only and pushes no secrets, an entry
    that breaks the MCPCredentialSecret contract is dropped and logged by
    server id and field, never a value, and the rest of the org's config still
    pushes.
    """
    try:
        from agentcc.services.mcp_credentials import assemble_for_push
    except ImportError:
        return {}
    try:
        secrets = assemble_for_push(org_id) or {}
    except Exception as e:
        logger.warning(
            "mcp_credentials_assemble_failed",
            org_id=str(org_id),
            error_type=type(e).__name__,
        )
        return {}
    if not isinstance(secrets, dict):
        logger.warning(
            "mcp_credentials_invalid",
            org_id=str(org_id),
            reason="result is not an object keyed by server id",
        )
        return {}
    valid = {}
    for server_id, secret in secrets.items():
        problem = _contract_problem(GatewayMCPCredentialSecret, secret)
        if problem is None and isinstance(server_id, str) and server_id:
            valid[server_id] = secret
            continue
        logger.warning(
            "mcp_credential_dropped",
            org_id=str(org_id),
            server_id=server_id if isinstance(server_id, str) else "",
            reason=problem or "server id is not a non-empty string",
        )
    return valid


def _contract_problem(contract_model, value):
    """Why `value` breaks `contract_model`, naming fields and error types only
    (pydantic's own messages can quote the value), or None."""
    try:
        contract_model.model_validate(value, strict=True)
    except ValidationError as e:
        return "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'value'}: {error['type']}"
            for error in e.errors()
        )
    return None


def _mcp_payload(org_id, config, *, with_upstream_keys=True):
    """The mcp section, with each assembled secret put where the gateway reads
    it: mcp.servers.<id>.credential.secret, or for a managed server the org's
    mcp.managed_tools.credentials.<id>.secret. A secret whose id names neither
    is dropped and logged. The stored section is never changed.

    An org with no server and no managed credential has nowhere to put a
    secret, so the vault is not asked: the bulk sync builds every org's
    payload on each poll."""
    mcp = config.mcp
    if not with_upstream_keys or not isinstance(mcp, dict):
        return mcp
    if not _mcp_credential_targets(mcp):
        return mcp
    secrets = _assemble_mcp_credentials(org_id)
    if not secrets:
        return mcp
    merged = copy.deepcopy(mcp)
    servers, managed = _mcp_credential_targets(merged)
    for server_id, secret in secrets.items():
        if isinstance(servers.get(server_id), dict):
            server = servers[server_id]
            credential = server.get("credential")
            server["credential"] = {
                **(credential if isinstance(credential, dict) else {}),
                "secret": secret,
            }
        elif isinstance(managed.get(server_id), dict):
            managed[server_id]["secret"] = secret
        else:
            logger.warning(
                "mcp_credential_dropped",
                org_id=str(org_id),
                server_id=server_id,
                reason="no mcp.servers or mcp.managed_tools.credentials entry",
            )
    return merged


def _mcp_credential_targets(mcp):
    """The server entries and managed credential entries of an mcp section
    that a secret can be put into, as (servers, managed); both empty when
    there are none."""
    servers = mcp.get("servers")
    managed_tools = mcp.get("managed_tools")
    managed = (
        managed_tools.get("credentials") if isinstance(managed_tools, dict) else None
    )
    servers = servers if isinstance(servers, dict) and servers else {}
    managed = managed if isinstance(managed, dict) and managed else {}
    if not servers and not managed:
        return ()
    return servers, managed


def _assemble_delegated_grants(org_id):
    """
    The push-only part of the identity section, from the delegated grants
    service: {"delegated_grants": {grant_id: grant}, "idp": {"client_secret":
    ...}}. Returns {} when that service is not installed.

    A failure is logged by its type only; a grant that breaks the
    DelegatedGrant contract, or a client secret that is not a non-empty
    string, is dropped and logged without its value, and the rest pushes.
    """
    try:
        from agentcc.services.delegated_grants import assemble_for_push
    except ImportError:
        return {}
    try:
        assembled = assemble_for_push(org_id) or {}
    except Exception as e:
        logger.warning(
            "delegated_grants_assemble_failed",
            org_id=str(org_id),
            error_type=type(e).__name__,
        )
        return {}
    if not isinstance(assembled, dict):
        logger.warning(
            "delegated_grants_invalid",
            org_id=str(org_id),
            reason="result is not an object",
        )
        return {}
    push_only = {}
    grants = assembled.get("delegated_grants")
    for grant_id, grant in (grants if isinstance(grants, dict) else {}).items():
        problem = _contract_problem(GatewayDelegatedGrant, grant)
        if problem is None and grant.get("grant_id") != grant_id:
            problem = "grant_id does not match its key"
        if problem is None:
            push_only.setdefault("delegated_grants", {})[grant_id] = grant
            continue
        logger.warning(
            "delegated_grant_dropped",
            org_id=str(org_id),
            grant_id=grant_id if isinstance(grant_id, str) else "",
            reason=problem,
        )
    idp = assembled.get("idp")
    client_secret = idp.get("client_secret") if isinstance(idp, dict) else None
    if isinstance(client_secret, str) and client_secret:
        push_only["idp"] = {"client_secret": client_secret}
    elif client_secret is not None:
        logger.warning(
            "identity_client_secret_dropped",
            org_id=str(org_id),
            reason="client_secret is not a non-empty string",
        )
    return push_only


def _with_delegated_grants(identity, push_only):
    """The identity section with the push-only parts merged in; the stored
    section is never changed."""
    merged = copy.deepcopy(identity) if isinstance(identity, dict) else {}
    if "idp" in push_only:
        idp = merged.get("idp")
        merged["idp"] = {**(idp if isinstance(idp, dict) else {}), **push_only["idp"]}
    if "delegated_grants" in push_only:
        merged["delegated_grants"] = push_only["delegated_grants"]
    return merged


def _config_meta(config, pushed_at):
    """
    Push-time metadata. pushed_at is set only by a push: the bulk sync builds
    the same payload on every poll, and a changing timestamp would make the
    gateway treat every org as changed each time.
    """
    meta = {"version": config.version, "config_id": str(config.id)}
    if pushed_at is not None:
        meta["pushed_at"] = pushed_at.isoformat()
    return meta


def _build_payload(org_id, config, *, pushed_at=None, with_upstream_keys=True):
    """
    Build the config payload for the gateway, assembling providers from credentials.

    with_upstream_keys=False leaves out every push-only secret (the per-org
    managed upstream keys, the MCP credential secrets and the delegated grants
    with the IdP client secret), so save-time validation never decrypts or
    assembles them: they are added, and each entry validated, only when a
    payload is pushed.
    """
    payload = {
        "providers": _assemble_providers(org_id),
        "guardrails": _transform_guardrails(config.guardrails, org_id=org_id),
        "routing": _floored_rollout_estimate(config.routing),
        "cache": normalize_cache_config(config.cache),
        "rate_limiting": config.rate_limiting,
        "budgets": _transform_budgets(config.budgets),
        "cost_tracking": config.cost_tracking,
        "ip_acl": config.ip_acl,
        "alerting": _normalize_alerting(config.alerting),
        "privacy": config.privacy,
        "tool_policy": config.tool_policy,
        "mcp": _mcp_payload(org_id, config, with_upstream_keys=with_upstream_keys),
        "a2a": config.a2a,
        "audit": config.audit,
        "model_database": config.model_database,
        "model_map": config.model_map,
        "managed_models": _floored_estimate(
            _managed_models_payload(
                org_id, config, with_upstream_keys=with_upstream_keys
            )
        ),
        "meta": _config_meta(config, pushed_at),
    }
    # Pushed as stored when the org set them; an empty section is left out, as
    # it says nothing, so identity appears only when stored or when its
    # push-only parts are added below.
    for section in ("tools", "identity"):
        if getattr(config, section):
            payload[section] = getattr(config, section)
    if with_upstream_keys:
        delegated = _assemble_delegated_grants(org_id)
        if delegated:
            payload["identity"] = _with_delegated_grants(
                payload.get("identity"), delegated
            )
    dumped = GatewayOrgConfig.model_validate(payload).model_dump(
        by_alias=True,
        exclude_none=True,
    )
    if with_upstream_keys:
        # The gateway needs the real secrets; a save-time validation
        # (with_upstream_keys=False) never decrypts, leaving the `enc::`
        # ciphertext in place for the contract to accept as a plain string.
        from agentcc.services.org_config_secrets import decrypt_payload_secrets

        decrypt_payload_secrets(dumped, org_id=org_id)
    return dumped


def pushed_guardrails(config):
    """The guardrails section a push of `config` sends, as the gateway
    contract dumps it: credentials decrypted and injected, names and provider
    defaults as the gateway reads them, each check's `enabled` a bool.

    Raises pydantic's ValidationError when the contract cannot read the
    section; validate_org_config refuses such a version first.
    """
    section = _transform_guardrails(
        config.guardrails, org_id=str(config.organization_id)
    )
    dumped = GatewayOrgConfig.model_validate({"guardrails": section}).model_dump(
        by_alias=True, exclude_none=True
    )
    return dumped.get("guardrails") or {}


class OrgConfigRejected(ValueError):
    """The gateway contract does not accept an org config."""


class SecretInConfig(OrgConfigRejected):
    """A config to be saved holds a push-only secret: managed upstream keys,
    or the identity section's IdP client secret or delegated grants."""

    code = "secret_in_config"

    def __init__(self, paths):
        self.paths = tuple(paths)
        super().__init__(
            f"{', '.join(self.paths)} cannot be saved in an org config. Push-only "
            "secrets (per-org managed upstream keys, the IdP client secret and "
            "delegated grants) are added at push time and never stored."
        )


def validate_org_config(config):
    """
    Raise OrgConfigRejected if the gateway cannot accept `config`.

    Call it before a config version becomes active. The gateway pulls every
    active config through the same contract, so a version the contract rejects
    stops that org's sync until someone repairs the row.
    """
    secret_paths = push_only_secret_paths(
        managed_models=config.managed_models,
        routing=config.routing,
        identity=config.identity,
    )
    if secret_paths:
        raise SecretInConfig(secret_paths)
    try:
        _build_payload(str(config.organization_id), config, with_upstream_keys=False)
    except ValidationError as e:
        fields = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in e.errors()
        )
        raise OrgConfigRejected(
            f"The gateway cannot accept this config. {fields}"
        ) from e


def push_org_config(org_id, config):
    """
    Push an org config to the gateway.

    Args:
        org_id: Organization UUID string
        config: AgentccOrgConfig instance

    Returns:
        True on success, False on failure.
    """
    try:
        client = get_gateway_client()
        payload = _build_payload(org_id, config, pushed_at=timezone.now())
        client.set_org_config(org_id, payload)
        logger.info("config_push_success", org_id=org_id, version=config.version)
        # The org or its rollout candidate opts in: either can route a call
        # to managed models, which needs the account (402, not 503).
        if _managed_models_enabled(config):
            transaction.on_commit(lambda: _ensure_managed_account(org_id))
        return True
    except GatewayClientError as e:
        logger.warning("config_push_failed", org_id=org_id, error=str(e))
        return False
    except Exception as e:
        logger.warning("config_push_error", org_id=org_id, error=str(e))
        return False


def _ensure_managed_account(org_id):
    """
    Make sure the gateway holds a managed credit account for an org that just
    opted in, so its first managed call is 402 "add credits", not 503
    managed_billing_unavailable.

    Uses the managed credit ledger when it is installed. Best effort: a failure
    is logged and not retried; the next config push with managed models on, or
    the next credit write, pushes the account, and until then the org's managed
    calls get 503.
    """
    try:
        from ee.usage.services.managed_credits import ensure_account_pushed
    except ImportError:
        return
    try:
        from accounts.models.organization import Organization

        org = Organization.objects.filter(id=org_id).first()
        if org is None:
            logger.warning("managed_account_org_missing", org_id=str(org_id))
            return
        ensure_account_pushed(org)
    except Exception:
        logger.exception("managed_account_ensure_failed", org_id=str(org_id))


def delete_org_config(org_id):
    """Remove an org config from the gateway.

    Returns:
        True on success, False on failure.
    """
    try:
        client = get_gateway_client()
        client.delete_org_config(org_id)
        logger.info("config_delete_success", org_id=org_id)
        return True
    except GatewayClientError as e:
        logger.warning("config_delete_failed", org_id=org_id, error=str(e))
        return False
    except Exception as e:
        logger.warning("config_delete_error", org_id=org_id, error=str(e))
        return False


def push_all_org_configs():
    """Push all active org configs to the gateway. Used for recovery/sync."""
    client = get_gateway_client()
    configs = AgentccOrgConfig.no_workspace_objects.filter(
        is_active=True, deleted=False
    ).select_related("organization")

    success = 0
    failed = 0
    for cfg in configs:
        org_id = str(cfg.organization_id)
        try:
            payload = _build_payload(org_id, cfg, pushed_at=timezone.now())
            client.set_org_config(org_id, payload)
            success += 1
        except GatewayClientError as e:
            logger.warning("config_push_failed", org_id=org_id, error=str(e))
            failed += 1
        except Exception as e:
            logger.warning("config_push_error", org_id=org_id, error=str(e))
            failed += 1

    logger.info("config_push_all_complete", success=success, failed=failed)
