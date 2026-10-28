"""Model calls for Insight.

Two tiers, as in LangSmith Insights: a fast model reads every unit and a
thinking model names categories, organises them and writes the summary.
Both default to Turing models (the only models that run on Future AGI keys):
Cloud reaches them through its own gateway, a licensed self-hosted install
through Future AGI's managed service. Deployments can point them at their own
models, which go through the deployment's gateway.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field

import structlog

from agentic_eval.core.utils.model_config import ModelConfigs

logger = structlog.get_logger(__name__)

FAST = "fast"
THINKING = "thinking"
_DEFAULTS = {
    FAST: ModelConfigs.TURING_FLASH.model_name,
    THINKING: ModelConfigs.TURING_LARGE.model_name,
}
_SETTINGS = {FAST: "INSIGHTS_FAST_MODEL", THINKING: "INSIGHTS_THINKING_MODEL"}
_TIMEOUT = 180


# The model gateway or provider refused the call itself (unknown model, refused
# credentials, an account without credit): retrying cannot help.
MODEL_REFUSED_MESSAGE = (
    "The analysis model refused the request, so trying again won't help: "
    "an administrator needs to check the Insights model settings."
)
_REFUSED_STATUSES = frozenset({400, 401, 403, 404, 422})


def refused(exc: BaseException | None) -> bool:
    return getattr(exc, "status_code", None) in _REFUSED_STATUSES


class InsightModelError(Exception):
    """The model could not produce a usable answer; safe to show users."""

    def __init__(self, message: str, *, unavailable: bool = False):
        super().__init__(message)
        self.unavailable = unavailable


def model_for(tier: str) -> str:
    name = _SETTINGS[tier]
    try:
        from django.conf import settings

        configured = getattr(settings, name, None)
    except Exception:  # pragma: no cover - settings always load in the app
        configured = None
    return configured or os.environ.get(name) or _DEFAULTS[tier]


@dataclass
class Spend:
    """Thread-safe running total of model cost for one run. ``unpriced`` is
    set when some of it was priced at the ceiling because nothing knew the
    model's price (see ``usage_cost``)."""

    cost_usd: float = 0.0
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    image_parts: int = 0
    audio_seconds: float = 0.0
    unpriced: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(
        self,
        cost: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        *,
        image_parts: int = 0,
        audio_seconds: float = 0.0,
        unpriced: bool = False,
    ) -> None:
        with self._lock:
            self.cost_usd += cost or 0.0
            self.calls += 1
            self.prompt_tokens += prompt_tokens or 0
            self.completion_tokens += completion_tokens or 0
            self.image_parts += image_parts or 0
            self.audio_seconds += audio_seconds or 0.0
            self.unpriced = self.unpriced or unpriced


def call_cost(
    model: str, gateway_cost: float, prompt_tokens: int, completion_tokens: int
) -> float:
    """What one model call cost: the gateway's figure, else the model's token
    price. The gateway prices only the models in its catalogue, not the Turing
    aliases, so a zero falls back to the platform's price list, as Falcon AI's
    billing does."""
    if gateway_cost and gateway_cost > 0:
        return float(gateway_cost)
    if not model or not (prompt_tokens or completion_tokens):
        return 0.0
    try:
        from agentic_eval.core_evals.fi_utils.token_count_helper import (
            calculate_total_cost,
        )

        priced = calculate_total_cost(
            model,
            {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        )
        return float(priced["total_cost"])
    except Exception:
        logger.warning("insight_call_unpriced", model=model, exc_info=True)
        return 0.0


# The least a model nothing prices is charged per million input and output
# tokens: the price of the dearest models an agent may route to, so an
# unknown one is never undercharged. An image part counts as this many input
# tokens on top of the reported ones.
UNPRICED_CEILING = (15.0, 75.0)
IMAGE_PART_TOKENS = 1_600


def _token_price(model: str) -> tuple[float, float] | None:
    """The price list's input and output price per million tokens, or None
    when the model isn't on it (the price list would charge a cheap default)."""
    if not model:
        return None
    try:
        from agentic_eval.core_evals.run_prompt.model_pricing import (
            get_model_pricing,
        )

        pricing = get_model_pricing(model) or {}
        return (
            float(pricing["input_per_1M_tokens"]),
            float(pricing["output_per_1M_tokens"]),
        )
    except Exception:
        return None


def ceiling_price() -> tuple[float, float]:
    """The highest price, per direction, of the two tier models and
    ``UNPRICED_CEILING``."""
    tiers = map(_token_price, (model_for(FAST), model_for(THINKING)))
    prices = [UNPRICED_CEILING, *(p for p in tiers if p)]
    return max(p[0] for p in prices), max(p[1] for p in prices)


def usage_cost(
    model: str,
    cost_usd,
    input_tokens: int,
    output_tokens: int,
    *,
    image_parts: int = 0,
) -> tuple[float, bool]:
    """What an agent's model calls cost, and whether anything knew the price:
    the gateway's figure, else the price list's, else the ceiling price with
    each image part as ``IMAGE_PART_TOKENS`` input tokens. An unknown cost is
    never priced at $0."""
    known = isinstance(cost_usd, int | float) and not isinstance(cost_usd, bool)
    if known and math.isfinite(cost_usd) and cost_usd > 0:
        return float(cost_usd), True
    if not (input_tokens or output_tokens or image_parts):
        return 0.0, True
    if _token_price(model) is not None:
        return call_cost(model, 0.0, input_tokens, output_tokens), True
    price_in, price_out = ceiling_price()
    tokens_in = input_tokens + image_parts * IMAGE_PART_TOKENS
    logger.warning("insight_call_priced_at_ceiling", model=model)
    return (tokens_in * price_in + output_tokens * price_out) / 1_000_000, False


def parse_json(raw: str):
    """The first JSON object or array in a model reply, tolerating fences."""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = text.rstrip("`").strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("no JSON in reply")
    start = min(starts)
    closer = "}" if text[start] == "{" else "]"
    end = text.rfind(closer)
    if end <= start:
        raise ValueError("unterminated JSON in reply")
    return json.loads(text[start : end + 1])


def _managed(model: str) -> bool:
    """Whether ``model`` is a Future AGI managed model (Turing) that this
    deployment reaches through the managed service. Cloud's gateway has the
    Turing models; a self-hosted install's local gateway doesn't, so there
    they go through the license's managed service, as Turing evals do."""
    from ee.licensing.managed_ai import is_managed_model
    from ee.usage.deployment import DeploymentMode

    return is_managed_model(model) and not DeploymentMode.is_cloud()


def _complete_managed(
    messages: list[dict], model: str, max_tokens: int, temperature: float
):
    from ee.licensing.managed_ai import (
        chat_completion,
        response_content,
        response_usage,
    )

    response = chat_completion(
        {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=_TIMEOUT,
    )
    if not response.get("choices"):
        # An error body, not a reply: retry it as a provider failure rather
        # than as a reply cut short.
        raise RuntimeError(
            f"managed completion returned no choices: {str(response.get('error'))[:200]}"
        )
    usage = response_usage(response)
    prompt_tokens = usage["prompt_tokens"]
    completion_tokens = usage["completion_tokens"]
    # The managed service returns only the body, without the gateway's cost
    # header, so the call is priced at the model's token price.
    cost = call_cost(model, 0.0, prompt_tokens, completion_tokens)
    return response_content(response), cost, prompt_tokens, completion_tokens


def _complete(messages: list[dict], model: str, max_tokens: int, temperature: float):
    if _managed(model):
        return _complete_managed(messages, model, max_tokens, temperature)

    from ee.usage.services.gateway_llm_client import (
        call_llm,
        call_llm_raw,
        get_gateway_client,
    )

    client = get_gateway_client()
    if client is None:
        content = call_llm(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if not content:
            raise InsightModelError(
                "The analysis model is unavailable right now.", unavailable=True
            )
        return content, 0.0, 0, 0
    result = call_llm_raw(
        client,
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=_TIMEOUT,
    )
    try:
        content = result.response.choices[0].message.content
    except (AttributeError, IndexError):
        content = ""
    usage = getattr(result.response, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    cost = call_cost(model, result.cost_usd, prompt_tokens, completion_tokens)
    return content or "", cost, prompt_tokens, completion_tokens


def chat_json(
    system: str,
    user: str,
    *,
    tier: str,
    max_tokens: int = 1200,
    temperature: float = 0.2,
    spend: Spend | None = None,
    attempts: int = 3,
):
    """One JSON answer from the tier's model, retried on transient failures
    and on replies that aren't JSON."""
    model = model_for(tier)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    last_error: Exception | None = None
    budget = max_tokens
    for attempt in range(attempts):
        try:
            content, cost, prompt_tokens, completion_tokens = _complete(
                messages, model, budget, temperature
            )
            # A reply that isn't JSON was still paid for.
            if spend is not None:
                spend.add(cost, prompt_tokens, completion_tokens)
            return parse_json(content)
        except InsightModelError as exc:
            last_error = exc
        except ValueError as exc:
            # Usually a reply cut off by the token cap (reasoning models spend
            # part of it before answering); retry with more room.
            last_error = exc
            budget = min(budget * 2, 8000)
            logger.info(
                "insight_model_reply_unparsable",
                model=model,
                attempt=attempt + 1,
                next_max_tokens=budget,
                error=str(exc)[:120],
            )
            continue
        except Exception as exc:  # provider/network errors
            last_error = exc
            logger.warning(
                "insight_model_call_failed",
                model=model,
                attempt=attempt + 1,
                error=str(exc)[:300],
            )
            if refused(exc):
                break
        time.sleep(min(8, 1.5 * (attempt + 1)))
    if refused(last_error):
        raise InsightModelError(MODEL_REFUSED_MESSAGE, unavailable=True) from last_error
    raise InsightModelError(
        "The analysis model is unavailable right now. Try again shortly.",
        unavailable=True,
    ) from last_error
