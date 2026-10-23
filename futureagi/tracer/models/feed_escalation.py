"""One shared escalation policy per workspace."""

from django.db import models

from tfc.utils.base_model import BaseModel
from tracer.services.feed_escalation_policy import (
    ALGORITHM_VERSION,
    POLICY_FIELDS,
    Policy,
)


class FeedEscalationPolicy(BaseModel):
    workspace = models.OneToOneField(
        "accounts.Workspace",
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="feed_escalation_policy",
    )
    min_findings = models.PositiveIntegerField(default=20)
    entry_multiplier = models.DecimalField(
        max_digits=3, decimal_places=1, default="3.0"
    )
    entry_delta = models.PositiveIntegerField(default=10)
    entry_confirmation_minutes = models.PositiveSmallIntegerField(default=15)
    recovery_min = models.PositiveIntegerField(default=5)
    recovery_multiplier = models.DecimalField(
        max_digits=3, decimal_places=1, default="1.5"
    )
    recovery_delta = models.PositiveIntegerField(default=3)
    recovery_confirmation_minutes = models.PositiveSmallIntegerField(default=10)
    algorithm_version = models.CharField(max_length=32, default=ALGORITHM_VERSION)
    defaults_version = models.PositiveSmallIntegerField(default=1)
    revision = models.PositiveBigIntegerField(default=0)
    updated_by = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="updated_feed_escalation_policies",
    )

    class Meta:
        db_table = "tracer_feed_escalation_policy"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(min_findings__gte=2, min_findings__lte=1_000_000),
                name="feed_policy_min_bounds",
            ),
            models.CheckConstraint(
                condition=models.Q(entry_delta__gte=1, entry_delta__lte=1_000_000),
                name="feed_policy_delta_bounds",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    entry_multiplier__gte="1.1", entry_multiplier__lte="20.0"
                ),
                name="feed_policy_multiplier_bounds",
            ),
            models.CheckConstraint(
                condition=models.Q(recovery_min__lt=models.F("min_findings")),
                name="feed_policy_recovery_min_lower",
            ),
            models.CheckConstraint(
                condition=models.Q(recovery_delta__lt=models.F("entry_delta")),
                name="feed_policy_recovery_delta_lower",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    recovery_multiplier__gte="1.0",
                    recovery_multiplier__lt=models.F("entry_multiplier"),
                ),
                name="feed_policy_recovery_mult_lower",
            ),
            models.CheckConstraint(
                condition=models.Q(entry_confirmation_minutes__in=range(5, 61, 5)),
                name="feed_policy_entry_duration",
            ),
            models.CheckConstraint(
                condition=models.Q(recovery_confirmation_minutes__in=range(5, 61, 5)),
                name="feed_policy_recovery_duration",
            ),
        ]

    def policy(self) -> Policy:
        return Policy(
            **{name: getattr(self, name) for name in POLICY_FIELDS},
            revision=self.revision,
        )
