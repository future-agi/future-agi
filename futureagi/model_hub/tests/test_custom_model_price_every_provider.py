"""One rule prices a custom model, whatever provider it is saved under.

A custom model can be saved (Settings, AI Providers) as a custom HTTP
endpoint, an OpenAI-compatible base, Azure, Bedrock or Vertex AI. A prompt
cell on it (experiment, Run Prompt, playground) records its cost at the saved
price per 1M tokens, pricing_source "custom_model", including a saved price
of zero. A custom HTTP endpoint is served by its own handler; the others by
the chat handler.
"""

from unittest.mock import Mock, patch

import pytest

from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
    ModelHandlerContext,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
    LLMHandler,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers.custom_model_handler import (
    CustomModelHandler,
)
from model_hub.models.custom_models import CustomAIModel

pytestmark = pytest.mark.django_db

CUSTOM_MODEL = "support-tuned-model"
PROMPT_TOKENS = 1000
COMPLETION_TOKENS = 500
PROVIDERS = ["custom", "openai", "azure", "bedrock", "vertex_ai"]
# Above the default price (0.15 / 0.60 per 1M), and free.
PRICES = [(1000.0, 2000.0), (0.0, 0.0)]


def saved_cost(input_cost, output_cost):
    return (PROMPT_TOKENS * input_cost + COMPLETION_TOKENS * output_cost) / 1e6


def save_custom_model(organization, workspace, user, provider, input_cost, output_cost):
    key_config = (
        {"api_base": "https://models.example.invalid/v1", "headers": {}}
        if provider == "custom"
        else {"api_key": "sk-test-not-a-real-key"}
    )
    return CustomAIModel.objects.create(
        user_model_id=CUSTOM_MODEL,
        provider=provider,
        input_token_cost=input_cost,
        output_token_cost=output_cost,
        organization=organization,
        workspace=workspace,
        key_config=key_config,
        user=user,
        deleted=False,
    )


def context_for(organization_id, model):
    return ModelHandlerContext(
        model=model,
        messages=[{"role": "user", "content": "Answer the customer."}],
        organization_id=str(organization_id),
        api_key="sk-test-not-a-real-key",
        provider="openai",
    )


def endpoint_cell_cost(organization_id, input_cost, output_cost):
    """The custom HTTP endpoint handler's cost for one answer with usage."""
    handler = CustomModelHandler.__new__(CustomModelHandler)
    handler.context = context_for(organization_id, CUSTOM_MODEL)
    handler._custom_model_config = {
        "input_token_cost": input_cost,
        "output_token_cost": output_cost,
        "provider": "custom",
    }
    _, _, cost = handler._calculate_token_costs(
        prompt_text="Answer the customer.",
        completion_text="An answer.",
        image_urls=[],
        usage={
            "prompt_tokens": PROMPT_TOKENS,
            "completion_tokens": COMPLETION_TOKENS,
        },
    )
    return cost


def chat_cell_cost(
    organization_id, model=CUSTOM_MODEL, reported="support-tuned-deployment"
):
    """The chat handler's cost for one answer; the provider names its own model."""
    response = Mock()
    response.choices = [Mock()]
    response.choices[0].message = Mock()
    response.choices[0].message.content = "An answer."
    response.choices[0].message.tool_calls = None
    response.usage = Mock()
    response.usage.prompt_tokens = PROMPT_TOKENS
    response.usage.completion_tokens = COMPLETION_TOKENS
    response.usage.total_tokens = PROMPT_TOKENS + COMPLETION_TOKENS
    response.model = reported
    with patch("litellm.completion", return_value=response):
        result = LLMHandler(context_for(organization_id, model)).execute_sync(
            streaming=False
        )
    return result.metadata["cost"]


@pytest.mark.parametrize("input_cost,output_cost", PRICES)
@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_cell_on_a_custom_model_records_its_saved_price(
    organization, workspace, user, provider, input_cost, output_cost
):
    save_custom_model(organization, workspace, user, provider, input_cost, output_cost)

    if provider == "custom":
        cost = endpoint_cell_cost(organization.id, input_cost, output_cost)
    else:
        cost = chat_cell_cost(organization.id)

    assert cost["pricing_source"] == "custom_model"
    assert cost["total_cost"] == pytest.approx(saved_cost(input_cost, output_cost))


def test_the_saved_price_wins_over_a_catalog_price_of_the_same_name(
    organization, workspace, user
):
    # A custom model saved under a catalog model's name is the
    # organization's own model: its saved price is used.
    CustomAIModel.objects.create(
        user_model_id="gpt-4o",
        provider="azure",
        input_token_cost=1000.0,
        output_token_cost=2000.0,
        organization=organization,
        workspace=workspace,
        key_config={"api_key": "sk-test-not-a-real-key"},
        user=user,
        deleted=False,
    )
    cost = chat_cell_cost(organization.id, model="gpt-4o", reported="gpt-4o")
    assert cost["pricing_source"] == "custom_model"
    assert cost["total_cost"] == pytest.approx(saved_cost(1000.0, 2000.0))
