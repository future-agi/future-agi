"""UI assignments only; telemetry existence and source tags belong to ClickHouse."""

from datetime import UTC, datetime

from django.db import models


class EntityTagAssignment(models.Model):
    # No FK to Trace/ObservationSpan: CH-only telemetry must remain editable.
    project = models.ForeignKey(
        "tracer.Project", on_delete=models.CASCADE, related_name="tag_assignments"
    )
    entity_kind = models.CharField(
        max_length=5, choices=[("trace", "Trace"), ("span", "Span")]
    )
    trace_id = models.CharField(max_length=128)
    span_id = models.CharField(max_length=128, blank=True)
    start_hour = models.DateTimeField()
    observation_type = models.CharField(max_length=64, blank=True)
    service_name = models.CharField(max_length=255, blank=True)
    manual_tags = models.JSONField(default=list)
    revision = models.PositiveBigIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tracer_entity_tag_assignment"
        constraints = [
            # Non-null sentinels make root POST and trace PATCH the same row;
            # PostgreSQL NULL uniqueness must not permit duplicate assignments.
            models.UniqueConstraint(
                fields=[
                    "project",
                    "entity_kind",
                    "trace_id",
                    "span_id",
                    "start_hour",
                    "observation_type",
                    "service_name",
                ],
                name="unique_entity_tag_assignment",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        entity_kind="trace",
                        span_id="",
                        start_hour=datetime(1970, 1, 1, tzinfo=UTC),
                        observation_type="",
                        service_name="",
                    )
                    | (models.Q(entity_kind="span") & ~models.Q(span_id=""))
                ),
                name="entity_tag_assignment_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(revision__gte=1), name="entity_tag_revision_positive"
            ),
        ]
