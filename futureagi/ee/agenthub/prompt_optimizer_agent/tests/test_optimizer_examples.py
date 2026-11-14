"""Examples follow the workspace and dataset of the evaluation run."""

from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from accounts.models.workspace import Workspace
from ee.agenthub.prompt_optimizer_agent.agent_task_v2 import PromptOptimizer
from ee.agenthub.prompt_optimizer_agent.tests.test_tenant_scope import make_metric
from ee.usage.models.usage import APICallType
from evaluations.engine.instance import create_eval_instance
from model_hub.models.choices import FeedbackSourceChoices, SourceChoices
from model_hub.models.develop_dataset import Cell, Column, Row
from model_hub.models.evals_metric import Feedback
from model_hub.tests.test_develop_optimisation_contracts import (
    create_dataset_with_column,
    create_optimization,
)
from model_hub.tests.test_few_shot_examples import _create_dataset_with_rows
from model_hub.utils.optimization_feedback import DatasetFeedbackVectorDB
from model_hub.views.eval_runner import EvaluationRunner
from tfc.constants.api_calls import APICallTypeChoices
from tfc.middleware.workspace_context import workspace_context

pytestmark = [pytest.mark.django_db, pytest.mark.requires_ee]


@pytest.mark.parametrize("path", ["prompt-feedback", "result", "engine"])
@pytest.mark.parametrize("scope", ["private", "default", "null", "system"])
def test_static_examples_use_run_workspace(organization, workspace, user, path, scope):
    private = Workspace.no_workspace_objects.create(
        name=f"Examples {uuid4()}", organization=organization, created_by=user
    )
    owning_workspace = private if scope == "private" else workspace
    if scope == "null":
        owning_workspace = None
    with workspace_context(None):
        dataset, column = create_dataset_with_column(
            organization, owning_workspace, user
        )
        metric = make_metric(dataset, column)
        own_examples = _create_dataset_with_rows(organization, name="own examples")
        own_examples.workspace = owning_workspace
        own_examples.save()
        other_examples = _create_dataset_with_rows(organization, name="other examples")
        other_examples.workspace = workspace if scope == "private" else private
        other_examples.save()
        legacy_examples = _create_dataset_with_rows(
            organization, name="legacy examples"
        )
        legacy_examples.workspace = workspace if scope == "null" else None
        legacy_examples.save()
        template = metric.template
        template.config.update(
            eval_type_id="CustomPromptEvaluator",
            model="turing_small",
            rule_prompt="Check {{output}}",
            few_shot_examples=[
                {"id": str(other_examples.id)},
                {"id": str(own_examples.id)},
                {"id": str(legacy_examples.id)},
                {"input": "literal", "output": "literal"},
            ],
        )
        if scope == "system":
            template.organization = None
            template.workspace = None
            template.owner = "system"
        template.save()
        optimization = create_optimization(dataset, column, metric)
        APICallType.objects.get_or_create(
            name=APICallTypeChoices.OPTIMISATION_EVALUATION.value
        )
        evaluator = Mock()
        evaluator.return_value.run.return_value = SimpleNamespace(
            eval_results=[
                {"data": "answer", "reason": "Useful", "metrics": [{"value": 0.8}]}
            ]
        )
        if path == "prompt-feedback":
            with (
                patch("ee.agenthub.prompt_optimizer_agent.agent_task_v2.LLM"),
                patch(
                    "evaluations.engine.registry.get_eval_class", return_value=evaluator
                ),
                patch(
                    "ee.usage.utils.usage_entries.check_if_api_call_is_rate_limited",
                    return_value=(False, {}),
                ),
            ):
                optimizer = PromptOptimizer(
                    prompt="Answer", train_data=[], optimizer=optimization
                )
                _, score = optimizer._get_evaluation_feedback(
                    [
                        {"role": "user", "content": "question"},
                        {"role": "assistant", "content": "answer"},
                    ]
                )
                assert score == 80.0
        elif path == "result":
            runner = EvaluationRunner(metric.id, column=column, optimize=optimization)
            runner.load_user_eval_metric()
            # Stale caller context must not replace the optimizer's dataset context.
            runner.workspace_id = other_examples.workspace_id
            runner._create_eval_instance({}, eval_class=evaluator, model="turing_small")
        else:
            create_eval_instance(
                evaluator,
                template,
                model="turing_small",
                organization_id=str(organization.id),
                workspace_id=str(owning_workspace.id) if owning_workspace else None,
            )
        expected = [{"input": "literal", "output": "literal"}]
        rows = [
            {"input": "input 1", "output": "output 1", "score": "Passed"},
            {"input": "input 2", "output": "output 2", "score": "Failed"},
        ]
        expected.extend(rows)
        if scope != "private":
            expected.extend(rows)
        evaluator.assert_called_once()
        assert evaluator.call_args.kwargs["few_shot_examples"] == expected


def make_feedback(metric, column, user, explanation="own correction"):
    row = Row.no_workspace_objects.create(dataset=metric.dataset, order=0)
    Cell.no_workspace_objects.create(
        dataset=metric.dataset, row=row, column=column, value="question"
    )
    return Feedback.no_workspace_objects.create(
        source=FeedbackSourceChoices.DATASET.value,
        source_id=str(column.id),
        row_id=str(row.id),
        user_eval_metric=metric,
        eval_template=metric.template,
        organization=metric.organization,
        workspace=metric.workspace,
        user=user,
        value="Passed",
        explanation=explanation,
    )


class InlineExecutor:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def submit(self, fn, *args):
        future = Future()
        try:
            future.set_result(fn(*args))
        except Exception as exc:
            future.set_exception(exc)
        return future


@pytest.mark.django_db(transaction=True)
def test_feedback_recalculation_uses_optimizer_dataset(
    auth_client, organization, workspace, user
):
    from model_hub.views.develop_dataset import run_evaluation_task

    dataset, column = create_dataset_with_column(organization, workspace, user)
    metric = make_metric(dataset, column)
    metric.template.config.update(
        eval_type_id="CustomPromptEvaluator",
        required_keys=["input"],
        model="turing_small",
        rule_prompt="Check {{input}}",
    )
    metric.template.save()
    metric.config = {"mapping": {"input": str(column.id)}, "config": {}}
    metric.save()
    prompt_column = Column.no_workspace_objects.create(
        dataset=dataset,
        name="Prompt output",
        data_type="text",
        source=SourceChoices.OTHERS.value,
    )
    optimization = create_optimization(dataset, prompt_column, metric)
    eval_column = Column.no_workspace_objects.create(
        dataset=dataset,
        name="Optimizer score",
        data_type="text",
        source=SourceChoices.OPTIMISATION_EVALUATION.value,
        source_id=f"{optimization.id}-sourceid-{metric.id}",
    )
    feedback = make_feedback(metric, column, user)
    feedback.source_id = str(eval_column.id)
    feedback.save()
    evaluator = Mock()
    evaluator.return_value.run.return_value = SimpleNamespace(
        eval_results=[
            {"data": "question", "reason": "Useful", "metrics": [{"value": 0.8}]}
        ]
    )
    evaluator.return_value.format_result.return_value = "Passed"

    def retrieve(**kwargs):
        vector_db = reader.return_value.db_client
        assert isinstance(vector_db, DatasetFeedbackVectorDB)
        assert vector_db.feedback_ids == [str(feedback.id)]
        assert kwargs["organization_id"] == dataset.organization_id
        assert kwargs["input_cols"] == [str(column.id)]
        return [
            [
                {
                    str(column.id): "question",
                    "feedback_comment": "updated correction",
                    "feedback_value": "Passed",
                    "input_type": "text",
                }
            ]
        ]

    with (
        patch("model_hub.views.develop_dataset.EmbeddingManager") as writer,
        patch(
            "model_hub.views.develop_dataset.run_evaluation_task.apply_async"
        ) as enqueue,
    ):
        response = auth_client.post(
            "/model-hub/feedback/submit-feedback/",
            {
                "feedback_id": str(feedback.id),
                "user_eval_metric_id": str(metric.id),
                "action_type": "recalculate_row",
                "explanation": "updated correction",
            },
            format="json",
        )
    assert response.status_code == 200, response.content
    assert writer.return_value.parallel_process_metadata.call_args.kwargs["metadatas"][
        "feedback_id"
    ] == str(feedback.id)
    payload = enqueue.call_args.kwargs["args"][0]
    with (
        workspace_context(None),
        patch("model_hub.views.develop_dataset.ThreadPoolExecutor", InlineExecutor),
        patch(
            "model_hub.views.develop_dataset.wrap_for_thread", side_effect=lambda fn: fn
        ),
        patch(
            "ee.usage.services.metering.check_usage",
            return_value=SimpleNamespace(allowed=True),
        ),
        patch("evaluations.engine.registry.get_eval_class", return_value=evaluator),
        patch(
            "model_hub.views.eval_runner.EvaluationRunner._handle_api_call",
            return_value=None,
        ),
        patch("model_hub.views.eval_runner.EvaluationRunner._emit_cell_usage"),
        patch("model_hub.views.eval_runner.EmbeddingManager") as reader,
    ):
        reader.return_value.retrieve_avg_rag_based_examples.side_effect = retrieve
        reader.return_value.process_examples.side_effect = lambda examples, **kwargs: (
            examples
        )
        run_evaluation_task(payload)
    evaluator.assert_called_once()
    evaluator.return_value.run.assert_called_once()
    assert "updated correction" in str(
        evaluator.return_value.run.call_args.kwargs["few_shots"]
    )
    assert (
        Cell.no_workspace_objects.get(column=eval_column, row_id=feedback.row_id).value
        == "Passed"
    )
