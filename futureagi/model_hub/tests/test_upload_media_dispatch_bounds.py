"""Bounded dispatch failures are visible and only operators can redrive them."""

import json
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.core.management import call_command, get_commands
from django.core.management.base import CommandError
from django.utils import timezone

from model_hub.tests import test_upload_readiness as upload_fixtures
from model_hub.tests.test_upload_readiness import run_upload
from model_hub.views.datasets.create import file_upload

pytestmark = pytest.mark.django_db(transaction=True)
upload = upload_fixtures.upload


@pytest.fixture
def pending(upload):
    upload.dataset_config.update(
        file_processing_status="completed",
        file_processing_completed_at=timezone.now().isoformat(),
        file_media_dispatch_status="pending",
    )
    upload.save(update_fields=["dataset_config"])
    return upload


def progress_url(dataset):
    return f"/model-hub/develops/dataset-creation-progress/{dataset.id}/"


def config(dataset):
    dataset.refresh_from_db()
    return dataset.dataset_config


def fail_dispatch(dataset, exception):
    with patch.object(
        file_upload.process_media_uploads, "delay", side_effect=exception
    ):
        with pytest.raises(type(exception)):
            run_upload(dataset)


def operator_retry(dataset, organization_id=None):
    assert "retry_dataset_media_dispatch" in get_commands(), "operator redrive missing"
    call_command(
        "retry_dataset_media_dispatch",
        dataset_id=str(dataset.id),
        org_id=str(organization_id or dataset.organization_id),
        stdout=StringIO(),
    )


def test_each_failure_records_attempt_and_only_exception_class(pending):
    for attempt, exception in enumerate(
        [RuntimeError("private-address-and-token"), TimeoutError("private-file-url")], 1
    ):
        fail_dispatch(pending, exception)
        state = config(pending)
        assert state.get("file_media_dispatch_attempts") == attempt
        assert state.get("file_media_dispatch_total_failures") == attempt
        assert (
            state.get("file_media_dispatch_last_error_class")
            == type(exception).__name__
        )
        assert state.get("file_media_dispatch_first_failed_at")
        assert state["file_media_dispatch_status"] == "pending"
        assert "private-" not in json.dumps(state)
        assert state["file_processing_status"] == "completed"


def test_three_failures_exhaust_budget_and_redelivery_does_not_dispatch(pending):
    from model_hub.services.file_media_dispatch import MAX_MEDIA_DISPATCH_ATTEMPTS

    assert MAX_MEDIA_DISPATCH_ATTEMPTS == 3
    for _ in range(MAX_MEDIA_DISPATCH_ATTEMPTS):
        fail_dispatch(pending, RuntimeError("private-detail"))
    state = config(pending)
    assert state["file_media_dispatch_status"] == "failed"
    assert state.get("file_media_dispatch_attempts") == 3
    with patch.object(file_upload.process_media_uploads, "delay") as dispatch:
        run_upload(pending)
    dispatch.assert_not_called()


@pytest.mark.parametrize("via", ["progress", "redelivery"])
def test_stale_pending_reaches_visible_failed_without_another_dispatch(
    pending, auth_client, via
):
    pending.dataset_config.update(
        file_media_dispatch_attempts=1,
        file_media_dispatch_first_failed_at=(
            timezone.now() - timedelta(hours=24)
        ).isoformat(),
        file_media_dispatch_last_error_class="RuntimeError",
    )
    pending.save(update_fields=["dataset_config"])
    with patch.object(file_upload.process_media_uploads, "delay") as dispatch:
        if via == "redelivery":
            run_upload(pending)
        response = auth_client.get(progress_url(pending))
    dispatch.assert_not_called()
    assert response.status_code == 200, response.data
    assert config(pending)["file_media_dispatch_status"] == "failed"
    result = response.data["result"]
    assert result.get("media_dispatch_status") == "failed"
    assert result.get("media_processing_message") == "Media processing failed"
    assert result.get("media_dispatch_attempts") == 1
    assert result.get("media_dispatch_last_error_class") == "RuntimeError"
    assert result["processing_status"] == "completed"


def test_legacy_pending_without_failure_timestamp_ages_from_publication(
    pending, auth_client
):
    pending.dataset_config["file_processing_completed_at"] = (
        timezone.now() - timedelta(hours=25)
    ).isoformat()
    pending.save(update_fields=["dataset_config"])
    response = auth_client.get(progress_url(pending))
    assert response.status_code == 200
    assert config(pending)["file_media_dispatch_status"] == "failed"


def test_fresh_pending_status_is_explicit_and_never_schedules_on_get(
    pending, auth_client
):
    with patch.object(file_upload.process_media_uploads, "delay") as dispatch:
        response = auth_client.get(progress_url(pending))
    assert response.status_code == 200
    assert response.data["result"].get("media_dispatch_status") == "pending"
    dispatch.assert_not_called()


def test_foreign_progress_does_not_read_or_expire_pending(pending, auth_client):
    from accounts.models.organization import Organization

    other = Organization.objects.create(name="Other tenant")
    pending.organization = other
    pending.workspace = None
    pending.dataset_config["file_media_dispatch_first_failed_at"] = (
        timezone.now() - timedelta(days=2)
    ).isoformat()
    pending.save(update_fields=["organization", "workspace", "dataset_config"])
    response = auth_client.get(progress_url(pending))
    assert response.status_code == 404
    from model_hub.models.develop_dataset import Dataset

    state = Dataset.no_workspace_objects.get(id=pending.id).dataset_config
    assert state["file_media_dispatch_status"] == "pending"


def test_application_viewer_cannot_retrigger_via_progress(pending, auth_client, user):
    from accounts.models.organization_membership import OrganizationMembership
    from accounts.models.workspace import WorkspaceMembership
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    user.organization_role = OrganizationRoles.MEMBER_VIEW_ONLY
    user.save(update_fields=["organization_role"])
    OrganizationMembership.no_workspace_objects.filter(user=user).update(
        role=OrganizationRoles.MEMBER_VIEW_ONLY, level=Level.VIEWER
    )
    WorkspaceMembership.no_workspace_objects.filter(user=user).update(
        role="Workspace Viewer", level=Level.VIEWER
    )
    pending.dataset_config["file_media_dispatch_status"] = "failed"
    pending.save(update_fields=["dataset_config"])
    with patch.object(file_upload.process_media_uploads, "delay") as dispatch:
        response = auth_client.post(progress_url(pending), {}, format="json")
    assert response.status_code in (403, 405)
    dispatch.assert_not_called()
    assert config(pending)["file_media_dispatch_status"] == "failed"


def test_operator_redrive_resets_budget_records_retry_and_dispatches_once(pending):
    pending.dataset_config.update(
        file_media_dispatch_status="failed",
        file_media_dispatch_attempts=3,
        file_media_dispatch_first_failed_at=(
            timezone.now() - timedelta(days=2)
        ).isoformat(),
        file_media_dispatch_last_error_class="RuntimeError",
    )
    pending.save(update_fields=["dataset_config"])
    with patch.object(file_upload.process_media_uploads, "delay") as dispatch:
        operator_retry(pending)
        dispatch.assert_called_once_with(str(pending.id))
        with pytest.raises(CommandError):
            operator_retry(pending)
    state = config(pending)
    assert state["file_media_dispatch_status"] == "dispatched"
    assert state["file_media_dispatch_attempts"] == 0
    assert state.get("file_media_dispatch_retrigger_count") == 1
    assert state.get("file_media_dispatch_last_retriggered_at")
    assert state["file_media_dispatch_last_error_class"] == "RuntimeError"


def test_operator_retry_failure_is_recorded_once_without_leaking_message(pending):
    pending.dataset_config["file_media_dispatch_status"] = "failed"
    pending.save(update_fields=["dataset_config"])
    with patch.object(
        file_upload.process_media_uploads,
        "delay",
        side_effect=TimeoutError("private-url"),
    ) as dispatch:
        with pytest.raises(CommandError) as error:
            operator_retry(pending)
    dispatch.assert_called_once()
    assert "private-url" not in str(error.value)
    state = config(pending)
    assert state["file_media_dispatch_status"] == "pending"
    assert state.get("file_media_dispatch_attempts") == 1
    assert state.get("file_media_dispatch_last_error_class") == "TimeoutError"
    assert state.get("file_media_dispatch_retrigger_count") == 1


@pytest.mark.parametrize("elapsed,expected", [(86399, "pending"), (86400, "failed")])
def test_named_age_bound_at_exact_boundary(
    pending, auth_client, monkeypatch, elapsed, expected
):
    from model_hub.services.file_media_dispatch import MAX_MEDIA_DISPATCH_AGE

    assert MAX_MEDIA_DISPATCH_AGE == timedelta(hours=24)
    now = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: now)
    pending.dataset_config["file_media_dispatch_first_failed_at"] = (
        now - timedelta(seconds=elapsed)
    ).isoformat()
    pending.save(update_fields=["dataset_config"])
    response = auth_client.get(progress_url(pending))
    assert response.status_code == 200
    assert response.data["result"]["media_dispatch_status"] == expected
    from model_hub.serializers.develop_dataset_contracts import (
        DatasetCreationProgressResponseSerializer,
    )

    serializer = DatasetCreationProgressResponseSerializer(data=response.data)
    assert serializer.is_valid(), serializer.errors


def test_retry_budget_is_fresh_but_lifetime_failure_history_is_retained(pending):
    for _ in range(3):
        fail_dispatch(pending, TimeoutError("private-url"))
    with patch.object(
        file_upload.process_media_uploads,
        "delay",
        side_effect=RuntimeError("private-url"),
    ):
        with pytest.raises(CommandError):
            operator_retry(pending)
    state = config(pending)
    assert state["file_media_dispatch_attempts"] == 1
    assert state["file_media_dispatch_total_failures"] == 4
    assert state["file_media_dispatch_status"] == "pending"
    assert state["file_media_dispatch_retrigger_count"] == 1
    assert (
        state["file_media_dispatch_first_failed_at"]
        >= state["file_media_dispatch_last_retriggered_at"]
    )


def test_dispatch_rejects_foreign_org_without_scheduling(pending):
    from model_hub.models.develop_dataset import Dataset
    from model_hub.services.file_media_dispatch import dispatch_import_media

    with patch.object(file_upload.process_media_uploads, "delay") as dispatch:
        with pytest.raises(Dataset.DoesNotExist):
            dispatch_import_media(pending.id, uuid4(), dispatch)
    dispatch.assert_not_called()
    assert config(pending)["file_media_dispatch_status"] == "pending"


def test_operator_redrive_requires_matching_org(pending):
    pending.dataset_config["file_media_dispatch_status"] = "failed"
    pending.save(update_fields=["dataset_config"])
    with patch.object(file_upload.process_media_uploads, "delay") as dispatch:
        with pytest.raises(CommandError):
            operator_retry(pending, uuid4())
    dispatch.assert_not_called()
    assert config(pending)["file_media_dispatch_status"] == "failed"
