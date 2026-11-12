"""Routed permanent deletion with telemetry references absent from Postgres."""

import uuid

import pytest
from django.db.models import QuerySet
from rest_framework import status

from accounts.models import Organization
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueLabel,
    QueueItem,
)
from model_hub.models.choices import AnnotatorRole, QueueItemSourceType
from model_hub.models.develop_annotations import AnnotationsLabels
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.models.trace_session import TraceSession


pytestmark = [
    pytest.mark.django_db,
    # The first URL resolution must happen with no workspace context, or the
    # view querysets stay pinned to the first test's workspace (conftest.py).
    pytest.mark.usefixtures("urlconf_loaded_without_workspace_context"),
]


@pytest.fixture
def managed_queue(organization, workspace, user):
    queue = AnnotationQueue.objects.create(
        name=f"Permanent erase {uuid.uuid4().hex}",
        organization=organization,
        workspace=workspace,
        created_by=user,
        status="active",
    )
    assert AnnotationQueueAnnotator.objects.filter(
        queue=queue, user=user, role=AnnotatorRole.MANAGER.value
    ).exists()
    return queue


def trace_item(queue, trace_id):
    return QueueItem.objects.create(
        queue=queue,
        organization_id=queue.organization_id,
        workspace_id=queue.workspace_id,
        source_type=QueueItemSourceType.TRACE.value,
        trace_id=trace_id,
    )


def permanent_delete(client, queue):
    return client.post(
        f"/model-hub/annotation-queues/{queue.pk}/hard-delete/",
        {"force": True, "confirm_name": queue.name},
        format="json",
    )


def assert_erased(response, queue, item=None):
    assert response.status_code == status.HTTP_200_OK, response.data
    assert response.data["result"]["hard_deleted"] is True
    assert not QuerySet(model=AnnotationQueue).filter(pk=queue.pk).exists()
    assert not QuerySet(model=AnnotationQueueAnnotator).filter(queue_id=queue.pk).exists()
    if item is not None:
        assert not QuerySet(model=QueueItem).filter(pk=item.pk).exists()


def test_p1_missing_trace_item_is_permanently_deleted(auth_client, managed_queue):
    missing_trace = uuid.uuid4()
    assert not QuerySet(model=Trace).filter(pk=missing_trace).exists()
    item = trace_item(managed_queue, missing_trace)

    response = permanent_delete(auth_client, managed_queue)

    assert_erased(response, managed_queue, item)


def test_p2_existing_trace_with_missing_session_is_permanently_deleted(
    auth_client, managed_queue, organization, workspace
):
    project = Project.objects.create(
        name=f"Erase source {uuid.uuid4().hex}",
        organization=organization,
        workspace=workspace,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    missing_session = uuid.uuid4()
    assert not QuerySet(model=TraceSession).filter(pk=missing_session).exists()
    trace = Trace.objects.create(project=project, session_id=missing_session)
    item = trace_item(managed_queue, trace.pk)
    assert QuerySet(model=Trace).filter(pk=trace.pk, session_id=missing_session).exists()

    response = permanent_delete(auth_client, managed_queue)

    assert_erased(response, managed_queue, item)
    assert QuerySet(model=Trace).filter(pk=trace.pk).exists()
    assert QuerySet(model=Project).filter(pk=project.pk).exists()


def test_p3_removed_label_without_items_is_permanently_deleted(
    auth_client, managed_queue, organization, workspace
):
    label = AnnotationsLabels.objects.create(
        name="Removed label",
        type="categorical",
        settings={
            "options": [{"label": "A"}, {"label": "B"}],
            "multi_choice": False,
            "rule_prompt": "",
            "auto_annotate": False,
            "strategy": None,
        },
        organization=organization,
        workspace=workspace,
    )
    url = f"/model-hub/annotation-queues/{managed_queue.pk}/"
    added = auth_client.post(
        f"{url}add-label/", {"label_id": str(label.pk), "required": False}, format="json"
    )
    assert added.status_code == status.HTTP_200_OK, added.data
    binding = QuerySet(model=AnnotationQueueLabel).get(queue=managed_queue, label=label)
    assert binding.deleted is False
    removed = auth_client.post(
        f"{url}remove-label/", {"label_id": str(label.pk)}, format="json"
    )
    assert removed.status_code == status.HTTP_200_OK, removed.data
    binding.refresh_from_db()
    assert binding.deleted is True
    assert not QuerySet(model=QueueItem).filter(queue=managed_queue).exists()

    response = permanent_delete(auth_client, managed_queue)

    assert_erased(response, managed_queue)
    assert not QuerySet(model=AnnotationQueueLabel).filter(pk=binding.pk).exists()
    assert QuerySet(model=AnnotationsLabels).filter(pk=label.pk).exists()


def test_n1_existing_trace_in_another_organization_still_refuses(
    auth_client, managed_queue
):
    other = Organization.objects.create(name=f"Other erase scope {uuid.uuid4().hex}")
    # Bulk insertion leaves workspace NULL rather than inheriting request context.
    project = Project(
        name=f"Foreign source {uuid.uuid4().hex}",
        organization=other,
        model_type="GenerativeLLM",
        trace_type="observe",
    )
    QuerySet(model=Project).bulk_create([project])
    trace = Trace.objects.create(project=project)
    item = trace_item(managed_queue, trace.pk)
    before = list(QuerySet(model=QueueItem).filter(pk=item.pk).values())

    response = permanent_delete(auth_client, managed_queue)

    assert response.status_code == status.HTTP_403_FORBIDDEN, response.data
    assert "outside the authorized organization scope" in str(response.data)
    assert QuerySet(model=AnnotationQueue).filter(pk=managed_queue.pk).exists()
    assert list(QuerySet(model=QueueItem).filter(pk=item.pk).values()) == before
    assert QuerySet(model=Trace).filter(pk=trace.pk).exists()
    assert QuerySet(model=Project).filter(pk=project.pk).exists()
    assert QuerySet(model=Organization).filter(pk=other.pk).exists()
