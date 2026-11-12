"""Dataset column names must not become evaluator placeholders."""

import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from model_hub.tests import test_dataset_optimization as existing

organization = existing.organization
user = existing.user
workspace = existing.workspace
dataset = existing.dataset
output_column = existing.output_column


@pytest.fixture
def equals_runner():
    """Run the real Equals function without the billing/worker infrastructure."""
    from agentic_eval.core_evals.fi_evals.function.functions import equals

    def run_equals(*, mappings, **kwargs):
        result = equals(**mappings)
        return {"output": result["result"], "reason": result["reason"]}

    with patch(
        "model_hub.views.utils.evals.run_eval_func", side_effect=run_equals
    ) as runner:
        yield runner


@pytest.mark.django_db
@pytest.mark.parametrize(
    "reference_name",
    ["output", "response", "input", "query", "prompt", "agent_prompt", "reference"],
)
@pytest.mark.parametrize("answer,expected_score", [("WRONG", 0.0), ("CORRECT", 1.0)])
def test_equals_uses_saved_reference_column(
    organization,
    workspace,
    dataset,
    output_column,
    equals_runner,
    reference_name,
    answer,
    expected_score,
):
    from ee.agenthub.fix_your_agent.direct_evaluator import DirectEvaluator
    from ee.agenthub.fix_your_agent.fix_your_agent import FixYourAgent
    from model_hub.models.choices import DataTypeChoices, SourceChoices
    from model_hub.models.develop_dataset import Cell, Column, Row
    from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
    from tfc.temporal.dataset_optimization.activities import (
        _prepare_dataset_execution_data,
    )

    question_column = Column.objects.create(
        name="question",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    reference_column = Column.objects.create(
        name=reference_name,
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    for column, value in (
        (question_column, "What is the correct answer?"),
        (reference_column, "CORRECT"),
        (output_column, "OLD ANSWER"),
    ):
        Cell.objects.create(dataset=dataset, column=column, row=row, value=value)

    template = EvalTemplate.objects.create(
        name="equals-optimization-reference",
        organization=organization,
        workspace=workspace,
        model="gpt-4o-mini",
        config={
            "eval_type_id": "Equals",
            "output": "Pass/Fail",
            "required_keys": ["text", "expected_text"],
        },
        output_type_normalized="pass_fail",
    )
    metric = UserEvalMetric.no_workspace_objects.create(
        name="equals-candidate-reference",
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        template=template,
        config={
            "mapping": {
                "text": str(output_column.id),
                "expected_text": str(reference_column.id),
            },
        },
        model="gpt-4o-mini",
    )
    original_config = deepcopy(metric.config)
    prepared = _prepare_dataset_execution_data(
        output_column, dataset, [metric], initial_prompt="Answer {{question}}"
    )
    # Exercise the real config reconstruction, including a JSON round trip.
    agent = FixYourAgent()
    with patch.object(agent, "optimize") as optimize:
        agent.optimize_from_execution(
            execution_data=json.loads(json.dumps(prepared)),
            use_dual_llm_sim=False,
            use_direct_evaluation=True,
        )
    configs = optimize.call_args.kwargs["eval_configs"]
    assert len(configs) == 1
    config = configs[0]
    mapping_before = deepcopy(config.mapping)
    input_data = optimize.call_args.kwargs["scenarios"][0]["input"]
    assert input_data[reference_name] == "CORRECT"
    evaluator = DirectEvaluator(user_eval_configs=configs)
    score, _ = evaluator._run_eval_with_config(
        config, input_data, answer, "Answer What is the correct answer?"
    )

    # At the hosted cut, reference_name='output' compares WRONG with WRONG,
    # giving 1.0 here instead of 0.0. Renaming it to 'reference' is the control.
    assert score == expected_score
    equals_runner.assert_called_once()
    assert equals_runner.call_args.kwargs["mappings"] == {
        "text": answer,
        "expected_text": "CORRECT",
    }
    assert config.mapping == mapping_before
    assert metric.config == original_config
    metric.refresh_from_db()
    assert metric.config == original_config


@pytest.mark.parametrize(
    "placeholder,expected",
    [
        ("output", "GENERATED"),
        ("response", "GENERATED"),
        ("input", "QUESTION"),
        ("query", "QUESTION"),
        ("prompt", "FILLED PROMPT"),
        ("agent_prompt", "FILLED PROMPT"),
        ("reference", "SAVED REFERENCE"),
        ("literal text", "literal text"),
    ],
)
def test_legacy_string_mapping_keeps_its_meaning(equals_runner, placeholder, expected):
    from types import SimpleNamespace
    from uuid import uuid4

    from ee.agenthub.fix_your_agent.direct_evaluator import DirectEvaluator
    from model_hub.models.evals_metric import EvalTemplate

    template = SimpleNamespace(
        id=uuid4(),
        name="legacy-mapping",
        config={"output": "Pass/Fail"},
        model="gpt-4o-mini",
        output_type_normalized="pass_fail",
        choice_scores=None,
    )
    config = SimpleNamespace(
        eval_template=template,
        config={},
        mapping={"text": placeholder, "expected_text": "comparison"},
    )
    # Legacy placeholders must keep priority over same-named dataset fields.
    inputs = {
        name: "SAVED VALUE"
        for name in ("output", "response", "input", "prompt", "agent_prompt")
    }
    inputs.update(query="QUESTION", reference="SAVED REFERENCE")
    evaluator = DirectEvaluator(user_eval_configs=[config])
    with patch.object(EvalTemplate.objects, "get", return_value=template):
        evaluator._run_eval_with_config(config, inputs, "GENERATED", "FILLED PROMPT")

    equals_runner.assert_called_once()
    assert equals_runner.call_args.kwargs["mappings"]["text"] == expected
