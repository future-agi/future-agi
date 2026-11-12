"""The optimizer model must not replace the source model."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from rest_framework.exceptions import ValidationError

from model_hub.models.ai_model import AIModel
from model_hub.models.choices import SourceChoices
from model_hub.models.dataset_optimization_trial import DatasetOptimizationTrial
from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.models.run_prompt import RunPrompter
from model_hub.serializers.dataset_optimization import DatasetOptimizationCreateSerializer
from model_hub.tests.test_dataset_optimization import (  # noqa: F401
    dataset,
    organization,
    output_column,
    user,
    workspace,
)


@pytest.mark.django_db
@pytest.mark.parametrize("has_model_record", [False, True])
def test_create_snapshots_source_execution_model_separately_from_optimizer(
    output_column, organization, workspace, has_model_record
):
    source = RunPrompter.objects.create(
        dataset=output_column.dataset,
        organization=organization,
        workspace=workspace,
        name="Source prompt",
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "Answer the question"}],
    )
    output_column.source = SourceChoices.RUN_PROMPT.value
    output_column.source_id = source.id
    output_column.save(update_fields=["source", "source_id"])
    if has_model_record:
        AIModel.objects.create(
            user_model_id="gpt-4o-mini",
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            organization=organization,
            workspace=workspace,
        )
    optimizer_model = AIModel.objects.create(
        user_model_id="gpt-4o",
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        organization=organization,
        workspace=workspace,
    )
    serializer = DatasetOptimizationCreateSerializer(
        data={
            "name": "Source model regression",
            "column_id": str(output_column.id),
            "optimizer_algorithm": OptimizeDataset.OptimizerAlgorithm.RANDOM_SEARCH,
            "optimizer_model_id": "gpt-4o",
            "optimizer_config": {"num_variations": 1},
        },
        context={
            "request": SimpleNamespace(organization=organization, workspace=workspace)
        },
    )
    assert serializer.is_valid(), serializer.errors
    run = serializer.save()

    # Later source edits must not change the model for a queued/resumed run.
    source.model = "gpt-4o"
    source.save(update_fields=["model"])
    run.refresh_from_db()
    assert run.optimizer_config["execution_model_name"] == "gpt-4o-mini"
    assert run.optimizer_model_id == optimizer_model.id
    assert run.optimizer_config["model_name"] == "gpt-4o"
    if has_model_record:
        assert run.model.user_model_id == "gpt-4o-mini"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "source_state", ["missing", "deleted", "other_dataset", "empty_model"]
)
def test_create_rejects_source_without_an_execution_model(
    output_column, organization, workspace, source_state
):
    from model_hub.models.develop_dataset import Dataset

    source_dataset = output_column.dataset
    if source_state == "other_dataset":
        source_dataset = Dataset.objects.create(
            name="Other dataset", organization=organization, workspace=workspace
        )
    source = RunPrompter.objects.create(
        dataset=source_dataset,
        organization=organization,
        workspace=workspace,
        name="Unavailable source",
        model="" if source_state == "empty_model" else "gpt-4o-mini",
        deleted=source_state == "deleted",
    )
    output_column.source = SourceChoices.RUN_PROMPT.value
    output_column.source_id = None if source_state == "missing" else source.id
    output_column.save(update_fields=["source", "source_id"])
    serializer = DatasetOptimizationCreateSerializer(
        data={
            "name": "Invalid source regression",
            "column_id": str(output_column.id),
            "optimizer_algorithm": OptimizeDataset.OptimizerAlgorithm.RANDOM_SEARCH,
            "optimizer_model_id": "gpt-4o",
            "optimizer_config": {"num_variations": 1},
        },
        context={
            "request": SimpleNamespace(organization=organization, workspace=workspace)
        },
    )
    assert serializer.is_valid(), serializer.errors
    with pytest.raises(ValidationError) as exc:
        serializer.save()
    assert "column_id" in exc.value.detail
    assert not OptimizeDataset.objects.filter(name="Invalid source regression").exists()


def test_execution_model_is_labelled_separately_in_run_parameters():
    from model_hub.utils.dataset_optimization import build_parameters_array

    parameters = build_parameters_array(
        {"model_name": "gpt-4o", "execution_model_name": "gpt-4o-mini"}
    )
    assert parameters == [
        {
            "key": "execution_model_name",
            "label": "Execution Model",
            "description": "Model used to execute the baseline and candidate prompts",
            "value": "gpt-4o-mini",
        }
    ]


@pytest.mark.parametrize(
    "snapshot, explicit_model, expected",
    [
        ("gpt-4o-mini", None, "gpt-4o-mini"),
        ("gpt-4o-mini", "renamed-source-model", "gpt-4o-mini"),
        (None, "gpt-4o-mini", "gpt-4o-mini"),
        (None, None, "gpt-4o"),
    ],
)
def test_activity_keeps_execution_model_separate_from_optimizer(
    monkeypatch, snapshot, explicit_model, expected
):
    from ee.agenthub.fix_your_agent.fix_your_agent import FixYourAgent
    from tfc.temporal.dataset_optimization import activities

    config = {"num_variations": 1, "model_name": "gpt-4o"}
    if snapshot:
        config["execution_model_name"] = snapshot
    dataset = SimpleNamespace(
        organization=SimpleNamespace(id="org"),
        workspace=SimpleNamespace(id="workspace"),
    )
    column = SimpleNamespace(
        id="column",
        name="Prompt Output",
        source="run_prompt",
        source_id="source",
        dataset=dataset,
    )
    run = SimpleNamespace(
        id="run",
        organization_id="org",
        column=column,
        model=SimpleNamespace(user_model_id=explicit_model) if explicit_model else None,
        optimizer_model=SimpleNamespace(user_model_id="gpt-4o"),
        optimizer_algorithm="random_search",
        optimizer_config=dict(config),
        user_eval_template_ids=Mock(),
    )
    run.user_eval_template_ids.exists.return_value = False
    run_manager = Mock()
    run_manager.only.return_value.get.return_value = run
    run_manager.select_related.return_value.get.return_value = run
    trial_manager = Mock()
    trial_manager.filter.return_value.order_by.return_value.first.return_value = None
    source_manager = Mock()
    # The source may have changed since creation; the snapshot must win.
    source_manager.get.return_value = SimpleNamespace(
        id="source",
        name="Source prompt",
        model="changed-source-model",
        messages=[{"role": "user", "content": "Answer the question"}],
    )
    optimize = Mock(
        return_value=SimpleNamespace(history=[], final_score=0.5, best_prompt="Answer")
    )
    monkeypatch.setattr(OptimizeDataset, "objects", run_manager)
    monkeypatch.setattr(DatasetOptimizationTrial, "objects", trial_manager)
    monkeypatch.setattr(RunPrompter, "objects", source_manager)
    monkeypatch.setattr(activities, "Heartbeater", Mock(return_value=AsyncMock()))
    monkeypatch.setattr(activities, "_safe_close_db", Mock())
    monkeypatch.setattr(activities, "close_old_connections", Mock())
    monkeypatch.setattr(
        activities, "get_dataset_optimization_steps", Mock(return_value=[])
    )
    monkeypatch.setattr(activities, "update_dataset_optimization_step", Mock())
    monkeypatch.setattr(
        activities, "_prepare_dataset_execution_data", Mock(return_value={})
    )
    monkeypatch.setattr(
        activities, "_calc_best_from_trials", Mock(return_value=(None, None, None))
    )
    monkeypatch.setattr(
        activities, "get_api_key_for_model", Mock(return_value=object())
    )
    monkeypatch.setattr("tfc.ee_gating.check_ee_feature", Mock())
    monkeypatch.setattr(FixYourAgent, "optimize_from_execution", optimize)

    asyncio.run(activities.run_optimization_activity({"run_id": "run"}))

    optimize.assert_called_once()
    call = optimize.call_args.kwargs
    assert call["execution_model"] == expected
    assert call["optimization_model"] == "gpt-4o"
    assert call["use_direct_evaluation"] is True
    assert call["skip_baseline"] is False
    assert "execution_model_name" not in call["optimizer_config"]
    assert run.optimizer_config == config
