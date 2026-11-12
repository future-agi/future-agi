"""The three retired legacy optimizer entries refuse before any work.

The optimize-dataset create route (Prompt Template and RAG Prompt Template),
the knowledge-base create route and the two background tasks behind them all
end in a PromptOptimizer construction that cannot run. Each entry refuses with
one sentence before a row is written and before any model call; the tasks set
the row to failed first. The drawer's other option, 'AI Output'
(RightAnswer), is not refused and still creates its row.

Every model entry point on these paths is replaced with a recorder, so a
regression shows up as a recorded call and never reaches a provider.
"""

import uuid
from types import SimpleNamespace

import pytest
from rest_framework import status

from model_hub.models.ai_model import AIModel
from model_hub.models.metric import Metric
from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.services import credit_ledger_port as credit_ledger
from model_hub.tasks import prompt_template_optimizer as template_task
from model_hub.views import optimize_dataset as optimize_views

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]

TEMPLATE_SENTENCE = (
    "This legacy optimizer (optimize-dataset PromptTemplate) is no longer "
    "available. Use Dataset Optimization instead."
)
RAG_SENTENCE = (
    "This legacy optimizer (optimize-dataset RagPromptTemplate) is no longer "
    "available. Use Dataset Optimization instead."
)
KNOWLEDGE_BASE_SENTENCE = (
    "This legacy optimizer (knowledge-base optimizer) is no longer "
    "available. Use Dataset Optimization instead."
)
TASK_SENTENCE = (
    "This legacy optimizer (prompt-template optimizer) is no longer "
    "available. Use Dataset Optimization instead."
)


@pytest.fixture
def calls(monkeypatch):
    """Record every model entry point and dispatch on the legacy paths."""
    seen = {
        "check_valid_metrics": [],
        "view_criteria": [],
        "view_optimizer": [],
        "topk_dispatch": [],
        "rag_dispatch": [],
        "task_criteria": [],
        "task_optimizer": [],
        "sleep": [],
    }

    def valid_metrics(*args, **kwargs):
        seen["check_valid_metrics"].append(args)
        return True, True

    def recorder(name, result=None):
        def record(*args, **kwargs):
            seen[name].append((args, kwargs))
            return result

        return record

    class RecordingOptimizer:
        def __init__(self, name):
            self.name = name

        def __call__(self, *args, **kwargs):
            seen[self.name].append((args, kwargs))
            return SimpleNamespace(get_optimized_prompt=lambda: [])

    monkeypatch.setattr(optimize_views, "check_valid_metrics", valid_metrics)
    monkeypatch.setattr(
        optimize_views,
        "create_criteria_text_prompt",
        recorder("view_criteria", ["Criterion"]),
    )
    monkeypatch.setattr(
        optimize_views, "PromptOptimizer", RecordingOptimizer("view_optimizer")
    )
    monkeypatch.setattr(
        optimize_views.get_topk_prompts, "apply_async", recorder("topk_dispatch")
    )
    monkeypatch.setattr(
        optimize_views.rag_prompt_optimzer,
        "apply_async",
        recorder("rag_dispatch"),
    )
    monkeypatch.setattr(
        template_task,
        "create_criteria_text_prompt",
        recorder("task_criteria", ["Criterion"]),
    )
    monkeypatch.setattr(
        template_task, "PromptOptimizer", RecordingOptimizer("task_optimizer")
    )
    monkeypatch.setattr(
        optimize_views, "time", SimpleNamespace(sleep=recorder("sleep"))
    )
    return seen


def _no_model_call(seen):
    return {
        name: seen[name]
        for name in (
            "view_criteria",
            "view_optimizer",
            "task_criteria",
            "task_optimizer",
        )
        if seen[name]
    }


def _ai_model(organization, workspace):
    return AIModel.all_objects.create(
        user_model_id=f"legacy-optimizer-model-{uuid.uuid4()}",
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        organization=organization,
        workspace=workspace,
    )


def _metric(ai_model):
    return Metric.objects.create(
        name=f"Legacy optimizer metric {uuid.uuid4()}",
        text_prompt="Score the answer for accuracy.",
        criteria_breakdown=["Score the answer for accuracy."],
        model=ai_model,
        metric_type=Metric.MetricTypes.WHOLE_USER_OUTPUT,
        evaluation_type=Metric.EvalMetricTypes.EVAL_PROMPT_TEMPLATE,
    )


def _create_body(ai_model, metric, optimize_type, name):
    return {
        "name": name,
        "start_date": "2026-01-01T00:00:00",
        "end_date": "2026-01-02T00:00:00",
        "model": str(ai_model.id),
        "optimize_type": optimize_type,
        "environment": OptimizeDataset.EnvTypes.TRAINING,
        "version": "v1",
        "metrics": [str(metric.id)],
        "prompt": "Answer {{question}}",
        "variables": {"question": "What is the refund window?"},
    }


def _pending_run(organization, workspace, optimize_type, run_status, **extra):
    return OptimizeDataset.no_workspace_objects.create(
        name=f"Pending legacy run {uuid.uuid4()}",
        optimize_type=optimize_type,
        organization=organization,
        workspace=workspace,
        environment=OptimizeDataset.EnvTypes.TRAINING,
        version="v1",
        status=run_status,
        **extra,
    )


@pytest.mark.parametrize(
    ("optimize_type", "sentence"),
    [
        (OptimizeDataset.OptimizeType.TEMPLATE, TEMPLATE_SENTENCE),
        (OptimizeDataset.OptimizeType.RAG_TEMPLATE, RAG_SENTENCE),
    ],
    ids=["prompt-template", "rag-prompt-template"],
)
def test_optimize_dataset_create_refuses_before_any_row_or_model_call(
    auth_client, organization, workspace, calls, optimize_type, sentence
):
    ai_model = _ai_model(organization, workspace)
    metric = _metric(ai_model)
    name = f"Refused legacy run {uuid.uuid4()}"

    response = auth_client.post(
        f"/model-hub/optimize-dataset/{ai_model.id}/",
        _create_body(ai_model, metric, optimize_type, name),
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["result"] == sentence
    assert not OptimizeDataset.all_objects.filter(name=name).exists()
    assert not OptimizeDataset.all_objects.filter(model=ai_model).exists()
    assert calls["check_valid_metrics"] == []
    assert calls["topk_dispatch"] == []
    assert calls["rag_dispatch"] == []
    assert _no_model_call(calls) == {}


def test_knowledge_base_create_refuses_before_any_row_or_model_call(
    auth_client, organization, workspace, calls
):
    name = f"Refused knowledge-base run {uuid.uuid4()}"

    response = auth_client.post(
        "/model-hub/optimize-dataset/knowledge-base/",
        {
            "name": name,
            "knowledge_base_metrics": ["Answers cite the retrieved passage."],
            "knowledge_base_filters": ["topic"],
            "prompt": "Answer from the passage.",
            "variables": {"topic": "billing"},
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["result"] == KNOWLEDGE_BASE_SENTENCE
    assert not OptimizeDataset.all_objects.filter(name=name).exists()
    assert calls["rag_dispatch"] == []
    assert _no_model_call(calls) == {}


@pytest.mark.parametrize(
    "run_status",
    [OptimizeDataset.StatusType.RUNNING, OptimizeDataset.StatusType.PENDING],
    ids=["running", "pending"],
)
def test_prompt_template_task_fails_the_run_without_a_model_call(
    organization, workspace, calls, run_status
):
    ai_model = _ai_model(organization, workspace)
    metric = _metric(ai_model)
    run = _pending_run(
        organization,
        workspace,
        OptimizeDataset.OptimizeType.TEMPLATE,
        run_status,
        model=ai_model,
    )
    run.metrics.set([metric])
    runner_data = {
        "prompt_template": [],
        "context": [],
        "user_chat": [],
        "model_chat": [],
        "variables": [],
        "metadata": [],
    }

    with pytest.raises(credit_ledger.LegacyOptimizerRetiredError) as refused:
        template_task.runner_topk_optimizer(runner_data, [metric.text_prompt], run.id)

    assert str(refused.value) == TASK_SENTENCE
    run.refresh_from_db()
    assert run.status == OptimizeDataset.StatusType.FAILED
    assert run.criteria_breakdown == []
    assert run.optimized_k_prompts is None
    assert _no_model_call(calls) == {}


@pytest.mark.parametrize(
    "run_status",
    [OptimizeDataset.StatusType.RUNNING, OptimizeDataset.StatusType.PENDING],
    ids=["running", "pending"],
)
def test_knowledge_base_task_fails_the_run_without_a_model_call(
    organization, workspace, calls, run_status
):
    run = _pending_run(
        organization,
        workspace,
        OptimizeDataset.OptimizeType.RAG_TEMPLATE,
        run_status,
        prompt="Answer from the passage.",
        knowledge_base_filters=["topic"],
        knowledge_base_metrics=["Answers cite the retrieved passage."],
        criteria_breakdown=["Criterion"],
        variables={"topic": "billing"},
    )

    with pytest.raises(credit_ledger.LegacyOptimizerRetiredError) as refused:
        optimize_views.rag_prompt_optimzer._original_func(run.id)

    assert str(refused.value) == KNOWLEDGE_BASE_SENTENCE
    run.refresh_from_db()
    assert run.status == OptimizeDataset.StatusType.FAILED
    assert run.optimized_k_prompts is None
    assert calls["sleep"] == []
    assert _no_model_call(calls) == {}


def test_ai_output_create_is_not_refused_and_still_creates_its_row(
    auth_client, organization, workspace, calls
):
    ai_model = _ai_model(organization, workspace)
    metric = _metric(ai_model)
    name = f"AI Output run {uuid.uuid4()}"

    response = auth_client.post(
        f"/model-hub/optimize-dataset/{ai_model.id}/",
        _create_body(ai_model, metric, OptimizeDataset.OptimizeType.RIGHT_ANSWER, name),
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["message"] == "Model created successfully"
    created = OptimizeDataset.no_workspace_objects.get(id=response.json()["data"]["id"])
    assert created.name == name
    assert created.optimize_type == OptimizeDataset.OptimizeType.RIGHT_ANSWER
    assert created.status == OptimizeDataset.StatusType.RUNNING
    assert created.model == ai_model
    assert list(created.metrics.values_list("id", flat=True)) == [metric.id]
    assert calls["check_valid_metrics"] == [("EVALUATE_ANSWER", str(ai_model.id))]
    assert calls["topk_dispatch"] == []
    assert calls["rag_dispatch"] == []
    assert _no_model_call(calls) == {}
