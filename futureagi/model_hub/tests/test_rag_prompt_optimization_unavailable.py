from unittest.mock import Mock

import pytest
from rest_framework import status

from model_hub.models.optimize_dataset import OptimizeDataset
from model_hub.serializers.contracts import (
    OptimizeDatasetKnowledgeBaseRequestSerializer,
    OptimizeDatasetMutationRequestSerializer,
)
from model_hub.tests.test_optimize_dataset_contracts import (
    create_ai_model,
    create_metric,
)
from model_hub.views import optimize_dataset

UNAVAILABLE_MESSAGE = "RAG prompt optimization is not available."


@pytest.fixture
def optimization_calls(monkeypatch):
    criteria = Mock(return_value=["Score the output."])
    template_job = Mock()
    rag_job = Mock()
    monkeypatch.setattr(
        optimize_dataset, "check_valid_metrics", lambda *args: (True, "")
    )
    # Keep the old criteria generator offline when checking earlier versions.
    monkeypatch.setattr(
        optimize_dataset, "create_criteria_text_prompt", criteria, raising=False
    )
    monkeypatch.setattr(optimize_dataset.get_topk_prompts, "apply_async", template_job)
    monkeypatch.setattr(optimize_dataset.rag_prompt_optimzer, "apply_async", rag_job)
    return criteria, template_job, rag_job


def optimization_payload(model, metric, optimize_type):
    return {
        "name": "Legacy prompt optimization",
        "start_date": "2026-01-01T00:00:00Z",
        "end_date": "2026-01-02T00:00:00Z",
        "model": str(model.id),
        "optimize_type": optimize_type,
        "environment": OptimizeDataset.EnvTypes.TRAINING,
        "version": "v1",
        "metrics": [str(metric.id)],
        "prompt": "Answer {{input}}",
        "variables": {"input": "question"},
    }


@pytest.mark.django_db
@pytest.mark.parametrize("optimize_type", ["RagPromptTemplate", "RAG_PROMPT_TEMPLATE"])
def test_rag_optimization_returns_unavailable(
    auth_client, organization, workspace, optimization_calls, optimize_type
):
    model = create_ai_model(organization, workspace)
    metric = create_metric(model)
    payload = optimization_payload(model, metric, optimize_type)
    serializer = OptimizeDatasetMutationRequestSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["optimize_type"] == optimize_type

    response = auth_client.post(
        f"/model-hub/optimize-dataset/{model.id}/", payload, format="json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
    assert response.json()["message"] == UNAVAILABLE_MESSAGE
    assert not OptimizeDataset.no_workspace_objects.filter(model=model).exists()
    for call in optimization_calls:
        call.assert_not_called()


@pytest.mark.django_db
def test_knowledge_base_optimization_returns_unavailable_without_scheduling(
    auth_client, optimization_calls
):
    payload = {
        "name": "Unavailable knowledge base optimization",
        "knowledge_base_metrics": ["Score the output."],
        "knowledge_base_filters": ["topic"],
        "prompt": "Answer {{input}}",
        "variables": {"input": "question"},
    }
    serializer = OptimizeDatasetKnowledgeBaseRequestSerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    previous_count = OptimizeDataset.no_workspace_objects.count()

    response = auth_client.post(
        "/model-hub/optimize-dataset/knowledge-base/", payload, format="json"
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
    assert response.json()["message"] == UNAVAILABLE_MESSAGE
    assert OptimizeDataset.no_workspace_objects.count() == previous_count
    for call in optimization_calls:
        call.assert_not_called()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("run_status", ["pending", "running"])
def test_queued_rag_optimization_records_unavailable_without_raising(
    organization, workspace, run_status
):
    optimization = OptimizeDataset.no_workspace_objects.create(
        name="Queued RAG optimization",
        organization=organization,
        workspace=workspace,
        optimize_type=OptimizeDataset.OptimizeType.RAG_TEMPLATE,
        environment=OptimizeDataset.EnvTypes.CORPUS,
        version="",
        prompt="Answer {{input}}",
        criteria_breakdown=["Score the output."],
        status=run_status,
    )

    optimize_dataset.rag_prompt_optimzer.run_sync(str(optimization.id))

    optimization.refresh_from_db()
    assert optimization.status == OptimizeDataset.StatusType.FAILED
    assert optimization.error_message == UNAVAILABLE_MESSAGE
    assert optimization.optimized_k_prompts is None


@pytest.mark.django_db
def test_template_optimization_still_creates_and_schedules(
    auth_client, organization, workspace, optimization_calls
):
    model = create_ai_model(organization, workspace)
    metric = create_metric(model)
    payload = optimization_payload(model, metric, OptimizeDataset.OptimizeType.TEMPLATE)

    response = auth_client.post(
        f"/model-hub/optimize-dataset/{model.id}/", payload, format="json"
    )

    # A team test's expected value changes: Prompt Template optimization is no
    # longer refused. PromptOptimizer now takes the criteria breakdown and the
    # criteria call is metered, so the create writes its row and schedules
    # the top-k task once, as this test's name says; RAG is never scheduled.
    assert response.status_code == status.HTTP_200_OK, response.json()
    created = OptimizeDataset.no_workspace_objects.get(model=model)
    assert created.optimize_type == OptimizeDataset.OptimizeType.TEMPLATE
    assert str(created.id) == str(response.json()["data"]["id"])
    criteria, template_job, rag_job = optimization_calls
    template_job.assert_called_once()
    scheduled = template_job.call_args.kwargs["args"]
    assert scheduled[0] == str(model.id)
    assert str(scheduled[4]) == str(created.id)
    criteria.assert_not_called()
    rag_job.assert_not_called()
