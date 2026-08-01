import json
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
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
from model_hub.serializers.contracts import (
    EvalApiLogIncompleteResponseSerializer,
    EvalApiLogTableQuerySerializer,
)
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


def _create_eval_log(organization, workspace, template, value):
    return APICallLog.objects.create(
        organization=organization,
        workspace=workspace,
        status=APICallStatusChoices.SUCCESS.value,
        cost=0,
        source=SourceChoices.EVAL_PLAYGROUND.value,
        source_id=str(template.id),
        config={
            "required_keys": ["output"],
            "mappings": {"output": value},
            "output": {"output": True, "reason": value},
        },
    )


def _eval_log_table_columns(template):
    return [
        {
            "id": "evaluation_id",
            "name": "Evaluation ID",
            "is_visible": True,
        },
        {
            "id": "output",
            "name": "output",
            "data_type": "text",
            "is_visible": True,
        },
        {
            "id": "eval_result",
            "name": template.name,
            "data_type": "boolean",
            "origin_type": SourceChoices.EVALUATION.value,
            "is_visible": True,
        },
        {
            "id": "created_at",
            "name": "Created At",
            "data_type": "datetime",
            "is_visible": False,
        },
    ]


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
def test_eval_playground_rejects_template_from_another_organization(auth_client, user):
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
def test_eval_logs_table_rejects_template_from_another_workspace(
    auth_client,
    user,
):
    other_workspace = Workspace.objects.create(
        name="other-eval-log-table-workspace",
        organization=user.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    template = _create_code_eval_template(
        user.organization,
        other_workspace,
        name="other-workspace-log-table",
    )

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
def test_eval_log_list_and_detail_scope_global_template_logs_to_workspace(
    auth_client,
    user,
    workspace,
):
    system_template = EvalTemplate.no_workspace_objects.create(
        name="workspace-scoped-system-eval-logs",
        organization=None,
        workspace=None,
        owner=OwnerChoices.SYSTEM.value,
        config={"output": "Pass/Fail", "required_keys": ["output"]},
        visible_ui=True,
    )
    other_workspace = Workspace.objects.create(
        name="other-system-eval-log-workspace",
        organization=user.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )

    def create_log(log_workspace, value):
        return APICallLog.objects.create(
            organization=user.organization,
            workspace=log_workspace,
            status=APICallStatusChoices.SUCCESS.value,
            cost=0,
            source=SourceChoices.EVAL_PLAYGROUND.value,
            source_id=str(system_template.id),
            config={
                "required_keys": ["output"],
                "mappings": {"output": value},
                "output": {"output": True},
            },
        )

    own_log = create_log(workspace, "own")
    legacy_log = create_log(None, "legacy")
    other_log = create_log(other_workspace, "other")

    list_response = auth_client.get(
        "/model-hub/get-eval-logs-details",
        {
            "eval_template_id": str(system_template.id),
            "source": "eval_playground",
            "current_page_index": 0,
            "page_size": 10,
        },
    )

    assert list_response.status_code == status.HTTP_200_OK
    visible_ids = {str(row["log_id"]) for row in list_response.data["result"]["table"]}
    assert visible_ids == {str(own_log.log_id), str(legacy_log.log_id)}

    hidden_detail = auth_client.get(
        "/model-hub/get-eval-logs",
        {"log_id": str(other_log.log_id)},
    )
    own_detail = auth_client.get(
        "/model-hub/get-eval-logs",
        {"log_id": str(own_log.log_id)},
    )
    legacy_detail = auth_client.get(
        "/model-hub/get-eval-logs",
        {"log_id": str(legacy_log.log_id)},
    )

    assert hidden_detail.status_code == status.HTTP_400_BAD_REQUEST
    assert own_detail.status_code == status.HTTP_200_OK
    assert legacy_detail.status_code == status.HTTP_200_OK

    from conftest import WorkspaceAwareAPIClient

    other_client = WorkspaceAwareAPIClient()
    other_client.force_authenticate(user=user)
    other_client.set_workspace(other_workspace)
    other_list = other_client.get(
        "/model-hub/get-eval-logs-details",
        {
            "eval_template_id": str(system_template.id),
            "source": "eval_playground",
            "current_page_index": 0,
            "page_size": 10,
        },
    )
    other_visible_ids = {
        str(row["log_id"]) for row in other_list.data["result"]["table"]
    }
    assert other_list.status_code == status.HTTP_200_OK
    assert other_visible_ids == {str(other_log.log_id)}
    assert (
        other_client.get(
            "/model-hub/get-eval-logs",
            {"log_id": str(own_log.log_id)},
        ).status_code
        == status.HTTP_400_BAD_REQUEST
    )
    assert (
        other_client.get(
            "/model-hub/get-eval-logs",
            {"log_id": str(other_log.log_id)},
        ).status_code
        == status.HTTP_200_OK
    )
    other_client.stop_workspace_injection()


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
    hidden = _create_code_eval_template(
        user.organization, workspace, name="hidden-eval-name-picker"
    )
    hidden.visible_ui = False
    hidden.save(update_fields=["visible_ui"])
    other_workspace = Workspace.objects.create(
        name="other-eval-name-picker-workspace",
        organization=user.organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    other_workspace_template = _create_code_eval_template(
        user.organization,
        other_workspace,
        name="other-workspace-eval-name-picker",
    )
    unused_system = EvalTemplate.no_workspace_objects.create(
        name="unused-system-eval-name-picker",
        description=None,
        organization=None,
        workspace=None,
        owner=OwnerChoices.SYSTEM.value,
        config={"output": "Pass/Fail"},
        visible_ui=True,
    )

    for template in (
        active,
        deleted,
        other_org,
        hidden,
        other_workspace_template,
    ):
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
    assert "hidden-eval-name-picker" not in names
    assert "other-workspace-eval-name-picker" not in names
    assert "unused-system-eval-name-picker" in names
    system_result = next(
        row for row in response.data["result"] if row["id"] == str(unused_system.id)
    )
    assert system_result["description"] == ""


@pytest.mark.django_db
def test_eval_template_name_picker_does_not_query_api_call_logs(
    auth_client, user, workspace
):
    _create_code_eval_template(
        user.organization, workspace, name="no-usage-query-eval-name-picker"
    )

    with CaptureQueriesContext(connection) as queries:
        response = auth_client.post(
            "/model-hub/get-eval-template-names",
            {"search_text": "no-usage-query-eval-name-picker"},
            format="json",
        )

    assert response.status_code == status.HTTP_200_OK
    usage_table = APICallLog._meta.db_table.lower()
    assert all(usage_table not in query["sql"].lower() for query in queries)


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
def test_eval_logs_table_caps_candidates_without_counting_full_history(
    auth_client, user, workspace, monkeypatch
):
    from model_hub.views import separate_evals

    template = _create_code_eval_template(
        user.organization, workspace, name="bounded-eval-log-table"
    )
    EvalSettings.objects.create(
        eval_id=template.id,
        user=user,
        source="eval_playground",
        column_config=[
            {"id": "column1", "name": "Evaluation ID"},
            {"id": "column2", "name": "Created At"},
        ],
    )
    APICallLog.objects.bulk_create(
        [
            APICallLog(
                organization=user.organization,
                workspace=workspace,
                status=APICallStatusChoices.SUCCESS.value,
                cost=0,
                source=SourceChoices.EVAL_PLAYGROUND.value,
                source_id=str(template.id),
                config={"output": {"output": True}},
            )
            for _ in range(3)
        ]
    )
    monkeypatch.setattr(separate_evals, "_EVAL_LOG_CANDIDATE_LIMIT", 2)

    with CaptureQueriesContext(connection) as queries:
        response = auth_client.get(
            "/model-hub/get-eval-logs-details",
            {
                "eval_template_id": str(template.id),
                "source": "eval_playground",
                "current_page_index": 0,
                "page_size": 2,
            },
        )

    assert response.status_code == status.HTTP_200_OK
    result = response.data["result"]
    assert len(result["table"]) == 2
    assert result["metadata"] == {
        "total_rows": 2,
        "total_pages": 1,
        "total_rows_is_lower_bound": True,
        "query_complete": False,
        "query_status": "bounded",
        "query_error_code": "candidate_limit_reached",
        "candidate_limit": 2,
        "candidate_rows_scanned": 2,
    }
    log_selects = [
        query["sql"]
        for query in queries
        if "usage_apicalllog" in query["sql"].lower()
        and query["sql"].lstrip().lower().startswith("select")
    ]
    assert len(log_selects) == 1
    assert "COUNT(" not in log_selects[0].upper()
    assert "LIMIT 3" in log_selects[0].upper()


@pytest.mark.django_db
def test_eval_logs_table_pushes_supported_sort_before_candidate_limit(
    auth_client,
    user,
    workspace,
    monkeypatch,
):
    from model_hub.views import separate_evals

    template = _create_code_eval_template(
        user.organization,
        workspace,
        name="pre-limit-sort-eval-logs",
    )
    EvalSettings.objects.create(
        eval_id=template.id,
        user=user,
        source="eval_playground",
        column_config=_eval_log_table_columns(template),
    )
    now = timezone.now()
    newest = _create_eval_log(user.organization, workspace, template, "newest")
    middle = _create_eval_log(user.organization, workspace, template, "middle")
    oldest = _create_eval_log(user.organization, workspace, template, "oldest")
    APICallLog.objects.filter(pk=newest.pk).update(created_at=now)
    APICallLog.objects.filter(pk=middle.pk).update(created_at=now - timedelta(hours=1))
    APICallLog.objects.filter(pk=oldest.pk).update(created_at=now - timedelta(hours=2))
    monkeypatch.setattr(separate_evals, "_EVAL_LOG_CANDIDATE_LIMIT", 2)

    response = auth_client.get(
        "/model-hub/get-eval-logs-details",
        {
            "eval_template_id": str(template.id),
            "source": "eval_playground",
            "current_page_index": 0,
            "page_size": 2,
            "sort": json.dumps([{"column_id": "created_at", "type": "ascending"}]),
        },
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.data["result"]
    assert [str(row["log_id"]) for row in result["table"]] == [
        str(oldest.log_id),
        str(middle.log_id),
    ]
    assert result["metadata"]["query_status"] == "bounded"


@pytest.mark.django_db
def test_eval_logs_table_pushes_created_at_filter_before_candidate_limit(
    auth_client,
    user,
    workspace,
    monkeypatch,
):
    from model_hub.views import separate_evals

    template = _create_code_eval_template(
        user.organization,
        workspace,
        name="pre-limit-filter-eval-logs",
    )
    EvalSettings.objects.create(
        eval_id=template.id,
        user=user,
        source="eval_playground",
        column_config=_eval_log_table_columns(template),
    )
    now = timezone.now()
    newest = _create_eval_log(user.organization, workspace, template, "newest")
    middle = _create_eval_log(user.organization, workspace, template, "middle")
    oldest = _create_eval_log(user.organization, workspace, template, "oldest")
    APICallLog.objects.filter(pk=newest.pk).update(created_at=now)
    APICallLog.objects.filter(pk=middle.pk).update(created_at=now - timedelta(hours=1))
    APICallLog.objects.filter(pk=oldest.pk).update(created_at=now - timedelta(hours=2))
    monkeypatch.setattr(separate_evals, "_EVAL_LOG_CANDIDATE_LIMIT", 2)

    response = auth_client.get(
        "/model-hub/get-eval-logs-details",
        {
            "eval_template_id": str(template.id),
            "source": "eval_playground",
            "current_page_index": 0,
            "page_size": 2,
            "filters": json.dumps(
                [
                    {
                        "column_id": "created_at",
                        "filter_config": {
                            "filter_type": "datetime",
                            "filter_op": "less_than",
                            "filter_value": (
                                now - timedelta(hours=1, minutes=30)
                            ).isoformat(),
                        },
                    }
                ]
            ),
        },
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.data["result"]
    assert [str(row["log_id"]) for row in result["table"]] == [str(oldest.log_id)]
    assert result["metadata"]["query_complete"] is True


@pytest.mark.django_db
def test_eval_logs_table_returns_retryable_incomplete_for_large_search(
    auth_client,
    user,
    workspace,
    monkeypatch,
):
    from model_hub.views import separate_evals

    template = _create_code_eval_template(
        user.organization,
        workspace,
        name="bounded-search-eval-logs",
    )
    EvalSettings.objects.create(
        eval_id=template.id,
        user=user,
        source="eval_playground",
        column_config=_eval_log_table_columns(template),
    )
    for value in ("needle-a", "other", "needle-b"):
        _create_eval_log(user.organization, workspace, template, value)
    monkeypatch.setattr(separate_evals, "_EVAL_LOG_CANDIDATE_LIMIT", 2)

    response = auth_client.get(
        "/model-hub/get-eval-logs-details",
        {
            "eval_template_id": str(template.id),
            "source": "eval_playground",
            "current_page_index": 0,
            "page_size": 2,
            "search": json.dumps({"key": "needle", "type": ["text"]}),
        },
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    error = response.data["result"]
    assert error["error_code"] == "eval_log_query_incomplete"
    assert error["retryable"] is True
    assert error["query_complete"] is False
    assert error["query_status"] == "incomplete"
    assert error["reason"] == "post_processing_exceeds_candidate_limit"
    assert error["unsupported_operations"] == ["search"]
    assert error["candidate_limit"] == 2
    serializer = EvalApiLogIncompleteResponseSerializer(data=response.data)
    assert serializer.is_valid(), serializer.errors


@pytest.mark.django_db
def test_eval_logs_table_applies_dynamic_search_when_scoped_set_is_complete(
    auth_client,
    user,
    workspace,
    monkeypatch,
):
    from model_hub.views import separate_evals

    template = _create_code_eval_template(
        user.organization,
        workspace,
        name="complete-search-eval-logs",
    )
    EvalSettings.objects.create(
        eval_id=template.id,
        user=user,
        source="eval_playground",
        column_config=_eval_log_table_columns(template),
    )
    matching = _create_eval_log(
        user.organization,
        workspace,
        template,
        "needle",
    )
    _create_eval_log(user.organization, workspace, template, "other")
    monkeypatch.setattr(separate_evals, "_EVAL_LOG_CANDIDATE_LIMIT", 2)

    response = auth_client.get(
        "/model-hub/get-eval-logs-details",
        {
            "eval_template_id": str(template.id),
            "source": "eval_playground",
            "current_page_index": 0,
            "page_size": 2,
            "search": json.dumps({"key": "needle", "type": ["text"]}),
        },
    )

    assert response.status_code == status.HTTP_200_OK
    result = response.data["result"]
    assert [str(row["log_id"]) for row in result["table"]] == [str(matching.log_id)]
    assert result["metadata"]["query_complete"] is True


@pytest.mark.django_db
def test_eval_logs_table_returns_retryable_incomplete_for_unproven_deep_page(
    auth_client,
    user,
    workspace,
    monkeypatch,
):
    from model_hub.views import separate_evals

    template = _create_code_eval_template(
        user.organization,
        workspace,
        name="bounded-deep-page-eval-logs",
    )
    EvalSettings.objects.create(
        eval_id=template.id,
        user=user,
        source="eval_playground",
        column_config=_eval_log_table_columns(template),
    )
    for value in ("a", "b", "c"):
        _create_eval_log(user.organization, workspace, template, value)
    monkeypatch.setattr(separate_evals, "_EVAL_LOG_CANDIDATE_LIMIT", 2)

    response = auth_client.get(
        "/model-hub/get-eval-logs-details",
        {
            "eval_template_id": str(template.id),
            "source": "eval_playground",
            "current_page_index": 1,
            "page_size": 2,
        },
    )

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    error = response.data["result"]
    assert error["error_code"] == "eval_log_query_incomplete"
    assert error["retryable"] is True
    assert error["reason"] == "page_exceeds_candidate_limit"
    assert error["requested_page"] == 1


@pytest.mark.django_db
def test_eval_logs_table_sanitizes_internal_exception_text(
    auth_client, user, workspace, monkeypatch
):
    from model_hub.views import separate_evals

    template = _create_code_eval_template(
        user.organization, workspace, name="sanitized-eval-log-table"
    )
    monkeypatch.setattr(
        separate_evals,
        "get_column_data",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("secret eval log implementation detail")
        ),
    )

    response = auth_client.get(
        "/model-hub/get-eval-logs-details",
        {
            "eval_template_id": str(template.id),
            "source": "eval_playground",
            "current_page_index": 0,
            "page_size": 10,
        },
    )

    assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    body = json.dumps(response.data)
    assert "secret eval log implementation detail" not in body
    assert "Unable to load evaluation logs" in body


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
