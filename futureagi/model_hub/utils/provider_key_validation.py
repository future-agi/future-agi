"""Lightweight, save-time validation of LLM provider API keys.

Probes a provider's "list models" endpoint (or closest equivalent) to confirm
a key is actually authenticated before it gets persisted, without making a
paid completion call. Deliberately fails open: a provider we don't have a
probe for, or an ambiguous network/response outcome, is always treated as
valid so a transient issue never blocks a legitimate save.
"""

import requests
import structlog

logger = structlog.get_logger(__name__)

REQUEST_TIMEOUT_SECONDS = 8

_AUTH_FAILURE_STATUS_CODES = {401, 403}


def _probe_openai_style(key, base_url):
    response = requests.get(
        f"{base_url}/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return response.status_code


def _probe_openai(key):
    return _probe_openai_style(key, "https://api.openai.com/v1")


def _probe_groq(key):
    return _probe_openai_style(key, "https://api.groq.com/openai/v1")


def _probe_together_ai(key):
    return _probe_openai_style(key, "https://api.together.xyz/v1")


def _probe_cohere(key):
    return _probe_openai_style(key, "https://api.cohere.ai/v1")


def _probe_mistral(key):
    return _probe_openai_style(key, "https://api.mistral.ai/v1")


def _probe_anthropic(key):
    response = requests.get(
        "https://api.anthropic.com/v1/models",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return response.status_code


def _probe_gemini(key):
    response = requests.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"key": key},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return response.status_code


def _probe_perplexity(key):
    # Perplexity has no public models-list endpoint, so probe with the
    # cheapest possible chat completion instead.
    response = requests.post(
        "https://api.perplexity.ai/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": "sonar",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    return response.status_code


# Probe map — only providers we can confidently auth-check with a cheap
# request. Any provider not listed here skips validation entirely.
PROVIDER_PROBES = {
    "openai": _probe_openai,
    "anthropic": _probe_anthropic,
    "gemini": _probe_gemini,
    "groq": _probe_groq,
    "cohere": _probe_cohere,
    "mistral": _probe_mistral,
    "perplexity": _probe_perplexity,
    "together_ai": _probe_together_ai,
}


def validate_provider_key(provider, key):
    """Best-effort, save-time validation of a provider API key.

    Returns (is_valid, error_message). Only rejects on an explicit auth
    failure (401/403) from a provider we have a probe for; everything else
    (unprobed provider, missing key, network error, non-auth error status)
    is treated as valid so we never block a save we can't confidently verify.
    """
    probe = PROVIDER_PROBES.get(provider)
    if probe is None or not key:
        return True, None

    try:
        status_code = probe(key)
    except requests.RequestException:
        logger.warning("provider_key_probe_unreachable", provider=provider)
        return True, None

    if status_code in _AUTH_FAILURE_STATUS_CODES:
        return (
            False,
            f"Invalid API key for {provider} — the provider rejected it (HTTP {status_code}).",
        )
    return True, None
