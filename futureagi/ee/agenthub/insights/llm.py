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
    """Thread-safe running total of model cost for one run."""

    cost_usd: float = 0.0
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(
        self, cost: float, prompt_tokens: int = 0, completion_tokens: int = 0
    ) -> None:
        with self._lock:
            self.cost_usd += cost or 0.0
            self.calls += 1
            self.prompt_tokens += prompt_tokens or 0
            self.completion_tokens += completion_tokens or 0


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
        time.sleep(min(8, 1.5 * (attempt + 1)))
    raise InsightModelError(
        "The analysis model is unavailable right now. Try again shortly.",
        unavailable=True,
    ) from last_error
