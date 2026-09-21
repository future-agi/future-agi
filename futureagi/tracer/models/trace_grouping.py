"""Durable feature-preparation work; not Feed membership or issue identity."""

import uuid

from django.db import models

from tfc.utils.base_model import BaseModel


class GroupingFeatureState(models.TextChoices):
    PENDING = "pending"
    READY = "ready"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class TraceGroupingFeatureJob(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report = models.ForeignKey(
        "tracer.TraceInvestigationReport",
        on_delete=models.CASCADE,
        related_name="grouping_feature_jobs",
    )
    policy_version = models.CharField(max_length=64)
    publication_result_digest = models.CharField(max_length=71)
    state = models.CharField(
        max_length=20,
        choices=GroupingFeatureState.choices,
        default=GroupingFeatureState.PENDING,
    )
    # Set to enqueue time, not the later grouping/cohort debounce deadline.
    not_before = models.DateTimeField()

    class Meta:
        db_table = "tracer_trace_grouping_feature_job"
        constraints = [
            models.UniqueConstraint(
                fields=["report", "policy_version"],
                name="unique_group_feature_report_policy",
            )
        ]
        indexes = [
            models.Index(fields=["state", "not_before"], name="group_feature_due_idx")
        ]
