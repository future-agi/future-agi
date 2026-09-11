import uuid
from unittest.mock import patch

import pytest
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.choices import (
    CellStatus,
    DatasetSourceChoices,
    DataTypeChoices,
    FeedbackSourceChoices,
    OwnerChoices,
    SourceChoices,
    StatusType,
)
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, Feedback, UserEvalMetric

FEEDBACK_URL = "/model-hub/feedback/"


def _result(response):
    data = response.json()
    if isinstance(data, dict):
        return data.get("result", data)
    return data


def _list_items(response):
    payload = _result(response)
    if isinstance(payload, dict):
        return payload.get("results", [])
    return payload


def _create_feedback_graph(user, workspace, marker="active"):
    organization = user.organization
    dataset = Dataset.objects.create(
        name=f"Legacy feedback {marker} dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
    )
    input_column = Column.objects.create(
        name=f"Legacy feedback {marker} input",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.OTHERS.value,
        status=StatusType.COMPLETED.value,
    )
    row = Row.objects.create(dataset=dataset, order=0)
    Cell.objects.create(
        dataset=dataset,
        column=input_column,
        row=row,
        value=f"{marker} input",
        status=CellStatus.PASS.value,
    )
    eval_template = EvalTemplate.objects.create(
        name=f"legacy-feedback-{marker}-{uuid.uuid4().hex[:8]}",
        description=f"Legacy feedback {marker} template",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "Pass/Fail", "eval_type_id": "test_eval_type"},
    )
    metric = UserEvalMetric.objects.create(
        name=f"Legacy Feedback {marker} Metric",
        organization=organization,
        workspace=workspace,
        user=user,
        template=eval_template,
        dataset=dataset,
        config={"mapping": {"input": str(input_column.id)}},
        status=StatusType.COMPLETED.value,
    )
    eval_column = Column.objects.create(
        name=f"Legacy feedback {marker} eval",
        dataset=dataset,
        data_type=DataTypeChoices.TEXT.value,
        source=SourceChoices.EVALUATION.value,
        source_id=str(metric.id),
        status=StatusType.COMPLETED.value,
    )
    Cell.objects.create(
        dataset=dataset,
        column=eval_column,
        row=row,
        value="Passed",
        status=CellStatus.PASS.value,
    )
    dataset.column_order = [str(input_column.id), str(eval_column.id)]
    dataset.save(update_fields=["column_order", "updated_at"])
    return {
        "dataset": dataset,
        "input_column": input_column,
        "eval_column": eval_column,
        "row": row,
        "eval_template": eval_template,
        "metric": metric,
    }


def _create_same_org_other_workspace_feedback_graph(user):
    hidden_workspace = Workspace.objects.create(
        name=f"Hidden Feedback Workspace {uuid.uuid4().hex[:8]}",
        organization=user.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    graph = _create_feedback_graph(user, hidden_workspace, marker="hidden")
    graph["workspace"] = hidden_workspace
    return graph


def _create_feedback(user, workspace, graph, **overrides):
    defaults = {
        "source": FeedbackSourceChoices.DATASET.value,
        "source_id": str(graph["eval_column"].id),
        "user_eval_metric": graph["metric"],
        "eval_template": graph["eval_template"],
        "value": "Failed",
        "explanation": "seeded feedback",
        "user": user,
        "row_id": str(graph["row"].id),
        "organization": user.organization,
        "workspace": workspace,
    }
    defaults.update(overrides)
    return Feedback.no_workspace_objects.create(**defaults)


@pytest.mark.django_db
def test_legacy_feedback_generated_routes_round_trip(auth_client, user, workspace):
    graph = _create_feedback_graph(user, workspace)
    create_payload = {
        "source": FeedbackSourceChoices.DATASET.value,
        "source_id": str(graph["eval_column"].id),
        "user_eval_metric": str(graph["metric"].id),
        "value": "Failed",
        "explanation": "legacy feedback initial",
        "row_id": str(graph["row"].id),
    }

    create_response = auth_client.post(FEEDBACK_URL, create_payload, format="json")
    assert create_response.status_code == status.HTTP_200_OK, create_response.json()
    feedback_id = _result(create_response)["id"]

    feedback = Feedback.no_workspace_objects.get(id=feedback_id)
    assert feedback.organization_id == user.organization_id
    assert feedback.workspace_id == workspace.id
    assert feedback.source == FeedbackSourceChoices.DATASET.value
    assert feedback.source_id == str(graph["eval_column"].id)
    assert feedback.user_eval_metric_id == graph["metric"].id
    assert feedback.eval_template_id == graph["eval_template"].id
    assert feedback.row_id == str(graph["row"].id)

    detail_response = auth_client.get(f"{FEEDBACK_URL}{feedback_id}/")
    assert detail_response.status_code == status.HTTP_200_OK, detail_response.json()
    assert str(_result(detail_response)["id"]) == str(feedback_id)

    list_response = auth_client.get(FEEDBACK_URL)
    assert list_response.status_code == status.HTTP_200_OK, list_response.json()
    assert str(feedback_id) in {str(item["id"]) for item in _list_items(list_response)}

    template_response = auth_client.get(
        f"{FEEDBACK_URL}get_template/",
        {"user_eval_metric_id": str(graph["metric"].id)},
    )
    assert template_response.status_code == status.HTTP_200_OK, template_response.json()
    template_payload = _result(template_response)
    assert template_payload["output_type"] == "Pass/Fail"
    assert template_payload["choices"] == ["Passed", "Failed"]
    assert template_payload["eval_name"] == graph["eval_template"].name
    assert template_payload["user_eval_name"] == graph["metric"].name

    details_response = auth_client.get(
        f"{FEEDBACK_URL}get-feedback-details/",
        {
            "user_eval_metric_id": str(graph["metric"].id),
            "row_id": str(graph["row"].id),
        },
    )
    assert details_response.status_code == status.HTTP_200_OK, details_response.json()
    details_payload = _result(details_response)
    assert details_payload["total_count"] == 1
    assert details_payload["feedback"][0]["id"] == str(feedback_id)

    summary_response = auth_client.get(
        f"{FEEDBACK_URL}get-feedback-summary/",
        {"user_eval_metric_id": str(graph["metric"].id)},
    )
    assert summary_response.status_code == status.HTTP_200_OK, summary_response.json()
    assert _result(summary_response)["total_feedback"] == 1

    put_payload = {
        **create_payload,
        "value": "Passed",
        "explanation": "legacy feedback put",
        "action_type": "recalculate_row",
    }
    put_response = auth_client.put(
        f"{FEEDBACK_URL}{feedback_id}/", put_payload, format="json"
    )
    assert put_response.status_code == status.HTTP_200_OK, put_response.json()

    patch_response = auth_client.patch(
        f"{FEEDBACK_URL}{feedback_id}/",
        {"feedback_improvement": "tighten rubric", "action_type": "retune"},
        format="json",
    )
    assert patch_response.status_code == status.HTTP_200_OK, patch_response.json()

    with (
        patch("model_hub.views.develop_dataset.EmbeddingManager") as embedding_manager,
        patch("model_hub.views.develop_dataset.EvaluationRunner") as runner,
    ):
        runner.return_value._get_required_fields_and_mappings.return_value = ([], {})
        submit_response = auth_client.post(
            f"{FEEDBACK_URL}submit-feedback/",
            {
                "action_type": "retune",
                "feedback_id": str(feedback_id),
                "user_eval_metric_id": str(graph["metric"].id),
                "value": "Failed",
                "explanation": "submitted feedback",
            },
            format="json",
        )
    assert submit_response.status_code == status.HTTP_200_OK, submit_response.json()
    submit_payload = _result(submit_response)
    assert submit_payload["action_type"] == "retune"
    embedding_manager.return_value.parallel_process_metadata.assert_called_once()
    embedding_manager.return_value.close.assert_called_once()

    feedback.refresh_from_db()
    assert feedback.value == "Failed"
    assert feedback.explanation == "submitted feedback"
    assert feedback.action_type == "retune"

    delete_response = auth_client.delete(f"{FEEDBACK_URL}{feedback_id}/")
    assert delete_response.status_code == status.HTTP_204_NO_CONTENT
    deleted_feedback = Feedback.all_objects.get(id=feedback_id)
    assert deleted_feedback.deleted is True
    assert deleted_feedback.deleted_at is not None


@pytest.mark.django_db
def test_get_template_returns_choice_scores_when_defined(
    auth_client, user, workspace
):
    """Feedback template must surface choice_scores so the FE can render a
    choice picker with derived scores instead of a raw numeric input."""
    organization = user.organization
    dataset = Dataset.objects.create(
        name="Choice scores template dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
    )
    eval_template = EvalTemplate.objects.create(
        name=f"choice-scores-template-{uuid.uuid4().hex[:8]}",
        description="Template mapping choices to derived scores",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "score", "eval_type_id": "test_eval_type"},
        choice_scores={"Yes": 1.0, "Maybe": 0.5, "No": 0.0},
    )
    metric = UserEvalMetric.objects.create(
        name="Choice Scores Metric",
        organization=organization,
        workspace=workspace,
        user=user,
        template=eval_template,
        dataset=dataset,
        config={"mapping": {}},
        status=StatusType.COMPLETED.value,
    )

    response = auth_client.get(
        f"{FEEDBACK_URL}get_template/",
        {"user_eval_metric_id": str(metric.id)},
    )

    assert response.status_code == status.HTTP_200_OK, response.json()
    payload = _result(response)
    assert payload["choice_scores"] == {"Yes": 1.0, "Maybe": 0.5, "No": 0.0}
    assert payload["eval_name"] == eval_template.name
    assert payload["user_eval_name"] == metric.name


@pytest.mark.django_db
def test_get_template_multi_choice_sourced_from_template_field(
    auth_client, user, workspace
):
    """multi_choice is sourced from the template's canonical direct field
    (mirrors how the YAML seeder populates it). Metric-side multi_choice
    overrides are ignored."""
    organization = user.organization
    dataset = Dataset.objects.create(
        name="Multi-choice override dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source=DatasetSourceChoices.BUILD.value,
    )
    eval_template = EvalTemplate.objects.create(
        name=f"tone-like-{uuid.uuid4().hex[:8]}",
        description="Template with choices and multi_choice on the direct field",
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        config={"output": "choices", "eval_type_id": "test_eval_type"},
        choices=["joy", "anger", "sadness"],
        multi_choice=True,
    )
    metric = UserEvalMetric.objects.create(
        name="Tone-like Metric",
        organization=organization,
        workspace=workspace,
        user=user,
        template=eval_template,
        dataset=dataset,
        config={"config": {"multi_choice": False}},
        status=StatusType.COMPLETED.value,
    )

    response = auth_client.get(
        f"{FEEDBACK_URL}get_template/",
        {"user_eval_metric_id": str(metric.id)},
    )

    assert response.status_code == status.HTTP_200_OK, response.json()
    payload = _result(response)
    assert payload["multi_choice"] is True
    assert payload["choices"] == ["joy", "anger", "sadness"]


@pytest.mark.django_db
def test_get_template_returns_null_choice_scores_when_absent(
    auth_client, user, workspace
):
    """Templates without choice_scores must still round-trip cleanly; the FE
    treats null as "no picker, use the numeric input"."""
    graph = _create_feedback_graph(user, workspace)

    response = auth_client.get(
        f"{FEEDBACK_URL}get_template/",
        {"user_eval_metric_id": str(graph["metric"].id)},
    )

    assert response.status_code == status.HTTP_200_OK, response.json()
    payload = _result(response)
    assert payload["choice_scores"] is None


@pytest.mark.django_db
def test_legacy_feedback_rejects_same_org_other_workspace_source_column(
    auth_client, user, workspace
):
    active_graph = _create_feedback_graph(user, workspace)
    hidden_graph = _create_same_org_other_workspace_feedback_graph(user)

    response = auth_client.post(
        FEEDBACK_URL,
        {
            "source": FeedbackSourceChoices.DATASET.value,
            "source_id": str(hidden_graph["eval_column"].id),
            "user_eval_metric": str(active_graph["metric"].id),
            "value": "Failed",
            "explanation": "hidden source should not attach",
            "row_id": str(hidden_graph["row"].id),
        },
        format="json",
    )
    assert response.status_code == status.HTTP_404_NOT_FOUND, response.json()
    assert not Feedback.no_workspace_objects.filter(
        source_id=str(hidden_graph["eval_column"].id),
        explanation="hidden source should not attach",
    ).exists()


@pytest.mark.django_db
def test_legacy_feedback_generated_routes_hide_same_org_other_workspace_feedback(
    auth_client, user, workspace
):
    active_graph = _create_feedback_graph(user, workspace)
    hidden_graph = _create_same_org_other_workspace_feedback_graph(user)
    hidden_feedback = _create_feedback(
        user,
        hidden_graph["workspace"],
        hidden_graph,
        explanation="hidden feedback must stay hidden",
    )

    detail_response = auth_client.get(f"{FEEDBACK_URL}{hidden_feedback.id}/")
    assert detail_response.status_code == status.HTTP_404_NOT_FOUND

    patch_response = auth_client.patch(
        f"{FEEDBACK_URL}{hidden_feedback.id}/",
        {"explanation": "should not update"},
        format="json",
    )
    assert patch_response.status_code == status.HTTP_404_NOT_FOUND

    list_response = auth_client.get(FEEDBACK_URL)
    assert list_response.status_code == status.HTTP_200_OK, list_response.json()
    assert str(hidden_feedback.id) not in {
        str(item["id"]) for item in _list_items(list_response)
    }

    details_response = auth_client.get(
        f"{FEEDBACK_URL}get-feedback-details/",
        {
            "user_eval_metric_id": str(hidden_graph["metric"].id),
            "row_id": str(hidden_graph["row"].id),
        },
    )
    assert details_response.status_code == status.HTTP_200_OK, details_response.json()
    assert _result(details_response)["total_count"] == 0

    template_response = auth_client.get(
        f"{FEEDBACK_URL}get_template/",
        {"user_eval_metric_id": str(hidden_graph["metric"].id)},
    )
    assert template_response.status_code == status.HTTP_400_BAD_REQUEST

    submit_response = auth_client.post(
        f"{FEEDBACK_URL}submit-feedback/",
        {
            "action_type": "retune",
            "feedback_id": str(hidden_feedback.id),
            "user_eval_metric_id": str(active_graph["metric"].id),
            "value": "Passed",
            "explanation": "should not submit",
        },
        format="json",
    )
    assert submit_response.status_code == status.HTTP_404_NOT_FOUND

    hidden_feedback.refresh_from_db()
    assert hidden_feedback.explanation == "hidden feedback must stay hidden"
    assert hidden_feedback.action_type is None


@pytest.mark.django_db
def test_legacy_feedback_create_validation_errors_return_400(auth_client):
    response = auth_client.post(
        FEEDBACK_URL,
        {
            "source": "unknown-source",
            "source_id": str(uuid.uuid4()),
            "value": "Failed",
        },
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.json()
