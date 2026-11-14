"""Optimization usage logs retain the types of UUID-backed dataset columns."""

import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from ee.agenthub.prompt_optimizer_agent.agent_task_v2 import PromptOptimizer
from ee.usage.models.usage import APICallLog, APICallType
from model_hub.models.choices import SourceChoices
from model_hub.models.develop_dataset import Column, Dataset
from model_hub.models.develop_optimisation import OptimizationDataset
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices

pytestmark = [pytest.mark.django_db, pytest.mark.requires_ee]


@pytest.mark.parametrize(
    ("data_type", "expected_type"),
    [
        ("image", "image"),
        ("audio", "audio"),
        ("text", "text"),
        ("integer", "text"),
        (None, "text"),
    ],
    ids=["image", "audio", "text", "other-type", "missing-column"],
)
def test_feedback_log_preserves_column_input_types(
    organization, workspace, user, data_type, expected_type
):
    dataset = Dataset.no_workspace_objects.create(
        name="Optimizer inputs",
        organization=organization,
        workspace=workspace,
        user=user,
    )
    input_id = uuid4()
    if data_type is not None:
        Column.no_workspace_objects.create(
            id=input_id,
            name="Input",
            dataset=dataset,
            data_type=data_type,
            source=SourceChoices.OTHERS.value,
        )
    output_column = Column.no_workspace_objects.create(
        name="Output",
        dataset=dataset,
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    # The real ORM returns UUIDs, while the metric's JSON mapping stores strings.
    assert isinstance(Column.objects.values("id").get(id=output_column.id)["id"], UUID)
    template = EvalTemplate.no_workspace_objects.create(
        name="Optimizer evaluation",
        organization=organization,
        workspace=workspace,
        criteria="Useful answer",
        config={
            "eval_type_id": "CustomCodeEval",
            "required_keys": ["input", "output"],
            "output": "score",
        },
    )
    metric = UserEvalMetric.no_workspace_objects.create(
        name="Optimizer metric",
        organization=organization,
        workspace=workspace,
        dataset=dataset,
        template=template,
        config={"mapping": {"input": str(input_id), "output": str(output_column.id)}},
    )
    optimization = OptimizationDataset.no_workspace_objects.create(
        name="Optimizer", optimize_type="PROMPT_TEMPLATE", dataset=dataset
    )
    optimization.user_eval_template_ids.set([metric])
    APICallType.objects.get_or_create(
        name=APICallTypeChoices.OPTIMISATION_EVALUATION.value
    )
    eval_result = SimpleNamespace(
        eval_results=[
            {"data": "answer", "reason": "Useful", "metrics": [{"value": 0.8}]}
        ]
    )

    with (
        patch("ee.agenthub.prompt_optimizer_agent.agent_task_v2.LLM"),
        patch(
            "model_hub.views.eval_runner.EvaluationRunner._create_eval_instance"
        ) as create_eval,
        patch(
            "ee.usage.utils.usage_entries.check_if_api_call_is_rate_limited",
            return_value=(False, {}),
        ),
    ):
        create_eval.return_value.run.return_value = eval_result
        optimizer = PromptOptimizer(
            prompt="Answer", train_data=[], optimizer=optimization
        )
        judgements, score = optimizer._get_evaluation_feedback(
            [
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "answer"},
            ]
        )

    log = APICallLog.objects.get(organization=organization, source_id=str(template.id))
    config = json.loads(log.config)
    assert config["input_data_types"] == {"input": expected_type, "output": "text"}
    assert config["mappings"] == {"input": "question", "output": "answer"}
    assert config["output"] == {"output": 0.8, "reason": "Useful"}
    assert log.status == APICallStatusChoices.SUCCESS.value
    assert log.workspace_id == workspace.id
    assert score == 80.0
    assert "Useful" in judgements
    create_eval.return_value.run.assert_called_once_with(
        input="question", output="answer"
    )
