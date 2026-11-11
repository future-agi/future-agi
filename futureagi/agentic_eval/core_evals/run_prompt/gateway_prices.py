"""The gateway's model price table, read the way the gateway reads it.

``gateway_prices.json`` is a byte copy of agentcc-gateway/internal/modeldb/litellm.json:
the backend image holds only futureagi/. futureagi/tests/test_gateway_prices.py keeps
the two equal, and agentcc-gateway/scripts/update-litellm-pricing.sh writes both.

``load_gateway_prices`` follows ``parseLiteLLM`` (agentcc-gateway/internal/modeldb/
litellm.go), and ``gateway_key`` follows ``ModelDB.Get`` (modeldb.go), so the backend
bills a name from the same entry the gateway does. Only the per-token chat rates are
kept: they are all the backend bills from this table.

Standard library only.
"""

import json
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

GATEWAY_PRICES_PATH = Path(__file__).with_name("gateway_prices.json")

# Field names of the gateway's ``liteLLMEntry`` (litellm.go), by the type Go decodes.
_FLOAT_FIELDS = (
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_read_input_token_cost",
    "input_cost_per_token_batches",
    "output_cost_per_token_batches",
    "output_cost_per_image",
    "input_cost_per_image",
    "input_cost_per_second",
    "input_cost_per_character",
    "input_cost_per_query",
    "ocr_cost_per_page",
    "output_cost_per_second",
    "input_cost_per_pixel",
    "output_cost_per_pixel",
    "input_cost_per_image_token",
    "output_cost_per_image_token",
    "max_input_tokens",
    "max_output_tokens",
    "max_tokens",
)
_STRING_FIELDS = ("litellm_provider", "mode", "deprecation_date")
_BOOL_FIELDS = (
    "supports_function_calling",
    "supports_parallel_function_calling",
    "supports_vision",
    "supports_audio_input",
    "supports_audio_output",
    "supports_pdf_input",
    "supports_response_schema",
    "supports_system_messages",
    "supports_prompt_caching",
    "supports_reasoning",
)
_LIST_FIELD = "supported_regions"

_KIND = dict.fromkeys(_FLOAT_FIELDS, "float")
_KIND.update(dict.fromkeys(_STRING_FIELDS, "string"))
_KIND.update(dict.fromkeys(_BOOL_FIELDS, "bool"))
_KIND[_LIST_FIELD] = "list"
_BY_LOWER = {name.lower(): name for name in _KIND}

# ``mapMode`` (litellm.go): the modes the gateway names. Anything else is chat.
_NAMED_MODES = {
    "chat": "chat",
    "completion": "chat",
    "embedding": "embedding",
    "image_generation": "image_generation",
    "image_edit": "image_generation",
    "audio_transcription": "audio_transcription",
    "audio_speech": "audio_speech",
    "rerank": "rerank",
    "moderation": "moderation",
    "ocr": "ocr",
    "video_generation": "video_generation",
    "search": "search",
}


class GatewayPricesError(RuntimeError):
    """The gateway price copy is missing or malformed."""


def _reject_constant(name: str):
    # Go's json.Unmarshal refuses NaN and Infinity, so it refuses the whole file.
    raise GatewayPricesError(
        f"The gateway price copy holds {name}, which is not valid JSON."
    )


def _decode_entry(raw) -> dict | None:
    """The fields of one entry as Go decodes them, or None where Go refuses it."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        return None
    fields = {}
    for name, value in raw.items():
        field = name if name in _KIND else _BY_LOWER.get(name.lower())
        if field is None or value is None:
            continue
        kind = _KIND[field]
        if kind == "float":
            if isinstance(value, bool) or not isinstance(value, int | float):
                return None
        elif kind == "string":
            if not isinstance(value, str):
                return None
        elif kind == "bool":
            if not isinstance(value, bool):
                return None
        elif not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return None
        fields[field] = value
    return fields


def map_mode(mode) -> str:
    return _NAMED_MODES.get(mode or "", "chat")


def is_zero_pricing(mode: str, fields: dict) -> bool:
    """``isZeroPricing``: the gateway drops these entries."""
    input_price = fields.get("input_cost_per_token") or 0
    output_price = fields.get("output_cost_per_token") or 0
    if mode == "chat":
        return input_price == 0
    if mode == "embedding":
        return input_price == 0 and output_price == 0
    return False


def load_gateway_prices(path) -> dict[str, tuple[str, MappingProxyType]]:
    """Read the copy into ``{key: (mode, per-1M rates)}``, as the gateway loads it."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise GatewayPricesError(
            f"The gateway price copy {path} is missing. It must be a copy of "
            "agentcc-gateway/internal/modeldb/litellm.json."
        ) from None
    try:
        data = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise GatewayPricesError(
            f"The gateway price copy {path} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise GatewayPricesError(
            f"The gateway price copy {path} must hold an object of model keys"
        )

    prices = {}
    for key, raw in data.items():
        if key == "sample_spec":
            continue
        fields = _decode_entry(raw)
        if fields is None:
            continue
        mode = map_mode(fields.get("mode"))
        if is_zero_pricing(mode, fields):
            continue
        rates = {
            "input_per_1M_tokens": round(
                (fields.get("input_cost_per_token") or 0) * 1_000_000, 12
            ),
            "output_per_1M_tokens": round(
                (fields.get("output_cost_per_token") or 0) * 1_000_000, 12
            ),
        }
        cached = fields.get("cache_read_input_token_cost") or 0
        if cached > 0:
            rates["cached_input_per_1M_tokens"] = round(cached * 1_000_000, 12)
        prices[key] = (mode, MappingProxyType(rates))
    if not prices:
        raise GatewayPricesError(f"The gateway price copy {path} loads no entries")
    return prices


GATEWAY_PRICES = load_gateway_prices(GATEWAY_PRICES_PATH)

# Lower-case form of each key. Two keys fold to one (together_ai/BAAI/bge-base-en-v1.5
# and its lower-case twin); the one that already is lower case wins.
_LOWER: dict[str, str] = {}
for _key in GATEWAY_PRICES:
    _folded = _key.lower()
    if _folded not in _LOWER or _key == _folded:
        _LOWER[_folded] = _key


def _go_rules(name: str, table) -> str | None:
    """``ModelDB.Get``: the exact key, then after the first "/", then the last segment."""
    if name in table:
        return table[name]
    first = name.find("/")
    if first >= 0 and name[first + 1 :] in table:
        return table[name[first + 1 :]]
    last = name.rfind("/")
    if 0 <= last < len(name) - 1 and name[last + 1 :] in table:
        return table[name[last + 1 :]]
    return None


_EXACT = MappingProxyType({key: key for key in GATEWAY_PRICES})


@lru_cache(maxsize=4096)
def gateway_key(name: str) -> str | None:
    """The key of the entry the gateway would find for a name, or None.

    The gateway's three rules run on the name as given. Only if all three miss do
    they run again on its lower-case form.
    """
    name = name.strip()
    found = _go_rules(name, _EXACT)
    if found is not None:
        return found
    return _go_rules(name.lower(), _LOWER)


def gateway_token_rates(name: str) -> tuple[str, dict] | None:
    """``(key, fresh dict of per-1M rates)`` for a chat entry, else None.

    The mode filter runs after the lookup, and no further rule is tried when the
    entry found is not chat.
    """
    key = gateway_key(name)
    if key is None:
        return None
    mode, rates = GATEWAY_PRICES[key]
    if mode != "chat":
        return None
    return key, dict(rates)
