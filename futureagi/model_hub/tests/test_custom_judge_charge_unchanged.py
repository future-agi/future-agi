"""An evaluation's charge for a custom judge model stays what it is today.

A custom model's saved price is shown on prompt cells, Run Prompt and the
playground. The evaluation charge is a different question, waiting on a
pricing answer: until then the usage event of an evaluated cell charges the
cost the judge reported plus the per-run fee, whatever price the custom judge
is saved at. The judge reports its tokens at the catalog price, else the
default 0.15 / 0.60 per 1M (a custom model under a LiteLLM provider), or no
cost at all (a custom HTTP endpoint). These cases pin that amount so the
shown price cannot move a charge by accident.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from agentic_eval.core.llm.llm import LLM
from model_hub.models.custom_models import CustomAIModel

pytestmark = pytest.mark.django_db

CUSTOM_MODEL = "support-judge-model"
FEE = 0.0005
USAGE = {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500}
DEFAULT_PRICED = round(1000 * 0.15 / 1e6, 6) + round(500 * 0.60 / 1e6, 6)
SAVED_PRICED = (1000 * 1000.0 + 500 * 2000.0) / 1e6
PROVIDERS = ["custom", "openai", "azure", "bedrock", "vertex_ai"]
PRICES = [(1000.0, 2000.0), (0.0, 0.0)]


def save_judge(organization, workspace, user, provider, input_cost, output_cost):
    key_config = (
        {"api_base": "https://models.example.invalid/v1", "headers": {}}
        if provider == "custom"
        else {"api_key": "sk-test-not-a-real-key"}
    )
    CustomAIModel.objects.create(
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


@pytest.mark.parametrize("input_cost,output_cost", PRICES)
@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_custom_judge_still_reports_its_tokens_at_the_default_price(
    organization, workspace, user, provider, input_cost, output_cost
):
    # The judge's own cost, which the charge reads: the judge holds no
    # organization, so its saved price is not used.
    save_judge(organization, workspace, user, provider, input_cost, output_cost)
    judge = LLM.__new__(LLM)
    judge.model_name = CUSTOM_MODEL
    judge.token_usage = dict(USAGE)
    judge.cost = {"total_cost": 0, "prompt_cost": 0, "completion_cost": 0}

    judge._update_cost()

    assert judge.cost["pricing_source"] == "default"
    assert judge.cost["total_cost"] == pytest.approx(DEFAULT_PRICED)


def test_the_judge_price_rule_is_ready_but_charges_nothing_yet(
    organization, workspace, user
):
    # The rule itself; no charge calls it (see the cases below), and using it
    # for a charge needs the owner's pricing decision first.
    from agentic_eval.core_evals.fi_utils.token_count_helper import (
        judge_model_cost,
    )

    save_judge(organization, workspace, user, "azure", 1000.0, 2000.0)

    custom = judge_model_cost(CUSTOM_MODEL, USAGE, DEFAULT_PRICED, organization.id)
    other = judge_model_cost("gpt-4o", USAGE, 0.0123, organization.id)

    assert custom == {
        "total_cost": pytest.approx(SAVED_PRICED),
        "pricing_source": "custom_model",
    }
    assert other == {"total_cost": 0.0123, "pricing_source": None}


@pytest.fixture
def billing_config():
    pytest.importorskip("ee.usage.services.emitter")
    config = Mock()
    config.get_eval_per_run_fee.return_value = FEE
    config.calculate_ai_credits.side_effect = lambda usd: usd * 100
    with patch("ee.usage.services.config.BillingConfig.get", return_value=config):
        yield config


def charged(organization, model, reported_cost):
    """The raw cost the evaluated cell's usage event carries."""
    from model_hub.views.eval_runner import EvaluationRunner

    run = EvaluationRunner.__new__(EvaluationRunner)
    run.user_eval_metric = SimpleNamespace(
        id="metric-1",
        model=model,
        template=SimpleNamespace(id="template-1"),
        organization=SimpleNamespace(id=organization.id),
    )
    run.organization_id = str(organization.id)
    run.eval_template = SimpleNamespace(eval_type="llm")
    run.source = "dataset"
    run.source_id = "dataset-1"
    run.replace_column_id = "column-1"
    instance = SimpleNamespace(
        cost={"total_cost": reported_cost}, token_usage=dict(USAGE)
    )
    with (
        patch("model_hub.views.eval_runner._emit_jev_usage", return_value=False),
        patch("ee.usage.services.emitter.emit") as emit,
    ):
        run._emit_cell_usage(
            instance, SimpleNamespace(log_id="log-1"), SimpleNamespace(id="row-1")
        )
    emit.assert_called_once()
    return float(emit.call_args.args[0].properties["raw_cost_usd"])


@pytest.mark.requires_ee
@pytest.mark.parametrize("input_cost,output_cost", PRICES)
@pytest.mark.parametrize("provider", PROVIDERS)
def test_a_custom_judge_is_charged_its_reported_cost_as_today(
    billing_config,
    organization,
    workspace,
    user,
    provider,
    input_cost,
    output_cost,
):
    save_judge(organization, workspace, user, provider, input_cost, output_cost)
    reported = 0.0 if provider == "custom" else DEFAULT_PRICED

    raw_cost = charged(organization, CUSTOM_MODEL, reported)

    assert raw_cost == pytest.approx(reported + FEE)
