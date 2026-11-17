"""Annotations added from the trace view stay in the organization of the request.

``POST /tracer/observation-span/add_annotations/`` (the trace-detail drawer and
the Compare drawer's annotate section) finds the span in the organization the
request is for. A person can belong to two organizations: the one stored on
their account (A) and a second one (B) they are working in. When they annotate
a span of B's project from B, the default queue, its item, the score and the
note are B's, and none of A's queues gains an item, a note or a member.

The default-queue helpers give no queue for a project of another organization
than the one they are given, so no caller makes a queue in one organization
over another organization's project.

The test client sets ``request.organization`` from the workspace it is given
(``auth_client.set_workspace``), as the organization header does in use.
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.db.models import Q
from django.utils import timezone
from rest_framework import status

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.ai_model import AIModel
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    QueueItem,
    QueueItemNote,
)
from model_hub.models.choices import (
    AnnotationQueueStatusChoices,
    AnnotationTypeChoices,
)
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.score import Score
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import clear_workspace_context
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.span_notes import SpanNotes
from tracer.models.trace import Trace

ADD_ANNOTATIONS_URL = "/tracer/observation-span/add_annotations/"


def _project(organization, workspace, label):
    # Project.save() runs full_clean(), so the row passes Project.clean().
    return Project.objects.create(
        name=f"{label} Project {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata={},
        config=[],
    )


def _span(project, label):
    trace = Trace.objects.create(
        project=project,
        name=f"{label} Trace",
        input={"prompt": "hello"},
        output={"response": "world"},
    )
    return ObservationSpan.objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name=f"{label} Span",
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=5),
        end_time=timezone.now(),
        input={"messages": [{"role": "user", "content": "Hello"}]},
        output={"choices": [{"message": {"content": "Hi"}}]},
        model="gpt-4",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
        cost=0.001,
        latency_ms=500,
        status="OK",
    )


def _star_label(organization, workspace, project):
    # AnnotationsLabels.save() runs full_clean(), so the row passes its clean().
    return AnnotationsLabels.objects.create(
        name="Quality Rating",
        type=AnnotationTypeChoices.STAR.value,
        settings={"no_of_stars": 5},
        organization=organization,
        workspace=workspace,
        project=project,
    )


def _second_organization(user):
    """Make ``user`` an active member of a second organization, B.

    B is not the organization stored on the account. B gets its own default
    workspace, an observe project with one span, and a star label.
    """
    suffix = uuid.uuid4().hex[:8]
    org = Organization.objects.create(name=f"Second Organization {suffix}")
    workspace = Workspace.no_workspace_objects.create(
        name=f"Second Workspace {suffix}",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    membership = OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=org,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        is_active=True,
    )
    WorkspaceMembership.no_workspace_objects.create(
        user=user,
        workspace=workspace,
        role=OrganizationRoles.WORKSPACE_MEMBER,
        level=Level.WORKSPACE_MEMBER,
        is_active=True,
        organization_membership=membership,
    )
    project = _project(org, workspace, "Second")
    return SimpleNamespace(
        org=org,
        workspace=workspace,
        project=project,
        span=_span(project, "Second"),
        label=_star_label(org, workspace, project),
    )


def _own_queue(organization, workspace, user):
    """A queue the person already manages in their stored organization."""
    return AnnotationQueue.no_workspace_objects.create(
        name=f"Own Queue {uuid.uuid4().hex[:8]}",
        organization=organization,
        workspace=workspace,
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        created_by=user,
    )


def _organization_rows(organization):
    """The ids of every queue, item, note, queue member and score of
    ``organization``, found by the row's organization or its queue's."""

    def ids(queryset):
        return set(queryset.values_list("id", flat=True))

    return {
        "queues": ids(
            AnnotationQueue.no_workspace_objects.filter(organization=organization)
        ),
        "items": ids(
            QueueItem.no_workspace_objects.filter(
                Q(organization=organization) | Q(queue__organization=organization)
            )
        ),
        "notes": ids(
            QueueItemNote.no_workspace_objects.filter(
                Q(organization=organization)
                | Q(queue_item__queue__organization=organization)
            )
        ),
        "queue_members": ids(
            AnnotationQueueAnnotator.no_workspace_objects.filter(
                queue__organization=organization
            )
        ),
        "scores": ids(Score.no_workspace_objects.filter(organization=organization)),
    }


def _annotate(client, span, label, value, notes):
    return client.post(
        ADD_ANNOTATIONS_URL,
        {
            "observation_span_id": span.id,
            "annotation_values": {str(label.id): value},
            "notes": notes,
        },
        format="json",
    )


@pytest.mark.django_db
@pytest.mark.integration
class TestAddAnnotationsStayInRequestOrganization:
    def test_member_working_in_second_organization_annotates_there(
        self, auth_client, user, organization, workspace
    ):
        assert user.organization_id == organization.id
        second = _second_organization(user)
        assert second.org.id != user.organization_id
        _own_queue(organization, workspace, user)
        before = _organization_rows(organization)

        auth_client.set_workspace(second.workspace)
        response = _annotate(
            auth_client,
            second.span,
            second.label,
            4,
            "note written in the second organization",
        )

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["result"]["success_labels"] == [str(second.label.id)]

        default_queues = list(
            AnnotationQueue.no_workspace_objects.filter(
                project=second.project, is_default=True
            )
        )
        assert len(default_queues) == 1
        queue = default_queues[0]
        assert queue.organization_id == second.org.id, (
            "the default queue for the second organization's project was made "
            "in the organization stored on the account"
        )

        item = QueueItem.no_workspace_objects.get(observation_span_id=second.span.id)
        assert item.queue_id == queue.id
        assert item.organization_id == second.org.id
        assert item.project_id == second.project.id

        score = Score.no_workspace_objects.get(
            observation_span_id=second.span.id,
            label_id=second.label.id,
            annotator_id=user.id,
        )
        assert score.organization_id == second.org.id
        assert score.queue_item_id == item.id

        note = QueueItemNote.no_workspace_objects.get(queue_item=item, annotator=user)
        assert note.organization_id == second.org.id
        assert note.notes == "note written in the second organization"

        assert _organization_rows(organization) == before

    def test_member_working_in_stored_organization_cannot_annotate_other_span(
        self, auth_client, user, organization, workspace
    ):
        second = _second_organization(user)
        own_label = _star_label(organization, workspace, None)
        _own_queue(organization, workspace, user)
        before = _organization_rows(organization)

        # auth_client works in the stored organization's workspace.
        response = _annotate(
            auth_client,
            second.span,
            own_label,
            4,
            "note written in the stored organization",
        )

        # As for any span outside the request's organization: the span is
        # not found and the endpoint answers 400, writing nothing.
        assert response.status_code == status.HTTP_400_BAD_REQUEST, response.data
        assert not AnnotationQueue.no_workspace_objects.filter(
            project=second.project
        ).exists()
        assert not QueueItem.no_workspace_objects.filter(
            observation_span_id=second.span.id
        ).exists()
        assert not Score.no_workspace_objects.filter(
            observation_span_id=second.span.id
        ).exists()
        assert not SpanNotes.no_workspace_objects.filter(
            span_id=second.span.id
        ).exists()
        assert _organization_rows(organization) == before

    def test_default_queue_for_another_organizations_project_is_refused(
        self, user, organization, workspace
    ):
        from model_hub.utils.annotation_queue_helpers import (
            _resolve_default_queue_scope,
            resolve_default_queue_for_source,
            resolve_default_queue_item_for_source,
            source_project,
        )

        second = _second_organization(user)
        _own_queue(organization, workspace, user)
        before = _organization_rows(organization)
        # Called as a management command calls it: no request workspace.
        clear_workspace_context()

        item = resolve_default_queue_item_for_source(
            "observation_span", second.span, organization, user
        )

        assert item is None, (
            "a default queue item was made in the organization stored on the "
            "account over the second organization's project"
        )
        assert (
            resolve_default_queue_for_source(
                "observation_span", second.span, organization, user
            )
            is None
        )
        assert _resolve_default_queue_scope(
            "observation_span", second.span, organization
        ) == (None, None)
        assert source_project(second.span, organization) is None
        assert not AnnotationQueue.no_workspace_objects.filter(
            project=second.project
        ).exists()
        assert not QueueItem.no_workspace_objects.filter(
            observation_span_id=second.span.id
        ).exists()
        assert _organization_rows(organization) == before

    def test_default_queue_resolves_in_the_projects_own_organization(
        self, user, organization, workspace
    ):
        from model_hub.utils.annotation_queue_helpers import (
            resolve_default_queue_item_for_source,
            source_project,
        )

        second = _second_organization(user)
        clear_workspace_context()

        assert source_project(second.span, second.org) == second.project
        # Callers that pass no organization keep the span's own project.
        assert source_project(second.span, None) == second.project

        item = resolve_default_queue_item_for_source(
            "observation_span", second.span, second.org, user
        )

        assert item is not None
        assert item.organization_id == second.org.id
        assert item.queue.organization_id == second.org.id
        assert item.queue.project_id == second.project.id
        assert item.queue.is_default is True

    def test_single_organization_member_annotates_as_before(
        self, auth_client, user, organization, workspace
    ):
        project = _project(organization, workspace, "Own")
        span = _span(project, "Own")
        label = _star_label(organization, workspace, project)

        response = _annotate(auth_client, span, label, 4, "own note")

        assert response.status_code == status.HTTP_200_OK, response.data
        assert response.data["result"]["success_labels"] == [str(label.id)]
        queue = AnnotationQueue.no_workspace_objects.get(
            project=project, is_default=True
        )
        assert queue.organization_id == organization.id
        item = QueueItem.no_workspace_objects.get(observation_span_id=span.id)
        assert item.queue_id == queue.id
        assert item.organization_id == organization.id
        score = Score.no_workspace_objects.get(
            observation_span_id=span.id, label_id=label.id, annotator_id=user.id
        )
        assert score.organization_id == organization.id
        assert score.queue_item_id == item.id
        assert score.value == {"rating": 4.0}
        note = QueueItemNote.no_workspace_objects.get(queue_item=item, annotator=user)
        assert note.organization_id == organization.id
        assert note.notes == "own note"
        assert (
            SpanNotes.no_workspace_objects.get(
                span_id=span.id, created_by_user=user
            ).notes
            == "own note"
        )
        assert AnnotationQueueAnnotator.no_workspace_objects.filter(
            queue=queue, user=user
        ).exists()

    def test_single_organization_default_queue_is_reused(
        self, auth_client, user, organization, workspace
    ):
        project = _project(organization, workspace, "Own")
        span = _span(project, "Own")
        label = _star_label(organization, workspace, project)

        first = _annotate(auth_client, span, label, 4, "first note")
        second = _annotate(auth_client, span, label, 2, "second note")

        assert first.status_code == status.HTTP_200_OK, first.data
        assert second.status_code == status.HTTP_200_OK, second.data
        queues = list(
            AnnotationQueue.no_workspace_objects.filter(
                project=project, is_default=True
            )
        )
        assert len(queues) == 1
        assert queues[0].organization_id == organization.id
        items = list(QueueItem.no_workspace_objects.filter(observation_span_id=span.id))
        assert len(items) == 1
        assert items[0].queue_id == queues[0].id
        scores = list(
            Score.no_workspace_objects.filter(
                observation_span_id=span.id, label_id=label.id, annotator_id=user.id
            )
        )
        assert len(scores) == 1
        assert scores[0].value == {"rating": 2.0}
        notes = list(
            QueueItemNote.no_workspace_objects.filter(
                queue_item=items[0], annotator=user
            )
        )
        assert len(notes) == 1
        assert notes[0].notes == "second note"
        assert (
            AnnotationQueueAnnotator.no_workspace_objects.filter(
                queue=queues[0], user=user
            ).count()
            == 1
        )
