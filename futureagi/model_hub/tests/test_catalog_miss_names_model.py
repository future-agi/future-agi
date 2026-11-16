"""Catalog-miss errors name the model that is no longer available.

LiteLLMModelManager.get_provider is shared by prompt preview, prompt templates,
simulation and the evaluation runner. Outside the preview the failing model is
often not the one the user is looking at: a saved eval's judge model fails
while the dataset or experiment shows only prompt models. The error, and the
eval cell reason built from it, must say which model to replace.

Synthetic data only. No provider call is made: the failure happens while the
eval configuration is prepared, before any LLM request.
"""

import json
import re

import pytest

from agentic_eval.core_evals.run_prompt.litellm_models import (
    LiteLLMModelManager,
    UnavailableModelError,
)
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    ModelTypes,
    OwnerChoices,
    SourceChoices,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.views import eval_runner as eval_runner_module
from model_hub.views.eval_runner import EvaluationRunner

pytestmark = pytest.mark.django_db

# Removed by LiteLLMModelManager._remove_failed_models (a retired Perplexity
# model), so it is absent from the runtime catalog and has no custom-model row.
RETIRED_MODEL = "perplexity/pplx-7b-chat"
CATALOG_MISS = "not available in the current model catalog"

# The eval Test panels show a 400 message only if it passes the frontend's
# getSafeActionErrorMessage (frontend/src/utils/errorUtils.js); otherwise they
# fall back to "Failed to run evaluation. Please retry.". Mirror its rules.
UI_INTERNAL_ERROR_MARKERS = re.compile(
    r"DB::|ClickHouse|Stack\s*trace|Traceback|Code:\s*\d+|"
    r"maximum:\s*\d+|elapsed\s+\d+",
    re.IGNORECASE,
)
# Case-sensitive there too: prose "select" is shown, query text is not.
UI_SQL_QUERY_MARKER = re.compile(r"\bSELECT\s|\bselect\s+\*")


def assert_ui_shows(message):
    assert len(message) <= 240, message
    assert "\n" not in message and "\r" not in message, message
    assert not UI_INTERNAL_ERROR_MARKERS.search(message), message
    assert not UI_SQL_QUERY_MARKER.search(message), message


@pytest.mark.parametrize("model", [RETIRED_MODEL, "not-a-catalog-model"])
def test_catalog_miss_names_the_model(model):
    with pytest.raises(UnavailableModelError) as raised:
        LiteLLMModelManager(model).get_provider(model)

    assert raised.value.model_name == model
    assert str(raised.value).startswith(f"Model '{model}' is {CATALOG_MISS}. ")
    assert_ui_shows(str(raised.value))
    assert "Please choose a supported model" in str(raised.value)


@pytest.mark.parametrize("model", ["", "   ", None], ids=["blank", "spaces", "none"])
def test_catalog_miss_without_a_model_name_stays_generic(model):
    with pytest.raises(UnavailableModelError) as raised:
        LiteLLMModelManager(model).get_provider(model)

    assert raised.value.model_name == model
    assert str(raised.value).startswith(f"The selected model is {CATALOG_MISS}. ")
    assert_ui_shows(str(raised.value))


@pytest.fixture
def eval_with_retired_judge(organization, user, workspace):
    """A one-row dataset and a user LLM-as-judge eval whose saved judge model
    has since been retired from the catalog."""
    dataset = Dataset.objects.create(
        name="catalog-miss-dataset",
        organization=organization,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
        model_type=ModelTypes.GENERATIVE_LLM.value,
        column_order=[],
        column_config={},
        workspace=workspace,
    )
    input_column = Column.objects.create(
        dataset=dataset,
        name="response",
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(
        dataset=dataset,
        row=row,
        column=input_column,
        value="Synthetic answer: the capital of France is Paris.",
        status=CellStatus.PASS.value,
    )
    template = EvalTemplate.objects.create(
        name="catalog-miss-judge",
        description="Synthetic judge template",
        owner=OwnerChoices.USER.value,
        organization=organization,
        workspace=workspace,
        eval_type="llm",
        config={
            "eval_type_id": "CustomPromptEvaluator",
            "custom_eval": True,
            "required_keys": ["response"],
            "optional_keys": [],
            "output": "Pass/Fail",
            "rule_prompt": "Is this response polite? {{response}}",
        },
        choices=[],
        model="turing_large",
    )
    metric = UserEvalMetric.objects.create(
        name="catalog-miss-eval",
        dataset=dataset,
        template=template,
        user=user,
        workspace=workspace,
        organization=organization,
        # The judge model saved on the eval binding overrides the template's.
        model=RETIRED_MODEL,
        config={"mapping": {"response": str(input_column.id)}, "config": {}},
    )
    eval_column = Column.objects.create(
        dataset=dataset,
        name=metric.name,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION.value,
        source_id=str(metric.id),
    )
    return dataset, row, metric, eval_column


def test_eval_cell_reason_names_the_retired_judge_model(
    eval_with_retired_judge, monkeypatch
):
    """Runs the real per-row dataset evaluation (EvaluationRunner.
    run_evaluation_for_row). The experiment and batch runners persist the same
    reason through the same _process_eval_result error handler."""
    # The usage ledger is unrelated to the reason text; take the runner's own
    # branch for installs without usage metering.
    monkeypatch.setattr(eval_runner_module, "log_and_deduct_cost_for_api_request", None)
    monkeypatch.setattr(eval_runner_module, "refund_cost_for_api_call", None)
    # close_old_connections would drop the test transaction's connection.
    monkeypatch.setattr(eval_runner_module, "close_old_connections", lambda: None)
    # A new eval has no feedback, so few-shot retrieval returns []. Its
    # EmbeddingManager.close() also closes every Django connection, which would
    # end the test transaction, so return the same empty result directly.
    monkeypatch.setattr(
        EvaluationRunner, "get_few_shot_examples", lambda self, *a, **kw: []
    )

    dataset, row, metric, eval_column = eval_with_retired_judge
    EvaluationRunner(
        user_eval_metric_id=metric.id,
        is_only_eval=True,
        source="dataset_evaluation",
        source_id=metric.template.id,
        source_configs={"dataset_id": str(dataset.id), "source": "dataset"},
    ).run_evaluation_for_row(row.id)

    cell = Cell.objects.get(column=eval_column, row=row, deleted=False)
    reason = json.loads(cell.value_infos).get("reason", "")
    assert cell.status == CellStatus.ERROR.value
    assert CATALOG_MISS in reason, reason
    assert f"Model '{RETIRED_MODEL}'" in reason, reason
