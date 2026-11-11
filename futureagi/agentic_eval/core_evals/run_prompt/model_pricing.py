import re
from datetime import UTC, datetime
from functools import lru_cache
from typing import NamedTuple, Optional

from agentic_eval.core_evals.run_prompt.available_models import AVAILABLE_MODELS
from agentic_eval.core_evals.run_prompt.gateway_prices import gateway_token_rates
from agentic_eval.core_evals.run_prompt.price_schedule import (
    PRICE_SCHEDULE,
    dated_rates,
)

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


_BEDROCK_ARN_MARKERS = (":inference-profile/", ":foundation-model/")
_GOOGLE_PREFIXES = (
    "publishers/google/models/",
    "vertex_ai_global/",
    "google/",
    "models/",
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


# A suffix that names the same model: a date, a version, "@default" or (for a
# request that is more specific than the catalogue name) "-latest". "-cyber",
# "-lite" or "-tts" name another model and must not take this one's price.
_SAME_MODEL_DATE = r"(?:[-@](?:\d{4}-\d{2}-\d{2}|\d{8}))"
_SAME_MODEL_VERSION = r"(?:[-:]v\d+(?::\d+)?|@default)"
_REQUESTED_SUFFIX = re.compile(
    _SAME_MODEL_DATE + "?" + r"(?:[-:]v\d+(?::\d+)?|@default|-latest)?"
)
_AVAILABLE_SUFFIX = re.compile(_SAME_MODEL_DATE + "?" + _SAME_MODEL_VERSION + "?")


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
                if (
                    requested_variant != available_variant
                    and _is_boundary_match(available_variant, requested_variant)
                    and _REQUESTED_SUFFIX.fullmatch(
                        requested_variant[len(available_variant) :]
                    )
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
                if suffix and _AVAILABLE_SUFFIX.fullmatch(suffix):
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

    # A Bedrock ARN names the row the platform builds for it (_bedrock_arn() in
    # ee/prompts/additional_models.py), whatever the account or region.
    if any(marker in requested_norm for marker in _BEDROCK_ARN_MARKERS):
        arn_model = requested_norm.rsplit("/", 1)[-1]
        for model in AVAILABLE_MODELS:
            available_name = model.get("model_name")
            if not isinstance(available_name, str):
                continue
            available_norm = _normalize_model_name(available_name)
            if (
                available_norm.startswith("bedrock/")
                and available_norm.rsplit("/", 1)[-1] == arn_model
            ):
                return model

    # Google's own name forms: the catalogue lists the bare model name.
    for prefix in _GOOGLE_PREFIXES:
        if requested_norm.startswith(prefix):
            requested_norm = requested_norm[len(prefix) :]
            break

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


@lru_cache(maxsize=512)
def _catalogue_pricing(model_name: str) -> Optional[tuple[Optional[str], dict]]:
    """The catalogue entry a name resolves to and its static pricing.

    Cached per process, so it never holds a dated price: one cached before
    midnight would still be billed after it.
    """
    model_info = get_model_info(model_name)
    if model_info and "pricing" in model_info:
        return model_info.get("model_name"), model_info["pricing"].copy()
    return None


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PricingLookup(NamedTuple):
    """A price and where it came from.

    ``source`` is "decision", "gateway_table" or "available_models". ``key`` is the
    gateway key or catalogue name that set the price (None for a decision model).
    """

    pricing: dict
    source: str
    key: Optional[str]


def _check_at(at: Optional[datetime]) -> datetime:
    if at is None:
        return _utc_now()
    if at.utcoffset() is None:
        raise ValueError(
            "The usage time has no time zone; pass an aware datetime (UTC)."
        )
    return at


def lookup_pricing(
    model_name: str, *, at: Optional[datetime] = None, non_token_call: bool = False
) -> Optional[PricingLookup]:
    """
    Price a model as in force at a moment, and say which table priced it.

    Order: the decision models; then the gateway's table (the price the gateway
    bills), unless the call is speech; then the platform catalogue. A table price
    whose input rate is 0 is no price. The dated rows of the key that set the
    price are laid over its static rates.

    Args:
        model_name: The model identifier
        at: When the usage happened, timezone-aware. None means now.
        non_token_call: The call is billed by characters, audio time or images,
            not tokens. The gateway's table holds only per-token prices, so it is
            skipped.

    Returns:
        A PricingLookup, or None if no table prices the name.
    """
    at = _check_at(at)

    from evaluations.decision_models import is_decision_model

    if is_decision_model(model_name):
        from evaluations.decision_pricing import calculate_decision_cost

        cost = calculate_decision_cost(model_name, {"input_tokens": 1000000})
        return PricingLookup(
            {
                "input_per_1M_tokens": cost["total_cost"],
                "output_per_1M_tokens": 0.0,
            },
            "decision",
            None,
        )

    if not non_token_call:
        gateway = gateway_token_rates(model_name)
        if gateway is not None:
            key, rates = gateway
            dated = dated_rates(key, at)
            if dated:
                rates.update(dated)
            if rates["input_per_1M_tokens"] > 0:
                return PricingLookup(rates, "gateway_table", key)

    found = _catalogue_pricing(model_name)
    if found is None:
        return None
    resolved_name, pricing = found
    priced = dict(pricing)
    if resolved_name:
        dated = dated_rates(resolved_name, at)
        if dated:
            priced.update(dated)
    if priced.get("input_per_1M_tokens") == 0:
        return None
    return PricingLookup(priced, "available_models", resolved_name)


def get_model_pricing(
    model_name: str,
    *,
    at: Optional[datetime] = None,
    non_token_call: bool = False,
) -> Optional[dict]:
    """
    Get pricing information for a model, as in force at a moment.

    Args:
        model_name: The model identifier
        at: When the usage happened, timezone-aware. None means now. A model
            with dated prices is priced at the row in force then.
        non_token_call: The call is billed by characters, audio time or images,
            so the gateway's per-token table is not used.

    Returns:
        Pricing dict with various structures depending on model type:

        For LLM/Chat models:
            {"input_per_1M_tokens": float, "output_per_1M_tokens": float}

        For TTS (Text-to-Speech) models:
            {"input_per_1M_characters": float}

        For STT (Speech-to-Text) models:
            {"input_per_minute": float}

        Returns None if model not found or pricing not available
    """
    found = lookup_pricing(model_name, at=at, non_token_call=non_token_call)
    return found.pricing if found else None


def price_change_dates(
    model_name: str, *, non_token_call: bool = False
) -> tuple[datetime, ...]:
    """The instants at which the price of a model changes, oldest first.

    Taken from the dated rows of the key that sets the price, so a view can price
    each part of a period at its own rate. Empty when the price never changes.
    """
    found = lookup_pricing(model_name, non_token_call=non_token_call)
    if found is None or found.key is None:
        return ()
    return tuple(start for start, _ in PRICE_SCHEDULE.get(found.key, ()))


_TOKEN_CALL_MODES = frozenset({"chat", "completion"})


def listed_pricing(row: dict, *, at: Optional[datetime] = None) -> Optional[dict]:
    """The price the model list shows for one of its rows: the one billed now.

    None for a custom model (the organisation sets its own price) and for a row
    whose billed price is not a per-token one the list can show.
    """
    if row.get("custom"):
        return None
    mode = row.get("mode") or "chat"
    pricing = get_model_pricing(row["model_name"], at=at, non_token_call=mode == "tts")
    if (
        pricing is not None
        and mode in _TOKEN_CALL_MODES
        and "input_per_1M_tokens" not in pricing
    ):
        # A token call bills the default when the table price has no per-token input.
        # Image, speech and transcription rows keep the price they are billed by.
        return None
    return pricing


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
#     _catalogue_pricing.cache_clear()
