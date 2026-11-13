"""Optimizer reads and usage writes stay within the owning dataset's tenant."""

import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest

from accounts.models.workspace import Workspace
from ee.agenthub.prompt_optimizer_agent.agent_task_v2 import PromptOptimizer
from ee.usage.models.usage import APICallLog, APICallType
from model_hub.models.choices import CellStatus, OwnerChoices, SourceChoices, StatusType
from model_hub.models.develop_dataset import Cell, Column, KnowledgeBaseFile, Row
from model_hub.models.evals_metric import (
    EvalTemplate,
    EvalTemplateVersion,
    UserEvalMetric,
)
from model_hub.models.run_prompt import RunPrompter
from model_hub.tests.test_develop_optimisation_contracts import (
    create_dataset_with_column,
    create_optimization,
    create_other_org_workspace_user,
    optimization_payload,
)
from model_hub.views.develop_optimiser import DevelopOptimizer
from tfc.constants.api_calls import APICallStatusChoices, APICallTypeChoices
from tfc.middleware.workspace_context import clear_workspace_context, workspace_context

pytestmark = [pytest.mark.django_db, pytest.mark.requires_ee]


@pytest.fixture(params=["organization", "workspace", "dataset"])
def neighboring_datasets(request, organization, workspace, user):
    """Dataset UUIDs are global; names and source/reference identifiers can collide.

    This path has no Project FK: Dataset is the project-like ownership boundary.
    Always seed a second organization, including for same-organization cases.
    """
    other_org, other_workspace, other_user = create_other_org_workspace_user()
    if request.param != "organization":
        other_org, other_user = organization, user
        other_workspace = workspace
        if request.param == "workspace":
            other_workspace = Workspace.no_workspace_objects.create(
                name=f"Other workspace {uuid4()}",
                organization=organization,
                created_by=user,
            )
    own_dataset, own_column = create_dataset_with_column(
        organization, workspace, user, name="Colliding dataset name"
    )
    other_dataset, other_column = create_dataset_with_column(
        other_org, other_workspace, other_user, name=own_dataset.name
    )
    assert own_column.name == other_column.name
    clear_workspace_context()
    return own_dataset, own_column, other_dataset, other_column


def make_metric(dataset, column):
    template = EvalTemplate.no_workspace_objects.create(
        name=f"optimizer-evaluation-{uuid4()}",
        organization=dataset.organization,
        workspace=dataset.workspace,
        owner=OwnerChoices.USER.value,
        criteria="Useful answer",
        config={
            "eval_type_id": "CustomCodeEval",
            "required_keys": ["input", "output"],
            "output": "score",
        },
    )
    return UserEvalMetric.no_workspace_objects.create(
        name="Optimizer metric",
        organization=dataset.organization,
        workspace=dataset.workspace,
        dataset=dataset,
        template=template,
        config={"mapping": {"input": str(column.id), "output": str(column.id)}},
    )


def run_feedback(optimization):
    APICallType.objects.get_or_create(
        name=APICallTypeChoices.OPTIMISATION_EVALUATION.value
    )
    result = SimpleNamespace(
        eval_results=[
            {"data": "answer", "reason": "Useful", "metrics": [{"value": 0.8}]}
        ]
    )
    with (
        workspace_context(None),
        patch("ee.agenthub.prompt_optimizer_agent.agent_task_v2.LLM"),
        patch(
            "model_hub.views.eval_runner.EvaluationRunner._create_eval_instance"
        ) as evaluator,
        patch(
            "ee.usage.utils.usage_entries.check_if_api_call_is_rate_limited",
            return_value=(False, {}),
        ),
    ):
        evaluator.return_value.run.return_value = result
        optimizer = PromptOptimizer(
            prompt="Answer", train_data=[], optimizer=optimization
        )
        feedback = optimizer._get_evaluation_feedback(
            [
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "answer"},
            ]
        )
    return feedback, evaluator


@pytest.mark.parametrize("media_type", ["image", "audio"])
def test_authenticated_feedback_does_not_disclose_foreign_column_type(
    auth_client, neighboring_datasets, media_type
):
    dataset, output_column, other_dataset, foreign_column = neighboring_datasets
    foreign_column.data_type = media_type
    foreign_column.save()
    # Preserve an authorized media input in the same log as the foreign binding.
    output_column.data_type = media_type
    output_column.save()
    template = make_metric(dataset, output_column).template
    response = auth_client.post(
        f"/model-hub/develops/{dataset.id}/add_user_eval/",
        {
            "name": "mapped-optimizer-metric",
            "template_id": str(template.id),
            "run": False,
            "config": {
                "mapping": {
                    "input": str(foreign_column.id),
                    "output": str(output_column.id),
                }
            },
        },
        format="json",
    )
    assert response.status_code == 200, response.content
    metric = UserEvalMetric.no_workspace_objects.get(
        dataset=dataset, name="mapped-optimizer-metric"
    )
    response = auth_client.post(
        "/model-hub/optimisation/create/",
        optimization_payload(dataset, output_column, metric, name="Scoped optimizer"),
        format="json",
    )
    assert response.status_code == 200, response.content
    optimization = dataset.optimizations_dataset.get(name="Scoped optimizer")
    (judgements, score), evaluator = run_feedback(optimization)
    log = APICallLog.no_workspace_objects.get(source_id=str(template.id))
    assert log.organization_id == dataset.organization_id
    assert log.workspace_id == dataset.workspace_id
    assert score == 80.0
    assert "Useful" in judgements
    evaluator.return_value.run.assert_called_once_with(
        input="question", output="answer"
    )
    response = auth_client.get("/model-hub/get-eval-logs", {"log_id": str(log.log_id)})
    assert response.status_code == 200, response.content
    types = response.json()["result"]["input_data_types"]
    assert types == json.loads(log.config)["input_data_types"]
    assert types == {"input": "text", "output": media_type}
    foreign_column.refresh_from_db()
    assert foreign_column.data_type == media_type
    assert (
        not APICallLog.no_workspace_objects.filter(
            organization=other_dataset.organization,
            workspace=other_dataset.workspace,
        )
        .exclude(log_id=log.log_id)
        .exists()
    )


@pytest.mark.parametrize("legacy", ["metric", "template", "both", "system"])
def test_default_workspace_and_system_templates_remain_usable(
    organization, workspace, user, legacy
):
    dataset, column = create_dataset_with_column(organization, workspace, user)
    metric = make_metric(dataset, column)
    if legacy in ("metric", "both"):
        metric.workspace = None
        metric.save()
    if legacy in ("template", "both", "system"):
        metric.template.workspace = None
        if legacy == "system":
            metric.template.organization = None
            metric.template.owner = OwnerChoices.SYSTEM.value
        metric.template.save()
    optimization = create_optimization(dataset, column, metric)
    (_, score), evaluator = run_feedback(optimization)
    assert score == 80.0
    evaluator.return_value.run.assert_called_once()


@pytest.mark.parametrize("own_version", [False, True])
def test_pinned_version_must_belong_to_the_authorized_template(
    neighboring_datasets, own_version
):
    dataset, column, other_dataset, other_column = neighboring_datasets
    metric = make_metric(dataset, column)
    template = (
        metric.template
        if own_version
        else make_metric(other_dataset, other_column).template
    )
    version, _ = EvalTemplateVersion.objects.get_or_create(
        eval_template=template,
        version_number=1,
        defaults={
            "organization": template.organization,
            "workspace": template.workspace,
        },
    )
    metric.pinned_version = version
    metric.save()
    optimization = create_optimization(dataset, column, metric)
    (_, score), evaluator = run_feedback(optimization)
    assert score == (80.0 if own_version else 0.0)
    assert evaluator.called is own_version


def test_optimizer_does_not_retrieve_org_wide_feedback(neighboring_datasets):
    from model_hub.views.eval_runner import EvaluationRunner

    dataset, column, _, _ = neighboring_datasets
    metric = make_metric(dataset, column)
    optimization = create_optimization(dataset, column, metric)
    runner = EvaluationRunner(metric.id, column=column, optimize=optimization)
    runner.load_user_eval_metric()
    with patch("model_hub.views.eval_runner.EmbeddingManager") as manager:
        manager.return_value.process_examples.return_value = ["Other dataset feedback"]
        assert runner.get_few_shot_examples(["question"], ["input"]) == []
        manager.assert_not_called()


@pytest.mark.parametrize("legacy", [False, True])
def test_optimizer_reuses_only_own_dataset_feedback(neighboring_datasets, user, legacy):
    from ee.agenthub.prompt_optimizer_agent.tests.test_optimizer_examples import (
        make_feedback,
    )
    from model_hub.utils.optimization_feedback import DatasetFeedbackVectorDB
    from model_hub.views.eval_runner import EvaluationRunner

    dataset, column, other_dataset, other_column = neighboring_datasets
    metric = make_metric(dataset, column)
    other_metric = make_metric(other_dataset, other_column)
    other_metric.template = metric.template
    other_metric.save()
    own = make_feedback(metric, column, user)
    foreign = make_feedback(other_metric, other_column, user, "other correction")
    if legacy:
        own.workspace = None
        own.save()
    optimization = create_optimization(dataset, column, metric)
    runner = EvaluationRunner(metric.id, column=column, optimize=optimization)
    runner.load_user_eval_metric()
    with patch("model_hub.views.eval_runner.EmbeddingManager") as manager:

        def retrieve(**kwargs):
            vector_db = manager.return_value.db_client
            assert isinstance(vector_db, DatasetFeedbackVectorDB)
            assert vector_db.feedback_ids == [str(own.id)]
            assert str(foreign.id) not in vector_db.feedback_ids
            return ["own correction"]

        manager.return_value.retrieve_avg_rag_based_examples.side_effect = retrieve
        manager.return_value.process_examples.side_effect = lambda examples, **kwargs: (
            examples
        )
        assert runner.get_few_shot_examples(["question"], ["input"]) == [
            "own correction"
        ]
        manager.return_value.close.assert_called_once()


def test_foreign_metric_link_cannot_run_or_write_usage(neighboring_datasets):
    dataset, column, other_dataset, other_column = neighboring_datasets
    foreign_metric = make_metric(other_dataset, other_column)
    # Exercise a historical/stale relation without relying on request middleware.
    optimization = create_optimization(dataset, column, foreign_metric)
    (_, score), evaluator = run_feedback(optimization)
    assert not APICallLog.no_workspace_objects.filter(
        source_id=str(foreign_metric.template_id)
    ).exists()
    evaluator.assert_not_called()
    assert score == 0.0


def test_metric_with_foreign_ownership_cannot_write_usage(neighboring_datasets):
    dataset, column, other_dataset, other_column = neighboring_datasets
    if dataset.organization_id == other_dataset.organization_id and (
        dataset.workspace_id == other_dataset.workspace_id
    ):
        # A foreign dataset on an otherwise locally owned metric is also invalid.
        metric = make_metric(dataset, column)
        metric.dataset = other_dataset
    else:
        metric = make_metric(other_dataset, other_column)
        metric.dataset = dataset
    metric.save()
    optimization = create_optimization(dataset, column, metric)
    (_, score), evaluator = run_feedback(optimization)
    assert not APICallLog.no_workspace_objects.filter(
        source_id=str(metric.template_id)
    ).exists()
    evaluator.assert_not_called()
    assert score == 0.0


def test_optimizer_details_ignore_colliding_foreign_source_ids(
    auth_client, neighboring_datasets
):
    dataset, column, other_dataset, foreign_column = neighboring_datasets
    metric = make_metric(dataset, column)
    optimization = create_optimization(dataset, column, metric)
    optimizer = DevelopOptimizer(optimization.id, avoid_cost=True)
    optimizer.create_column()
    foreign_column.source = SourceChoices.OPTIMISATION.value
    foreign_column.source_id = str(optimization.id)
    foreign_column.save()
    response = auth_client.get(f"/model-hub/optimisation/{optimization.id}/details/")
    assert response.status_code == 200, response.content
    assert {row["id"] for row in response.json()["optimized_columns"]} == {
        str(optimizer.old_column.id),
        str(optimizer.new_column.id),
    }
    assert Column.no_workspace_objects.get(id=foreign_column.id).dataset_id == (
        other_dataset.id
    )


def test_optimizer_details_do_not_resolve_foreign_column_names(
    auth_client, neighboring_datasets
):
    dataset, column, _, foreign_column = neighboring_datasets
    foreign_column.name = "Private column name"
    foreign_column.save()
    metric = make_metric(dataset, column)
    optimization = create_optimization(dataset, column, metric)
    optimization.optimized_k_prompts = [f"Use {{{{{foreign_column.id}}}}}"]
    optimization.save()
    response = auth_client.get(f"/model-hub/optimisation/{optimization.id}/details/")
    assert response.status_code == 200, response.content
    assert response.json()["optimized_k_prompts"] == optimization.optimized_k_prompts


@pytest.mark.parametrize("spoof_input", [False, True])
def test_usage_completion_does_not_update_colliding_foreign_log(
    neighboring_datasets,
    spoof_input,
):
    dataset, column, other_dataset, other_column = neighboring_datasets
    metric = make_metric(dataset, column)
    optimization = create_optimization(dataset, column, metric)
    optimization.status = StatusType.COMPLETED.value
    optimization.save()
    call_type, _ = APICallType.objects.get_or_create(
        name=APICallTypeChoices.DATASET_OPTIMIZATION.value
    )
    # Insert the foreign row first: the unscoped .first() selects its lower PK.
    foreign_log = APICallLog.no_workspace_objects.create(
        organization=other_dataset.organization,
        workspace=other_dataset.workspace,
        api_call_type=call_type,
        cost=0,
        reference_id=str(optimization.id),
        source="optimisation",
        config=json.dumps(
            {"input": str(column.id if spoof_input else other_column.id)}
        ),
        status=APICallStatusChoices.PROCESSING.value,
    )
    own_log = APICallLog.no_workspace_objects.create(
        organization=dataset.organization,
        workspace=dataset.workspace,
        api_call_type=call_type,
        cost=0,
        reference_id=str(optimization.id),
        source="optimisation",
        config=json.dumps({"input": str(column.id)}),
        status=APICallStatusChoices.PROCESSING.value,
    )
    with (
        patch(
            "model_hub.views.develop_optimiser.log_and_deduct_cost_for_api_request",
            return_value=own_log,
        ) as log_usage,
        patch(
            "model_hub.views.develop_optimiser.count_tiktoken_tokens", return_value=0
        ),
        patch("ee.usage.services.emitter.emit"),
    ):
        optimizer = DevelopOptimizer(optimization.id)
        optimizer._update_api_call_log_row()
    log_usage.assert_called_once()
    assert log_usage.call_args.args[0] == dataset.organization
    assert log_usage.call_args.kwargs["workspace"] == dataset.workspace
    foreign_log.refresh_from_db()
    own_log.refresh_from_db()
    assert foreign_log.status == APICallStatusChoices.PROCESSING.value
    assert own_log.status == APICallStatusChoices.SUCCESS.value


def test_optimizer_does_not_read_foreign_run_prompt(neighboring_datasets):
    dataset, column, other_dataset, _ = neighboring_datasets
    foreign_prompt = RunPrompter.no_workspace_objects.create(
        dataset=other_dataset,
        organization=other_dataset.organization,
        workspace=other_dataset.workspace,
        name="Private prompt",
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": [{"type": "text", "text": "Private"}]}],
    )
    column.source = SourceChoices.RUN_PROMPT.value
    column.source_id = str(foreign_prompt.id)
    column.save()
    metric = make_metric(dataset, column)
    optimization = create_optimization(dataset, column, metric)
    optimization.messages = []
    optimization.save()
    optimizer = DevelopOptimizer(optimization.id, avoid_cost=True)
    from django.http import Http404

    with pytest.raises(Http404):
        optimizer._get_messages()


@pytest.mark.parametrize("failure_path", ["stopped", "usage-limit"])
def test_failure_paths_do_not_write_foreign_cells(neighboring_datasets, failure_path):
    from model_hub.tasks.user_evaluation import _mark_cells_usage_limit_error
    from model_hub.utils.eval_cell_status import mark_eval_cells_stopped

    dataset, column, other_dataset, foreign_column = neighboring_datasets
    metric = make_metric(dataset, column)
    # Same source identifier, different owning datasets and rows.
    for candidate in (column, foreign_column):
        candidate.source = SourceChoices.EVALUATION.value
        candidate.source_id = str(metric.id)
        candidate.save()
    own_row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    other_row = Row.no_workspace_objects.create(dataset=other_dataset, order=0)
    own_cell = Cell.no_workspace_objects.create(
        dataset=dataset, row=own_row, column=column, status=CellStatus.RUNNING.value
    )
    foreign_cell = Cell.no_workspace_objects.create(
        dataset=other_dataset,
        row=other_row,
        column=foreign_column,
        value="Private value",
        status=CellStatus.RUNNING.value,
    )
    if failure_path == "stopped":
        mark_eval_cells_stopped(metric, reason="Stopped")
    else:
        with patch(
            "model_hub.services.experiment_utils.is_user_eval_stopped",
            return_value=False,
        ):
            _mark_cells_usage_limit_error(
                metric,
                SimpleNamespace(
                    error_code="USAGE_LIMIT_EXCEEDED",
                    reason="Stopped",
                    dimension="runs",
                    current_usage=1,
                    limit=1,
                    upgrade_cta=None,
                ),
            )
    own_cell.refresh_from_db()
    foreign_cell.refresh_from_db()
    assert own_cell.status == CellStatus.ERROR.value
    assert foreign_cell.status == CellStatus.RUNNING.value
    assert foreign_cell.value == "Private value"
    assert not Cell.no_workspace_objects.filter(
        column=foreign_column, row=own_row
    ).exists()


def test_mapped_knowledge_base_reference_requires_tenant_scope(neighboring_datasets):
    from model_hub.views.eval_runner import process_mapping

    dataset, _, other_dataset, _ = neighboring_datasets
    kb = KnowledgeBaseFile.no_workspace_objects.create(
        name="Referenced knowledge base",
        organization=other_dataset.organization,
        workspace=other_dataset.workspace,
    )
    row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    _, values = process_mapping({"input": str(kb.id)}, row)
    # Knowledge bases are workspace resources, not dataset-owned resources.
    same_tenant = (dataset.organization_id, dataset.workspace_id) == (
        other_dataset.organization_id,
        other_dataset.workspace_id,
    )
    assert values == ([str(kb.id)] if same_tenant else [])


def test_result_mapping_rejects_inconsistent_cell_dataset(neighboring_datasets):
    from model_hub.views.eval_runner import process_mapping

    dataset, column, other_dataset, _ = neighboring_datasets
    row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    Cell.no_workspace_objects.create(
        dataset=other_dataset, row=row, column=column, value="Private value"
    )
    _, values = process_mapping({"input": str(column.id)}, row)
    assert values == []


def test_result_evaluation_does_not_read_foreign_prompt(neighboring_datasets):
    from model_hub.views.eval_runner import process_mapping

    dataset, _, other_dataset, foreign_column = neighboring_datasets
    foreign_prompt = RunPrompter.no_workspace_objects.create(
        dataset=other_dataset,
        organization=other_dataset.organization,
        workspace=other_dataset.workspace,
        name="Private prompt",
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Private instructions"}],
    )
    foreign_column.source = SourceChoices.RUN_PROMPT.value
    foreign_column.source_id = str(foreign_prompt.id)
    foreign_column.save()
    row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    _, values = process_mapping(
        {"prompt": str(foreign_column.id)},
        row,
        eval_template_name="prompt_instruction_adherence",
    )
    assert values == []


def test_result_evaluation_does_not_disclose_foreign_input_type(neighboring_datasets):
    from model_hub.views.eval_runner import EvaluationRunner

    dataset, column, _, foreign_column = neighboring_datasets
    foreign_column.data_type = "image"
    foreign_column.save()
    metric = make_metric(dataset, column)
    metric.config["config"] = {}
    metric.config["mapping"]["input"] = str(foreign_column.id)
    metric.save()
    optimization = create_optimization(dataset, column, metric)
    row = Row.no_workspace_objects.create(dataset=dataset, order=0)
    call_type, _ = APICallType.objects.get_or_create(
        name=APICallTypeChoices.OPTIMISATION_EVALUATION.value
    )
    log = APICallLog.no_workspace_objects.create(
        organization=dataset.organization,
        workspace=dataset.workspace,
        api_call_type=call_type,
        cost=0,
        config="{}",
        status=APICallStatusChoices.PROCESSING.value,
    )
    runner = EvaluationRunner(
        metric.id, column=column, optimize=optimization, source="optimization"
    )
    runner.load_user_eval_metric()
    result = SimpleNamespace(
        eval_results=[
            {"data": "answer", "reason": "Useful", "metrics": [{"value": 0.8}]}
        ]
    )
    evaluator = SimpleNamespace(format_result=lambda *_: 0.8)
    with (
        patch.object(runner, "_handle_api_call", return_value=log),
        patch.object(
            runner,
            "_run_evaluation",
            return_value=(result, [], [], {}, evaluator, None),
        ),
        patch.object(runner, "_emit_cell_usage"),
    ):
        runner._process_eval_result(row, metric.config["mapping"])
    log.refresh_from_db()
    assert log.status == APICallStatusChoices.SUCCESS.value
    assert json.loads(log.config)["input_data_types"] == {
        "input": "text",
        "output": "text",
    }
