import uuid

from django.db import models
from django.db.models import Exists, OuterRef, Q

from accounts.models.user import User
from tfc.utils.base_model import BaseModel
from tracer.models.observation_span import ObservationSpan


class SpanNotes(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    span = models.ForeignKey(
        ObservationSpan, on_delete=models.CASCADE, related_name="notes",
        db_constraint=False,  # CH scale: SCALE_ARCHITECTURE.md §9a
    )
    notes = models.TextField()
    created_by_user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="span_notes_created"
    )
    created_by_annotator = models.CharField(max_length=255, null=False, blank=False)

    def __str__(self):
        return f"Span Notes {self.id}"


def span_notes_readable_in(organization):
    """The filter for the span notes ``organization`` may read.

    A note has no organization of its own, and a span id can be held by
    projects of several organizations, so the span a note points at cannot say
    whose the note is. Its author can. A note is readable in ``organization``
    when its author:

    - has, or has had, a membership of any state there (active, deactivated
      or removed), so the notes of a member who left stay with the
      organization;
    - is an account whose stored organization it is and that has no
      membership row there (the accounts that predate memberships), even
      after it joins another organization;
    - or is, or was, an annotator of one of its annotation queues.

    An author who belongs to two organizations writes notes both can read.
    """
    from accounts.models.organization_membership import OrganizationMembership
    from model_hub.models.annotation_queues import AnnotationQueueAnnotator

    organization_id = getattr(organization, "pk", organization)
    if organization_id is None:
        return Q(pk__in=[])
    author = OuterRef("created_by_user_id")
    # The base managers apply no soft-delete and no workspace filter: every
    # row counts, whatever its state.
    memberships_here = OrganizationMembership._base_manager.filter(
        user_id=author, organization_id=organization_id
    )
    annotator_rows = AnnotationQueueAnnotator._base_manager.filter(
        user_id=author, queue__organization_id=organization_id
    )
    return (
        Q(Exists(memberships_here))
        | (
            Q(created_by_user__organization_id=organization_id)
            & ~Q(Exists(memberships_here))
        )
        | Q(Exists(annotator_rows))
    )
