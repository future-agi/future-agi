"""W10-ANNOTATIONS-L04: restoring an archived queue after name reuse."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.db import IntegrityError
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.annotation_queues import AnnotationQueue, AnnotationQueueLabel
from model_hub.tests.test_annotation_queues_api import (
    create_queue,
    get_queue_id,
    queue_detail_url,
    queue_restore_url,
)
from model_hub.views.annotation_queues import _restore_archived_default_queue
from tracer.models.project import Project

pytestmark = pytest.mark.django_db

RESTORE_HELPER = "model_hub.views.annotation_queues._restore_archived_default_queue"
CONFLICT_MESSAGE = (
    "An active queue already uses this name in the same scope. "
    "Rename the active queue using Edit, then return to Archived and restore this queue."
)


def test_restore_name_conflict_then_rename_preserves_original_queue(auth_client):
    assert create_queue(auth_client, name="Review").status_code == 201
    original_id = get_queue_id(auth_client, "Review")
    label_ids = set(
        AnnotationQueueLabel.objects.filter(queue_id=original_id).values_list(
            "label_id", flat=True
        )
    )
    assert label_ids
    assert auth_client.delete(queue_detail_url(original_id)).status_code == 200
    original = AnnotationQueue.all_objects.get(pk=original_id)
    archived_at = original.deleted_at
    assert create_queue(auth_client, name="Review").status_code == 201
    replacement_id = get_queue_id(auth_client, "Review")
    assert str(replacement_id) != str(original_id)

    with patch(RESTORE_HELPER, wraps=_restore_archived_default_queue) as restore:
        response = auth_client.post(queue_restore_url(original_id), {}, format="json")
        restore.assert_not_called()

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.data["code"] == "queue_name_conflict"
    assert response.data["message"] == CONFLICT_MESSAGE
    original.refresh_from_db()
    assert original.deleted is True
    assert original.deleted_at == archived_at
    assert AnnotationQueue.objects.filter(pk=replacement_id, deleted=False).exists()

    renamed = auth_client.patch(
        queue_detail_url(replacement_id), {"name": "Review replacement"}, format="json"
    )
    assert renamed.status_code == status.HTTP_200_OK
    restored = auth_client.post(queue_restore_url(original_id), {}, format="json")
    assert restored.status_code == status.HTTP_200_OK
    original.refresh_from_db()
    assert original.deleted is False
    assert original.deleted_at is None
    assert original.name == "Review"
    assert set(
        AnnotationQueueLabel.objects.filter(queue_id=original_id).values_list(
            "label_id", flat=True
        )
    ) == label_ids


@pytest.mark.parametrize("same_project", [True, False])
def test_restore_name_conflict_respects_project_scope(
    auth_client, organization, workspace, user, same_project
):
    projects = [
        Project.objects.create(
            name=f"Restore project {index}",
            organization=organization,
            workspace=workspace,
            model_type="GenerativeLLM",
            trace_type="observe",
        )
        for index in range(2)
    ]
    original = AnnotationQueue.objects.create(
        name="Review",
        organization=organization,
        workspace=workspace,
        created_by=user,
        project=projects[0],
        deleted=True,
    )
    AnnotationQueue.objects.create(
        name="Review",
        organization=organization,
        workspace=workspace,
        created_by=user,
        project=projects[0 if same_project else 1],
    )

    response = auth_client.post(queue_restore_url(original.pk), {}, format="json")

    assert response.status_code == (409 if same_project else 200)
    original.refresh_from_db()
    assert original.deleted is same_project
    if same_project:
        assert response.data["code"] == "queue_name_conflict"


def test_restore_checks_org_name_constraint_across_workspaces(
    auth_client, organization, workspace, user
):
    original = AnnotationQueue.objects.create(
        name="Review",
        organization=organization,
        workspace=workspace,
        created_by=user,
        deleted=True,
    )
    other_workspace = Workspace.objects.create(
        name="Other restore workspace",
        organization=organization,
        is_default=False,
        is_active=True,
        created_by=user,
    )
    AnnotationQueue.objects.create(
        name="Review", organization=organization, workspace=other_workspace
    )

    response = auth_client.post(queue_restore_url(original.pk), {}, format="json")

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.data["code"] == "queue_name_conflict"
    assert response.data["message"] == CONFLICT_MESSAGE
    original.refresh_from_db()
    assert original.deleted is True


@pytest.mark.parametrize(
    "constraint_name",
    [
        "unique_active_queue_name_org",
        "unique_active_queue_name_org_project",
        "unique_active_queue_name_org_dataset",
        "unique_active_queue_name_org_agent",
    ],
)
def test_restore_handles_name_collision_after_precheck(
    auth_client, organization, workspace, user, constraint_name
):
    original = AnnotationQueue.objects.create(
        name="Review",
        organization=organization,
        workspace=workspace,
        created_by=user,
        deleted=True,
    )
    # Simulate PostgreSQL rejecting the save after a concurrent name claim.
    driver_error = Exception("private database diagnostic")
    driver_error.diag = SimpleNamespace(constraint_name=constraint_name)
    error = IntegrityError("private database diagnostic")
    error.__cause__ = driver_error
    with patch(RESTORE_HELPER, side_effect=error):
        response = auth_client.post(queue_restore_url(original.pk), {}, format="json")

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.data["code"] == "queue_name_conflict"
    assert response.data["message"] == CONFLICT_MESSAGE
    assert "private database diagnostic" not in str(response.data)
    original.refresh_from_db()
    assert original.deleted is True
