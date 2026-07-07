"""Validate a model-provider API key against the provider before it is stored.

The AI Providers settings page used to persist keys without ever checking them,
so an invalid key was only discovered later when a prompt/optimization run failed
with a cryptic "API key not configured" error. This module makes the failure
visible at save time by probing the provider with a minimal completion call and
rejecting the key only on a definitive authentication failure.

Semantics are deliberately fail-open: ``is_provider_key_valid`` returns ``False``
ONLY when the provider authoritatively rejects the key (``AuthenticationError``).
Any other outcome -- an unmapped provider, a network/timeout error, a rate limit,
or a self-hosted install with no outbound access -- is inconclusive and returns
``True`` so a transient issue can never block a legitimate save.
"""

import litellm
import structlog
from litellm.exceptions import AuthenticationError

logger = structlog.get_logger(__name__)

# Cheap, non-reasoning chat models used purely to auth-probe a provider key.
# Provider-prefixed so litellm routes unambiguously without a custom_llm_provider
# hint. Kept explicit (not "first chat model in the catalog") so the probe can
# never land on a reasoning model whose max_tokens quirk would surface a param
# error instead of the auth error we need to see. A provider absent from this map
# is not probed (fail-open) -- this covers the JSON-config providers
# (vertex_ai/azure/bedrock/sagemaker) and any provider we have no cheap probe for.
PROVIDER_PROBE_MODEL = {
    "openai": "openai/gpt-4o-mini",
    "anthropic": "anthropic/claude-3-5-haiku-20241022",
    "groq": "groq/llama-3.1-8b-instant",
    "cohere": "cohere/command-r",
    "mistral": "mistral/mistral-small-latest",
    "gemini": "gemini/gemini-1.5-flash",
    "perplexity": "perplexity/sonar",
    "together_ai": "together_ai/meta-llama/Llama-3.2-3B-Instruct-Turbo",
}

# One-token probe keeps the validation call as cheap as possible.
_PROBE_MESSAGES = [{"role": "user", "content": "ping"}]
_PROBE_MAX_TOKENS = 1


def is_provider_key_valid(provider, key):
    """Return whether ``key`` is usable for ``provider``.

    ``False`` only when the provider authenticates the request and rejects the
    key. ``True`` when the key works OR when validity cannot be determined
    (unmapped provider, missing key, network/other error) -- fail-open by design.
    """
    if not provider or not key:
        return True

    probe_model = PROVIDER_PROBE_MODEL.get(provider)
    if not probe_model:
        return True

    try:
        litellm.completion(
            model=probe_model,
            messages=_PROBE_MESSAGES,
            api_key=key,
            max_tokens=_PROBE_MAX_TOKENS,
        )
        return True
    except AuthenticationError:
        return False
    except Exception as exc:  # noqa: BLE001 - inconclusive, never block the save
        logger.info(
            "provider_key_validation_inconclusive",
            provider=provider,
            error=str(exc),
        )
        return True
