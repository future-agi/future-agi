"""A custom model's saved price is what a prompt cell's cost uses.

An experiment prompt cell (and Run Prompt) on a model saved under Settings,
AI Providers with a provider that LiteLLM serves runs through the chat
handler. Its cost was priced by the name the provider reports, which is not
in the platform catalog, so it fell to the default price (0.15 / 0.60 per 1M
tokens, pricing_source "default") instead of the price the owner saved.

The cell's cost is priced at the saved price of the organization's own
custom model of that name, pricing_source "custom_model"; a saved price of
zero is a price (the cell costs nothing). A catalog model, a model name with
no custom model in the organization and another organization's custom model
are priced as before.
"""

from unittest.mock import Mock, patch

import pytest

from accounts.models import Organization
from agentic_eval.core_evals.fi_utils.token_count_helper import (
    DEFAULT_FALLBACK_PRICING,
    calculate_total_cost,
)
from agentic_eval.core_evals.run_prompt.model_pricing import get_model_pricing
from agentic_eval.core_evals.run_prompt.runprompt_handlers import (
    ModelHandlerContext,
)
from agentic_eval.core_evals.run_prompt.runprompt_handlers.handlers import (
    LLMHandler,
)
from model_hub.models.custom_models import CustomAIModel
from tfc.middleware.workspace_context import workspace_context

pytestmark = pytest.mark.django_db

CUSTOM_MODEL = "support-tuned-model"
PROMPT_TOKENS = 1000
COMPLETION_TOKENS = 500
# The walk's saved price: 1000 per 1M input tokens, 2000 per 1M output tokens.
SAVED_INPUT, SAVED_OUTPUT = 1000.0, 2000.0
SAVED_COST = (PROMPT_TOKENS * SAVED_INPUT + COMPLETION_TOKENS * SAVED_OUTPUT) / 1e6


def save_custom_model(organization, workspace, user, name, input_cost, output_cost):
    """A custom model as Settings, AI Providers saves it (an OpenAI-served one)."""
    return CustomAIModel.objects.create(
        user_model_id=name,
        provider="openai",
        input_token_cost=input_cost,
        output_token_cost=output_cost,
        organization=organization,
        workspace=workspace,
        key_config={"api_key": "sk-test-not-a-real-key"},
        user=user,
        deleted=False,
    )


def provider_response(reported_model):
    """A LiteLLM chat completion as the provider answers it."""
    response = Mock()
    response.choices = [Mock()]
    response.choices[0].message = Mock()
    response.choices[0].message.content = "An answer."
    response.choices[0].message.tool_calls = None
    response.usage = Mock()
    response.usage.prompt_tokens = PROMPT_TOKENS
    response.usage.completion_tokens = COMPLETION_TOKENS
    response.usage.total_tokens = PROMPT_TOKENS + COMPLETION_TOKENS
    response.model = reported_model
    return response


def run_cell(organization_id, model, reported_model):
    """One prompt cell through the chat handler, as Run Prompt runs it."""
    context = ModelHandlerContext(
        model=model,
        messages=[{"role": "user", "content": "Answer the customer."}],
        organization_id=str(organization_id),
        api_key="sk-test-not-a-real-key",
        provider="openai",
    )
    with patch("litellm.completion") as completion:
        completion.return_value = provider_response(reported_model)
        result = LLMHandler(context).execute_sync(streaming=False)
    return result.metadata["cost"]


def default_cost():
    rates = DEFAULT_FALLBACK_PRICING
    return round(
        (
            PROMPT_TOKENS * rates["input_per_1M_tokens"]
            + COMPLETION_TOKENS * rates["output_per_1M_tokens"]
        )
        / 1e6,
        6,
    )


def test_a_cell_on_a_custom_model_is_priced_at_its_saved_price(
    organization, workspace, user
):
    save_custom_model(
        organization, workspace, user, CUSTOM_MODEL, SAVED_INPUT, SAVED_OUTPUT
    )
    # The provider reports a name of its own, which is not in the catalog.
    cost = run_cell(organization.id, CUSTOM_MODEL, "support-tuned-deployment")

    assert cost["pricing_source"] == "custom_model"
    assert cost["prompt_cost"] == pytest.approx(PROMPT_TOKENS * SAVED_INPUT / 1e6)
    assert cost["completion_cost"] == pytest.approx(
        COMPLETION_TOKENS * SAVED_OUTPUT / 1e6
    )
    assert cost["total_cost"] == pytest.approx(SAVED_COST)


def test_a_catalog_model_is_priced_from_the_catalog_as_before(
    organization, workspace, user
):
    save_custom_model(
        organization, workspace, user, CUSTOM_MODEL, SAVED_INPUT, SAVED_OUTPUT
    )
    cost = run_cell(organization.id, "gpt-4o", "gpt-4o")

    rates = get_model_pricing("gpt-4o")
    assert cost["pricing_source"] == "available_models"
    assert cost["total_cost"] == pytest.approx(
        round(PROMPT_TOKENS * rates["input_per_1M_tokens"] / 1e6, 6)
        + round(COMPLETION_TOKENS * rates["output_per_1M_tokens"] / 1e6, 6)
    )


def test_a_custom_model_saved_at_zero_costs_nothing(organization, workspace, user):
    save_custom_model(organization, workspace, user, CUSTOM_MODEL, 0, 0)
    cost = run_cell(organization.id, CUSTOM_MODEL, "support-tuned-deployment")

    assert cost["pricing_source"] == "custom_model"
    assert cost["total_cost"] == 0


def test_a_model_with_no_custom_model_of_that_name_keeps_the_default_price(
    organization, workspace, user
):
    save_custom_model(
        organization, workspace, user, CUSTOM_MODEL, SAVED_INPUT, SAVED_OUTPUT
    )
    cost = run_cell(organization.id, "unsaved-model", "unsaved-deployment")

    assert cost["pricing_source"] == "default"
    assert cost["total_cost"] == pytest.approx(default_cost())


def test_another_organizations_custom_model_is_never_read(
    organization, workspace, user
):
    other = Organization.objects.create(name="Another organization")
    save_custom_model(other, None, None, CUSTOM_MODEL, SAVED_INPUT, SAVED_OUTPUT)
    # As in a background worker, with no workspace context: the price
    # rule's own organization filter is the only guard there.
    with workspace_context(None):
        cost = run_cell(organization.id, CUSTOM_MODEL, "support-tuned-deployment")

    assert cost["pricing_source"] == "default"
    assert cost["total_cost"] == pytest.approx(default_cost())


def test_another_organizations_custom_model_under_a_catalog_name_is_never_read(
    organization, workspace, user
):
    other = Organization.objects.create(name="Another organization")
    save_custom_model(other, None, None, "gpt-4o", SAVED_INPUT, SAVED_OUTPUT)
    # As in a background worker, with no workspace context: the price
    # rule's own organization filter is the only guard there.
    with workspace_context(None):
        cost = run_cell(organization.id, "gpt-4o", "gpt-4o")

    rates = get_model_pricing("gpt-4o")
    assert cost["pricing_source"] == "available_models"
    assert cost["total_cost"] == pytest.approx(
        round(PROMPT_TOKENS * rates["input_per_1M_tokens"] / 1e6, 6)
        + round(COMPLETION_TOKENS * rates["output_per_1M_tokens"] / 1e6, 6)
    )


def test_a_deleted_custom_model_keeps_the_default_price(organization, workspace, user):
    saved = save_custom_model(
        organization, workspace, user, CUSTOM_MODEL, SAVED_INPUT, SAVED_OUTPUT
    )
    CustomAIModel.objects.filter(pk=saved.pk).update(deleted=True)
    cost = run_cell(organization.id, CUSTOM_MODEL, "support-tuned-deployment")

    assert cost["pricing_source"] == "default"


def test_a_caller_that_names_no_organization_is_priced_as_before(
    organization, workspace, user
):
    # Evaluator cost, the hosted harness, gateway log ingestion and the credit
    # ledger's estimates call the cost function without an organization.
    save_custom_model(
        organization, workspace, user, CUSTOM_MODEL, SAVED_INPUT, SAVED_OUTPUT
    )
    usage = {"prompt_tokens": PROMPT_TOKENS, "completion_tokens": COMPLETION_TOKENS}

    cost = calculate_total_cost(CUSTOM_MODEL, usage)

    assert cost["pricing_source"] == "default"
    assert cost["total_cost"] == pytest.approx(default_cost())
