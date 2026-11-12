"""Every place that shows a custom model's cost shows its saved price, and no
credits amount ever takes it.

- A Run Prompt column's header (the grid's average cost) and the dataset's
  prompt summary agree with its cells.
- An experiment run records a known cost for a cell priced at a custom
  model's saved price, so the run shows it (display only: nothing charges
  or settles from a run item's cost).
- A Future AGI credits settle never reads a cost priced at a custom model's
  saved price, even on a managed call whose model name an organization's
  custom model has taken; a credits call priced from the catalog settles as
  before.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest

from agentic_eval.core_evals.fi_utils.token_count_helper import calculate_total_cost
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.custom_models import CustomAIModel
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.run_prompt import RunPrompter
from model_hub.services import credit_ledger_port as credit_ledger
from model_hub.views.experiment_runner import _native_prompt_usage
from tfc.utils.functions import get_prompt_stats

CUSTOM_MODEL = "support-tuned-model"
PROMPT_TOKENS = 1000
COMPLETION_TOKENS = 500
USAGE = {"prompt_tokens": PROMPT_TOKENS, "completion_tokens": COMPLETION_TOKENS}
SAVED_COST = (PROMPT_TOKENS * 1000.0 + COMPLETION_TOKENS * 2000.0) / 1e6


def value_info(total_cost, pricing_source):
    return {
        "metadata": {
            "usage": {**USAGE, "total_tokens": PROMPT_TOKENS + COMPLETION_TOKENS},
            "cost": {"total_cost": total_cost, "pricing_source": pricing_source},
        }
    }


@pytest.fixture
def custom_column(db, organization, workspace, user):
    """A Run Prompt column on a custom model saved at 1000 / 2000 per 1M."""
    CustomAIModel.objects.create(
        user_model_id=CUSTOM_MODEL,
        provider="azure",
        input_token_cost=1000.0,
        output_token_cost=2000.0,
        organization=organization,
        workspace=workspace,
        key_config={"api_key": "sk-test-not-a-real-key"},
        user=user,
        deleted=False,
    )
    dataset = Dataset.objects.create(
        name="Support answers",
        organization=organization,
        workspace=workspace,
        source=DatasetSourceChoices.BUILD.value,
    )
    prompter = RunPrompter.objects.create(
        name="Answer",
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        status=StatusType.COMPLETED.value,
        model=CUSTOM_MODEL,
        messages=[{"role": "user", "content": "Answer the customer."}],
        run_prompt_config={},
    )
    column = Column.objects.create(
        name="Answer",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.RUN_PROMPT.value,
        source_id=str(prompter.id),
    )
    dataset.column_order = [str(column.id)]
    dataset.save()
    for order in range(2):
        row = Row.objects.create(dataset=dataset, order=order)
        Cell.objects.create(
            dataset=dataset,
            row=row,
            column=column,
            value="An answer.",
            status=CellStatus.PASS.value,
            prompt_tokens=PROMPT_TOKENS,
            completion_tokens=COMPLETION_TOKENS,
            response_time=100,
        )
    return dataset, prompter, column


def cell_cost(organization):
    """What each cell shows: its tokens at the saved price."""
    cost = calculate_total_cost(
        CUSTOM_MODEL, dict(USAGE), organization_id=organization.id
    )
    assert cost["pricing_source"] == "custom_model"
    return cost["total_cost"]


@pytest.mark.django_db
def test_a_run_prompt_columns_average_cost_agrees_with_its_cells(
    auth_client, organization, custom_column
):
    dataset, _, column = custom_column
    response = auth_client.get(
        f"/model-hub/develops/{dataset.id}/get-dataset-table/",
        {"column_config_only": "true", "page_size": 10, "current_page_index": 0},
    )

    assert response.status_code == 200, response.content
    header = next(
        c
        for c in response.json()["result"]["column_config"]
        if c["id"] == str(column.id)
    )
    assert header["metadata"]["average_cost"] == pytest.approx(cell_cost(organization))
    assert header["metadata"]["average_cost"] == pytest.approx(SAVED_COST)


# The summary reads each prompt's cells in worker threads, which see only
# committed rows.
@pytest.mark.django_db(transaction=True)
def test_the_datasets_prompt_summary_agrees_with_its_cells(organization, custom_column):
    dataset, prompter, _ = custom_column

    summary = get_prompt_stats([prompter], dataset.id)

    assert summary["avg_cost"] == pytest.approx(round(cell_cost(organization), 4))
    assert summary["avg_cost"] == pytest.approx(SAVED_COST)


def test_an_experiment_run_records_a_saved_custom_price():
    usage, cost = _native_prompt_usage(value_info(SAVED_COST, "custom_model"))

    assert cost == pytest.approx(SAVED_COST)
    assert usage["prompt_tokens"] == PROMPT_TOKENS


@pytest.mark.parametrize(
    "pricing_source,recorded",
    [("available_models", 0.0123), ("default", None), ("fallback", None)],
)
def test_an_experiment_run_records_other_prices_as_before(pricing_source, recorded):
    _, cost = _native_prompt_usage(value_info(0.0123, pricing_source))

    assert cost == recorded


def test_a_credits_settle_never_reads_a_saved_custom_price():
    assert (
        credit_ledger.cost_from_value_info(value_info(SAVED_COST, "custom_model"))
        is None
    )


def test_a_credits_call_priced_from_the_catalog_settles_as_before():
    catalog = value_info(0.0123, "available_models")
    assert credit_ledger.cost_from_value_info(catalog) == Decimal("0.0123")

    # The settle observes the call's cost through the same reader.
    call = SimpleNamespace(
        output_seen=False,
        _observed=None,
        envelope=SimpleNamespace(model="gpt-4o"),
    )
    observed = credit_ledger.ManagedCall.observe(call, value_info=catalog)

    assert observed == Decimal("0.0123")


class _Call:
    """The settle state of one managed call, observed as the ledger does."""

    observe = credit_ledger.ManagedCall.observe

    def __init__(self):
        self.output_seen = False
        self._observed = None
        self.envelope = SimpleNamespace(model=CUSTOM_MODEL)


def test_a_managed_call_never_settles_a_saved_custom_price():
    # A managed call whose model name an organization's custom model took
    # while it ran: its Run Prompt result is priced at the saved price.
    call = _Call()

    credit_ledger.observe_value_info(
        call, (None, value_info(SAVED_COST, "custom_model"))
    )

    assert call._observed is None
