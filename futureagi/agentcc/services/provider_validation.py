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
)
PROVIDER_FORMAT_ERROR = (
    "Unsupported api_format. Allowed values: " + ", ".join(PROVIDER_API_FORMATS) + "."
)

# Native provider IDs plus the Go registry's KnownProviders presets. These may
# inherit the operator's endpoint. A custom ID must supply a URL or be present
# in the gateway's operator config (not merely an org's stored credentials).
KNOWN_PROVIDER_NAMES = frozenset(PROVIDER_API_FORMATS) | {
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


def ensure_provider_configuration(provider_name, api_format, base_url):
    """Reject an unbuildable format or a custom name with no upstream.

    No network discovery of a provider is performed. For a custom alias with
    no URL, the trusted gateway control API is the authority for operator
    configuration. If unavailable, ask for an explicit URL rather than save a
    configuration whose endpoint we cannot establish.
    """
    if api_format not in PROVIDER_API_FORMATS:
        raise ValueError(PROVIDER_FORMAT_ERROR)
    if base_url or provider_name in KNOWN_PROVIDER_NAMES:
        return

    from agentcc.services.gateway_client import GatewayClientError, get_gateway_client

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
