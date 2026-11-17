"""Require the credential selected by a model call, not an unused alias.

Stored configurations must be whitelisted before this predicate is used.
The key-only branch wins whenever ``key`` exists, including an empty value.
Otherwise Azure uses its normalizer, OpenAI takes ``api_key``, and AWS
providers require their access-key pair. Other providers keep their own checks.
"""

ADD_API_KEY_MESSAGE = "Add an API key for this model."

_KEYED_PROVIDERS = ("openai", "azure", "bedrock", "sagemaker")
_AWS_KEY_PAIR = ("aws_access_key_id", "aws_secret_access_key")


class ModelKeyMissing(ValueError):
    """A model configuration without a key of its own."""

    def __init__(self):
        super().__init__(ADD_API_KEY_MESSAGE)


def _filled(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _keyed_provider(provider) -> str | None:
    name = str(provider or "").strip().lower()
    for keyed in _KEYED_PROVIDERS:
        if (
            name == keyed
            or name.startswith(f"{keyed}/")
            or name.startswith(f"{keyed}_")
        ):
            return keyed
    return None


def model_key_missing(provider, configuration) -> bool:
    """Whether the selected credential is absent, blank or not text.

    This does not whitelist or rewrite the configuration. In particular, a
    stored OpenAI ``api_key`` must first be dropped by its provider whitelist;
    validation's OpenAI branch, in contrast, accepts that canonical field.
    """
    keyed = _keyed_provider(provider)
    if keyed is None:
        return False
    if isinstance(configuration, str):
        return not _filled(configuration)
    if not isinstance(configuration, dict):
        return True
    if "key" in configuration:
        return not _filled(configuration["key"])
    if keyed in ("bedrock", "sagemaker"):
        return not all(_filled(configuration.get(field)) for field in _AWS_KEY_PAIR)
    if str(provider or "").strip().lower() == "azure":
        from model_hub.utils.azure_endpoints import normalize_azure_custom_model_config

        normalized = normalize_azure_custom_model_config(configuration)
        return not _filled(normalized["api_key"])
    # Azure call-provider spellings (such as azure_ai) take canonical api_key;
    # unlike the stored azure provider, their dispatch does not normalize apiKey.
    return not _filled(configuration.get("api_key"))


def refuse_model_without_its_own_key(provider, configuration) -> None:
    """Raise ModelKeyMissing when the call's selected credential is absent."""
    if model_key_missing(provider, configuration):
        raise ModelKeyMissing()
