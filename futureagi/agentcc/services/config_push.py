"""
Config Push Service — pushes per-org configs from Django to the Go gateway.
Called when org configs are created, activated, or deleted.
"""

import structlog
from django.conf import settings as django_settings

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


def _inject_guardrail_credentials(checks):
    """
    Decrypt and inject real credentials into guardrail checks, replacing
    "__encrypted__" sentinel values with actual secrets from the policy's
    encrypted_check_configs blob.
    """
    from integrations.services.credentials import CredentialManager
    from agentcc.models.guardrail_policy import AgentccGuardrailPolicy

    ENCRYPTED_SENTINEL = "__encrypted__"

    # Collect unique policy IDs from merged checks metadata
    policy_ids = set()
    for check in checks if isinstance(checks, list) else checks.values():
        pid = check.get("_policy") if isinstance(check, dict) else None
        if pid:
            policy_ids.add(pid)

    if not policy_ids:
        return

    # Bulk-load policies and decrypt their credential blobs
    policies = AgentccGuardrailPolicy.no_workspace_objects.filter(
        id__in=policy_ids, deleted=False
    ).only("id", "encrypted_check_configs")
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

    # Inject real credentials into each check's config
    if isinstance(checks, list):
        items = [(check.get("name"), check) for check in checks]
    else:
        items = [(name, check) for name, check in checks.items()]
    for check_name, check in items:
        if not isinstance(check, dict):
            continue
        pid = check.get("_policy")
        cfg = check.get("config")
        if not pid or not check_name or not isinstance(cfg, dict):
            continue
        policy_secrets = decrypted_map.get(pid, {})
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
    """
    if not guardrails_data:
        return guardrails_data

    raw_checks = guardrails_data.get("checks")
    rules = guardrails_data.get("rules")

    # Checks as list (from guardrail_sync — merged policies)
    if isinstance(raw_checks, list) and raw_checks:
        # Inject decrypted guardrail credentials (replaces __encrypted__ sentinels)
        _inject_guardrail_credentials(raw_checks)

        checks = {}
        for rule in raw_checks:
            rule_name = rule.get("name", "")
            registry_name = _RULE_TO_REGISTRY.get(rule_name, rule_name)
            cfg = dict(rule.get("config") or {})
            if rule_name in _RULE_PROVIDER_DEFAULTS and "provider" not in cfg:
                cfg["provider"] = _RULE_PROVIDER_DEFAULTS[rule_name]
            if "eval_ids" in cfg and "eval_id" not in cfg:
                ids = cfg.pop("eval_ids", [])
                if ids:
                    cfg["eval_id"] = str(ids[0])
            checks[registry_name] = {
                "enabled": rule.get("enabled", True),
                "action": rule.get("action", "block"),
                "confidence_threshold": rule.get("threshold", 0.8),
                "config": cfg,
            }
        if org_id:
            _inject_fi_credentials(checks, org_id)
        result = {
            "checks": checks,
            "fail_open": guardrails_data.get(
                "failOpen", guardrails_data.get("fail_open", False)
            ),
            "pipeline_mode": guardrails_data.get("pipeline_mode", "parallel"),
        }
        if guardrails_data.get("timeout_ms"):
            result["timeout_ms"] = guardrails_data["timeout_ms"]
        return result

    # Checks as dict (from frontend saves via OrgConfig)
    if isinstance(raw_checks, dict) and raw_checks:
        # Inject decrypted guardrail credentials (replaces __encrypted__ sentinels)
        _inject_guardrail_credentials(raw_checks)

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
            if (
                isinstance(inner, dict)
                and "eval_ids" in inner
                and "eval_id" not in inner
            ):
                ids = inner.pop("eval_ids", [])
                if ids:
                    inner["eval_id"] = str(ids[0])
            mapped[registry_name] = clean_cfg
        if org_id:
            _inject_fi_credentials(mapped, org_id)
        return {**guardrails_data, "checks": mapped}

    # Convert rules array → checks map
    if not isinstance(rules, list) or not rules:
        return guardrails_data

    # Inject decrypted guardrail credentials before transformation
    _inject_guardrail_credentials(rules)

    checks = {}
    for rule in rules:
        rule_name = rule.get("name", "")
        registry_name = _RULE_TO_REGISTRY.get(rule_name, rule_name)
        cfg = dict(rule.get("config") or {})
        if rule_name in _RULE_PROVIDER_DEFAULTS and "provider" not in cfg:
            cfg["provider"] = _RULE_PROVIDER_DEFAULTS[rule_name]
        if "eval_ids" in cfg and "eval_id" not in cfg:
            ids = cfg.pop("eval_ids", [])
            if ids:
                cfg["eval_id"] = str(ids[0])
        checks[registry_name] = {
            "enabled": rule.get("enabled", True),
            "action": rule.get("action", "block"),
            "confidence_threshold": rule.get("threshold", 0.8),
            "config": cfg,
        }

    # Auto-inject org's FI platform credentials for futureagi-eval
    if org_id:
        _inject_fi_credentials(checks, org_id)

    result = {
        "checks": checks,
        "fail_open": guardrails_data.get(
            "failOpen", guardrails_data.get("fail_open", False)
        ),
        "pipeline_mode": guardrails_data.get("pipeline_mode", "parallel"),
    }
    if guardrails_data.get("timeout_ms"):
        result["timeout_ms"] = guardrails_data["timeout_ms"]
    return result


def _inject_fi_credentials(checks, org_id):
    """
    Auto-inject the org's FI platform API credentials into futureagi-eval config.
    Since FutureAGI IS the platform, the org's own api_key/secret_key are already
    stored in the OrgApiKey model — no need for users to configure them manually.
    """
    # Check both possible names (pre and post registry mapping)
    fi_check = checks.get("futureagi-eval")
    if fi_check is None:
        return

    if not fi_check.get("enabled", False):
        return

    cfg = fi_check.get("config") or {}

    # Only inject if not already set (don't overwrite explicit config)
    if cfg.get("api_key") and cfg.get("secret_key"):
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
            cfg.setdefault("api_key", org_key.api_key)
            cfg.setdefault("secret_key", org_key.secret_key)
            cfg.setdefault("base_url", django_settings.BASE_URL)
            cfg.setdefault("call_type", "protect")
            fi_check["config"] = cfg
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


def _assemble_providers(org_id):
    """Build providers dict from AgentccProviderCredential rows for an org."""
    from integrations.services.credentials import CredentialManager
    from agentcc.models.provider_credential import AgentccProviderCredential

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
        providers[cred.provider_name] = {
            **cred.extra_config,
            "api_key": decrypted.get("api_key", ""),
            **{k: v for k, v in decrypted.items() if k != "api_key"},
            "base_url": cred.base_url,
            "api_format": cred.api_format,
            "models": cred.models_list,
            "enabled": True,
            "default_timeout": f"{cred.default_timeout_seconds}s",
            "max_concurrent": cred.max_concurrent,
            "conn_pool_size": cred.conn_pool_size,
        }
    return providers


def _normalize_alerting(alerting):
    """Normalize alerting config so rules/channels are always arrays (Go expects arrays)."""
    if not alerting or not isinstance(alerting, dict):
        return alerting
    result = {**alerting}
    for key in ("rules", "channels"):
        val = result.get(key)
        if isinstance(val, dict):
            result[key] = [
                {"name": name, **cfg} if isinstance(cfg, dict) else {"name": name}
                for name, cfg in val.items()
            ]
    return result


def _extract_budget_action(entry):
    if not isinstance(entry, dict):
        return None

    action = entry.get("action") or entry.get("action_mode") or entry.get("on_exceed")
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


def _transform_budget_levels(levels):
    if not isinstance(levels, dict):
        return levels

    return {
        name: (
            _normalize_budget_action(entry, "hard")
            if isinstance(entry, dict)
            else entry
        )
        for name, entry in levels.items()
    }


def _transform_budgets(budgets):
    """Normalize control-plane budget action modes into gateway hard flags."""
    if not budgets or not isinstance(budgets, dict):
        return budgets

    result = {**budgets}

    flat_org_budget = result.get("org_limit")
    if isinstance(flat_org_budget, dict):
        if flat_org_budget.get("limit") is not None:
            result["org_limit"] = flat_org_budget["limit"]
        if flat_org_budget.get("period"):
            result["org_period"] = flat_org_budget["period"]

        normalized_action = _extract_budget_action(flat_org_budget)
        if normalized_action:
            result["action"] = normalized_action
            result["hard_limit"] = normalized_action == "block"
        elif "hard" in flat_org_budget and "hard_limit" not in result:
            result["hard_limit"] = bool(flat_org_budget["hard"])

    flat_hard_budget = result.get("hard_limit")
    if isinstance(flat_hard_budget, dict):
        if "org_limit" not in result and flat_hard_budget.get("limit") is not None:
            result["org_limit"] = flat_hard_budget["limit"]
        if "org_period" not in result and flat_hard_budget.get("period"):
            result["org_period"] = flat_hard_budget["period"]

        normalized_action = _extract_budget_action(flat_hard_budget)
        if normalized_action:
            result["action"] = normalized_action
            result["hard_limit"] = normalized_action == "block"
        elif "hard" in flat_hard_budget:
            result["hard_limit"] = bool(flat_hard_budget["hard"])

    for org_key in ("organization", "org"):
        org_budget = result.get(org_key)
        if not isinstance(org_budget, dict):
            continue

        if "org_limit" not in result and org_budget.get("limit") is not None:
            result["org_limit"] = org_budget["limit"]
        if "org_period" not in result and org_budget.get("period"):
            result["org_period"] = org_budget["period"]

        normalized_action = _extract_budget_action(org_budget)
        if normalized_action:
            result["action"] = normalized_action
            result["hard_limit"] = normalized_action == "block"
        elif "hard" in org_budget and "hard_limit" not in result:
            result["hard_limit"] = bool(org_budget["hard"])
        break

    top_level = _normalize_budget_action(result, "hard_limit")
    if isinstance(top_level, dict):
        result = top_level

    for level_key in ("teams", "users", "keys", "tags"):
        result[level_key] = _transform_budget_levels(result.get(level_key))

    return result


def _build_payload(org_id, config):
    """Build the config payload for the gateway, assembling providers from credentials."""
    return {
        "providers": _assemble_providers(org_id),
        "guardrails": _transform_guardrails(config.guardrails, org_id=org_id),
        "routing": config.routing,
        "cache": normalize_cache_config(config.cache),
        "rate_limiting": config.rate_limiting,
        "budgets": _transform_budgets(config.budgets),
        "cost_tracking": config.cost_tracking,
        "ip_acl": config.ip_acl,
        "alerting": _normalize_alerting(config.alerting),
        "privacy": config.privacy,
        "tool_policy": config.tool_policy,
        "mcp": config.mcp,
        "a2a": config.a2a,
        "audit": config.audit,
        "model_database": config.model_database,
        "model_map": config.model_map,
    }


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
        payload = _build_payload(org_id, config)
        client.set_org_config(org_id, payload)
        logger.info("config_push_success", org_id=org_id, version=config.version)
        return True
    except GatewayClientError as e:
        logger.warning("config_push_failed", org_id=org_id, error=str(e))
        return False
    except Exception as e:
        logger.warning("config_push_error", org_id=org_id, error=str(e))
        return False


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
        payload = _build_payload(org_id, cfg)
        try:
            client.set_org_config(org_id, payload)
            success += 1
        except GatewayClientError as e:
            logger.warning("config_push_failed", org_id=org_id, error=str(e))
            failed += 1

    logger.info("config_push_all_complete", success=success, failed=failed)
