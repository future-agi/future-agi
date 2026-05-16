import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace

# Re-export from the single canonical definition in annotation_queues.
# Kept as an alias so existing imports (e.g. ``from model_hub.models.score
# import SCORE_SOURCE_FK_MAP``) continue to work.
from model_hub.models.annotation_queues import (  # noqa: E402, F401
    SOURCE_TYPE_FK_MAP as SCORE_SOURCE_FK_MAP,
)
from model_hub.models.choices import QueueItemSourceType, ScoreSource
from model_hub.models.develop_annotations import AnnotationsLabels
from tfc.utils.base_model import BaseModel


class Score(BaseModel):
    """
    Universal annotation/score primitive.

    Attaches to exactly ONE source object via source_type discriminator + FK.
    Whether created from an annotation queue, inline annotation on a trace,
    or programmatically via API — it's the same Score object, visible everywhere.

    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # ── Source reference (exactly one FK populated) ──────────────────────
    source_type = models.CharField(
        max_length=30,
        choices=QueueItemSourceType.get_choices(),
    )
    trace = models.ForeignKey(
        "tracer.Trace",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scores",
    )
    observation_span = models.ForeignKey(
        "tracer.ObservationSpan",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scores",
    )
    trace_session = models.ForeignKey(
        "tracer.TraceSession",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scores",
    )
    call_execution = models.ForeignKey(
        "simulate.CallExecution",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scores",
    )
    prototype_run = models.ForeignKey(
        "model_hub.RunPrompter",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scores",
    )
    dataset_row = models.ForeignKey(
        "model_hub.Row",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scores",
    )

    # ── What was scored ──────────────────────────────────────────────────
    label = models.ForeignKey(
        AnnotationsLabels,
        on_delete=models.CASCADE,
        related_name="scores",
    )
    value = models.JSONField()

    # ── Who scored it ────────────────────────────────────────────────────
    annotator = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scores",
    )
    score_source = models.CharField(
        max_length=20,
        choices=ScoreSource.get_choices(),
        default=ScoreSource.HUMAN.value,
    )
    notes = models.TextField(null=True, blank=True)

    # ── Queue provenance (optional) ─────────────────────────────────────
    queue_item = models.ForeignKey(
        "model_hub.QueueItem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="scores",
    )

    # ── Scoping ──────────────────────────────────────────────────────────
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="scores",
    )
    project = models.ForeignKey(
        "model_hub.DevelopAI",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="scores",
    )
    workspace = models.ForeignKey(
        Workspace,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="scores",
    )

    class Meta:
        indexes = [
            models.Index(fields=["trace", "label"]),
            models.Index(fields=["observation_span", "label"]),
            models.Index(fields=["trace_session", "label"]),
            models.Index(fields=["call_execution", "label"]),
            models.Index(fields=["source_type", "label", "created_at"]),
            models.Index(fields=["dataset_row", "label"]),
            models.Index(fields=["prototype_run", "label"]),
            models.Index(fields=["queue_item"]),
        ]
        constraints = [
            # One score per (source, label, annotator, queue_item). Including
            # queue_item lets the same annotator score the same label on the
            # same source independently per queue — required because a trace
            # can belong to multiple annotation queues with overlapping labels
            # and each queue is its own review context.
            models.UniqueConstraint(
                fields=["trace", "label", "annotator", "queue_item"],
                condition=Q(deleted=False, trace__isnull=False),
                name="unique_score_trace_label_annotator",
            ),
            models.UniqueConstraint(
                fields=["observation_span", "label", "annotator", "queue_item"],
                condition=Q(deleted=False, observation_span__isnull=False),
                name="unique_score_span_label_annotator",
            ),
            models.UniqueConstraint(
                fields=["trace_session", "label", "annotator", "queue_item"],
                condition=Q(deleted=False, trace_session__isnull=False),
                name="unique_score_session_label_annotator",
            ),
            models.UniqueConstraint(
                fields=["call_execution", "label", "annotator", "queue_item"],
                condition=Q(deleted=False, call_execution__isnull=False),
                name="unique_score_call_label_annotator",
            ),
            models.UniqueConstraint(
                fields=["prototype_run", "label", "annotator", "queue_item"],
                condition=Q(deleted=False, prototype_run__isnull=False),
                name="unique_score_run_label_annotator",
            ),
            models.UniqueConstraint(
                fields=["dataset_row", "label", "annotator", "queue_item"],
                condition=Q(deleted=False, dataset_row__isnull=False),
                name="unique_score_row_label_annotator",
            ),
            # Duplicate constraints for NULL annotator (PostgreSQL NULL != NULL).
            # Kept at the pre-revamp (source, label) grain rather than adding
            # ``queue_item`` here: null-annotator rows are programmatic/auto
            # scores, ``queue_item`` itself can be NULL for them, and NULL ≠
            # NULL in Postgres uniqueness — so a (source, label, queue_item)
            # constraint would silently allow duplicate auto-scores. Keep
            # the strict (source, label) uniqueness for programmatic scores
            # and accept that they aren't per-queue.
            models.UniqueConstraint(
                fields=["trace", "label"],
                condition=Q(
                    deleted=False,
                    trace__isnull=False,
                    annotator__isnull=True,
                ),
                name="unique_score_trace_label_null_annotator",
            ),
            models.UniqueConstraint(
                fields=["observation_span", "label"],
                condition=Q(
                    deleted=False,
                    observation_span__isnull=False,
                    annotator__isnull=True,
                ),
                name="unique_score_span_label_null_annotator",
            ),
            models.UniqueConstraint(
                fields=["trace_session", "label"],
                condition=Q(
                    deleted=False,
                    trace_session__isnull=False,
                    annotator__isnull=True,
                ),
                name="unique_score_session_label_null_annotator",
            ),
            models.UniqueConstraint(
                fields=["call_execution", "label"],
                condition=Q(
                    deleted=False,
                    call_execution__isnull=False,
                    annotator__isnull=True,
                ),
                name="unique_score_call_label_null_annotator",
            ),
            models.UniqueConstraint(
                fields=["prototype_run", "label"],
                condition=Q(
                    deleted=False,
                    prototype_run__isnull=False,
                    annotator__isnull=True,
                ),
                name="unique_score_run_label_null_annotator",
            ),
            models.UniqueConstraint(
                fields=["dataset_row", "label"],
                condition=Q(
                    deleted=False,
                    dataset_row__isnull=False,
                    annotator__isnull=True,
                ),
                name="unique_score_row_label_null_annotator",
            ),
        ]

    def clean(self):
        super().clean()
        fk_field = SCORE_SOURCE_FK_MAP.get(self.source_type)
        if not fk_field:
            raise ValidationError(f"Invalid source_type: {self.source_type}")
        if getattr(self, f"{fk_field}_id") is None:
            raise ValidationError(
                f"source_type '{self.source_type}' requires '{fk_field}' to be set."
            )
        # Ensure no other source FK is set
        for st, field in SCORE_SOURCE_FK_MAP.items():
            if field != fk_field and getattr(self, f"{field}_id") is not None:
                raise ValidationError(
                    f"Only '{fk_field}' should be set for source_type '{self.source_type}', "
                    f"but '{field}' is also set."
                )

    def __str__(self):
        return f"Score: {self.id} ({self.source_type}, label={self.label_id})"

    def get_source_id(self):
        """Return the ID of the populated source FK."""
        fk_field = SCORE_SOURCE_FK_MAP.get(self.source_type)
        if fk_field:
            return getattr(self, f"{fk_field}_id")
        return None
