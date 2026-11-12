"""Save-time provider contract shared by the credential and console APIs."""

# Keep aligned with providers.createProvider; test_provider_validation parses
# its Go switch, including aliases, so adding an adapter requires updating here.
PROVIDER_API_FORMATS = (
    "openai",
    "anthropic",
    "gemini",
    "google",
    "bedrock",
    "cohere",
    "azure",
    "deepgram",
    "cartesia",
    "systemone",
    "typesafe",
)
PROVIDER_FORMAT_ERROR = (
    "Unsupported api_format. Allowed values: " + ", ".join(PROVIDER_API_FORMATS) + "."
)

# Native provider IDs plus the Go registry's KnownProviders presets retain
# their existing endpoint exemption for legacy formats only. A wire format is
# not a provider identity: decision formats use their own vendor defaults below,
# never this legacy name set. Do not add decision-only names to this set.
KNOWN_PROVIDER_NAMES = (frozenset(PROVIDER_API_FORMATS) - {"systemone", "typesafe"}) | {
    "vertex",
    "groq",
    "mistral",
    "together",
    "fireworks",
    "deepinfra",
    "perplexity",
    "cerebras",
    "xai",
    "huggingface",
    "anyscale",
    "replicate",
    "openrouter",
}

PROVIDER_CREDENTIAL_ALIASES = {
    "access_key": "aws_access_key_id",
    "secret_key": "aws_secret_access_key",
    "region": "aws_region",
}
PROVIDER_CREDENTIAL_FIELDS = frozenset(
    {
        "api_key",
        "aws_access_key_id",
        "aws_secret_access_key",
        "aws_region",
        "aws_session_token",
        "service_account_json",
    }
)
ALLOWED_CREDENTIAL_KEYS = tuple(
    sorted(PROVIDER_CREDENTIAL_FIELDS | PROVIDER_CREDENTIAL_ALIASES.keys())
)


def provider_credential_input_names():
    """Every credential spelling, including generated and legacy aliases.

    Resolve lazily because config_push already imports this module, and its
    publisher also consumes the shared secret policy. Do not duplicate the
    contract's alias introspection or broaden the existing save vocabulary.
    """
    from agentcc.contracts.gateway_admin import ProviderConfig
    from agentcc.services.config_push import field_input_names

    return frozenset(PROVIDER_CREDENTIAL_ALIASES).union(
        *(
            field_input_names(ProviderConfig, name)
            for name in PROVIDER_CREDENTIAL_FIELDS
        )
    )


def ensure_provider_configuration(provider_name, api_format, base_url):
    """Reject an unbuildable format or a name/format pair with no upstream.

    Decision formats require an explicit URL unless the Go factory recognizes
    a fixed vendor default. Operator configuration is never an exemption for
    these formats. Legacy formats retain their native/operator endpoint policy.
    """
    if api_format not in PROVIDER_API_FORMATS:
        raise ValueError(PROVIDER_FORMAT_ERROR)
    if base_url:
        return
    if api_format in ("systemone", "typesafe"):
        # Match systemone.vendorByID and override.hasDefaultUpstream: only
        # TypeSafe/Perplexity prefixes have fixed decision-vendor defaults.
        # Org instance suffixes do not select a different vendor.
        decision_name = provider_name.lower().partition("_org_")[0]
        if decision_name.startswith(("typesafe", "perplexity")):
            return
    elif provider_name in KNOWN_PROVIDER_NAMES:
        return
    else:
        from agentcc.services.gateway_client import (
            GatewayClientError,
            get_gateway_client,
        )

        try:
            config = get_gateway_client().get_config()
        except GatewayClientError:
            config = {}
        providers = config.get("provider_ids", []) if isinstance(config, dict) else []
        if isinstance(providers, list) and provider_name in providers:
            return
    raise ValueError(
        f"Provider '{provider_name}' has no known default endpoint. "
        "Set base_url to an http(s) URL with a host."
    )
