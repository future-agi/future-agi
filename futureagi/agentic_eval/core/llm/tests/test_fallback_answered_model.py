"""A completion records and prices the model that answered it.

When the requested model fails three times, ``LLM._get_completion_content``
(and its async form) asks the final fallback models: Vertex first, then
OpenAI. The model that answers is recorded on the LLM (``answered_model``,
``answered_provider``), its tokens are priced at that model's price, and the
usage log names that model and its provider.

Only the model library, the price list and the usage log are replaced. The
price list prices from synthetic per-token rates, so each cost below says
which model's price was used.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentic_eval.core.llm import llm as llm_module
from agentic_eval.core.llm.llm import LLM
from agentic_eval.core.utils.model_config import ModelConfigs

REQUESTED_MODEL = "gpt-4o"
REQUESTED_PROVIDER = "openai"
VERTEX = ModelConfigs.VERTEX_GEMINI_3_7_FLASH
OPENAI = ModelConfigs.OPENAI_GPT_5_1
MESSAGES = [{"role": "user", "content": "Is the reply polite?"}]

# Synthetic (prompt, completion) rates in dollars per token.
RATES = {
    REQUESTED_MODEL: (0.00001, 0.00003),
    VERTEX.model_name: (0.000001, 0.000002),
    OPENAI.model_name: (0.000004, 0.000008),
}
# The price list's default rate, for a model it has no price for.
DEFAULT_RATE = (0.0000005, 0.000001)


def price(model_name, prompt_tokens, completion_tokens):
    return priced_at(RATES[model_name], prompt_tokens, completion_tokens)


def priced_at(rate, prompt_tokens, completion_tokens):
    prompt_rate, completion_rate = rate
    prompt_cost = prompt_tokens * prompt_rate
    completion_cost = completion_tokens * completion_rate
    return {
        "total_cost": round(prompt_cost + completion_cost, 6),
        "prompt_cost": round(prompt_cost, 6),
        "completion_cost": round(completion_cost, 6),
    }


def summed(*costs):
    return {key: sum(cost[key] for cost in costs) for key in costs[0]}


def answer(content, prompt_tokens, completion_tokens):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content), finish_reason="stop"
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
    )


def with_response_cost(response, response_cost):
    """``response`` carrying the cost the model library attached to it."""
    response._hidden_params = {"response_cost": response_cost}
    return response


def empty_answer(prompt_tokens):
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
            total_tokens=prompt_tokens,
        ),
    )


def primary_down():
    return [RuntimeError("primary model unavailable")] * llm_module.MAX_RETRIES


def make_llm():
    with patch.object(LLM, "_init_client"):
        return LLM(
            provider=REQUESTED_PROVIDER,
            model_name=REQUESTED_MODEL,
            temperature=0,
            max_tokens=256,
        )


def asked_models(call):
    return [c.kwargs["model"] for c in call.call_args_list]


def assert_cost(llm, expected):
    for key in ("total_cost", "prompt_cost", "completion_cost"):
        assert llm.cost[key] == pytest.approx(expected[key]), key


@pytest.fixture
def library(monkeypatch):
    """The model library (each call records its model), the price list
    (synthetic rates; it records which model it priced, and prices a model
    in ``default_priced`` at its default rate) and the usage log (it keeps
    each row)."""
    models = MagicMock()
    models.acompletion = AsyncMock()
    priced = []
    default_priced = set()
    logged = []

    def calculate_total_cost(model_name, token_usage, *args, **kwargs):
        priced.append(model_name)
        prompt_tokens = token_usage.get("prompt_tokens", 0)
        completion_tokens = token_usage.get("completion_tokens", 0)
        if model_name in default_priced:
            cost = priced_at(DEFAULT_RATE, prompt_tokens, completion_tokens)
            return {**cost, "pricing_source": "default"}
        cost = price(model_name, prompt_tokens, completion_tokens)
        return {**cost, "pricing_source": "available_models"}

    monkeypatch.setattr(llm_module, "litellm", models)
    monkeypatch.setattr(llm_module, "calculate_total_cost", calculate_total_cost)
    monkeypatch.setattr(llm_module, "RETRY_DELAY", 0)
    monkeypatch.setattr(llm_module, "log_to_clickhouse", logged.append)
    models.priced = priced
    models.default_priced = default_priced
    models.logged = logged
    return models


def test_a_vertex_fallback_answer_is_recorded_and_priced_as_that_model(library):
    """(a) The requested model fails three times and the Vertex fallback
    answers: the cost is the Vertex price for its tokens, and Vertex is
    recorded as the model that answered."""
    library.completion.side_effect = [
        *primary_down(),
        answer("vertex answer", 1000, 500),
    ]
    llm = make_llm()

    out = llm._get_completion_content(messages=MESSAGES)

    assert out == "vertex answer"
    assert asked_models(library.completion) == [REQUESTED_MODEL] * 3 + [
        VERTEX.model_name
    ]
    assert llm.token_usage["prompt_tokens"] == 1000
    assert llm.token_usage["completion_tokens"] == 500
    assert_cost(llm, price(VERTEX.model_name, 1000, 500))
    assert llm.answered_model == VERTEX.model_name
    assert llm.answered_provider == VERTEX.provider


@pytest.mark.parametrize(
    "vertex_outcome",
    [RuntimeError("vertex unavailable"), empty_answer(300)],
    ids=["vertex_call_fails", "vertex_answer_empty"],
)
def test_an_openai_fallback_answer_is_recorded_and_priced_as_that_model(
    library, vertex_outcome
):
    """(b) Vertex does not answer either and the OpenAI fallback answers:
    the OpenAI tokens are priced at the OpenAI price, any Vertex tokens at
    the Vertex price, and OpenAI is recorded as the model that answered."""
    library.completion.side_effect = [
        *primary_down(),
        vertex_outcome,
        answer("openai answer", 800, 400),
    ]
    llm = make_llm()

    out = llm._get_completion_content(messages=MESSAGES)

    assert out == "openai answer"
    assert asked_models(library.completion) == [REQUESTED_MODEL] * 3 + [
        VERTEX.model_name,
        OPENAI.model_name,
    ]
    expected = price(OPENAI.model_name, 800, 400)
    if not isinstance(vertex_outcome, Exception):
        expected = summed(price(VERTEX.model_name, 300, 0), expected)
    assert_cost(llm, expected)
    assert llm.answered_model == OPENAI.model_name
    assert llm.answered_provider == OPENAI.provider


def test_without_a_fallback_the_requested_model_is_recorded_as_answering(library):
    """(c) The requested model answers: it is recorded as the model that
    answered."""
    library.completion.return_value = answer("requested answer", 200, 100)
    llm = make_llm()

    out = llm._get_completion_content(messages=MESSAGES)

    assert out == "requested answer"
    assert asked_models(library.completion) == [REQUESTED_MODEL]
    assert llm.answered_model == REQUESTED_MODEL
    assert llm.answered_provider == REQUESTED_PROVIDER


def test_without_a_fallback_the_cost_is_the_requested_models_price(library):
    """(c) The requested model answers: its tokens are priced at its own
    price and no other model's price is looked up."""
    library.completion.return_value = answer("requested answer", 200, 100)
    llm = make_llm()

    llm._get_completion_content(messages=MESSAGES)

    assert_cost(llm, price(REQUESTED_MODEL, 200, 100))
    assert set(library.priced) == {REQUESTED_MODEL}


def test_fallback_tokens_keep_the_fallback_price_after_a_later_answer(library):
    """A Vertex fallback answers one completion and the requested model
    answers the next on the same LLM: each model's tokens keep that model's
    price, and the requested model is recorded as answering the last one."""
    library.completion.side_effect = [
        *primary_down(),
        answer("vertex answer", 1000, 500),
        answer("requested answer", 200, 100),
    ]
    llm = make_llm()

    first = llm._get_completion_content(messages=MESSAGES)
    second = llm._get_completion_content(messages=MESSAGES)

    assert (first, second) == ("vertex answer", "requested answer")
    assert llm.token_usage["prompt_tokens"] == 1200
    assert llm.token_usage["completion_tokens"] == 600
    assert_cost(
        llm,
        summed(
            price(VERTEX.model_name, 1000, 500),
            price(REQUESTED_MODEL, 200, 100),
        ),
    )
    assert llm.answered_model == REQUESTED_MODEL
    assert llm.answered_provider == REQUESTED_PROVIDER


def test_async_a_vertex_fallback_answer_is_recorded_and_priced_as_that_model(
    library,
):
    """(d) The async form of (a)."""
    library.acompletion.side_effect = [
        *primary_down(),
        answer("vertex answer", 1000, 500),
    ]
    llm = make_llm()

    out = asyncio.run(llm._get_completion_content_async(messages=MESSAGES))

    assert out == "vertex answer"
    assert asked_models(library.acompletion) == [REQUESTED_MODEL] * 3 + [
        VERTEX.model_name
    ]
    assert_cost(llm, price(VERTEX.model_name, 1000, 500))
    assert llm.answered_model == VERTEX.model_name
    assert llm.answered_provider == VERTEX.provider


def test_async_an_openai_fallback_answer_is_recorded_and_priced_as_that_model(
    library,
):
    """(d) The async form of (b), Vertex call failing."""
    library.acompletion.side_effect = [
        *primary_down(),
        RuntimeError("vertex unavailable"),
        answer("openai answer", 800, 400),
    ]
    llm = make_llm()

    out = asyncio.run(llm._get_completion_content_async(messages=MESSAGES))

    assert out == "openai answer"
    assert asked_models(library.acompletion) == [REQUESTED_MODEL] * 3 + [
        VERTEX.model_name,
        OPENAI.model_name,
    ]
    assert_cost(llm, price(OPENAI.model_name, 800, 400))
    assert llm.answered_model == OPENAI.model_name
    assert llm.answered_provider == OPENAI.provider


def test_async_without_a_fallback_the_requested_model_answers_at_its_price(
    library,
):
    """(d) The async form of (c)."""
    library.acompletion.return_value = answer("requested answer", 200, 100)
    llm = make_llm()

    out = asyncio.run(llm._get_completion_content_async(messages=MESSAGES))

    assert out == "requested answer"
    assert asked_models(library.acompletion) == [REQUESTED_MODEL]
    assert_cost(llm, price(REQUESTED_MODEL, 200, 100))
    assert set(library.priced) == {REQUESTED_MODEL}
    assert llm.answered_model == REQUESTED_MODEL
    assert llm.answered_provider == REQUESTED_PROVIDER


def test_a_fallback_answer_is_logged_under_the_model_that_answered(library):
    """(e) The Vertex fallback answers: the usage log row for the completion
    names the Vertex model and the Vertex provider."""
    library.completion.side_effect = [
        *primary_down(),
        answer("vertex answer", 1000, 500),
    ]
    llm = make_llm()

    llm._get_completion_content(messages=MESSAGES)

    assert [row["LLMModelName"] for row in library.logged] == [VERTEX.model_name]
    assert [row["ServiceName"] for row in library.logged] == [VERTEX.provider]
    assert library.logged[0]["ResponseBody"] == "vertex answer"


def test_without_a_fallback_the_answer_is_logged_under_the_requested_model(
    library,
):
    """(e) The requested model answers: the usage log row names it and its
    provider."""
    library.completion.return_value = answer("requested answer", 200, 100)
    llm = make_llm()

    llm._get_completion_content(messages=MESSAGES)

    assert [row["LLMModelName"] for row in library.logged] == [REQUESTED_MODEL]
    assert [row["ServiceName"] for row in library.logged] == [REQUESTED_PROVIDER]


def test_a_default_priced_fallback_answer_costs_what_the_library_reported(library):
    """(f) The price list has no price for the OpenAI fallback model and the
    model library reported the answer's cost: that cost is the total. A
    later answer from the requested model on the same LLM adds only its own
    price."""
    reported = 0.0051
    library.default_priced.add(OPENAI.model_name)
    library.completion.side_effect = [
        *primary_down(),
        RuntimeError("vertex unavailable"),
        with_response_cost(answer("openai answer", 800, 400), reported),
        answer("requested answer", 200, 100),
    ]
    llm = make_llm()

    first = llm._get_completion_content(messages=MESSAGES)

    assert first == "openai answer"
    assert llm.cost["total_cost"] == pytest.approx(reported)
    assert llm.answered_model == OPENAI.model_name

    second = llm._get_completion_content(messages=MESSAGES)

    assert second == "requested answer"
    assert llm.cost["total_cost"] == pytest.approx(
        reported + price(REQUESTED_MODEL, 200, 100)["total_cost"]
    )
    assert llm.answered_model == REQUESTED_MODEL


def test_a_default_priced_fallback_answer_is_priced_at_the_default_rate(library):
    """(f) The price list has no price for the OpenAI fallback model and the
    model library reported a cost of 0: the answer's tokens are priced at
    the price list's default rate."""
    library.default_priced.add(OPENAI.model_name)
    library.completion.side_effect = [
        *primary_down(),
        RuntimeError("vertex unavailable"),
        with_response_cost(answer("openai answer", 800, 400), 0),
    ]
    llm = make_llm()

    out = llm._get_completion_content(messages=MESSAGES)

    assert out == "openai answer"
    assert_cost(llm, priced_at(DEFAULT_RATE, 800, 400))
    assert llm.answered_model == OPENAI.model_name
