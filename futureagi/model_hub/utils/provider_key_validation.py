"""Validate a model-provider API key against the provider before it is stored.

The AI Providers settings page used to persist keys without ever checking them,
so an invalid key was only discovered later when a prompt/optimization run failed
with a cryptic "API key not configured" error. This module makes the failure
visible at save time by probing the provider with a minimal completion call and
rejecting the key only on a definitive authentication failure.

Semantics are deliberately fail-open: ``is_provider_key_valid`` returns ``False``
ONLY when the provider authoritatively rejects the key (see
``_is_definitive_auth_rejection`` for the exact set). Any other outcome -- an
unmapped provider, a network/timeout error, a rate limit, or a self-hosted
install with no outbound access -- is inconclusive and returns ``True`` so a
transient issue can never block a legitimate save.

Why this doesn't reuse ``model_hub.utils.utils.validate_model_working``: that
helper is a good fit for probing a *specific* model a user is registering (it
takes a mandatory ``model_name`` and already knows how to route
openai/azure/vertex_ai/bedrock/sagemaker/custom credentials), but its exception
handling collapses every failure into a bare ``Exception(str(...))`` before it
reaches the caller -- the original litellm exception type and status code are
gone by the time we'd see it. That makes it unusable for the "reject only on a
definitive auth failure, by exception type/status" contract this module needs
(see ``_is_definitive_auth_rejection``), and changing its contract to preserve
that would ripple into its other caller (``CustomAIModelCreateView``, which
intentionally rejects on *any* validation error, not just auth failures).
Keeping this a separate, narrow probe avoids that risk; the shared concept is
still centralized on this module's side via ``validate_or_400`` in
``run_prompt.py`` so there's exactly one call site for "probe and reject" here.
"""

import litellm
import structlog
from litellm.exceptions import AuthenticationError, PermissionDeniedError

logger = structlog.get_logger(__name__)

# Cheap, non-reasoning chat models used purely to auth-probe a provider key.
# Provider-prefixed so litellm routes unambiguously without a custom_llm_provider
# hint. Kept explicit (not "first chat model in the catalog") so the probe can
# never land on a reasoning model whose max_tokens quirk would surface a param
# error instead of the auth error we need to see. A provider absent from this map
# is not probed (fail-open) -- this covers the JSON-config providers
# (vertex_ai/azure/bedrock/sagemaker) and any provider we have no cheap,
# currently-supported probe model for (e.g. voice-only or embedding-only
# providers, which litellm.completion cannot probe at all).
PROVIDER_PROBE_MODEL = {
    "openai": "openai/gpt-4o-mini",
    "anthropic": "anthropic/claude-haiku-4-5",
    "groq": "groq/llama-3.1-8b-instant",
    "cohere": "cohere/command-r",
    "cohere_chat": "cohere_chat/command-r",
    "mistral": "mistral/mistral-small-latest",
    "gemini": "gemini/gemini-2.5-flash-lite",
    "perplexity": "perplexity/sonar",
    "together_ai": "together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo-Free",
    "deepseek": "deepseek/deepseek-chat",
    "openrouter": "openrouter/mistralai/mistral-7b-instruct",
    "fireworks_ai": "fireworks_ai/accounts/fireworks/models/llama-v3p1-8b-instruct",
    "cerebras": "cerebras/llama3.1-8b",
}

# One-token probe keeps the validation call as cheap as possible.
_PROBE_MESSAGES = [{"role": "user", "content": "ping"}]
_PROBE_MAX_TOKENS = 1

# Bound the worst case latency of the synchronous save-time probe. Without
# this, a reachable-but-hanging provider blocks the request thread up to
# litellm's ~600s default -- the save spinner hangs and the request eventually
# 5xx's at the gateway. 10s is generous for a 1-token completion while still
# keeping a stuck provider from degrading the whole worker.
_PROBE_TIMEOUT_SECONDS = 10

# HTTP statuses that unambiguously mean "the provider authenticated the
# request and rejected the credentials" -- never a transient/network/model
# issue.
_AUTH_REJECT_STATUS_CODES = {401, 403}


def _is_definitive_auth_rejection(exc: Exception) -> bool:
    """Return True only when ``exc`` is a definitive "bad credentials" signal.

    litellm normalizes most provider auth failures to ``AuthenticationError``
    (401) or ``PermissionDeniedError`` (403), but some provider integrations
    fall back to the generic ``APIError`` (or another ``APIError`` subclass,
    e.g. ``BadRequestError``) while still carrying the real HTTP status on
    ``.status_code`` or on the wrapped ``.response``. Checking both means a
    provider that reports a 401/403 through one of those unmapped exception
    types is still treated as a rejection instead of silently falling through
    to the fail-open branch below.
    """
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return True

    if getattr(exc, "status_code", None) in _AUTH_REJECT_STATUS_CODES:
        return True

    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) in _AUTH_REJECT_STATUS_CODES:
        return True

    return False


def is_provider_key_valid(provider, key):
    """Return whether ``key`` is usable for ``provider``.

    ``False`` only when the provider authenticates the request and definitively
    rejects the key (see ``_is_definitive_auth_rejection``). ``True`` when the
    key works OR when validity cannot be determined (unmapped provider, missing
    key, network/timeout/rate-limit/other error) -- fail-open by design.
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
            timeout=_PROBE_TIMEOUT_SECONDS,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - classified below
        if _is_definitive_auth_rejection(exc):
            return False
        # Inconclusive (network blip, rate limit, retired probe model, egress
        # blocked in a self-hosted install, ...): never block the save, but
        # log at WARNING -- same convention as the sibling
        # validate_model_working -- since this is the only signal an operator
        # gets if probing has silently become a no-op for a provider.
        logger.warning(
            "provider_key_validation_inconclusive",
            provider=provider,
            error=str(exc),
        )
        return True
