"""The span notes backfill copies a note only where its organization reads it.

``backfill_span_notes_to_queue_items`` copies each legacy span note onto the
default queue's item for its span, in the organization of the project whose
Postgres row holds the span id. A span id can be held by projects of several
organizations (another organization's batch can create a row with an id it
learned), so that project does not say whose a note is.

The command copies a note only when the queue item's organization reads it by
the span notes rule (``span_notes_readable_in``), checked before the default
queue is resolved. It adds to the organization's default queue only an author
who is an active member there; anyone else's note is copied, or not, without
the author joining any of the organization's queues.
"""

import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.utils import timezone

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace, WorkspaceMembership
from model_hub.models.ai_model import AIModel
from model_hub.models.annotation_queues import (
    AnnotationQueue,
    AnnotationQueueAnnotator,
    AnnotationQueueStatusChoices,
    QueueItemNote,
)
from model_hub.models.choices import AnnotatorRole
from tfc.constants.levels import Level
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import (
    clear_workspace_context,
    set_workspace_context,
)
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.span_notes import SpanNotes, span_notes_readable_in
from tracer.models.trace import Trace

pytestmark = pytest.mark.django_db

COMMAND = "backfill_span_notes_to_queue_items"
OWNER_NOTE = "owner: refund approved after the second call"
SENDER_NOTE = "sender: escalated to billing"


def _organization(label):
    """An organization with its owner, default workspace and Observe project."""
    clear_workspace_context()
    org = Organization.objects.create(name=f"Synthetic {label}")
    set_workspace_context(organization=org)
    user = User.objects.create_user(
        email=f"lfbackfill-{label}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="synthetic-password",
        name=f"Synthetic {label}",
        organization=org,
        organization_role=OrganizationRoles.OWNER,
    )
    membership, _ = OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=org,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    workspace = Workspace.objects.create(
        name=f"Synthetic {label} workspace",
        organization=org,
        is_default=True,
        is_active=True,
        created_by=user,
    )
    WorkspaceMembership.no_workspace_objects.get_or_create(
        user=user,
        workspace=workspace,
        defaults={
            "role": "Workspace Owner",
            "level": Level.OWNER,
            "is_active": True,
            "organization_membership": membership,
        },
    )
    set_workspace_context(workspace=workspace, organization=org, user=user)
    project = Project.objects.create(
        name=f"Synthetic {label} observe",
        organization=org,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
        metadata={},
    )
    clear_workspace_context()
    return SimpleNamespace(org=org, user=user, workspace=workspace, project=project)


@pytest.fixture
def owner(db):
    """The organization whose member wrote the note."""
    yield _organization("owner")
    clear_workspace_context()


@pytest.fixture
def sender(db):
    """The organization whose project holds the span's Postgres row."""
    yield _organization("sender")
    clear_workspace_context()


def _user(label, organization=None):
    return User.objects.create_user(
        email=f"lfbackfill-{label}-{uuid.uuid4().hex[:8]}@futureagi.com",
        password="synthetic-password",
        name=f"Synthetic {label}",
        organization=organization,
        organization_role=OrganizationRoles.MEMBER,
    )


def _membership(user, organization, **fields):
    return OrganizationMembership.no_workspace_objects.create(
        user=user,
        organization=organization.org,
        role=OrganizationRoles.MEMBER,
        level=Level.MEMBER,
        **fields,
    )


def _span(organization):
    """A span with a Postgres row in ``organization``'s project."""
    trace = Trace.no_workspace_objects.create(
        project=organization.project, name="Synthetic trace"
    )
    return ObservationSpan.no_workspace_objects.create(
        id=f"lfbackfill-{uuid.uuid4().hex[:16]}",
        project=organization.project,
        trace=trace,
        name="Synthetic step",
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=5),
        end_time=timezone.now(),
        status="OK",
    )


def _note(author, span, text):
    return SpanNotes.no_workspace_objects.create(
        span=span,
        notes=text,
        created_by_user=author,
        created_by_annotator=author.email,
    )


def _external_queue(organization):
    """A queue of ``organization`` its owner made (not a default queue)."""
    return AnnotationQueue.no_workspace_objects.create(
        name=f"Synthetic external review {uuid.uuid4().hex[:8]}",
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        organization=organization.org,
        workspace=organization.workspace,
        created_by=organization.user,
    )


def _default_queue(organization):
    """The default queue of ``organization``'s project, made by its owner."""
    return AnnotationQueue.no_workspace_objects.create(
        is_default=True,
        name=f"Default - {organization.project.name}",
        description=f"Default annotation queue for {organization.project.name}",
        status=AnnotationQueueStatusChoices.ACTIVE.value,
        organization=organization.org,
        workspace=organization.workspace,
        project=organization.project,
        created_by=organization.user,
    )


def _queue_roles(organization):
    """Every annotator row of ``organization``'s queues, in any state."""
    return sorted(
        (str(row.queue_id), str(row.user_id), row.role, row.deleted)
        for row in AnnotationQueueAnnotator._base_manager.filter(
            queue__organization=organization.org
        )
    )


def _enrolments(user, organization):
    """``user``'s annotator rows in ``organization``'s queues, in any state."""
    return sorted(
        (str(row.queue_id), row.role, row.deleted)
        for row in AnnotationQueueAnnotator._base_manager.filter(
            user=user, queue__organization=organization.org
        )
    )


def _copied_notes(organization):
    """The queue item notes ``organization`` holds, as (author id, text)."""
    return sorted(
        (str(note.annotator_id), note.notes)
        for note in QueueItemNote._base_manager.filter(organization=organization.org)
    )


def _backfill():
    """Run the command as an operator does: with no request context."""
    clear_workspace_context()
    call_command(COMMAND)


def test_a_note_by_another_organizations_member_is_not_copied_or_enrolled(
    owner, sender
):
    # The sender's project holds the span's Postgres row (a span id both
    # organizations hold); the owner's member wrote a note on that id.
    span = _span(sender)
    _default_queue(sender)
    note = _note(owner.user, span, OWNER_NOTE)
    roles_before = _queue_roles(sender)

    _backfill()

    assert _copied_notes(sender) == []
    assert _enrolments(owner.user, sender) == []
    assert _queue_roles(sender) == roles_before
    # So the run opens none of that author's notes to the sender.
    assert not (
        SpanNotes._base_manager.filter(
            span_notes_readable_in(sender.org), pk=note.pk
        ).exists()
    )


def test_a_note_by_the_organizations_own_member_is_still_copied(sender):
    member = _user("member", sender.org)
    _membership(member, sender)
    span = _span(sender)
    _note(member, span, SENDER_NOTE)

    _backfill()

    copied = QueueItemNote._base_manager.get(organization=sender.org, annotator=member)
    assert copied.notes == SENDER_NOTE
    assert copied.queue_item.observation_span_id == span.id
    assert copied.queue_item.queue.is_default is True
    assert copied.queue_item.queue.project_id == sender.project.id
    # As before, the member is the default queue's manager.
    assert _enrolments(member, sender) == [
        (str(copied.queue_item.queue_id), AnnotatorRole.MANAGER.value, False)
    ]


def test_an_annotator_only_authors_note_is_copied_without_joining_a_queue(sender):
    # An account with no membership anywhere, annotating one of the sender's
    # own queues. The sender reads its notes. The project has no default
    # queue yet, so the run makes one.
    author = _user("annotator")
    queue = _external_queue(sender)
    AnnotationQueueAnnotator.no_workspace_objects.create(queue=queue, user=author)
    span = _span(sender)
    _note(author, span, SENDER_NOTE)
    enrolments_before = _enrolments(author, sender)

    _backfill()

    assert _copied_notes(sender) == [(str(author.id), SENDER_NOTE)]
    assert _enrolments(author, sender) == enrolments_before


def test_a_departed_members_note_is_copied_without_joining_a_queue(sender):
    # A member who has left: the membership row stays, deactivated. The
    # sender reads their notes. The project has no default queue yet, so the
    # run makes one.
    author = _user("departed", sender.org)
    _membership(author, sender, is_active=False)
    span = _span(sender)
    _note(author, span, SENDER_NOTE)

    _backfill()

    assert _copied_notes(sender) == [(str(author.id), SENDER_NOTE)]
    assert _enrolments(author, sender) == []
