import uuid

from django.db import models

from accounts.models.user import User
from model_hub.models.develop_annotations import AnnotationsLabels
from tfc.utils.base_model import BaseModel
from tracer.models.observation_span import ObservationSpan
from tracer.models.trace import Trace


class TraceAnnotation(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    trace = models.ForeignKey(
        Trace,
        on_delete=models.CASCADE,
        related_name="annotation_labels",
        null=True,
        blank=True,
    )
    annotation_label = models.ForeignKey(
        AnnotationsLabels,
        on_delete=models.CASCADE,
        related_name="trace_annotation",
        blank=False,
        null=False,
    )
    annotation_value = models.CharField(max_length=255, blank=True, null=True)
    observation_span = models.ForeignKey(
        ObservationSpan,
        on_delete=models.CASCADE,
        related_name="trace_annotation",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="trace_annotation",
        null=True,
        blank=True,
    )
    annotation_value_bool = models.BooleanField(null=True, blank=True)
    annotation_value_float = models.FloatField(null=True, blank=True)
    annotation_value_str_list = models.JSONField(default=list, blank=True, null=True)

    updated_by = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return f"Trace Annotation {self.id}"

    class Meta:
        db_table = "trace_annotation"
        ordering = ["-created_at"]

        indexes = [
            # Add this!
            models.Index(fields=["observation_span", "annotation_label", "created_at"]),
            models.Index(fields=["annotation_label", "created_at"]),
            models.Index(fields=["annotation_value_float"]),
            models.Index(fields=["annotation_value_bool"]),
        ]
