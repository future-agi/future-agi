from functools import lru_cache
from typing import Optional

from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS
# (available_models always available)


_MATCH_BOUNDARY_CHARS = {"-", "/", ".", "@", ":"}
_ROUTER_PROVIDERS = {"openrouter"}
_PROVIDER_PRIORITY = (
    "openai",
    "anthropic",
    "azure",
    "bedrock",
    "gemini",
    "vertex_ai",
    "openrouter",
)


def _normalize_model_name(name: str) -> str:
    return name.strip().lower()


def _extract_provider_hint(model_name_normalized: str) -> Optional[str]:
    if "/" not in model_name_normalized:
        return None
    provider = model_name_normalized.split("/", 1)[0].strip()
    if not provider:
        return None
    # Only return provider hint if it's a known provider
    # This avoids treating size/quality prefixes like "256-x-256" or "standard"
    # as providers
    if provider in _PROVIDER_PRIORITY or provider in _ROUTER_PROVIDERS:
        return provider
    return None


def _aliases(model_name_normalized: str) -> set[str]:
    aliases = {model_name_normalized}
    if "/" in model_name_normalized:
        tail = model_name_normalized.rsplit("/", 1)[-1].strip()
        if tail:
            aliases.add(tail)
    return aliases


def _is_boundary_match(prefix: str, full: str) -> bool:
    if not full.startswith(prefix):
        return False
    if len(full) == len(prefix):
        return True
    return full[len(prefix)] in _MATCH_BOUNDARY_CHARS


def _looks_like_version_suffix(suffix: str) -> bool:
    if not suffix:
        return False
    if suffix[0] not in _MATCH_BOUNDARY_CHARS:
        return False
    tail = suffix[1:]
    if not tail:
        return False
    if tail.startswith("latest"):
        return True
    if tail.startswith("preview"):
        return True
    if tail[0].isdigit():
        return True
    if tail.startswith("v") and len(tail) > 1 and tail[1].isdigit():
        return True
    return False


def _version_sort_key(suffix: str) -> tuple[int, int, str]:
    """
    Returns a comparable key for version-like suffixes.

    Ordering:
    - "latest" wins over numeric versions
    - numeric versions are compared by digit-length, then lexicographically
    - non-numeric tails are last
    """
    tail = suffix[1:] if suffix and suffix[0] in _MATCH_BOUNDARY_CHARS else suffix
    tail = tail.strip()
    if tail.startswith("latest"):
        return (2, 0, tail)

    if tail and tail[0].isdigit():
        i = 0
        while i < len(tail) and (tail[i].isdigit() or tail[i] == "-"):
            i += 1
        leading = tail[:i].strip("-")
        digits = leading.replace("-", "")
        if digits:
            return (1, len(digits), digits)

    if tail.startswith("v") and len(tail) > 1 and tail[1].isdigit():
        j = 1
        while j < len(tail) and tail[j].isdigit():
            j += 1
        digits = tail[1:j]
        if digits:
            return (0, len(digits), digits)

    return (0, 0, tail)


def _pick_best_candidate(
    candidates: list[dict],
    *,
    provider_hint: Optional[str],
    prefer_unprefixed: bool,
) -> Optional[dict]:
    if not candidates:
        return None

    def provider_priority(provider: str) -> int:
        if provider in _PROVIDER_PRIORITY:
            return _PROVIDER_PRIORITY.index(provider)
        return len(_PROVIDER_PRIORITY)

    def sort_key(model: dict) -> tuple:
        model_name = model.get("model_name")
        model_name_norm = (
            _normalize_model_name(model_name) if isinstance(model_name, str) else ""
        )
        provider = model.get("providers")
        provider_norm = (
            _normalize_model_name(provider) if isinstance(provider, str) else ""
        )

        hint_mismatch = 0
        if provider_hint:
            hint_mismatch = 0 if provider_norm == provider_hint else 1

        is_unprefixed = ("/" not in model_name_norm) and ("." not in model_name_norm)
        unprefixed_mismatch = 0
        if prefer_unprefixed:
            unprefixed_mismatch = 0 if is_unprefixed else 1

        return (
            hint_mismatch,
            unprefixed_mismatch,
            provider_priority(provider_norm),
            len(model_name_norm),
            model_name_norm,
        )

    return sorted(candidates, key=sort_key)[0]


def _find_best_match(
    models: list[dict],
    *,
    requested_norm: str,
    provider_hint: Optional[str],
) -> Optional[dict]:
    requested_aliases = _aliases(requested_norm)
    prefer_unprefixed = (
        (provider_hint is None)
        and ("/" not in requested_norm)
        and ("." not in requested_norm)
    )

    # 1) Exact match
    for model in models:
        available_name = model.get("model_name")
        if not isinstance(available_name, str):
            continue
        if _normalize_model_name(available_name) == requested_norm:
            return model

    # 2) Exact match against aliases (handles missing/extra provider prefixes)
    alias_candidates: list[dict] = []
    for model in models:
        available_name = model.get("model_name")
        if not isinstance(available_name, str):
            continue
        available_norm = _normalize_model_name(available_name)
        if available_norm in requested_aliases or requested_norm in _aliases(
            available_norm
        ):
            alias_candidates.append(model)
    best = _pick_best_candidate(
        alias_candidates,
        provider_hint=provider_hint,
        prefer_unprefixed=prefer_unprefixed,
    )
    if best:
        return best

    # 3) Requested is more specific than available (e.g., version suffix)
    best_prefix_len = -1
    prefix_candidates: list[dict] = []
    for model in models:
        available_name = model.get("model_name")
        if not isinstance(available_name, str):
            continue
        available_norm = _normalize_model_name(available_name)
        for available_variant in _aliases(available_norm):
            for requested_variant in requested_aliases:
                if requested_variant != available_variant and _is_boundary_match(
                    available_variant, requested_variant
                ):
                    match_len = len(available_variant)
                    if match_len > best_prefix_len:
                        best_prefix_len = match_len
                        prefix_candidates = [model]
                    elif match_len == best_prefix_len:
                        prefix_candidates.append(model)

    best = _pick_best_candidate(
        prefix_candidates,
        provider_hint=provider_hint,
        prefer_unprefixed=prefer_unprefixed,
    )
    if best:
        return best

    # 4) Available is more specific than requested (prefer version-like expansions only)
    version_candidates: list[tuple[tuple[int, int, str], dict]] = []
    for model in models:
        available_name = model.get("model_name")
        if not isinstance(available_name, str):
            continue
        available_norm = _normalize_model_name(available_name)
        for available_variant in _aliases(available_norm):
            for requested_variant in requested_aliases:
                if requested_variant == available_variant:
                    continue
                if not _is_boundary_match(requested_variant, available_variant):
                    continue
                suffix = available_variant[len(requested_variant) :]
                if _looks_like_version_suffix(suffix):
                    version_candidates.append((_version_sort_key(suffix), model))

    if version_candidates:
        best_key = max(key for key, _ in version_candidates)
        best_models = [model for key, model in version_candidates if key == best_key]
        return _pick_best_candidate(
            best_models,
            provider_hint=provider_hint,
            prefer_unprefixed=prefer_unprefixed,
        )

    return None


@lru_cache(maxsize=512)
def get_model_info(model_name: str) -> Optional[dict]:
    """
    Get full model configuration by model name with fuzzy matching support.

    Matching strategy (in order):
    1. Exact match
    2. Alias match (handles provider prefixes like "openai/tts-1" vs "tts-1")
    3. Longest prefix match with boundary checks for versioned names
       (e.g., "gpt-5-nano-2025-08-07" matches "gpt-5-nano")
    4. Version-like expansion for unversioned names
       (e.g., "claude-3-haiku" can match "claude-3-haiku-20240307")

    Args:
        model_name: The model identifier (e.g., "gpt-4o", "gpt-5-nano-2025-08-07")

    Returns:
        Full model configuration dict or None if not found
    """
    requested_norm = _normalize_model_name(model_name)
    if not requested_norm:
        return None

    provider_hint = _extract_provider_hint(requested_norm)
    if provider_hint:
        provider_models = [
            m
            for m in AVAILABLE_MODELS
            if isinstance(m.get("providers"), str)
            and _normalize_model_name(m["providers"]) == provider_hint
        ]
        result = _find_best_match(
            provider_models, requested_norm=requested_norm, provider_hint=provider_hint
        )
        if result:
            return result
        # Fallback: try searching all models with the model name after stripping
        # the provider prefix (e.g., "openai/dall-e-3" -> search for "dall-e-3")
        stripped_name = (
            requested_norm.split("/", 1)[-1]
            if "/" in requested_norm
            else requested_norm
        )
        if stripped_name != requested_norm:
            result = _find_best_match(
                AVAILABLE_MODELS,
                requested_norm=stripped_name,
                provider_hint=provider_hint,
            )
            if result:
                return result

    # If no provider is explicitly requested, prefer direct providers over router providers.
    direct_provider_models = [
        m
        for m in AVAILABLE_MODELS
        if not (
            isinstance(m.get("providers"), str)
            and _normalize_model_name(m["providers"]) in _ROUTER_PROVIDERS
        )
    ]
    best = _find_best_match(
        direct_provider_models, requested_norm=requested_norm, provider_hint=None
    )
    if best:
        return best

    return _find_best_match(
        AVAILABLE_MODELS, requested_norm=requested_norm, provider_hint=None
    )


def _has_numeric_pricing(pricing: dict) -> bool:
    """
    Return True if the pricing dict contains at least one usable numeric price.

    Some catalogue entries carry placeholder pricing that is present but not
    actually priceable, e.g. azure_ai ``{"details": "Varies by deployment..."}``
    or together_ai ``{"per_1M_tokens": "pay-per-token"}``. Such dicts have no
    numeric value and must be treated as "no pricing" so a detectable fallback
    estimate is used instead of silently costing $0.

    ``bool`` is explicitly excluded because ``isinstance(True, int)`` is True.
    """
    return any(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in pricing.values()
    )


@lru_cache(maxsize=512)
def get_model_pricing(model_name: str) -> Optional[dict]:
    """
    Get pricing information for a model.

    Args:
        model_name: The model identifier

    Returns:
        Pricing dict with various structures depending on model type:

        For LLM/Chat models:
            {"input_per_1M_tokens": float, "output_per_1M_tokens": float}

        For TTS (Text-to-Speech) models:
            {"input_per_1M_characters": float}

        For STT (Speech-to-Text) models:
            {"input_per_minute": float}

        Returns None if model not found, pricing not available, or pricing is a
        placeholder with no numeric values (so callers apply fallback pricing).
    """
    model_info = get_model_info(model_name)
    if model_info and "pricing" in model_info:
        pricing = model_info["pricing"]
        if isinstance(pricing, dict):
            # Placeholder / non-numeric pricing (present but not priceable) is
            # treated as missing so the fallback chain applies a real estimate.
            if not _has_numeric_pricing(pricing):
                return None
            return pricing.copy()  # Return a copy to prevent mutations
    return None


def list_available_models(
    mode: Optional[str] = None, provider: Optional[str] = None
) -> list[str]:
    """
    List all available model names, optionally filtered.

    Args:
        mode: Filter by mode (e.g., "chat", "tts", "stt")
        provider: Filter by provider (e.g., "openai", "anthropic")

    Returns:
        List of model names

    Example:
        >>> models = list_available_models(mode="chat", provider="openai")
        >>> "gpt-4o" in models
        True
    """
    models = AVAILABLE_MODELS

    if mode:
        models = [m for m in models if m.get("mode") == mode]
    if provider:
        models = [m for m in models if m.get("providers") == provider]

    return [
        m["model_name"]
        for m in models
        if "model_name" in m and isinstance(m["model_name"], str)
    ]


# def clear_pricing_cache() -> None:
#     """
#     Clear the LRU cache for pricing lookups.

#     Useful for testing or if AVAILABLE_MODELS is updated at runtime.
#     """
#     get_model_info.cache_clear()
#     get_model_pricing.cache_clear()
