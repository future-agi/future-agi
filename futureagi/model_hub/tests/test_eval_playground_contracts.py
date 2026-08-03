import json

import pytest
from rest_framework import status

from accounts.models import Organization, User
from accounts.models.workspace import Workspace
from ee.usage.models.usage import APICallLog, APICallStatusChoices
from model_hub.models.choices import OwnerChoices, SourceChoices
from model_hub.models.error_localizer_model import (
    ErrorLocalizerSource,
    ErrorLocalizerStatus,
    ErrorLocalizerTask,
)
from model_hub.models.evals_metric import EvalSettings, EvalTemplate, Feedback
from model_hub.serializers.contracts import EvalApiLogTableQuerySerializer
from model_hub.views.separate_evals import create_column_config_playground


def _create_workspace(organization, user, name):
    return Workspace.objects.create(
        name=name,
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=user,
    )


def _create_code_eval_template(organization, workspace=None, name="playground-code"):
    return EvalTemplate.no_workspace_objects.create(
        name=name,
        organization=organization,
        workspace=workspace,
        owner=OwnerChoices.USER.value,
        eval_type="code",
        config={
            "code": "def evaluate(output=None, expected=None, **kwargs):\n    return True",
            "output": "Pass/Fail",
            "eval_type_id": "CustomCodeEval",
            "required_keys": ["output", "expected"],
        },
        visible_ui=True,
        output_type_normalized="pass_fail",
        pass_threshold=0.5,
    )


def _create_other_org_template(user, name="other-playground-code"):
    other_org = Organization.objects.create(name=f"{name}-org")
    other_user = User.objects.create_user(
        email=f"{name}@example.com",
        password="testpassword123",
        name=f"{name} user",
        organization=other_org,
    )
    other_workspace = _create_workspace(other_org, other_user, f"{name}-workspace")
    return _create_code_eval_template(other_org, other_workspace, name=name)


@pytest.mark.django_db
def test_eval_playground_rejects_template_from_another_organization(
    auth_client, user
):
    template = _create_other_org_template(user)

    response = auth_client.post(
        "/model-hub/eval-playground/",
        {
            "template_id": str(template.id),
            "model": "",
            "mapping": {"output": "same", "expected": "same"},
            "config": {"params": {}},
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_eval_playground_feedback_rejects_other_org_log(auth_client, user):
    template = _create_other_org_template(user, name="other-playground-feedback")
    log = APICallLog.objects.create(
        organization=template.organization,
        workspace=template.workspace,
        status=APICallStatusChoices.SUCCESS.value,
        cost=0,
        source=SourceChoices.EVAL_PLAYGROUND.value,
        source_id=str(template.id),
        config=json.dumps(
            {
                "required_keys": ["output"],
                "mappings": {"output": "other org output"},
                "input_data_types": {"output": "text"},
            }
        ),
    )

    response = auth_client.post(
        "/model-hub/eval-playground/feedback/",
        {
            "log_id": str(log.log_id),
            "action_type": "retune",
            "value": "passed",
            "explanation": "should not attach to another org log",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not Feedback.objects.filter(
        source=SourceChoices.EVAL_PLAYGROUND.value,
        source_id=str(log.log_id),
        organization=user.organization,
    ).exists()


@pytest.mark.django_db
def test_eval_playground_feedback_recalculate_updates_feedback_and_schedules_rerun(
    auth_client, monkeypatch, user, workspace
):
    template = _create_code_eval_template(
        user.organization, workspace, name="same-org-recalculate-feedback"
    )
    log = APICallLog.objects.create(
        organization=user.organization,
        workspace=workspace,
        status=APICallStatusChoices.SUCCESS.value,
        cost=0,
        source=SourceChoices.EVAL_PLAYGROUND.value,
        source_id=str(template.id),
        config=json.dumps(
            {
                "required_keys": ["output", "expected"],
                "mappings": {"output": "wrong", "expected": "right"},
                "input_data_types": {"output": "text", "expected": "text"},
                "output": {"output": "Failed", "reason": "not equal"},
                "model": "",
            }
        ),
    )

    class DummyEmbeddingManager:
        def data_formatter(self, *args, **kwargs):
            return [], []

        def close(self):
            return None

    scheduled = {}

    def fake_delay(*args, **kwargs):
        scheduled["args"] = args
        scheduled["kwargs"] = kwargs

    monkeypatch.setattr(
        "model_hub.views.separate_evals.EmbeddingManager",
        DummyEmbeddingManager,
    )
    monkeypatch.setattr(
        "model_hub.views.separate_evals.run_eval_func_task.delay",
        fake_delay,
    )

    response = auth_client.post(
        "/model-hub/eval-playground/feedback/",
        {
            "log_id": str(log.log_id),
            "action_type": "recalculate",
            "value": "failed",
            "explanation": "rerun with corrected feedback",
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.data["result"]
    assert result["message"] == "Metric queued for recalculation"
    feedback = Feedback.objects.get(id=result["feedback_id"])
    assert feedback.source == SourceChoices.EVAL_PLAYGROUND.value
    assert feedback.source_id == str(log.log_id)
    assert feedback.eval_template_id == template.id
    assert feedback.action_type == "recalculate"
    assert feedback.value == "failed"
    assert feedback.explanation == "rerun with corrected feedback"

    assert scheduled["args"][0] == {"output": "wrong", "expected": "right"}
    assert scheduled["args"][1] == str(template.id)
    assert scheduled["args"][2] == str(user.organization.id)
    assert scheduled["args"][5] == str(log.log_id)
    assert scheduled["kwargs"] == {
        "input_data_types": {"output": "text", "expected": "text"}
    }


@pytest.mark.django_db
def test_eval_feedback_list_rejects_template_from_another_organization(
    auth_client, user
):
    template = _create_other_org_template(user, name="other-feedback-list")

    response = auth_client.get(
        f"/model-hub/eval-templates/{template.id}/feedback-list/"
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_eval_log_detail_rejects_other_org_log(auth_client, user):
    template = _create_other_org_template(user, name="other-log-detail")
    log = APICallLog.objects.create(
        organization=template.organization,
        workspace=template.workspace,
        status=APICallStatusChoices.SUCCESS.value,
        cost=0,
        source=SourceChoices.EVAL_PLAYGROUND.value,
        source_id=str(template.id),
        config=json.dumps(
            {
                "required_keys": ["output"],
                "mappings": {"output": "other org output"},
                "output": {"output": True, "reason": "other org reason"},
            }
        ),
    )

    response = auth_client.get(
        "/model-hub/get-eval-logs",
        {"log_id": str(log.log_id)},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_eval_log_detail_surfaces_completed_error_localizer_task(
    auth_client, user, workspace
):
    template = _create_code_eval_template(
        user.organization, workspace, name="same-org-error-localizer-log"
    )
    log = APICallLog.objects.create(
        organization=user.organization,
        workspace=workspace,
        status=APICallStatusChoices.SUCCESS.value,
        cost=0,
        source=SourceChoices.EVAL_PLAYGROUND.value,
        source_id=str(template.id),
        config=json.dumps(
            {
                "required_keys": ["output", "expected"],
                "mappings": {"output": "wrong", "expected": "right"},
                "output": {"output": "Failed", "reason": "not equal"},
            }
        ),
    )
    task = ErrorLocalizerTask.objects.create(
        eval_template=template,
        source=ErrorLocalizerSource.PLAYGROUND,
        source_id=log.log_id,
        status=ErrorLocalizerStatus.COMPLETED,
        input_data={"output": "wrong", "expected": "right"},
        input_keys=["output", "expected"],
        input_types={"output": "text", "expected": "text"},
        eval_result="Failed",
        eval_explanation="not equal",
        error_analysis={"issue": "output does not match expected"},
        selected_input_key="output",
        organization=user.organization,
        workspace=workspace,
    )

    response = auth_client.get(
        "/model-hub/get-eval-logs",
        {"log_id": str(log.log_id)},
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.data["result"]
    assert result["error_localizer_status"] == ErrorLocalizerStatus.COMPLETED
    assert result["error_details"] == {
        "error_analysis": task.error_analysis,
        "selected_input_key": "output",
        "input_types": task.input_types,
        "input_data": task.input_data,
    }


@pytest.mark.django_db
def test_eval_log_detail_surfaces_pending_error_localizer_task_status(
    auth_client, user, workspace
):
    template = _create_code_eval_template(
        user.organization, workspace, name="same-org-error-localizer-pending"
    )
    log = APICallLog.objects.create(
        organization=user.organization,
        workspace=workspace,
        status=APICallStatusChoices.SUCCESS.value,
        cost=0,
        source=SourceChoices.EVAL_PLAYGROUND.value,
        source_id=str(template.id),
        config=json.dumps(
            {
                "required_keys": ["output", "expected"],
                "mappings": {"output": "wrong", "expected": "right"},
                "output": {"output": "Failed", "reason": "not equal"},
            }
        ),
    )
    ErrorLocalizerTask.objects.create(
        eval_template=template,
        source=ErrorLocalizerSource.PLAYGROUND,
        source_id=log.log_id,
        status=ErrorLocalizerStatus.PENDING,
        input_data={"output": "wrong", "expected": "right"},
        input_keys=["output", "expected"],
        input_types={"output": "text", "expected": "text"},
        eval_result="Failed",
        eval_explanation="not equal",
        organization=user.organization,
        workspace=workspace,
    )

    response = auth_client.get(
        "/model-hub/get-eval-logs",
        {"log_id": str(log.log_id)},
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.data["result"]
    assert result["error_localizer_status"] == ErrorLocalizerStatus.PENDING
    assert "error_details" not in result


@pytest.mark.django_db
def test_eval_log_delete_does_not_delete_other_org_log(auth_client, user):
    template = _create_other_org_template(user, name="other-log-delete")
    log = APICallLog.objects.create(
        organization=template.organization,
        workspace=template.workspace,
        status=APICallStatusChoices.SUCCESS.value,
        cost=0,
        source=SourceChoices.EVAL_PLAYGROUND.value,
        source_id=str(template.id),
        config=json.dumps(
            {
                "required_keys": ["output"],
                "mappings": {"output": "other org output"},
                "output": {"output": True, "reason": "other org reason"},
            }
        ),
    )

    response = auth_client.delete(
        "/model-hub/get-eval-logs",
        {"log_ids": [str(log.log_id)]},
        format="json",
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    log.refresh_from_db()
    assert log.deleted is False


@pytest.mark.django_db
def test_eval_log_delete_soft_deletes_playground_error_localizer_task(
    auth_client, user, workspace
):
    template = _create_code_eval_template(
        user.organization, workspace, name="same-org-delete-localizer-task"
    )
    log = APICallLog.objects.create(
        organization=user.organization,
        workspace=workspace,
        status=APICallStatusChoices.SUCCESS.value,
        cost=0,
        source=SourceChoices.EVAL_PLAYGROUND.value,
        source_id=str(template.id),
        config=json.dumps(
            {
                "required_keys": ["output", "expected"],
                "mappings": {"output": "wrong", "expected": "right"},
                "output": {"output": "Failed", "reason": "not equal"},
            }
        ),
    )
    task = ErrorLocalizerTask.objects.create(
        eval_template=template,
        source=ErrorLocalizerSource.PLAYGROUND,
        source_id=log.log_id,
        status=ErrorLocalizerStatus.PENDING,
        input_data={"output": "wrong", "expected": "right"},
        input_keys=["output", "expected"],
        input_types={"output": "text", "expected": "text"},
        eval_result="Failed",
        eval_explanation="not equal",
        organization=user.organization,
        workspace=workspace,
    )

    response = auth_client.delete(
        "/model-hub/get-eval-logs",
        {"log_ids": [str(log.log_id)]},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    log.refresh_from_db()
    task.refresh_from_db()
    assert log.deleted is True
    assert log.deleted_at is not None
    assert task.deleted is True
    assert task.deleted_at is not None


@pytest.mark.django_db
def test_eval_logs_table_rejects_other_org_template_without_creating_settings(
    auth_client, user
):
    template = _create_other_org_template(user, name="other-log-table")

    response = auth_client.get(
        "/model-hub/get-eval-logs-details",
        {
            "eval_template_id": str(template.id),
            "source": "eval_playground",
            "current_page_index": 0,
            "page_size": 10,
        },
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert not EvalSettings.objects.filter(
        eval_id=template.id,
        user=user,
        deleted=False,
    ).exists()


@pytest.mark.django_db
def test_eval_log_column_config_patch_rejects_other_org_template_without_creating_settings(
    auth_client, user
):
    template = _create_other_org_template(user, name="other-log-settings")

    response = auth_client.patch(
        "/model-hub/get-eval-logs",
        {
            "eval_id": str(template.id),
            "source": "eval_playground",
            "column_config": [
                {
                    "id": "column1",
                    "name": "Evaluation ID",
                    "status": "completed",
                    "is_visible": True,
                    "is_frozen": None,
                    "source_type": "text",
                    "data_type": "text",
                }
            ],
        },
        format="json",
    )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert not EvalSettings.objects.filter(
        eval_id=template.id,
        user=user,
        deleted=False,
    ).exists()


@pytest.mark.django_db
def test_get_eval_config_rejects_other_org_user_template(auth_client, user):
    template = _create_other_org_template(user, name="other-eval-config")

    response = auth_client.get(
        "/model-hub/get-eval-config",
        {"eval_id": str(template.id)},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_get_eval_config_rejects_deleted_user_template(auth_client, user, workspace):
    template = _create_code_eval_template(
        user.organization, workspace, name="deleted-eval-config"
    )
    template.deleted = True
    template.save(update_fields=["deleted"])

    response = auth_client.get(
        "/model-hub/get-eval-config",
        {"eval_id": str(template.id)},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_eval_template_name_picker_excludes_deleted_and_other_org_sources(
    auth_client, user, workspace
):
    active = _create_code_eval_template(
        user.organization, workspace, name="active-eval-name-picker"
    )
    deleted = _create_code_eval_template(
        user.organization, workspace, name="deleted-eval-name-picker"
    )
    deleted.deleted = True
    deleted.save(update_fields=["deleted"])
    other_org = _create_other_org_template(user, name="other-eval-name-picker")

    for template in (active, deleted, other_org):
        APICallLog.objects.create(
            organization=user.organization,
            workspace=workspace,
            status=APICallStatusChoices.SUCCESS.value,
            cost=0,
            source=SourceChoices.EVAL_PLAYGROUND.value,
            source_id=str(template.id),
            config=json.dumps({"mappings": {"output": "value"}}),
        )

    response = auth_client.post(
        "/model-hub/get-eval-template-names",
        {"search_text": "eval-name-picker"},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    names = {row["name"] for row in response.data["result"]}
    assert "active-eval-name-picker" in names
    assert "deleted-eval-name-picker" not in names
    assert "other-eval-name-picker" not in names


@pytest.mark.django_db
def test_eval_usage_template_list_excludes_deleted_and_other_org_log_sources(
    auth_client, user, workspace
):
    active = _create_code_eval_template(
        user.organization, workspace, name="active-eval-usage-list"
    )
    deleted = _create_code_eval_template(
        user.organization, workspace, name="deleted-eval-usage-list"
    )
    deleted.deleted = True
    deleted.save(update_fields=["deleted"])
    other_org = _create_other_org_template(user, name="other-eval-usage-list")

    for template in (active, deleted, other_org):
        APICallLog.objects.create(
            organization=user.organization,
            workspace=workspace,
            status=APICallStatusChoices.SUCCESS.value,
            cost=0,
            source=SourceChoices.EVAL_PLAYGROUND.value,
            source_id=str(template.id),
            config=json.dumps({"output": {"output": True}}),
        )

    response = auth_client.post(
        "/model-hub/get-eval-templates",
        {
            "search_text": "eval-usage-list",
            "current_page_index": 0,
            "page_size": 10,
        },
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    names = {row["eval_template_name"] for row in response.data["result"]["row_data"]}
    assert "active-eval-usage-list" in names
    assert "deleted-eval-usage-list" not in names
    assert "other-eval-usage-list" not in names


def test_eval_logs_table_query_serializer_parses_search_object():
    template_id = "11111111-1111-4111-8111-111111111111"

    serializer = EvalApiLogTableQuerySerializer(
        data={
            "eval_template_id": template_id,
            "source": "eval_playground",
            "current_page_index": 0,
            "page_size": 10,
            "search": json.dumps({"key": "needle", "type": ["text"]}),
        }
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["search"] == {
        "key": "needle",
        "type": ["text"],
    }


@pytest.mark.django_db
def test_eval_logs_table_uses_log_mappings_when_template_required_keys_empty(
    user, workspace
):
    template = _create_code_eval_template(
        user.organization, workspace, name="same-org-log-table-fallback"
    )
    template.config["required_keys"] = []
    template.save(update_fields=["config"])
    APICallLog.objects.create(
        organization=user.organization,
        workspace=workspace,
        status=APICallStatusChoices.SUCCESS.value,
        cost=0,
        source=SourceChoices.EVAL_PLAYGROUND.value,
        source_id=str(template.id),
        config=json.dumps(
            {
                "required_keys": ["output", "expected"],
                "mappings": {
                    "output": "needle answer",
                    "expected": "needle answer",
                },
                "output": {"output": True, "reason": "needle reason"},
                "input_data_types": {"output": "text", "expected": "text"},
            }
        ),
    )

    columns = create_column_config_playground(template.id, "eval_playground")
    assert {column["name"] for column in columns} >= {"output", "expected"}


@pytest.mark.django_db
def test_eval_template_bulk_delete_soft_deletes_eval_settings(auth_client, user):
    template = _create_code_eval_template(
        user.organization, name="same-org-log-settings-delete"
    )
    setting = EvalSettings.objects.create(
        eval_id=template.id,
        user=user,
        source="eval_playground",
        column_config=[{"id": "column1", "name": "Evaluation ID"}],
    )

    response = auth_client.post(
        "/model-hub/eval-templates/bulk-delete/",
        {"template_ids": [str(template.id)]},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    template.refresh_from_db()
    setting.refresh_from_db()
    assert template.deleted is True
    assert setting.deleted is True
    assert setting.deleted_at is not None


@pytest.mark.django_db
def test_eval_template_single_delete_soft_deletes_eval_settings(auth_client, user):
    template = _create_code_eval_template(
        user.organization, name="single-delete-cascades-settings"
    )
    setting = EvalSettings.objects.create(
        eval_id=template.id,
        user=user,
        source="eval_playground",
        column_config=[{"id": "column1", "name": "Evaluation ID"}],
    )

    response = auth_client.post(
        "/model-hub/delete-eval-template/",
        {"eval_template_id": str(template.id)},
        format="json",
    )

    assert response.status_code == status.HTTP_200_OK
    template.refresh_from_db()
    setting.refresh_from_db()
    assert template.deleted is True
    assert setting.deleted is True
    assert setting.deleted_at is not None
