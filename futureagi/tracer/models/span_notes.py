import uuid

from django.db import models

from accounts.models.user import User
from tfc.utils.base_model import BaseModel
from tracer.models.observation_span import ObservationSpan


class SpanNotes(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    span = models.ForeignKey(
        ObservationSpan, on_delete=models.CASCADE, related_name="notes"
    )
    notes = models.TextField()
    created_by_user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="span_notes_created"
    )
    created_by_annotator = models.CharField(max_length=255, null=False, blank=False)

    def __str__(self):
        return f"Span Notes {self.id}"
