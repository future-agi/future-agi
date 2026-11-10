"""Decision-only token estimates; never use a similarly named chat price.

USD per million input tokens, reviewed 2026-11-06. Native: OpenAI Decisions
pricing and Perplexity Decisions pricing. Router: public OpenRouter model catalog.
Successful router calls prefer usage.cost; table rates are estimates otherwise.
The existing general evaluator billing path adds its configured per-run fee.
"""

from decimal import Decimal

from evaluations.decision_models import get_decision_model
from evaluations.types import DecisionCost, DecisionUsage

_ROUTER_INPUT = {
    "parallel/decisions-fast:free": "0",
    "morph/reflex": "0.042",
    "baseten/joey-1": "0.01",
    "baseten/elephant-1": "0.035",
    "baseten/mammoth-1": "0.1",
    "liquid/d1.1": "0.02",
    "liquid/d1": "0.02",
    "liquid/d1-ultra": "0.042",
    "~liquid/d1-latest": "0.02",
    "~liquid/d1-ultra-latest": "0.042",
    "nace-ai/drex-max": "0.06",
    "nace-ai/drex-v2.0": "0.06",
    "nace-ai/drex-v1.5": "0.04",
    "perplexity/pplx-decider-v1.2": "0.02",
    "perplexity/pplx-decider-flash-v1.2": "0.01",
    "~perplexity/pplx-decider-latest": "0.019",
    "~perplexity/pplx-decider-flash-latest": "0.01",
    "typesafe/jev-1.14": "0.042",
    "typesafe/jev-1.13": "0.042",
    "~typesafe/jev-latest": "0.042",
    "respan/span-02": "0.01",
    "respan/span-01": "0.02",
    "respan/span-01-lite": "0",
    "respan/span-01-lite:free": "0",
    "scaledown/classify-1": "0.04",
    "microsoft/microsoft-decision-1": "0.042",
    "cloudflare/clef-omni": "0.15",
    "cloudflare/clef-flash": "0.038",
    "cloudflare/clef": "0.192",
    "upstage/solar-decide": "0.05",
    "upstage/solar-decide-flash": "0.05",
    "openai/gpt-6-luna-decisions": "0.1",
    "togethercomputer/tev1-4b-experimental": "0.042",
    "inception/mercury-decide": "0.02",
    "jaredpalmer/kev-4b": "0.042",
}


def calculate_decision_cost(
    model: str,
    usage: DecisionUsage,
    *,
    provider_cost: int | float | Decimal | None = None,
) -> DecisionCost:
    spec = get_decision_model(model)
    if spec is None:
        raise ValueError("Unknown decision pricing model")
    if spec.author == "TypeSafe":
        return {
            "model": model,
            "prompt_cost": 0.0,
            "completion_cost": 0.0,
            "total_cost": 0.0,
            "pricing_source": "jev_flat_fee",
        }
    elif spec.connection == "openai":
        rate = Decimal("0.1")
    elif spec.connection == "perplexity":
        rate = Decimal("0.01" if "flash" in spec.upstream_model else "0.02")
    else:
        rate = Decimal(_ROUTER_INPUT[spec.group])
    tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
    cost = Decimal(tokens) * rate / Decimal(1000000)
    source = "decision_catalog_estimate"
    if spec.connection == "openrouter" and provider_cost is not None:
        cost = Decimal(str(provider_cost))
        if not cost.is_finite() or cost < 0:
            raise ValueError("Invalid decision cost")
        source = "provider_usage"
    return {
        "model": model,
        "prompt_cost": float(cost),
        "completion_cost": 0.0,
        "total_cost": float(cost),
        "pricing_source": source,
    }
