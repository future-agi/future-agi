"""The retired legacy optimizer entries refuse before any work; Prompt
Template runs again.

A line test's expected value changes: Prompt Template is no longer retired.
Its create writes its row and schedules its run, and its task builds the
criteria for the run's organization and workspace and hands them to
PromptOptimizer. The optimize-dataset create route for RAG Prompt Template,
the knowledge-base create route and the knowledge-base task still refuse with
one sentence before a row is written and before any model call. The drawer's
other option, 'AI Output' (RightAnswer), still creates its row.

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
from model_hub.tasks import prompt_template_optimizer as template_task
from model_hub.views import optimize_dataset as optimize_views

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]

RAG_UNAVAILABLE_SENTENCE = "RAG prompt optimization is not available."


@pytest.fixture
def calls(monkeypatch):
    """Record every model entry point and dispatch on the legacy paths."""
    seen = {
        "check_valid_metrics": [],
        "topk_dispatch": [],
        "rag_dispatch": [],
        "task_criteria": [],
        "task_optimizer": [],
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
    return seen


def _no_model_call(seen):
    return {
        name: seen[name]
        for name in (
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
        # A line test's expected value changes: the prompt-template case is
        # gone, because Prompt Template is no longer refused; its create is
        # test_prompt_template_create_creates_its_row_and_schedules_the_run.
        # RAG Prompt Template and the knowledge-base create answer "RAG prompt
        # optimization is not available.", and the knowledge-base task marks
        # the run failed with that sentence without raising.
        (OptimizeDataset.OptimizeType.RAG_TEMPLATE, RAG_UNAVAILABLE_SENTENCE),
    ],
    ids=["rag-prompt-template"],
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


def test_prompt_template_create_creates_its_row_and_schedules_the_run(
    auth_client, organization, workspace, calls
):
    # A line test's expected value changes: this was the prompt-template case
    # of test_optimize_dataset_create_refuses_before_any_row_or_model_call,
    # which answered 400 with the retirement sentence and wrote no row. The
    # create now writes its row and schedules its run; the request itself
    # calls no model.
    ai_model = _ai_model(organization, workspace)
    metric = _metric(ai_model)
    name = f"Prompt Template run {uuid.uuid4()}"

    response = auth_client.post(
        f"/model-hub/optimize-dataset/{ai_model.id}/",
        _create_body(ai_model, metric, OptimizeDataset.OptimizeType.TEMPLATE, name),
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["message"] == "Model created successfully"
    created = OptimizeDataset.no_workspace_objects.get(id=response.json()["data"]["id"])
    assert created.name == name
    assert created.optimize_type == OptimizeDataset.OptimizeType.TEMPLATE
    assert created.status == OptimizeDataset.StatusType.RUNNING
    assert created.model == ai_model
    assert list(created.metrics.values_list("id", flat=True)) == [metric.id]
    assert calls["check_valid_metrics"] == [
        ("EVALUATE_PROMPT_TEMPLATE", str(ai_model.id))
    ]
    ((dispatch_args, dispatch_kwargs),) = calls["topk_dispatch"]
    assert dispatch_args == ()
    scheduled = dispatch_kwargs["args"]
    assert scheduled[:3] == (
        str(ai_model.id),
        OptimizeDataset.EnvTypes.TRAINING,
        "v1",
    )
    assert [str(metric_id) for metric_id in scheduled[3]] == [str(metric.id)]
    assert str(scheduled[4]) == str(created.id)
    assert scheduled[5:] == ("2026-01-01", "2026-01-02")
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
    assert response.json()["result"] == RAG_UNAVAILABLE_SENTENCE
    assert not OptimizeDataset.all_objects.filter(name=name).exists()
    assert calls["rag_dispatch"] == []
    assert _no_model_call(calls) == {}


@pytest.mark.parametrize(
    "run_status",
    [OptimizeDataset.StatusType.RUNNING, OptimizeDataset.StatusType.PENDING],
    ids=["running", "pending"],
)
def test_prompt_template_task_hands_the_run_criteria_to_the_optimizer(
    organization, workspace, calls, run_status, monkeypatch
):
    # A line test's expected value changes: this was
    # test_prompt_template_task_fails_the_run_without_a_model_call, which
    # expected LegacyOptimizerRetiredError, a failed run and no criteria call.
    # The task now asks the criteria helper for the run's organization and
    # workspace (so its call is metered), hands the criteria and the run's
    # prompt to PromptOptimizer, and saves what it returns.
    ai_model = _ai_model(organization, workspace)
    metric = _metric(ai_model)
    run = _pending_run(
        organization,
        workspace,
        OptimizeDataset.OptimizeType.TEMPLATE,
        run_status,
        model=ai_model,
        prompt="Answer {{question}}",
    )
    run.metrics.set([metric])
    # A line test's expected value changes: a run with no traced rows is now
    # refused before any call, so the task is given one row.
    runner_data = {
        "prompt_template": ["Answer {{question}}"],
        "context": [None],
        "user_chat": ["What is the refund window?"],
        "model_chat": ["Thirty days."],
        "variables": [{"question": "What is the refund window?"}],
        "metadata": [{}],
    }
    # A line test's expected value changes: a run with no managed route is
    # refused before any call, so the run is given its
    # route here (a stand-in run client). The criteria helper and
    # PromptOptimizer both get that one client.
    run_client = SimpleNamespace(refusal=None, settled=0)
    routes = []

    def run_route(*args):
        routes.append(args)
        return run_client

    monkeypatch.setattr(template_task, "_run_client", run_route, raising=False)

    template_task.runner_topk_optimizer(runner_data, [metric.text_prompt], run.id)

    run.refresh_from_db()
    assert run.status == OptimizeDataset.StatusType.COMPLETED
    assert run.criteria_breakdown == ["Criterion"]
    assert run.optimized_k_prompts == []
    assert routes == [(organization, workspace)]
    ((criteria_args, criteria_kwargs),) = calls["task_criteria"]
    assert criteria_args == ("0. Score the answer for accuracy.",)
    assert criteria_kwargs == {"llm": run_client}
    ((optimizer_args, optimizer_kwargs),) = calls["task_optimizer"]
    assert optimizer_args == ()
    assert optimizer_kwargs["prompt"] == "Answer {{question}}"
    assert optimizer_kwargs["criteria_breakdown"] == ["Criterion"]
    assert optimizer_kwargs["client"] is run_client
    assert "optimize_rag" not in optimizer_kwargs


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

    optimize_views.rag_prompt_optimzer._original_func(run.id)

    run.refresh_from_db()
    assert run.status == OptimizeDataset.StatusType.FAILED
    assert run.error_message == RAG_UNAVAILABLE_SENTENCE
    assert run.optimized_k_prompts is None
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
