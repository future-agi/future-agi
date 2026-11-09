"""Rollout state (READINESS 3.13, lane 1.10a).

Plain `models.Model`, not `BaseModel`: these rows are read across workspaces, so the workspace-scoped
default manager must never filter them, and `disable_auto_workspace_assignment` keeps the request's
workspace off an org-level entry (tfc/utils/signals.py would otherwise turn it into a workspace one).
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator
from django.db import models

from omega_rollout import products as p

PRODUCT_CHOICES = [
    (product, p.PRODUCT_LABELS[product]) for product in p.HOSTED_PRODUCTS
]
STAGE_CHOICES = [(stage, stage) for stage in p.STAGES]
COHORT_CHOICES = [(cohort, cohort) for cohort in p.COHORTS]
EFFECT_CHOICES = [(p.EFFECT_ALLOW, "allow"), (p.EFFECT_DENY, "deny")]


class RolloutEpoch(models.Model):
    """One row (id=1): a counter bumped by every change, carried in each key as `rollout_epoch`, so a
    long response's re-check can tell that the rollout changed after its key was minted.
    """

    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    epoch = models.PositiveBigIntegerField(default=0)

    class Meta:
        db_table = "omega_rollout_epoch"


class ProductRollout(models.Model):
    """A product's stage, S3 percentage and kill state. A product with no row is S0 and dark."""

    product = models.CharField(max_length=32, primary_key=True, choices=PRODUCT_CHOICES)
    stage = models.CharField(max_length=2, choices=STAGE_CHOICES, default=p.S0)
    percentage = models.PositiveSmallIntegerField(
        default=0, validators=[MaxValueValidator(100)]
    )
    paused = models.BooleanField(default=False)
    # The Hub only: a paused Hub refuses installs too when the operator chooses (READINESS 3.13).
    installs_paused = models.BooleanField(default=False)
    epoch = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        db_table = "omega_rollout_product"
        # Staff may read the rollout; only staff holding this permission change it (READINESS RC-10).
        permissions = [("operate_rollout", "Can change Omega product rollout")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(percentage__lte=100),
                name="omega_rollout_percentage_max_100",
            ),
        ]


class ProductAccess(models.Model):
    """An allow or deny entry for one org, or for one workspace of it."""

    disable_auto_workspace_assignment = True

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.CharField(max_length=32, choices=PRODUCT_CHOICES)
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="+"
    )
    workspace = models.ForeignKey(
        "accounts.Workspace",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="+",
    )
    effect = models.CharField(max_length=8, choices=EFFECT_CHOICES)
    cohort = models.CharField(
        max_length=16, choices=COHORT_CHOICES, default=p.COHORT_PARTNER
    )
    reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        db_table = "omega_rollout_access"
        indexes = [
            models.Index(
                fields=["organization", "product"], name="omega_rollout_access_org"
            )
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "organization"],
                condition=models.Q(workspace__isnull=True),
                name="omega_rollout_access_one_org_entry",
            ),
            models.UniqueConstraint(
                fields=["product", "organization", "workspace"],
                condition=models.Q(workspace__isnull=False),
                name="omega_rollout_access_one_workspace_entry",
            ),
        ]


class WaitlistRequest(models.Model):
    """An org asking for early access on Settings -> Early access (READINESS 3.13, S2)."""

    REQUESTED = "requested"
    INVITED = "invited"
    DECLINED = "declined"
    STATUS_CHOICES = [
        (REQUESTED, "requested"),
        (INVITED, "invited"),
        (DECLINED, "declined"),
    ]

    disable_auto_workspace_assignment = True

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.CharField(max_length=32, choices=PRODUCT_CHOICES)
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="+"
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    note = models.TextField(blank=True, default="")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=REQUESTED)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        db_table = "omega_rollout_waitlist"
        constraints = [
            models.UniqueConstraint(
                fields=["product", "organization"],
                name="omega_rollout_waitlist_one_per_org",
            ),
        ]


class RolloutAuditEvent(models.Model):
    """Every rollout change, global ones included. `accounts.AuditLog` needs an organization, so a
    stage, percentage, kill or restore change is also written there under the operator's org, and an
    org-scoped change under the target org too (omega_rollout/audit.py)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    action = models.CharField(max_length=48)
    product = models.CharField(max_length=32, choices=PRODUCT_CHOICES)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    organization_id = models.UUIDField(null=True, blank=True, db_index=True)
    workspace_id = models.UUIDField(null=True, blank=True)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True, default="")
    counts = models.JSONField(default=dict, blank=True)
    epoch = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "omega_rollout_audit"
        ordering = ("-created_at",)
