"""Durable sampling decisions, separate from evaluation-row identities."""

import uuid

from django.db import models

from tracer.models.eval_task import EvalTask


class EvalSamplingCohort(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(
        EvalTask, on_delete=models.CASCADE, related_name="sampling_cohorts"
    )
    policy_version = models.CharField(max_length=64)
    policy = models.JSONField()
    selection_context = models.JSONField(default=dict)
    frozen_at = models.DateTimeField()
    complete = models.BooleanField(default=False)
    # Single-writer reconcile lease. Each short write transaction renews it
    # and proves its token, so a stale or concurrent poller cannot write.
    lease_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tracer_eval_sampling_cohort"
        constraints = [
            models.UniqueConstraint(
                fields=["task", "policy_version"], name="sampling_cohort_task_version"
            )
        ]


class EvalSamplingMember(models.Model):
    id = models.BigAutoField(primary_key=True)
    cohort = models.ForeignKey(
        EvalSamplingCohort, on_delete=models.CASCADE, related_name="members"
    )
    unit_key = models.CharField(max_length=64)
    unit_id = models.TextField()
    stratum = models.TextField()
    selected = models.BooleanField()
    inclusion_probability = models.FloatField()
    population_size = models.BigIntegerField(null=True)

    class Meta:
        db_table = "tracer_eval_sampling_member"
        constraints = [
            models.UniqueConstraint(
                fields=["cohort", "unit_key"], name="sampling_member_cohort_unit"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    inclusion_probability__gte=0, inclusion_probability__lte=1
                ),
                name="sampling_probability_range",
            ),
            models.CheckConstraint(
                condition=models.Q(selected=False)
                | models.Q(inclusion_probability__gt=0),
                name="sampling_selected_probability",
            ),
        ]
