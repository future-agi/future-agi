import contextvars
import copy
import re
import secrets
import time
from collections.abc import Mapping

from pydantic import ValidationError as ContractValidationError
from rest_framework import serializers

from agentcc.contracts import gateway_admin as contract
from agentcc.models import AgentccOrgConfig
from agentcc.org_config_defaults import (
    default_cache_config,
    default_cost_tracking_config,
    normalize_cache_config,
    normalize_cost_tracking_config,
)
from agentcc.services import guardrail_urls
from agentcc.services.config_push import field_input_names, push_only_secret_paths
from agentcc.services.guardrail_env_refs import (
    GuardrailEnvReference,
    guardrail_env_reference_path,
)
from tfc.middleware.workspace_context import get_current_organization

SECRET_IN_CONFIG = "secret_in_config"
VS_ID_IMMUTABLE = "vs_id_immutable"
FORWARD_HEADER_REFUSED = "forward_header_refused"
CAPABILITY_MANAGED_NOT_ALLOWED = "capability_managed_not_allowed"

# Keys of org-config contract v2, by the contract model of the section that
# holds them. They are validated strictly here; the other keys keep the lax
# contract check that validate_org_config applies on save.
_V2_ROUTING_KEYS = (
    "data_policy",
    "agents",
    "presets",
    "idempotency",
    "prompt_caching",
    "max_total_attempts",
    "retry_budget",
)
_V2_FASTEST_KEYS = ("hedge_after_ms", "max_hedges", "allow_managed")
_V2_GUARDRAIL_KEYS = ("tool_calls",)
_V2_PRIVACY_KEYS = ("retention", "content_mode")
_V2_RATE_LIMIT_KEYS = ("per_key_max_parallel",)
_V2_BUDGET_LEVEL_KEYS = ("include_byok", "alert_thresholds")
# Org config mcp v2: the keys added to the section and to each
# server entry. The legacy keys (enabled, guardrails, a server's url,
# transport, auth and tools_cache_ttl) keep the lax check.
_V2_MCP_KEYS = (
    "access",
    "virtual_servers",
    "managed_tools",
    "pinning",
    "scanning",
    "flow_control",
    "policy",
    "shaping",
)
_V2_MCP_SERVER_KEYS = (
    "era",
    "credential",
    "trusted_annotations",
    "timeout_ms",
    "tools_cache_ttl_seconds",
    "forward_headers",
    "propagate_depth",
    "enabled",
    "oauth",
    "labels",
)
_MCP_SERVER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_MCP_VIRTUAL_SERVER_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_MCP_VIRTUAL_SERVER_ID = re.compile(r"^vs_[0-9A-HJKMNP-TV-Z]{26}$")
# A managed_tools glob names a FutureAGI managed server and tool
# (fagi.<id>/<tool>) or a capability and vendor (<capability>/<vendor>).
_MCP_MANAGED_GLOB = re.compile(
    r"^(fagi\.[a-z0-9*][a-z0-9_.*-]*|[a-z*][a-z0-9_.*]*)/[^/\s]+$"
)
# Headers an org may never forward upstream: credentials, MCP session and
# protocol state, and the gateway's own agent headers.
_MCP_REFUSED_FORWARD_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "x-api-key",
        "proxy-authorization",
        "mcp-session-id",
        "mcp-protocol-version",
    }
)
_MCP_REFUSED_FORWARD_PREFIXES = ("x-agentcc-", "x-omega-")
# A forwarded header name must be an RFC 9110 token.
_HTTP_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
# A tenant server is reached over HTTP (or SSE for a 2024-11-05 upstream);
# stdio servers are operator-only.
_MCP_TENANT_TRANSPORTS = ("http", "sse")
_MCP_URL_SCHEMES = ("http", "https")
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
# The tool price cards the gateway ships (agentcc-gateway/catalog/tools) whose
# resale_status is byok_only, as <capability>/<vendor>: the vendor's terms bar
# resale, so no org may opt it into managed use.
# test_org_config_tools keeps this equal to the shipped cards.
BYOK_ONLY_TOOL_CARDS = frozenset(
    f"{capability}/{vendor}"
    for capability, vendors in {
        "browsers": (
            "bright_data",
            "browserless",
            "cloudflare_browser",
            "e2b_desktop",
            "hyperbrowser",
            "steel",
        ),
        "connectors": ("arcade", "composio", "merge", "nango", "pipedream", "zapier"),
        "data": (
            "apollo",
            "fmp",
            "mapbox",
            "massive",
            "newsapi",
            "open_meteo",
            "openweather",
            "pdl",
            "tomorrow_io",
        ),
        "documents": (
            "aws_textract",
            "azure_document_intelligence",
            "google_document_ai",
            "llamaparse",
            "unstructured",
        ),
        "fetch": (
            "apify",
            "bright_data",
            "cloudflare_browser",
            "jina",
            "scrapingbee",
            "spider",
            "zyte",
        ),
        "guardrails": (
            "azure_content_safety",
            "bedrock_guardrails",
            "google_model_armor",
            "lakera",
            "llama_guard",
            "openai_moderation",
        ),
        "media": ("gemini_media", "ideogram", "kling", "openai_images", "replicate"),
        "memory": ("letta", "mixedbread", "pinecone", "qdrant", "supermemory"),
        "payments": (
            "blockrun",
            "coinbase_cdp",
            "lithic",
            "mastercard",
            "ramp",
            "skyfire",
            "stripe",
            "visa",
        ),
        "sandboxes": (
            "cloudflare_sandbox",
            "daytona",
            "e2b",
            "modal",
            "runloop",
            "vercel_sandbox",
        ),
        "search": (
            "brave",
            "gemini_grounding",
            "jina",
            "linkup",
            "searxng",
            "serpapi",
            "serper",
            "youcom",
        ),
        "speech": ("gladia", "google_speech"),
    }.items()
    for vendor in vendors
)
# Org config tools v1 names (the gateway's card and option patterns).
_TOOL_CAPABILITY = re.compile(r"^[a-z][a-z0-9_.]*$")
_TOOL_VENDOR = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
_TOOL_OPTION = re.compile(r"^[a-z][a-z0-9_]*$")
_DOMAIN_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def _contract_messages(path, error):
    return [
        f"{'.'.join([path, *(str(part) for part in item['loc'])])}: {item['msg']}"
        for item in error.errors()
    ]


def _validate_contract(path, value, contract_model, fields=None):
    """
    Validate `value` strictly against `contract_model`. With `fields`, only
    those keys (in any accepted spelling) are checked.
    """
    if not isinstance(value, dict):
        return
    if fields is not None:
        names = frozenset().union(
            *(field_input_names(contract_model, field) for field in fields)
        )
        value = {key: item for key, item in value.items() if key in names}
        if not value:
            return
    try:
        contract_model.model_validate(value, strict=True)
    except ContractValidationError as e:
        raise serializers.ValidationError(_contract_messages(path, e)) from None


def _reject_guardrail_env_references(value):
    """Refuse a guardrails section that holds "${" in a check's credential, URL
    or other provider setting (a futureagi check's api_key, secret_key or
    base_url, an external-provider check's top-level settings). Any other "${"
    (a blocklist word, an expression, a name) is plain text. The check is
    shared with the config write path so both answer the same way; here it
    surfaces as a serializer error naming the field path, before any DB write.
    """
    path = guardrail_env_reference_path(value, "guardrails")
    if path:
        raise serializers.ValidationError(
            str(GuardrailEnvReference(path)), code=GuardrailEnvReference.code
        )


def _reject_push_only_secrets(**sections):
    paths = push_only_secret_paths(**sections)
    if paths:
        raise serializers.ValidationError(
            f"{', '.join(paths)} cannot be saved in an org config. Push-only "
            "secrets (per-org managed upstream keys, the IdP client secret and "
            "delegated grants) are added at push time and never stored.",
            code=SECRET_IN_CONFIG,
        )


class AgentccOrgConfigSerializer(serializers.ModelSerializer):
    # Read-only and not a model field: the base representation skips it (no
    # such attribute), and mask_config_data fills it in after the sections are
    # normalised, so it always lists the pointers of the masks this response
    # carries.
    secrets_set = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
        help_text=(
            "JSON pointers of the infrastructure secrets stored in this config "
            "(cache, a2a, audit, alerting and mcp). Each one reads as a mask, "
            "never its value."
        ),
    )

    def to_representation(self, instance):
        from agentcc.services.org_config_secrets import mask_config_data

        data = super().to_representation(instance)
        data["cost_tracking"] = normalize_cost_tracking_config(
            data.get("cost_tracking")
        )
        data["cache"] = normalize_cache_config(data.get("cache"))
        # Every infrastructure secret (cache, a2a, audit, alerting and MCP) is
        # returned as a mask with a "set" flag; it is decrypted only for the
        # gateway push. Provider and guardrail credentials are masked
        # on their own serializers.
        mask_config_data(data)
        return data

    class Meta:
        model = AgentccOrgConfig
        fields = [
            "id",
            "organization",
            "version",
            "guardrails",
            "routing",
            "cache",
            "rate_limiting",
            "budgets",
            "cost_tracking",
            "ip_acl",
            "alerting",
            "privacy",
            "tool_policy",
            "mcp",
            "a2a",
            "audit",
            "model_database",
            "model_map",
            "managed_models",
            "tools",
            "identity",
            "is_active",
            "created_by",
            "change_description",
            "created_at",
            "updated_at",
            "secrets_set",
        ]
        read_only_fields = [
            "id",
            "organization",
            "version",
            "is_active",
            "created_by",
            "created_at",
            "updated_at",
        ]


class AgentccOrgConfigWriteSerializer(serializers.Serializer):
    """Serializer for creating a new org config version."""

    guardrails = serializers.JSONField(required=False, default=dict)
    routing = serializers.JSONField(required=False, default=dict)
    cache = serializers.JSONField(required=False, default=default_cache_config)
    rate_limiting = serializers.JSONField(required=False, default=dict)
    budgets = serializers.JSONField(required=False, default=dict)
    cost_tracking = serializers.JSONField(
        required=False, default=default_cost_tracking_config
    )
    ip_acl = serializers.JSONField(required=False, default=dict)
    alerting = serializers.JSONField(required=False, default=dict)
    privacy = serializers.JSONField(required=False, default=dict)
    tool_policy = serializers.JSONField(required=False, default=dict)
    mcp = serializers.JSONField(required=False, default=dict)
    a2a = serializers.JSONField(required=False, default=dict)
    audit = serializers.JSONField(required=False, default=dict)
    model_database = serializers.JSONField(required=False, default=dict)
    model_map = serializers.JSONField(required=False, default=dict)
    managed_models = serializers.JSONField(required=False, default=dict)
    tools = serializers.JSONField(required=False, default=dict)
    identity = serializers.JSONField(required=False, default=dict)
    change_description = serializers.CharField(
        required=False, default="", allow_blank=True
    )

    def validate_guardrails(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("guardrails must be a JSON object")
        _reject_guardrail_env_references(value)
        # A check's callback URL (url/endpoint/base_url) must be a well-formed
        # http(s) URL before the gateway dials it.
        try:
            guardrail_urls.reject_bad_guardrail_urls(value)
        except guardrail_urls.BadGuardrailURL as e:
            raise serializers.ValidationError(str(e), code=e.code) from None
        _validate_contract(
            "guardrails", value, contract.GuardrailConfig, _V2_GUARDRAIL_KEYS
        )
        return value

    def validate_routing(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("routing must be a JSON object")
        _reject_push_only_secrets(routing=value)
        _validate_routing_v2("routing", value, contract.RoutingConfig)
        if "rollout" in value:
            _validate_rollout(value["rollout"])
        return value

    def validate_cache(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("cache must be a JSON object")
        return normalize_cache_config(value)

    def validate_rate_limiting(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("rate_limiting must be a JSON object")
        _validate_contract(
            "rate_limiting", value, contract.RateLimitConfig, _V2_RATE_LIMIT_KEYS
        )
        return value

    def validate_budgets(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("budgets must be a JSON object")
        valid_periods = {"daily", "weekly", "monthly", "total"}
        # Validate hierarchical budget levels: teams, users, keys, tags.
        for level_key in ("teams", "users", "keys", "tags"):
            level_map = value.get(level_key)
            if level_map is None:
                continue
            if not isinstance(level_map, dict):
                raise serializers.ValidationError(
                    f"budgets.{level_key} must be a JSON object"
                )
            for name, entry in level_map.items():
                if not isinstance(entry, dict):
                    raise serializers.ValidationError(
                        f"budgets.{level_key}.{name} must be a JSON object"
                    )
                if "limit" in entry:
                    try:
                        float(entry["limit"])
                    except (TypeError, ValueError):
                        raise serializers.ValidationError(
                            f"budgets.{level_key}.{name}.limit must be a number"
                        ) from None
                if "period" in entry and entry["period"] not in valid_periods:
                    raise serializers.ValidationError(
                        f"budgets.{level_key}.{name}.period must be one of: "
                        f"{', '.join(sorted(valid_periods))}"
                    )
                if "hard" in entry and not isinstance(entry["hard"], bool):
                    raise serializers.ValidationError(
                        f"budgets.{level_key}.{name}.hard must be a boolean"
                    )
                _validate_contract(
                    f"budgets.{level_key}.{name}",
                    entry,
                    contract.BudgetLevelConfig,
                    _V2_BUDGET_LEVEL_KEYS,
                )
                per_model = entry.get("per_model")
                if per_model is not None:
                    if not isinstance(per_model, dict):
                        raise serializers.ValidationError(
                            f"budgets.{level_key}.{name}.per_model must be a JSON object"
                        )
                    for model_name, model_limit in per_model.items():
                        try:
                            float(model_limit)
                        except (TypeError, ValueError):
                            raise serializers.ValidationError(
                                f"budgets.{level_key}.{name}.per_model.{model_name} must be a number"
                            ) from None
        for org_key in ("organization", "org"):
            _validate_contract(
                f"budgets.{org_key}",
                value.get(org_key),
                contract.BudgetLevelConfig,
                _V2_BUDGET_LEVEL_KEYS,
            )
        return value

    def validate_cost_tracking(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("cost_tracking must be a JSON object")
        return normalize_cost_tracking_config(value)

    def validate_ip_acl(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("ip_acl must be a JSON object")
        return value

    def validate_alerting(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("alerting must be a JSON object")
        # An alert channel URL must be well-formed before the gateway dials it.
        try:
            guardrail_urls.reject_bad_alerting_urls(value)
        except guardrail_urls.BadAlertingURL as e:
            raise serializers.ValidationError(str(e), code=e.code) from None
        return value

    def validate_privacy(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("privacy must be a JSON object")
        _validate_contract("privacy", value, contract.PrivacyConfig, _V2_PRIVACY_KEYS)
        return value

    def validate_tool_policy(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("tool_policy must be a JSON object")
        return value

    def validate_mcp(self, value):
        """
        Org config mcp v2. The v2 keys are typed strictly; a server cannot be
        marked private or forward a refused header; managed_tools globs name
        fagi.<id>/<tool> or <capability>/<vendor>. Each virtual server gets its
        immutable vs_<ULID> id here: a new slug is assigned one, an existing
        slug keeps the id its org's active config holds, and a client that sets
        an id on a new slug or changes one gets 400 vs_id_immutable.
        """
        if not isinstance(value, dict):
            raise serializers.ValidationError("mcp must be a JSON object")
        _ISSUED_VS_IDS.set(frozenset())
        return _checked_mcp(value, _active_mcp(self._organization()))

    def _organization(self):
        """The org the config belongs to: from the serializer context when the
        view passes it, else the authenticated request's org."""
        org = self.context.get("organization")
        if org is not None:
            return org
        request = self.context.get("request")
        org = getattr(request, "organization", None)
        return org if org is not None else get_current_organization()

    def validate_a2a(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("a2a must be a JSON object")
        return value

    def validate_audit(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("audit must be a JSON object")
        valid_severities = {"info", "warn", "error", "critical"}
        if value.get("min_severity") and value["min_severity"] not in valid_severities:
            raise serializers.ValidationError(
                f"min_severity must be one of: {', '.join(sorted(valid_severities))}"
            )
        valid_sink_types = {"stdout", "file", "webhook"}
        for sink in value.get("sinks", []):
            if not isinstance(sink, dict):
                raise serializers.ValidationError("Each sink must be a JSON object")
            sink_type = sink.get("type", "stdout")
            if sink_type not in valid_sink_types:
                raise serializers.ValidationError(
                    f"Sink type must be one of: {', '.join(sorted(valid_sink_types))}"
                )
        return value

    def validate_model_database(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("model_database must be a JSON object")
        overrides = value.get("overrides", {})
        if overrides and not isinstance(overrides, dict):
            raise serializers.ValidationError(
                "model_database.overrides must be a JSON object"
            )
        for model_id, override in overrides.items():
            if not isinstance(override, dict):
                raise serializers.ValidationError(
                    f"Override for model '{model_id}' must be a JSON object"
                )
        return value

    def validate_managed_models(self, value):
        """An org opts in and sets its own limits; markup is not an org setting."""
        if not isinstance(value, dict):
            raise serializers.ValidationError("managed_models must be a JSON object")
        _reject_push_only_secrets(managed_models=value)
        _validate_contract("managed_models", value, contract.ManagedModelsConfig)
        return value

    def validate_identity(self, value):
        """The stored section has no secret: the IdP client secret and the
        delegated grants are push-only (added by config_push at push time), so
        a save that carries either is refused, as validate_managed_models
        refuses upstream_keys."""
        if not isinstance(value, dict):
            raise serializers.ValidationError("identity must be a JSON object")
        _reject_push_only_secrets(identity=value)
        _validate_contract("identity", value, contract.IdentityConfig)
        return value

    def validate_tools(self, value):
        """Org config tools v1. The section is typed strictly by the contract;
        capability, vendor and option names follow the gateway's patterns; the
        domain policy holds host names or ``*.<host>`` wildcards, none both
        allowed and denied; developer-pinned options are typed (never null, of
        their default's type, a pinned provider among the providers). A save
        whose mcp.managed_tools.allow names a byok_only vendor is refused with
        capability_managed_not_allowed by the mcp rules (_checked_mcp, so under
        mcp and on every write path): the tools section has no managed flag, so
        that glob is the only managed opt-in a vendor gets."""
        if not isinstance(value, dict):
            raise serializers.ValidationError("tools must be a JSON object")
        _validate_contract("tools", value, contract.ToolsConfig)
        problems = _tools_problems(value)
        if problems:
            raise serializers.ValidationError(problems)
        return value

    def validate_model_map(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("model_map must be a JSON object")
        for model_name, provider_id in value.items():
            if not isinstance(model_name, str) or not isinstance(provider_id, str):
                raise serializers.ValidationError(
                    "model_map must be a mapping of string model names to string provider IDs"
                )
        return value


def _tools_problems(tools):
    """validate_tools' checks beyond the contract, as messages."""
    problems = []
    capabilities = tools.get("capabilities") or {}
    for name in sorted(capabilities):
        path = f"tools.capabilities.{name}"
        if not _TOOL_CAPABILITY.match(name):
            problems.append(
                f"{path}: a capability name must match {_TOOL_CAPABILITY.pattern}"
            )
        capability = capabilities[name] or {}
        providers = capability.get("providers") or []
        for vendor in providers:
            if not _TOOL_VENDOR.match(vendor):
                problems.append(
                    f"{path}.providers: {vendor!r} must match {_TOOL_VENDOR.pattern}"
                )
        if len(set(providers)) != len(providers):
            problems.append(f"{path}.providers lists a vendor twice")
        problems += _tool_option_problems(
            path,
            capability.get("defaults") or {},
            capability.get("pinned") or {},
            providers,
        )
    domains = (tools.get("policy") or {}).get("domains") or {}
    return problems + _domain_policy_problems(domains)


def _tool_option_problems(path, defaults, pinned, providers):
    problems = []
    for kind, options in (("defaults", defaults), ("pinned", pinned)):
        for key in sorted(options):
            if not _TOOL_OPTION.match(key):
                problems.append(
                    f"{path}.{kind}: option {key!r} must match {_TOOL_OPTION.pattern}"
                )
            if options[key] is None:
                problems.append(f"{path}.{kind}.{key} must not be null")
    for key in sorted(pinned.keys() & defaults.keys()):
        pinned_type, default_type = _json_type(pinned[key]), _json_type(defaults[key])
        if None not in (pinned[key], defaults[key]) and pinned_type != default_type:
            problems.append(
                f"{path}.pinned.{key} is a {pinned_type} but its default is a {default_type}"
            )
    if "provider" in pinned:
        vendor = pinned["provider"]
        if not isinstance(vendor, str) or not _TOOL_VENDOR.match(vendor):
            problems.append(f"{path}.pinned.provider must be a vendor id")
        elif providers and vendor not in providers:
            problems.append(
                f"{path}.pinned.provider {vendor!r} is not in {path}.providers"
            )
    return problems


def _json_type(value):
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    return "array" if isinstance(value, list) else "object"


def _domain_policy_problems(domains):
    problems = []
    named = {}
    for kind in ("allow", "deny"):
        entries = domains.get(kind) or []
        for entry in entries:
            if not _is_domain_pattern(entry):
                problems.append(
                    f"tools.policy.domains.{kind}: {entry!r} is not a host name or *.<host name>"
                )
        named[kind] = {entry.lower() for entry in entries if isinstance(entry, str)}
    both = named["allow"] & named["deny"]
    if both:
        problems.append(
            f"tools.policy.domains: {', '.join(sorted(both))} is both allowed and denied"
        )
    return problems


def _is_domain_pattern(entry):
    if not isinstance(entry, str):
        return False
    host = entry.removeprefix("*.")
    if not host or len(host) > 253:
        return False
    return all(_DOMAIN_LABEL.match(label) for label in host.lower().split("."))


def _refuse_managed_byok_only(mcp):
    """Refuse an mcp.managed_tools.allow glob that names a byok_only card
    (``search/brave``, ``*/brave``, ``search/brav[e]``), under every spelling
    the contract accepts for managed_tools: config_push pushes ``managedTools``
    as managed_tools, so both are read. A vendor glob with * or ?
    (``search/*``) is a wildcard and names no vendor; the gateway never admits
    a byok_only vendor as managed. The gateway's CheckOrgConfig applies the
    same rule."""
    named = [glob for glob in _managed_tools_allow(mcp) if _names_byok_only(glob)]
    if named:
        raise serializers.ValidationError(
            f"mcp.managed_tools.allow names a byok_only vendor ({', '.join(named)}): "
            "its terms allow only the org's own key, so it cannot be managed.",
            code=CAPABILITY_MANAGED_NOT_ALLOWED,
        )


def _managed_tools_sections(mcp):
    """mcp.managed_tools under each of the contract's spellings, in a fixed
    order; only the ones that are objects."""
    if not isinstance(mcp, Mapping):
        return []
    return [
        mcp[key]
        for key in sorted(field_input_names(contract.MCPOrgConfig, "managed_tools"))
        if isinstance(mcp.get(key), Mapping)
    ]


def _managed_tools_allow(mcp):
    """Every allow glob of mcp.managed_tools, read under each of the
    contract's spellings of managed_tools and allow."""
    globs = []
    for managed in _managed_tools_sections(mcp):
        for allow_key in sorted(
            field_input_names(contract.MCPManagedToolsConfig, "allow")
        ):
            allow = managed.get(allow_key)
            if isinstance(allow, list):
                globs += [glob for glob in allow if isinstance(glob, str)]
    return globs


def _names_byok_only(glob):
    """Whether a <capability-glob>/<vendor-glob> names a byok_only card under
    the gateway's glob syntax (Go's path.Match)."""
    capability_glob, slash, vendor_glob = glob.partition("/")
    if not slash or capability_glob.startswith("fagi."):
        return False
    if any(char in vendor_glob for char in "*?"):
        return False
    capability_re, vendor_re = _go_glob(capability_glob), _go_glob(vendor_glob)
    if capability_re is None or vendor_re is None:
        return False
    return any(
        capability_re.fullmatch(capability) and vendor_re.fullmatch(vendor)
        for capability, vendor in (card.split("/", 1) for card in BYOK_ONLY_TOOL_CARDS)
    )


def _go_glob(pattern):
    """Go's path.Match pattern syntax, the gateway's glob matcher, as a
    regex: * and ? within a segment, [...] classes with ^ and ranges, and \\
    escapes. None for a malformed pattern, which path.Match matches nothing
    with. Python's fnmatch differs (a literal \\, [!...] negation), so it is
    not used for a glob the gateway also reads."""
    out = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "[":
            parsed = _go_glob_class(pattern, i + 1)
            if parsed is None:
                return None
            regex, i = parsed
            out.append(regex)
            continue
        if char == "\\":
            i += 1
            if i == len(pattern):
                return None
            out.append(re.escape(pattern[i]))
        else:
            out.append({"*": "[^/]*", "?": "[^/]"}.get(char) or re.escape(char))
        i += 1
    return re.compile("".join(out), re.DOTALL)


def _go_glob_class(pattern, i):
    """The [...] class whose body starts at i, as (regex, index after its
    ]), or None when it is malformed."""
    negated = pattern.startswith("^", i)
    i += negated
    ranges, count = [], 0
    while not (pattern.startswith("]", i) and count):
        low, i = _go_glob_class_char(pattern, i)
        high = low
        if low is not None and pattern.startswith("-", i):
            high, i = _go_glob_class_char(pattern, i + 1)
        if low is None or high is None:
            return None
        if low <= high:
            ranges.append(f"{re.escape(low)}-{re.escape(high)}")
        count += 1
    if not ranges:
        return ("." if negated else "(?!)"), i + 1
    return f"[{'^' if negated else ''}{''.join(ranges)}]", i + 1


def _go_glob_class_char(pattern, i):
    """One class character at i (path.Match's getEsc), unescaped, and the
    index after it; None for a -, a ], a dangling \\ or a class that ends
    there."""
    if i >= len(pattern) or pattern[i] in "-]":
        return None, i
    if pattern[i] == "\\":
        i += 1
    if i + 1 >= len(pattern):
        return None, i
    return pattern[i], i + 1


def _active_mcp(org):
    """The mcp section of the org's active config, {} when there is none."""
    if org is None:
        return {}
    mcp = (
        AgentccOrgConfig.no_workspace_objects.filter(
            organization=org, is_active=True, deleted=False
        )
        .values_list("mcp", flat=True)
        .first()
    )
    return mcp if isinstance(mcp, dict) else {}


# The vs ids this request's validate_mcp issued.
# The config writer reruns the mcp rules under the org lock
# (enforce_mcp_write) and accepts an id on a slug the locked active config
# does not hold only when it was issued here, never one a client chose.
_ISSUED_VS_IDS = contextvars.ContextVar("agentcc_issued_vs_ids", default=frozenset())


def _checked_mcp(value, stored):
    """``value`` (an mcp section) checked against the org's stored one, with
    every virtual server's id: the rules of validate_mcp."""
    stored = stored if isinstance(stored, dict) else {}
    servers = value.get("servers")
    if servers is not None and not isinstance(servers, dict):
        raise serializers.ValidationError("mcp.servers must be a JSON object")
    stored_servers = stored.get("servers")
    stored_servers = stored_servers if isinstance(stored_servers, dict) else {}
    for server_id, entry in (servers or {}).items():
        _validate_mcp_server(
            server_id, entry, stored_entry=stored_servers.get(server_id)
        )
    _validate_contract("mcp", value, contract.MCPOrgConfig, _V2_MCP_KEYS)
    # The byok_only refusal first, so a glob that names such a vendor gets its
    # own code even when its spelling ([s]earch/brave) also fails the shape
    # check; then the shape check under every spelling the contract reads
    # (and config_push pushes) as managed_tools.
    _refuse_managed_byok_only(value)
    for managed_tools in _managed_tools_sections(value):
        _validate_managed_tool_globs(managed_tools)
    if value.get("virtual_servers") is None:
        return value
    value = copy.deepcopy(value)
    value["virtual_servers"] = _with_virtual_server_ids(
        value["virtual_servers"],
        stored.get("virtual_servers"),
        issued=_ISSUED_VS_IDS.get(),
    )
    return value


def enforce_mcp_write(value, stored):
    """The mcp rules, rerun by every config write under the org lock against
    the locked active version.

    update-config and the single-section MCP actions (update-mcp-server and
    the rest) never run validate_mcp, and POST org-configs/ runs it before
    the lock. So the writer reruns the server, header and glob rules, and the
    id rule against the version it is about to replace: an existing slug
    keeps its stored id, a new slug gets one, and an id a client set on a new
    slug or changed is refused. Raises serializers.ValidationError.
    """
    if not isinstance(value, dict) or not value:
        return value
    try:
        return _checked_mcp(value, stored)
    finally:
        _ISSUED_VS_IDS.set(frozenset())


def enforce_tools_write(value):
    """validate_tools' rules, rerun by every config write under the org lock:
    update-config never runs the write serializer, so the writer reruns the
    strict contract and the name, domain and pinned-option checks on a changed
    tools section. Raises serializers.ValidationError."""
    if not isinstance(value, dict) or not value:
        return value
    _validate_contract("tools", value, contract.ToolsConfig)
    problems = _tools_problems(value)
    if problems:
        raise serializers.ValidationError(problems)
    return value


def _validate_mcp_server(server_id, entry, *, stored_entry):
    path = f"mcp.servers.{server_id}"
    is_new = stored_entry is None
    if is_new and not _MCP_SERVER_ID.fullmatch(server_id):
        raise serializers.ValidationError(
            f"{path}: a server id must match {_MCP_SERVER_ID.pattern}"
        )
    if not isinstance(entry, dict):
        raise serializers.ValidationError(f"{path} must be a JSON object")
    if "private_network" in entry:
        raise serializers.ValidationError(
            f"{path}.private_network is not an org setting. Private upstream "
            "addresses are reachable only through the operator list "
            "mcp.egress.private_allow."
        )
    _validate_contract(path, entry, contract.MCPServerOrgConfig, _V2_MCP_SERVER_KEYS)
    _validate_mcp_server_endpoint(path, entry, stored_entry)
    _validate_forward_headers(path, entry.get("forward_headers") or [])


def _validate_mcp_server_endpoint(path, entry, stored_entry):
    """A server is reached over http or sse at an absolute http(s) URL; stdio
    and command servers are operator-only. A server the org's active config
    already holds keeps its saved values unchanged; a changed transport or url
    is checked like a new server's."""
    stored = stored_entry if isinstance(stored_entry, dict) else None
    transport = entry.get("transport")
    if (
        transport is not None
        and transport not in _MCP_TENANT_TRANSPORTS
        and (stored is None or transport != stored.get("transport"))
    ):
        raise serializers.ValidationError(
            f"{path}.transport must be one of {', '.join(_MCP_TENANT_TRANSPORTS)}; "
            "stdio servers are defined only in the operator's gateway config."
        )
    url = entry.get("url")
    if (
        url is not None
        and (stored is None or url != stored.get("url"))
        and not _is_http_url(url)
    ):
        raise serializers.ValidationError(
            f"{path}.url must be an absolute http or https URL with no user "
            "or password in it; send a credential as credential.secret."
        )


def _is_http_url(url):
    # One source of truth with the guardrail/alert callback check: an
    # absolute http(s) URL, a host, no user or password, no control bytes (DEL
    # and others break Go's net/url). _MCP_URL_SCHEMES is ("http", "https").
    return guardrail_urls.is_http_url(url)


def _validate_forward_headers(path, headers):
    """Refused names are compared trimmed and case-folded, so ' Authorization'
    is refused as Authorization; any other name must be an HTTP token."""
    refused = [
        header
        for header in headers
        if header.strip().lower() in _MCP_REFUSED_FORWARD_HEADERS
        or header.strip().lower().startswith(_MCP_REFUSED_FORWARD_PREFIXES)
    ]
    if refused:
        raise serializers.ValidationError(
            f"{path}.forward_headers cannot forward {', '.join(refused)}: "
            "credentials, MCP session headers and the gateway's own "
            "x-agentcc-* and x-omega-* headers are never sent upstream.",
            code=FORWARD_HEADER_REFUSED,
        )
    malformed = [h for h in headers if not _HTTP_HEADER_NAME.fullmatch(h)]
    if malformed:
        raise serializers.ValidationError(
            f"{path}.forward_headers: {', '.join(repr(h) for h in malformed)} "
            "is not an HTTP header name."
        )


def _validate_managed_tool_globs(managed_tools):
    if not isinstance(managed_tools, Mapping):
        return
    for key in ("allow", "deny"):
        bad = [
            glob
            for glob in managed_tools.get(key) or []
            if not isinstance(glob, str) or not _MCP_MANAGED_GLOB.fullmatch(glob)
        ]
        if bad:
            raise serializers.ValidationError(
                f"mcp.managed_tools.{key}: {', '.join(bad)} must name "
                "fagi.<id>/<tool> or <capability>/<vendor>."
            )


def _with_virtual_server_ids(virtual_servers, stored, *, issued=frozenset()):
    """Every virtual server with its id: the stored one for an existing slug,
    a new one for a new slug. A client-set id is refused unless it is the
    slug's stored id. ``issued`` are ids this request already assigned (the
    writer's rerun of an id validate_mcp gave a new slug): such an id is kept
    for a slug still new, and replaced by the stored one when a concurrent
    write stored the slug first."""
    if not isinstance(virtual_servers, dict):
        raise serializers.ValidationError("mcp.virtual_servers must be a JSON object")
    stored = stored if isinstance(stored, dict) else {}
    out = {}
    for slug, entry in virtual_servers.items():
        path = f"mcp.virtual_servers.{slug}"
        if not _MCP_VIRTUAL_SERVER_SLUG.fullmatch(slug):
            raise serializers.ValidationError(
                f"{path}: a virtual server slug must match "
                f"{_MCP_VIRTUAL_SERVER_SLUG.pattern}"
            )
        stored_entry = stored.get(slug)
        stored_id = stored_entry.get("id") if isinstance(stored_entry, dict) else None
        if not (
            isinstance(stored_id, str) and _MCP_VIRTUAL_SERVER_ID.fullmatch(stored_id)
        ):
            stored_id = None
        if not isinstance(entry, dict):
            raise serializers.ValidationError(f"{path} must be a JSON object")
        sent_id = entry.get("id")
        if sent_id is not None and sent_id in issued:
            sent_id = None if stored_id else sent_id
        if sent_id is not None and sent_id != stored_id and sent_id not in issued:
            raise serializers.ValidationError(
                f"{path}.id is assigned when the virtual server is created and "
                "never changes; leave it out of a new virtual server and do not "
                "change an existing one's.",
                code=VS_ID_IMMUTABLE,
            )
        out[slug] = {**entry, "id": stored_id or sent_id or _new_virtual_server_id()}
    return out


def _new_virtual_server_id():
    """vs_ plus a ULID: 48 bits of milliseconds, then 80 random bits, in
    Crockford base32. Recorded as issued by this request (_ISSUED_VS_IDS)."""
    value = (time.time_ns() // 1_000_000) << 80 | secrets.randbits(80)
    vs_id = "vs_" + "".join(
        _CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5)
    )
    _ISSUED_VS_IDS.set(_ISSUED_VS_IDS.get() | {vs_id})
    return vs_id


def _validate_routing_v2(path, routing, contract_model):
    """The v2 keys of a routing section, which may be a rollout's candidate."""
    auto = routing.get("auto")
    if auto is not None:
        validate_auto_routing(auto, path=f"{path}.auto")
    _validate_contract(path, routing, contract_model, _V2_ROUTING_KEYS)
    _validate_contract(
        f"{path}.fastest",
        routing.get("fastest"),
        contract.FastestResponseConfig,
        _V2_FASTEST_KEYS,
    )


def _validate_rollout(rollout):
    """
    routing.rollout is new in v2 and checked strictly, except the candidate
    routing it carries: that is a copy of another config's routing, so its
    older keys keep the lax check like any saved routing.
    """
    if not isinstance(rollout, dict):
        raise serializers.ValidationError("routing.rollout must be a JSON object")
    candidate = rollout.get("routing")
    if candidate is not None and not isinstance(candidate, dict):
        raise serializers.ValidationError(
            "routing.rollout.routing must be a JSON object"
        )
    if isinstance(candidate, dict) and "rollout" in candidate:
        raise serializers.ValidationError(
            "routing.rollout.routing cannot carry a rollout of its own"
        )
    own_fields = {**rollout, "routing": {}} if candidate is not None else rollout
    _validate_contract("routing.rollout", own_fields, contract.RolloutConfig)
    if candidate is not None:
        _validate_routing_v2(
            "routing.rollout.routing", candidate, contract.RolloutRoutingConfig
        )


def validate_auto_routing(auto, path="routing.auto"):
    """Check routing.auto, which tunes the gateway's model "auto"."""
    if not isinstance(auto, dict):
        raise serializers.ValidationError(f"{path} must be a JSON object")
    _validate_contract(path, auto, contract.AutoRoutingConfig)
