"""Versioned metric definitions, the refresh publication ledger and snapshots.

Postgres side of the metrics storage:

* ``DerivedMetricVersion`` carries every semantic field. ``DerivedMetric`` only
  points at its active version, and reads derive their metadata from it, so a
  draft edit to grain, grouping or labels changes nothing until activation.
* ``MetricProjectRefresh`` is one small row per project: lease and fencing
  epoch, generation and row-sequence allocation, change cursors, the committed
  read-manifest head and pending-work flags.
* ``MetricRefreshAttempt`` is one small row per refresh pass. Its state moves
  only by compare-and-set. The dirty-key ledger (``MetricAttemptLedgerChunk``)
  and the frozen batch hashes (``MetricAttemptBatch``) are append-only rows keyed
  by the attempt; no state change copies or re-reads the dirty-key payload.
* ``MetricReadManifest`` is immutable: a compacted checkpoint plus the exact
  committed delta attempts a reader may see. ``MetricGenerationPin`` holds one
  for a request or a snapshot until it expires or is invalidated.
* ``MetricErasureRequest`` records an erasure request. Its subject ids are
  private: they are never logged and never echoed in error text.

The writer, lease policy, publication and read helpers that call these
primitives live in ``tracer/services/derived_metrics``.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Exists, F, OuterRef, Q, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from tfc.utils.base_model import BaseModel

logger = structlog.get_logger(__name__)

# ClickHouse row versions are (fencing_epoch << 32) | row_sequence in a UInt64,
# so both halves must fit in 32 bits. Exhaustion fails closed; nothing wraps.
MAX_FENCING_EPOCH = 2**32 - 1
MAX_ROW_SEQUENCE = 2**32 - 1
MAX_GENERATION = 2**63 - 1

MAX_GROUP_DIMS = 2
SETTLE_MINUTES_DEFAULT = 30
SETTLE_MINUTES_MAX = 1440
CADENCE_SECONDS_DEFAULT = 900
CADENCE_SECONDS_MIN = 60
CADENCE_SECONDS_MAX = 86_400

LEASE_MAX_TTL = timedelta(hours=1)
# One ledger chunk holds a bounded slice of the dirty-key set; a full day of
# changes at the planned scale is a few dozen chunks.
LEDGER_CHUNK_MAX_KEYS = 10_000
# Hard ceiling on committed deltas above a checkpoint. Reads over a 20-delta
# chain missed their budgets while a compacted checkpoint met them, so the
# writer compacts well before this; publication refuses to grow past it.
MAX_MANIFEST_DELTAS = 20
PIN_MAX_LIFETIME = timedelta(hours=24)
ATTEMPT_LEDGER_RETENTION = timedelta(days=7)
ATTEMPT_LEDGER_MAX_RETENTION = timedelta(days=30)
ERASURE_MAX_SUBJECT_IDS = 10_000
ERASURE_MAX_CLAIM_TTL = timedelta(hours=1)

_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class MetricLedgerError(Exception):
    """Base error for the metric ledger.

    Messages carry codes and counts only, never unit, session or user ids.
    """

    code = "metric_ledger_error"

    def __init__(self, message: str | None = None):
        super().__init__(message or self.code)


class MetricFencingError(MetricLedgerError):
    """The caller does not hold the project's current lease and epoch."""

    code = "metric_fencing_lost"


class MetricStateConflict(MetricLedgerError):
    """A compare-and-set found a different state than the caller expected."""

    code = "metric_state_conflict"


class MetricLedgerExhausted(MetricLedgerError):
    """An epoch, sequence, generation or delta chain reached its limit."""

    code = "metric_ledger_exhausted"


class MetricLedgerConflict(MetricLedgerError):
    """An immutable ledger entry was rewritten with different content."""

    code = "metric_ledger_conflict"


def _sha256_json(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _now(now: datetime | None) -> datetime:
    return now or timezone.now()


def _require_code(value: str, label: str) -> str:
    if not isinstance(value, str) or not _CODE_RE.match(value):
        raise ValueError(f"{label} must be a short lowercase code")
    return value


def row_version(fencing_epoch: int, row_sequence: int) -> int:
    """The ClickHouse ``_version`` for a staged row; fails closed out of range."""

    if not 1 <= fencing_epoch <= MAX_FENCING_EPOCH:
        raise MetricLedgerExhausted("fencing epoch is outside the 32-bit range")
    if not 1 <= row_sequence <= MAX_ROW_SEQUENCE:
        raise MetricLedgerExhausted("row sequence is outside the 32-bit range")
    return (fencing_epoch << 32) | row_sequence


class DerivedMetric(BaseModel):
    """A named metric. Its meaning lives on ``active_version``."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE)
    workspace = models.ForeignKey("accounts.Workspace", on_delete=models.CASCADE)
    project = models.ForeignKey("tracer.Project", on_delete=models.CASCADE)
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    # Latest version number created (the draft when it is not yet active).
    current_version = models.PositiveIntegerField(default=1)
    # The version every read, list, filter, dashboard and task uses. It moves
    # only through ``activate_version`` once that version's backfill is done.
    active_version = models.ForeignKey(
        "tracer.DerivedMetricVersion",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="+",
    )
    created_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True
    )

    class Meta:
        ordering = ["-created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "name"],
                condition=models.Q(deleted=False),
                name="derived_metric_project_name",
            )
        ]

    def active_semantics(self) -> dict | None:
        """Read metadata for this metric, taken from the active version only."""

        version = self.active_version
        return None if version is None else version.semantics()

    @classmethod
    def activate_version(
        cls,
        metric_id,
        version_id,
        *,
        expected_active_id,
        now: datetime | None = None,
    ) -> None:
        """Switch the active version atomically (compare-and-set).

        The version must belong to the metric and have finished its backfill.
        ``expected_active_id`` is the version the caller read; if another
        activation happened in between, nothing changes.
        """

        now = _now(now)
        with transaction.atomic():
            ready = DerivedMetricVersion.objects.filter(
                id=version_id,
                metric_id=metric_id,
                backfill_status=DerivedMetricVersion.BackfillStatus.COMPLETE,
            ).exists()
            if not ready:
                raise MetricStateConflict(
                    "version is not a completed backfill of this metric"
                )
            current = (
                Q(active_version__isnull=True)
                if expected_active_id is None
                else Q(active_version_id=expected_active_id)
            )
            moved = cls._base_manager.filter(
                current, id=metric_id, deleted=False
            ).update(active_version_id=version_id, updated_at=now)
            if moved != 1:
                raise MetricStateConflict("the active version changed")
            DerivedMetricVersion.objects.filter(
                id=version_id, activated_at__isnull=True
            ).update(activated_at=now)


class DerivedMetricVersion(models.Model):
    """One immutable definition of a metric. Every semantic field lives here."""

    class Kind(models.TextChoices):
        SHARE = "share"
        AVERAGE = "average"
        TOTAL = "total"
        COUNT = "count"
        UNIQUE_COUNT = "unique_count"
        PERCENTILE = "percentile"
        CUSTOM = "custom"

    class Level(models.TextChoices):
        SPAN = "span"
        TRACE = "trace"
        CONVERSATION = "conversation"
        USER = "user"

    class UserRule(models.TextChoices):
        NONE = ""
        ANY = "any"
        EVERY = "every"
        AVERAGE = "average"
        TOTAL = "total"

    class ValueType(models.TextChoices):
        BOOLEAN = "boolean"
        NUMBER = "number"
        TEXT = "text"

    class BackfillStatus(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        COMPLETE = "complete"
        FAILED = "failed"

    # Kinds a user-level metric may use, with the per-user rules each allows.
    USER_LEVEL_RULES = {
        Kind.SHARE: (UserRule.ANY, UserRule.EVERY),
        Kind.COUNT: (UserRule.ANY, UserRule.EVERY),
        Kind.AVERAGE: (UserRule.AVERAGE, UserRule.TOTAL),
    }
    SEMANTIC_FIELDS = (
        "kind",
        "level",
        "user_rule",
        "field_spec",
        "group_dims",
        "value_type",
        "unit",
        "value_format",
        "value_labels",
        "sampling",
        "formula",
        "inputs",
        "selection_unit",
        "weighting",
        "cohort_key",
        "filters",
        "definition_hash",
    )
    # Only backfill progress and activation change after a version is created.
    MUTABLE_FIELDS = frozenset(
        {
            "backfill_status",
            "backfill_cursor",
            "backfill_days_done",
            "backfill_days_total",
            "backfill_updated_at",
            "activated_at",
        }
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    metric = models.ForeignKey(
        DerivedMetric, on_delete=models.CASCADE, related_name="versions"
    )
    version = models.PositiveIntegerField()
    formula = models.CharField(max_length=2048)
    inputs = models.JSONField()
    # Legacy recipe unit (span | session | user), kept losslessly for custom
    # formulas. ``level`` is the grain the new storage uses.
    selection_unit = models.CharField(max_length=16)
    weighting = models.CharField(max_length=32)
    cohort_key = models.CharField(max_length=160, blank=True, default="")
    filters = models.JSONField(default=list)
    definition_hash = models.CharField(max_length=64)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.CUSTOM)
    level = models.CharField(
        max_length=16, choices=Level.choices, default=Level.CONVERSATION
    )
    user_rule = models.CharField(
        max_length=16, choices=UserRule.choices, blank=True, default=UserRule.NONE
    )
    # Typed sources, reducers, ordering and condition stage.
    field_spec = models.JSONField(default=dict, blank=True)
    group_dims = ArrayField(models.CharField(max_length=160), default=list, blank=True)
    value_type = models.CharField(
        max_length=16, choices=ValueType.choices, default=ValueType.NUMBER
    )
    unit = models.CharField(max_length=32, blank=True, default="")
    value_format = models.CharField(max_length=32, blank=True, default="")
    # Display labels for yes/no values; editing them makes a new version.
    value_labels = models.JSONField(default=dict, blank=True)
    # Sampling inputs beyond ``weighting`` (scope and stratum keys).
    sampling = models.JSONField(default=dict, blank=True)
    backfill_status = models.CharField(
        max_length=16, choices=BackfillStatus.choices, default=BackfillStatus.PENDING
    )
    backfill_cursor = models.JSONField(null=True, blank=True)
    backfill_days_done = models.PositiveIntegerField(default=0)
    backfill_days_total = models.PositiveIntegerField(default=0)
    backfill_updated_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["metric", "version"], name="derived_metric_version_unique"
            ),
            models.CheckConstraint(
                condition=Q(group_dims__len__lte=MAX_GROUP_DIMS),
                name="derived_metric_version_group_dims",
            ),
            models.CheckConstraint(
                condition=Q(
                    kind__in=[
                        "share",
                        "average",
                        "total",
                        "count",
                        "unique_count",
                        "percentile",
                        "custom",
                    ]
                ),
                name="derived_metric_version_kind",
            ),
            models.CheckConstraint(
                condition=Q(level__in=["span", "trace", "conversation", "user"]),
                name="derived_metric_version_level",
            ),
            models.CheckConstraint(
                condition=Q(value_type__in=["boolean", "number", "text"]),
                name="derived_metric_version_value_type",
            ),
            # A user rule exists exactly when the level is user, and it must
            # match the kind. Custom formulas are not allowed at level user.
            models.CheckConstraint(
                condition=(
                    Q(level="user", kind__in=["share", "count"])
                    & Q(user_rule__in=["any", "every"])
                )
                | Q(level="user", kind="average", user_rule__in=["average", "total"])
                | (~Q(level="user") & Q(user_rule="")),
                name="derived_metric_version_user_rule",
            ),
            models.CheckConstraint(
                condition=Q(
                    backfill_status__in=["pending", "running", "complete", "failed"]
                ),
                name="derived_metric_version_backfill_status",
            ),
            models.CheckConstraint(
                condition=Q(backfill_days_done__lte=F("backfill_days_total")),
                name="derived_metric_version_backfill_days",
            ),
            models.CheckConstraint(
                condition=Q(activated_at__isnull=True) | Q(backfill_status="complete"),
                name="derived_metric_version_activation_ready",
            ),
        ]

    def semantics(self) -> dict:
        return {field: getattr(self, field) for field in self.SEMANTIC_FIELDS}

    def clean(self):
        super().clean()
        errors = {}
        if len(self.group_dims or []) > MAX_GROUP_DIMS:
            errors["group_dims"] = (
                f"A metric can be grouped by at most {MAX_GROUP_DIMS} dimensions."
            )
        if self.level == self.Level.USER:
            allowed = self.USER_LEVEL_RULES.get(self.kind)
            if allowed is None:
                errors["kind"] = (
                    "User-level metrics support Share, Count and Average only; "
                    "custom formulas are not available for users."
                )
            elif self.user_rule not in allowed:
                errors["user_rule"] = (
                    "Choose how each user's conversations combine: "
                    + " or ".join(str(rule) for rule in allowed)
                    + "."
                )
        elif self.user_rule:
            errors["user_rule"] = "A user rule applies to user-level metrics only."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            update_fields = kwargs.get("update_fields")
            if update_fields is None or not set(update_fields) <= self.MUTABLE_FIELDS:
                raise MetricLedgerConflict(
                    "metric versions are immutable; an edit creates a new version"
                )
        super().save(*args, **kwargs)


class DerivedMetricRun(models.Model):
    """A frozen run or, with ``kind=snapshot``, a saved snapshot of one window."""

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        COMPLETE = "complete"
        FAILED = "failed"

    class Kind(models.TextChoices):
        RUN = "run"
        SNAPSHOT = "snapshot"

    class CleanupStatus(models.TextChoices):
        NONE = ""
        PENDING = "pending"
        RUNNING = "running"
        DONE = "done"
        FAILED = "failed"

    SNAPSHOT_GRAINS = ("conversation", "trace")
    CLEANUP_TRANSITIONS = {
        CleanupStatus.PENDING: (CleanupStatus.RUNNING,),
        CleanupStatus.RUNNING: (CleanupStatus.DONE, CleanupStatus.FAILED),
        CleanupStatus.FAILED: (CleanupStatus.RUNNING,),
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    definition = models.ForeignKey(
        DerivedMetricVersion, on_delete=models.PROTECT, related_name="runs"
    )
    requested_by = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True
    )
    window_start = models.DateTimeField()
    window_end = models.DateTimeField()
    as_of = models.DateTimeField()
    source_run_id = models.CharField(max_length=255, blank=True, default="")
    filters = models.JSONField(default=list)
    fingerprint = models.CharField(max_length=64, unique=True)
    snapshot_id = models.UUIDField(null=True)
    result_id = models.UUIDField(null=True)
    attempt_id = models.UUIDField(null=True)
    lease_expires_at = models.DateTimeField(null=True)
    source_manifest = models.JSONField(null=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    error = models.CharField(max_length=300, blank=True, default="")
    row_count = models.PositiveBigIntegerField(default=0)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.RUN)
    # Save snapshot pins the committed manifest and definition at request time.
    pin = models.ForeignKey(
        "tracer.MetricGenerationPin",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    pinned_manifest = models.ForeignKey(
        "tracer.MetricReadManifest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    pinned_generation = models.BigIntegerField(null=True, blank=True)
    captured_at = models.DateTimeField(null=True, blank=True)
    supported_grain = models.CharField(max_length=16, blank=True, default="")
    # Set when an erasure removed units from a completed snapshot.
    changed_at = models.DateTimeField(null=True, blank=True)
    change_reason = models.CharField(max_length=64, blank=True, default="")
    removed_unit_count = models.PositiveBigIntegerField(default=0)
    # Set when a user deletes the snapshot; cleanup removes its stored rows.
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    cleanup_status = models.CharField(
        max_length=16,
        choices=CleanupStatus.choices,
        blank=True,
        default=CleanupStatus.NONE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)

    class Meta:
        ordering = ["-created_at", "id"]
        indexes = [
            models.Index(
                fields=["definition", "status", "-window_end"],
                name="derived_run_window_idx",
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(kind="run", supported_grain="")
                | Q(kind="snapshot", supported_grain__in=["conversation", "trace"]),
                name="derived_metric_run_snapshot_grain",
            ),
            models.CheckConstraint(
                condition=Q(deleted_at__isnull=True, cleanup_status="")
                | Q(
                    deleted_at__isnull=False,
                    cleanup_status__in=["pending", "running", "done", "failed"],
                ),
                name="derived_metric_run_cleanup_state",
            ),
            models.CheckConstraint(
                condition=Q(changed_at__isnull=True, change_reason="")
                | (Q(changed_at__isnull=False) & ~Q(change_reason="")),
                name="derived_metric_run_change_state",
            ),
        ]

    @classmethod
    def mark_snapshot_changed(
        cls,
        run_id,
        *,
        reason: str,
        removed_units: int,
        now: datetime | None = None,
    ) -> None:
        """Record that an erasure removed units from a completed snapshot."""

        _require_code(reason, "reason")
        if removed_units < 0:
            raise ValueError("removed_units must not be negative")
        moved = cls.objects.filter(
            id=run_id,
            kind=cls.Kind.SNAPSHOT,
            status=cls.Status.COMPLETE,
            deleted_at__isnull=True,
        ).update(
            changed_at=_now(now),
            change_reason=reason,
            removed_unit_count=F("removed_unit_count") + removed_units,
        )
        if moved != 1:
            raise MetricStateConflict("only a completed, undeleted snapshot can change")

    @classmethod
    def mark_snapshot_deleted(
        cls, run_id, *, user_id, now: datetime | None = None
    ) -> None:
        """Delete a snapshot for its user and queue cleanup of its stored rows."""

        moved = cls.objects.filter(
            id=run_id, kind=cls.Kind.SNAPSHOT, deleted_at__isnull=True
        ).update(
            deleted_at=_now(now),
            deleted_by_id=user_id,
            cleanup_status=cls.CleanupStatus.PENDING,
        )
        if moved != 1:
            raise MetricStateConflict("snapshot is missing or already deleted")

    @classmethod
    def advance_snapshot_cleanup(cls, run_id, *, from_status: str, to_status: str):
        if to_status not in cls.CLEANUP_TRANSITIONS.get(from_status, ()):
            raise ValueError("unsupported snapshot cleanup transition")
        moved = cls.objects.filter(
            id=run_id,
            kind=cls.Kind.SNAPSHOT,
            deleted_at__isnull=False,
            cleanup_status=from_status,
        ).update(cleanup_status=to_status)
        if moved != 1:
            raise MetricStateConflict("snapshot cleanup is not in the expected state")


class DerivedMetricMonitorDelivery(models.Model):
    """Monitor evaluations; alert_type is empty for a healthy result.

    Legacy rows reference a run. Refresh-evaluated rows reference the metric
    version and window instead, keyed by (monitor, version, window end).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    monitor = models.ForeignKey(
        "tracer.UserAlertMonitor",
        on_delete=models.CASCADE,
        related_name="derived_metric_deliveries",
    )
    run = models.ForeignKey(
        DerivedMetricRun,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="monitor_deliveries",
    )
    metric_version = models.ForeignKey(
        DerivedMetricVersion,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="monitor_deliveries",
    )
    window_start = models.DateTimeField(null=True, blank=True)
    window_end = models.DateTimeField(null=True, blank=True)
    value = models.FloatField(null=True, blank=True)
    fired_at = models.DateTimeField(null=True, blank=True)
    alert_type = models.CharField(max_length=16)
    delivered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["monitor", "run"], name="derived_metric_monitor_run_delivery"
            ),
            models.UniqueConstraint(
                fields=["monitor", "metric_version", "window_end"],
                condition=Q(metric_version__isnull=False),
                name="derived_metric_monitor_window_delivery",
            ),
            models.CheckConstraint(
                condition=Q(run__isnull=False)
                | Q(
                    metric_version__isnull=False,
                    window_start__isnull=False,
                    window_end__isnull=False,
                ),
                name="derived_metric_delivery_source",
            ),
            models.CheckConstraint(
                condition=Q(window_start__isnull=True)
                | Q(window_end__isnull=True)
                | Q(window_end__gt=F("window_start")),
                name="derived_metric_delivery_window",
            ),
        ]
        indexes = [
            models.Index(
                fields=["monitor", "-delivered_at"], name="derived_monitor_delivery_idx"
            )
        ]


@dataclass(frozen=True)
class LeaseGrant:
    project_id: uuid.UUID
    fencing_epoch: int
    lease_token: uuid.UUID
    expires_at: datetime


class MetricProjectRefresh(models.Model):
    """Refresh state for one project: lease, allocation, cursors and head."""

    # Fields a publication may advance together with the manifest head.
    PUBLISHABLE_FIELDS = frozenset(
        {
            "watermark_ns",
            "eval_watermark_ns",
            "remap_cursor_ns",
            "reconciliation_cursor",
            "reconciliation_started_at",
            "reconciliation_completed_at",
            "next_due_at",
            "pending_backfill",
            "pending_expiry",
            "pending_repair",
            "pending_erasure",
            "incremental_last_success_at",
            "backfill_last_progress_at",
        }
    )
    MONOTONE_FIELDS = ("watermark_ns", "eval_watermark_ns", "remap_cursor_ns")

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.OneToOneField(
        "tracer.Project", on_delete=models.CASCADE, related_name="metric_refresh"
    )
    settle_minutes = models.PositiveSmallIntegerField(default=SETTLE_MINUTES_DEFAULT)
    cadence_seconds = models.PositiveIntegerField(default=CADENCE_SECONDS_DEFAULT)
    # Lease. The epoch only grows; each new lease takes the next epoch and
    # restarts the epoch-wide row sequence.
    fencing_epoch = models.BigIntegerField(default=0)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_holder = models.CharField(max_length=128, blank=True, default="")
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    generation_high_water = models.BigIntegerField(default=0)
    row_sequence_high_water = models.BigIntegerField(default=0)
    head_manifest = models.ForeignKey(
        "tracer.MetricReadManifest",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="+",
    )
    # Change cursors (source _version arrival, eval arrival, remap version).
    watermark_ns = models.BigIntegerField(default=0)
    eval_watermark_ns = models.BigIntegerField(default=0)
    remap_cursor_ns = models.BigIntegerField(default=0)
    reconciliation_cursor = models.JSONField(null=True, blank=True)
    reconciliation_started_at = models.DateTimeField(null=True, blank=True)
    reconciliation_completed_at = models.DateTimeField(null=True, blank=True)
    next_due_at = models.DateTimeField(null=True, blank=True)
    # Pending work dispatches regardless of the cadence.
    pending_backfill = models.BooleanField(default=False)
    pending_expiry = models.BooleanField(default=False)
    pending_repair = models.BooleanField(default=False)
    pending_erasure = models.BooleanField(default=False)
    refresh_requested_at = models.DateTimeField(null=True, blank=True)
    last_started_at = models.DateTimeField(null=True, blank=True)
    # Kept apart so frequent backfill chunks never postpone incremental work.
    incremental_last_success_at = models.DateTimeField(null=True, blank=True)
    backfill_last_progress_at = models.DateTimeField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True, default="")
    consecutive_failures = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    settle_minutes__gte=1, settle_minutes__lte=SETTLE_MINUTES_MAX
                ),
                name="metric_refresh_settle_minutes",
            ),
            models.CheckConstraint(
                condition=Q(
                    cadence_seconds__gte=CADENCE_SECONDS_MIN,
                    cadence_seconds__lte=CADENCE_SECONDS_MAX,
                ),
                name="metric_refresh_cadence",
            ),
            models.CheckConstraint(
                condition=Q(fencing_epoch__gte=0, fencing_epoch__lte=MAX_FENCING_EPOCH),
                name="metric_refresh_epoch_range",
            ),
            models.CheckConstraint(
                condition=Q(
                    row_sequence_high_water__gte=0,
                    row_sequence_high_water__lte=MAX_ROW_SEQUENCE,
                ),
                name="metric_refresh_sequence_range",
            ),
            models.CheckConstraint(
                condition=Q(generation_high_water__gte=0),
                name="metric_refresh_generation_range",
            ),
            models.CheckConstraint(
                condition=Q(lease_token__isnull=True, lease_expires_at__isnull=True)
                | Q(
                    lease_token__isnull=False,
                    lease_expires_at__isnull=False,
                    fencing_epoch__gte=1,
                ),
                name="metric_refresh_lease_shape",
            ),
        ]
        indexes = [models.Index(fields=["next_due_at"], name="metric_refresh_due_idx")]

    @classmethod
    def for_project(cls, project_id) -> MetricProjectRefresh:
        row, _ = cls.objects.get_or_create(project_id=project_id)
        return row

    @staticmethod
    def lease_q(epoch: int, token, now: datetime, prefix: str = "") -> Q:
        return Q(
            **{
                f"{prefix}fencing_epoch": epoch,
                f"{prefix}lease_token": token,
                f"{prefix}lease_expires_at__gt": now,
            }
        )

    @classmethod
    def _locked(cls, project_id, epoch: int, token, now: datetime):
        """Lock the project row and require the caller's current lease."""

        row = cls.objects.select_for_update().filter(project_id=project_id).first()
        if row is None or not (
            row.fencing_epoch == epoch
            and row.lease_token == token
            and row.lease_expires_at is not None
            and row.lease_expires_at > now
        ):
            raise MetricFencingError("the refresh lease is not held at this epoch")
        return row

    @classmethod
    def take_lease(
        cls, project_id, *, holder: str, ttl: timedelta, now: datetime | None = None
    ) -> LeaseGrant | None:
        """Take the project lease at the next epoch, or None while it is held."""

        now = _now(now)
        if not timedelta(0) < ttl <= LEASE_MAX_TTL:
            raise ValueError("lease ttl must be positive and at most one hour")
        cls.for_project(project_id)
        with transaction.atomic():
            row = cls.objects.select_for_update().get(project_id=project_id)
            if row.lease_expires_at is not None and row.lease_expires_at > now:
                return None
            if row.fencing_epoch >= MAX_FENCING_EPOCH:
                raise MetricLedgerExhausted("fencing epoch exhausted")
            row.fencing_epoch += 1
            row.lease_token = uuid.uuid4()
            row.lease_holder = holder[:128]
            row.lease_expires_at = now + ttl
            row.row_sequence_high_water = 0
            row.updated_at = now
            row.save(
                update_fields=[
                    "fencing_epoch",
                    "lease_token",
                    "lease_holder",
                    "lease_expires_at",
                    "row_sequence_high_water",
                    "updated_at",
                ]
            )
        return LeaseGrant(
            project_id, row.fencing_epoch, row.lease_token, row.lease_expires_at
        )

    @classmethod
    def renew_lease(
        cls,
        project_id,
        *,
        epoch: int,
        token,
        ttl: timedelta,
        now: datetime | None = None,
    ) -> datetime:
        now = _now(now)
        if not timedelta(0) < ttl <= LEASE_MAX_TTL:
            raise ValueError("lease ttl must be positive and at most one hour")
        expires = now + ttl
        moved = cls.objects.filter(
            cls.lease_q(epoch, token, now), project_id=project_id
        ).update(lease_expires_at=expires, updated_at=now)
        if moved != 1:
            raise MetricFencingError("the refresh lease is not held at this epoch")
        return expires

    @classmethod
    def release_lease(
        cls, project_id, *, epoch: int, token, now: datetime | None = None
    ) -> None:
        now = _now(now)
        cls.objects.filter(
            project_id=project_id, fencing_epoch=epoch, lease_token=token
        ).update(
            lease_token=None, lease_expires_at=None, lease_holder="", updated_at=now
        )

    @classmethod
    def allocate_row_sequences(
        cls, project_id, *, epoch: int, token, count: int, now: datetime | None = None
    ) -> tuple[int, int]:
        """Reserve ``count`` row sequences in the current epoch: (first, last)."""

        now = _now(now)
        if count < 1:
            raise ValueError("count must be at least 1")
        with transaction.atomic():
            row = cls._locked(project_id, epoch, token, now)
            first = row.row_sequence_high_water + 1
            last = row.row_sequence_high_water + count
            if last > MAX_ROW_SEQUENCE:
                raise MetricLedgerExhausted(
                    "row sequences exhausted for this epoch; take a new lease"
                )
            row.row_sequence_high_water = last
            row.updated_at = now
            row.save(update_fields=["row_sequence_high_water", "updated_at"])
        return first, last


class MetricRefreshAttempt(models.Model):
    """One refresh pass. A small row whose state moves by compare-and-set."""

    class State(models.TextChoices):
        PREPARED = "prepared"
        STAGING = "staging"
        VERIFIED = "verified"
        COMMITTED = "committed"
        ABORTED = "aborted"

    class Purpose(models.TextChoices):
        INCREMENTAL = "incremental"
        BACKFILL = "backfill"
        EXPIRY = "expiry"
        REPAIR = "repair"
        ERASURE = "erasure"
        COMPACTION = "compaction"

    TRANSITIONS = {
        State.PREPARED: (State.STAGING, State.ABORTED),
        State.STAGING: (State.VERIFIED, State.ABORTED),
        State.VERIFIED: (State.COMMITTED, State.ABORTED),
    }
    OPEN_STATES = (State.PREPARED, State.STAGING, State.VERIFIED)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "tracer.Project",
        on_delete=models.CASCADE,
        related_name="metric_refresh_attempts",
    )
    generation = models.BigIntegerField()
    fencing_epoch = models.BigIntegerField()
    lease_token = models.UUIDField()
    purpose = models.CharField(max_length=16, choices=Purpose.choices)
    state = models.CharField(
        max_length=16, choices=State.choices, default=State.PREPARED
    )
    parent_manifest = models.ForeignKey(
        "tracer.MetricReadManifest",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="+",
    )
    # Frozen at prepare: discovery ceilings/cursors and the metric versions.
    source_ceilings = models.JSONField(default=dict)
    metric_versions = models.JSONField(default=list)
    prepared_sha256 = models.CharField(max_length=64)
    # Set once when the dirty-key ledger is complete.
    ledger_chunk_count = models.PositiveIntegerField(default=0)
    ledger_key_count = models.PositiveBigIntegerField(default=0)
    ledger_sha256 = models.CharField(max_length=64, blank=True, default="")
    ledger_sealed_at = models.DateTimeField(null=True, blank=True)
    abort_reason = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    staged_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    committed_at = models.DateTimeField(null=True, blank=True)
    aborted_at = models.DateTimeField(null=True, blank=True)
    # The ledger is private and retention-bounded.
    expires_at = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "generation"], name="metric_attempt_generation"
            ),
            models.CheckConstraint(
                condition=Q(
                    state__in=[
                        "prepared",
                        "staging",
                        "verified",
                        "committed",
                        "aborted",
                    ]
                ),
                name="metric_attempt_state",
            ),
            models.CheckConstraint(
                condition=Q(
                    purpose__in=[
                        "incremental",
                        "backfill",
                        "expiry",
                        "repair",
                        "erasure",
                        "compaction",
                    ]
                ),
                name="metric_attempt_purpose",
            ),
            models.CheckConstraint(
                condition=Q(generation__gte=1),
                name="metric_attempt_generation_range",
            ),
            models.CheckConstraint(
                condition=Q(fencing_epoch__gte=1, fencing_epoch__lte=MAX_FENCING_EPOCH),
                name="metric_attempt_epoch_range",
            ),
            # Nothing is staged before the complete dirty-key ledger is sealed.
            models.CheckConstraint(
                condition=Q(state__in=["prepared", "aborted"])
                | Q(ledger_sealed_at__isnull=False),
                name="metric_attempt_sealed_before_staging",
            ),
            models.CheckConstraint(
                condition=Q(ledger_sealed_at__isnull=True) | ~Q(ledger_sha256=""),
                name="metric_attempt_seal_hash",
            ),
            models.CheckConstraint(
                condition=Q(expires_at__gt=F("created_at")),
                name="metric_attempt_expiry",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "state"], name="metric_attempt_state_idx"),
            models.Index(fields=["expires_at"], name="metric_attempt_expiry_idx"),
        ]

    def __repr__(self):
        return f"<MetricRefreshAttempt {self.id} gen={self.generation} {self.state}>"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise MetricLedgerConflict(
                "attempt rows change only through compare-and-set transitions"
            )
        super().save(*args, **kwargs)

    @classmethod
    def prepare(
        cls,
        project_id,
        *,
        epoch: int,
        token,
        purpose: str,
        source_ceilings: dict,
        metric_versions: list,
        retention: timedelta = ATTEMPT_LEDGER_RETENTION,
        now: datetime | None = None,
    ) -> MetricRefreshAttempt:
        """Allocate the next generation and record a durable attempt.

        Runs under the project row lock and pins the committed head as the
        parent manifest. Nothing may be written to ClickHouse before the
        attempt's dirty-key ledger is sealed.
        """

        now = _now(now)
        if purpose not in cls.Purpose.values:
            raise ValueError("unknown attempt purpose")
        if not timedelta(0) < retention <= ATTEMPT_LEDGER_MAX_RETENTION:
            raise ValueError("attempt retention must be positive and at most 30 days")
        frozen = {
            "source_ceilings": source_ceilings,
            "metric_versions": metric_versions,
        }
        with transaction.atomic():
            refresh = MetricProjectRefresh._locked(project_id, epoch, token, now)
            if refresh.generation_high_water >= MAX_GENERATION:
                raise MetricLedgerExhausted("generations exhausted")
            refresh.generation_high_water += 1
            refresh.updated_at = now
            refresh.last_started_at = now
            refresh.save(
                update_fields=["generation_high_water", "updated_at", "last_started_at"]
            )
            return cls.objects.create(
                project_id=project_id,
                generation=refresh.generation_high_water,
                fencing_epoch=epoch,
                lease_token=token,
                purpose=purpose,
                parent_manifest_id=refresh.head_manifest_id,
                source_ceilings=source_ceilings,
                metric_versions=metric_versions,
                prepared_sha256=_sha256_json(frozen),
                created_at=now,
                expires_at=now + retention,
            )

    @classmethod
    def _fenced(cls, epoch: int, token, now: datetime) -> Exists:
        return Exists(
            MetricProjectRefresh.objects.filter(
                MetricProjectRefresh.lease_q(epoch, token, now),
                project_id=OuterRef("project_id"),
            )
        )

    @classmethod
    def _locked_open(cls, attempt_id, epoch: int, token, now: datetime, state: str):
        """Lock the small attempt row; require its writer's current lease."""

        attempt = (
            cls.objects.select_for_update()
            .only(
                "id",
                "project_id",
                "state",
                "fencing_epoch",
                "lease_token",
                "ledger_sealed_at",
            )
            .filter(id=attempt_id)
            .first()
        )
        if attempt is None:
            raise MetricStateConflict("attempt not found")
        if (
            attempt.fencing_epoch != epoch
            or attempt.lease_token != token
            or not MetricProjectRefresh.objects.filter(
                MetricProjectRefresh.lease_q(epoch, token, now),
                project_id=attempt.project_id,
            ).exists()
        ):
            raise MetricFencingError("the refresh lease is not held at this epoch")
        if attempt.state != state:
            raise MetricStateConflict(f"attempt is {attempt.state}, expected {state}")
        return attempt

    @classmethod
    def append_ledger_chunk(
        cls,
        attempt_id,
        *,
        epoch: int,
        token,
        part: str,
        chunk_no: int,
        keys: list,
        now: datetime | None = None,
    ) -> MetricAttemptLedgerChunk:
        """Append one bounded slice of the dirty-key ledger (write once).

        A retry with identical content is a no-op; different content under the
        same ledger key is refused.
        """

        now = _now(now)
        if part not in MetricAttemptLedgerChunk.Part.values:
            raise ValueError("unknown ledger part")
        if chunk_no < 0:
            raise ValueError("chunk_no must not be negative")
        payload = list(keys)
        if not 1 <= len(payload) <= LEDGER_CHUNK_MAX_KEYS:
            raise ValueError(
                f"a ledger chunk holds 1 to {LEDGER_CHUNK_MAX_KEYS} keys; got {len(payload)}"
            )
        digest = _sha256_json(payload)
        with transaction.atomic():
            attempt = cls._locked_open(
                attempt_id, epoch, token, now, cls.State.PREPARED
            )
            if attempt.ledger_sealed_at is not None:
                raise MetricStateConflict("the dirty-key ledger is already sealed")
            existing = MetricAttemptLedgerChunk.objects.filter(
                attempt_id=attempt_id, part=part, chunk_no=chunk_no
            ).first()
            if existing is not None:
                if existing.payload_sha256 == digest:
                    return existing
                raise MetricLedgerConflict(
                    "a ledger chunk was already written with different content"
                )
            return MetricAttemptLedgerChunk.objects.create(
                attempt_id=attempt_id,
                part=part,
                chunk_no=chunk_no,
                key_count=len(payload),
                payload=payload,
                payload_sha256=digest,
                created_at=now,
            )

    @classmethod
    def seal_ledger(
        cls,
        attempt_id,
        *,
        epoch: int,
        token,
        expected_chunks: int,
        expected_keys: int,
        now: datetime | None = None,
    ) -> str:
        """Mark the dirty-key ledger complete and record its hash (once).

        Reads chunk metadata (counts and hashes), never the key payloads.
        """

        now = _now(now)
        with transaction.atomic():
            attempt = cls._locked_open(
                attempt_id, epoch, token, now, cls.State.PREPARED
            )
            if attempt.ledger_sealed_at is not None:
                raise MetricStateConflict("the dirty-key ledger is already sealed")
            rows = list(
                MetricAttemptLedgerChunk.objects.filter(attempt_id=attempt_id)
                .order_by("part", "chunk_no")
                .values_list("part", "chunk_no", "key_count", "payload_sha256")
            )
            key_count = sum(row[2] for row in rows)
            if len(rows) != expected_chunks or key_count != expected_keys:
                raise MetricLedgerConflict(
                    f"ledger is incomplete: expected {expected_chunks} chunks and "
                    f"{expected_keys} keys, found {len(rows)} and {key_count}"
                )
            numbers: dict[str, list[int]] = {}
            for part, chunk_no, _count, _digest in rows:
                numbers.setdefault(part, []).append(chunk_no)
            if any(found != list(range(len(found))) for found in numbers.values()):
                raise MetricLedgerConflict("ledger chunks are not contiguous")
            digest = hashlib.sha256(
                "\n".join(f"{part}:{no}:{sha}" for part, no, _c, sha in rows).encode()
            ).hexdigest()
            cls.objects.filter(
                id=attempt_id, state=cls.State.PREPARED, ledger_sealed_at__isnull=True
            ).update(
                ledger_chunk_count=len(rows),
                ledger_key_count=key_count,
                ledger_sha256=digest,
                ledger_sealed_at=now,
            )
        return digest

    @classmethod
    def transition(
        cls,
        attempt_id,
        *,
        epoch: int,
        token,
        from_state: str,
        to_state: str,
        now: datetime | None = None,
    ) -> None:
        """Move the attempt one step by compare-and-set on its small row.

        Committing goes through ``MetricReadManifest.publish`` and aborting
        through ``abort``. The checks read only the attempt row, its batch
        receipts and the project lease, never the dirty-key ledger payload.
        """

        now = _now(now)
        if to_state in (cls.State.COMMITTED, cls.State.ABORTED):
            raise ValueError(
                "commit with MetricReadManifest.publish; abort with abort()"
            )
        if to_state not in cls.TRANSITIONS.get(from_state, ()):
            raise ValueError("unsupported attempt transition")
        condition = Q(
            id=attempt_id, state=from_state, fencing_epoch=epoch, lease_token=token
        )
        updates: dict = {"state": to_state}
        if to_state == cls.State.STAGING:
            condition &= Q(ledger_sealed_at__isnull=False)
            updates["staged_at"] = now
        else:
            condition &= ~Exists(
                MetricAttemptBatch.objects.filter(
                    attempt_id=OuterRef("pk"), verified_at__isnull=True
                )
            )
            updates["verified_at"] = now
        moved = cls.objects.filter(condition, cls._fenced(epoch, token, now)).update(
            **updates
        )
        if moved != 1:
            cls._explain_failed_move(
                attempt_id, epoch, token, from_state, to_state, now
            )

    @classmethod
    def _explain_failed_move(cls, attempt_id, epoch, token, from_state, to_state, now):
        attempt = (
            cls.objects.only(
                "id",
                "project_id",
                "state",
                "fencing_epoch",
                "lease_token",
                "ledger_sealed_at",
            )
            .filter(id=attempt_id)
            .first()
        )
        if attempt is None:
            raise MetricStateConflict("attempt not found")
        lease_ok = MetricProjectRefresh.objects.filter(
            MetricProjectRefresh.lease_q(epoch, token, now),
            project_id=attempt.project_id,
        ).exists()
        if (
            attempt.fencing_epoch != epoch
            or attempt.lease_token != token
            or not lease_ok
        ):
            raise MetricFencingError("the refresh lease is not held at this epoch")
        if attempt.state != from_state:
            raise MetricStateConflict(
                f"attempt is {attempt.state}, expected {from_state}"
            )
        if to_state == cls.State.STAGING:
            raise MetricStateConflict("the dirty-key ledger is not sealed")
        missing = MetricAttemptBatch.objects.filter(
            attempt_id=attempt_id, verified_at__isnull=True
        ).count()
        raise MetricStateConflict(f"{missing} batches have no visibility receipt")

    @classmethod
    def abort(
        cls,
        attempt_id,
        *,
        epoch: int,
        token,
        reason: str,
        now: datetime | None = None,
    ) -> None:
        """Abort an open attempt.

        The attempt's own writer may abort it, and so may the holder of a newer
        epoch taking over the project. Its rows stay excluded from every
        manifest; its ledger tells the next pass which keys to recompute.
        """

        now = _now(now)
        _require_code(reason, "reason")
        own = Q(fencing_epoch=epoch, lease_token=token)
        moved = cls.objects.filter(
            own | Q(fencing_epoch__lt=epoch),
            cls._fenced(epoch, token, now),
            id=attempt_id,
            state__in=cls.OPEN_STATES,
        ).update(state=cls.State.ABORTED, aborted_at=now, abort_reason=reason)
        if moved != 1:
            state = (
                cls.objects.filter(id=attempt_id)
                .values_list("state", flat=True)
                .first()
            )
            if state in cls.OPEN_STATES:
                raise MetricFencingError("the refresh lease is not held at this epoch")
            raise MetricStateConflict(f"attempt is {state or 'missing'}, not open")


class MetricAttemptLedgerChunk(models.Model):
    """Append-only slice of an attempt's dirty-key ledger.

    Holds old and new keys (bucket, group, scope, stratum), vanished keys,
    source-membership preimages and cleanup intent. Written once before any
    ClickHouse write and never rewritten. Private and never logged.
    """

    class Part(models.TextChoices):
        DIRTY_BEFORE = "dirty_before"
        DIRTY_AFTER = "dirty_after"
        VANISHED = "vanished"
        MEMBERSHIP_PREIMAGE = "membership_preimage"
        CLEANUP_INTENT = "cleanup_intent"

    id = models.BigAutoField(primary_key=True)
    attempt = models.ForeignKey(
        MetricRefreshAttempt, on_delete=models.CASCADE, related_name="ledger_chunks"
    )
    part = models.CharField(max_length=32, choices=Part.choices)
    chunk_no = models.PositiveIntegerField()
    key_count = models.PositiveIntegerField()
    payload = models.JSONField()
    payload_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "part", "chunk_no"], name="metric_ledger_chunk_key"
            ),
            models.CheckConstraint(
                condition=Q(
                    part__in=[
                        "dirty_before",
                        "dirty_after",
                        "vanished",
                        "membership_preimage",
                        "cleanup_intent",
                    ]
                ),
                name="metric_ledger_chunk_part",
            ),
            models.CheckConstraint(
                condition=Q(key_count__gte=1, key_count__lte=LEDGER_CHUNK_MAX_KEYS),
                name="metric_ledger_chunk_size",
            ),
        ]

    def __repr__(self):
        return f"<MetricAttemptLedgerChunk {self.part}#{self.chunk_no} keys={self.key_count}>"

    def __str__(self):
        return repr(self)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise MetricLedgerConflict("ledger chunks are append-only")
        super().save(*args, **kwargs)


class MetricAttemptBatch(models.Model):
    """A frozen output batch and, once verified, its visibility receipt."""

    class Table(models.TextChoices):
        FIELD_VALUES = "field_values"
        BUCKET_TOTALS = "bucket_totals"
        UNIT_STATE = "unit_state"
        SOURCE_DEPENDENCIES = "source_dependencies"
        BUCKET_RETIRED_PARTS = "bucket_retired_parts"

    id = models.BigAutoField(primary_key=True)
    attempt = models.ForeignKey(
        MetricRefreshAttempt, on_delete=models.CASCADE, related_name="batches"
    )
    table_name = models.CharField(max_length=32, choices=Table.choices)
    batch_no = models.PositiveIntegerField()
    row_count = models.PositiveBigIntegerField()
    row_sequence_first = models.BigIntegerField(null=True, blank=True)
    row_sequence_last = models.BigIntegerField(null=True, blank=True)
    payload_sha256 = models.CharField(max_length=64)
    # Durable replay reference for the frozen payload, when it is not inline.
    payload_ref = models.CharField(max_length=512, blank=True, default="")
    frozen_at = models.DateTimeField(default=timezone.now)
    visible_row_count = models.PositiveBigIntegerField(null=True, blank=True)
    visible_sha256 = models.CharField(max_length=64, blank=True, default="")
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "table_name", "batch_no"], name="metric_batch_key"
            ),
            models.CheckConstraint(
                condition=Q(
                    table_name__in=[
                        "field_values",
                        "bucket_totals",
                        "unit_state",
                        "source_dependencies",
                        "bucket_retired_parts",
                    ]
                ),
                name="metric_batch_table",
            ),
            models.CheckConstraint(
                condition=Q(
                    row_sequence_first__isnull=True, row_sequence_last__isnull=True
                )
                | Q(
                    row_sequence_first__gte=1,
                    row_sequence_last__gte=F("row_sequence_first"),
                    row_sequence_last__lte=MAX_ROW_SEQUENCE,
                ),
                name="metric_batch_sequence_range",
            ),
            models.CheckConstraint(
                condition=Q(verified_at__isnull=True)
                | Q(
                    visible_row_count=F("row_count"),
                    visible_sha256=F("payload_sha256"),
                ),
                name="metric_batch_receipt_matches",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise MetricLedgerConflict(
                "frozen batches change only by recording a receipt"
            )
        super().save(*args, **kwargs)

    @classmethod
    def freeze(
        cls,
        attempt_id,
        *,
        epoch: int,
        token,
        table_name: str,
        batch_no: int,
        row_count: int,
        payload_sha256: str,
        payload_ref: str = "",
        row_sequences: tuple[int, int] | None = None,
        now: datetime | None = None,
    ) -> MetricAttemptBatch:
        """Freeze a batch's hash before it is submitted (write once)."""

        now = _now(now)
        if table_name not in cls.Table.values:
            raise ValueError("unknown metric table")
        if not re.fullmatch(r"[0-9a-f]{64}", payload_sha256 or ""):
            raise ValueError("payload_sha256 must be a sha256 hex digest")
        first, last = row_sequences if row_sequences else (None, None)
        with transaction.atomic():
            MetricRefreshAttempt._locked_open(
                attempt_id, epoch, token, now, MetricRefreshAttempt.State.STAGING
            )
            existing = cls.objects.filter(
                attempt_id=attempt_id, table_name=table_name, batch_no=batch_no
            ).first()
            if existing is not None:
                if (existing.payload_sha256, existing.row_count) == (
                    payload_sha256,
                    row_count,
                ):
                    return existing
                raise MetricLedgerConflict(
                    "a batch was already frozen with a different payload"
                )
            return cls.objects.create(
                attempt_id=attempt_id,
                table_name=table_name,
                batch_no=batch_no,
                row_count=row_count,
                row_sequence_first=first,
                row_sequence_last=last,
                payload_sha256=payload_sha256,
                payload_ref=payload_ref,
                frozen_at=now,
            )

    @classmethod
    def record_receipt(
        cls,
        batch_id,
        *,
        epoch: int,
        token,
        visible_row_count: int,
        visible_sha256: str,
        now: datetime | None = None,
    ) -> None:
        """Record what the shard shows for a frozen batch; it must match."""

        now = _now(now)
        batch = cls.objects.filter(id=batch_id).first()
        if batch is None:
            raise MetricStateConflict("batch not found")
        if (visible_row_count, visible_sha256) != (
            batch.row_count,
            batch.payload_sha256,
        ):
            raise MetricLedgerConflict(
                f"visible rows do not match the frozen batch: expected "
                f"{batch.row_count} rows, saw {visible_row_count}"
            )
        if batch.verified_at is not None:
            return
        moved = cls.objects.filter(
            Exists(
                MetricRefreshAttempt.objects.filter(
                    MetricRefreshAttempt._fenced(epoch, token, now),
                    id=OuterRef("attempt_id"),
                    state=MetricRefreshAttempt.State.STAGING,
                    fencing_epoch=epoch,
                    lease_token=token,
                )
            ),
            id=batch_id,
            verified_at__isnull=True,
        ).update(
            visible_row_count=visible_row_count,
            visible_sha256=visible_sha256,
            verified_at=now,
        )
        if moved != 1:
            raise MetricFencingError("the refresh lease is not held at this epoch")


class MetricReadManifest(models.Model):
    """Immutable committed read state: a checkpoint plus exact delta attempts.

    Readers restrict rows to ``allowed_attempts()`` before collapsing to the
    latest logical row. A generation number alone is not enough, because
    aborted attempts leave gaps.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "tracer.Project",
        on_delete=models.CASCADE,
        related_name="metric_read_manifests",
    )
    generation = models.BigIntegerField()
    published_by = models.OneToOneField(
        MetricRefreshAttempt,
        on_delete=models.RESTRICT,
        related_name="published_manifest",
    )
    parent = models.ForeignKey(
        "self", on_delete=models.RESTRICT, null=True, blank=True, related_name="+"
    )
    checkpoint_attempt = models.ForeignKey(
        MetricRefreshAttempt,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="+",
    )
    checkpoint_generation = models.BigIntegerField(null=True, blank=True)
    delta_attempt_ids = ArrayField(models.UUIDField(), default=list, blank=True)
    delta_generations = ArrayField(models.BigIntegerField(), default=list, blank=True)
    visibility_receipts = models.JSONField(default=dict, blank=True)
    manifest_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "generation"], name="metric_manifest_generation"
            ),
            models.CheckConstraint(
                condition=Q(delta_attempt_ids__len__lte=MAX_MANIFEST_DELTAS),
                name="metric_manifest_delta_bound",
            ),
            models.CheckConstraint(
                condition=Q(
                    checkpoint_attempt__isnull=True, checkpoint_generation__isnull=True
                )
                | Q(
                    checkpoint_attempt__isnull=False,
                    checkpoint_generation__isnull=False,
                ),
                name="metric_manifest_checkpoint_shape",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise MetricLedgerConflict("read manifests are immutable")
        super().save(*args, **kwargs)

    def allowed_attempts(self) -> list[tuple[int, uuid.UUID]]:
        """(generation, attempt id) pairs whose rows this manifest makes visible."""

        pairs = []
        if self.checkpoint_attempt_id is not None:
            pairs.append((self.checkpoint_generation, self.checkpoint_attempt_id))
        pairs.extend(zip(self.delta_generations, self.delta_attempt_ids, strict=True))
        return pairs

    @classmethod
    def publish(
        cls,
        attempt_id,
        *,
        epoch: int,
        token,
        cursors: dict | None = None,
        visibility_receipts: dict | None = None,
        now: datetime | None = None,
    ) -> MetricReadManifest:
        """Commit a verified attempt and move the project head, in one transaction.

        Fenced by the project lease: a writer that lost its lease, or an attempt
        prepared against an older head, cannot publish.
        """

        now = _now(now)
        cursors = dict(cursors or {})
        unknown = set(cursors) - MetricProjectRefresh.PUBLISHABLE_FIELDS
        if unknown:
            raise ValueError("unknown cursor fields: " + ", ".join(sorted(unknown)))
        attempt = (
            MetricRefreshAttempt.objects.only(
                "id",
                "project_id",
                "generation",
                "purpose",
                "state",
                "fencing_epoch",
                "lease_token",
                "parent_manifest_id",
            )
            .filter(id=attempt_id)
            .first()
        )
        if attempt is None:
            raise MetricStateConflict("attempt not found")
        with transaction.atomic():
            refresh = MetricProjectRefresh._locked(
                attempt.project_id, epoch, token, now
            )
            if attempt.fencing_epoch != epoch or attempt.lease_token != token:
                raise MetricFencingError("the attempt belongs to another epoch")
            if attempt.parent_manifest_id != refresh.head_manifest_id:
                raise MetricStateConflict(
                    "the committed head moved after this attempt began"
                )
            parent = (
                cls.objects.filter(id=refresh.head_manifest_id).first()
                if refresh.head_manifest_id
                else None
            )
            if attempt.purpose == MetricRefreshAttempt.Purpose.COMPACTION:
                checkpoint = (attempt.id, attempt.generation)
                deltas: list[tuple[uuid.UUID, int]] = []
            else:
                checkpoint = (
                    (parent.checkpoint_attempt_id, parent.checkpoint_generation)
                    if parent and parent.checkpoint_attempt_id
                    else (None, None)
                )
                deltas = (
                    list(
                        zip(
                            parent.delta_attempt_ids,
                            parent.delta_generations,
                            strict=True,
                        )
                    )
                    if parent
                    else []
                )
                deltas.append((attempt.id, attempt.generation))
            if len(deltas) > MAX_MANIFEST_DELTAS:
                raise MetricLedgerExhausted(
                    f"the delta chain is full ({MAX_MANIFEST_DELTAS}); compact first"
                )
            for field in MetricProjectRefresh.MONOTONE_FIELDS:
                if field in cursors and cursors[field] < getattr(refresh, field):
                    raise MetricStateConflict(f"{field} cannot move backwards")
            committed = MetricRefreshAttempt.objects.filter(
                id=attempt.id,
                state=MetricRefreshAttempt.State.VERIFIED,
                fencing_epoch=epoch,
                lease_token=token,
            ).update(state=MetricRefreshAttempt.State.COMMITTED, committed_at=now)
            if committed != 1:
                raise MetricStateConflict("attempt is not verified")
            body = {
                "generation": attempt.generation,
                "checkpoint": [
                    str(checkpoint[0]) if checkpoint[0] else None,
                    checkpoint[1],
                ],
                "deltas": [[str(a), g] for a, g in deltas],
            }
            manifest = cls.objects.create(
                project_id=attempt.project_id,
                generation=attempt.generation,
                published_by_id=attempt.id,
                parent_id=refresh.head_manifest_id,
                checkpoint_attempt_id=checkpoint[0],
                checkpoint_generation=checkpoint[1],
                delta_attempt_ids=[a for a, _ in deltas],
                delta_generations=[g for _, g in deltas],
                visibility_receipts=visibility_receipts or {},
                manifest_sha256=_sha256_json(body),
                created_at=now,
            )
            for field, value in cursors.items():
                setattr(refresh, field, value)
            refresh.head_manifest = manifest
            refresh.last_success_at = now
            refresh.consecutive_failures = 0
            refresh.last_error_code = ""
            refresh.updated_at = now
            refresh.save(
                update_fields=[
                    *cursors,
                    "head_manifest",
                    "last_success_at",
                    "consecutive_failures",
                    "last_error_code",
                    "updated_at",
                ]
            )
        return manifest


class MetricGenerationPin(models.Model):
    """Holds one committed manifest (and optionally a definition) for a reader.

    Pins always expire. Retention and erasure override them: an invalidated or
    expired pin is not usable, and the consumer fails as expired.
    """

    class Consumer(models.TextChoices):
        REQUEST = "request"
        SNAPSHOT = "snapshot"
        ALERT = "alert"
        EXPORT = "export"

    class Invalidation(models.TextChoices):
        NONE = ""
        RETENTION = "retention_expired"
        ERASURE = "erased"
        VERSION_RETIRED = "version_retired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "tracer.Project",
        on_delete=models.CASCADE,
        related_name="metric_generation_pins",
    )
    manifest = models.ForeignKey(
        MetricReadManifest, on_delete=models.RESTRICT, related_name="pins"
    )
    definition_version = models.ForeignKey(
        DerivedMetricVersion,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="+",
    )
    consumer_kind = models.CharField(max_length=16, choices=Consumer.choices)
    consumer_ref = models.CharField(max_length=128, blank=True, default="")
    captured_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    invalidated_at = models.DateTimeField(null=True, blank=True)
    invalidation_reason = models.CharField(
        max_length=32, choices=Invalidation.choices, blank=True, default=""
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    consumer_kind__in=["request", "snapshot", "alert", "export"]
                ),
                name="metric_pin_consumer",
            ),
            models.CheckConstraint(
                condition=Q(expires_at__gt=F("captured_at"))
                & Q(expires_at__lte=F("captured_at") + PIN_MAX_LIFETIME),
                name="metric_pin_lifetime",
            ),
            models.CheckConstraint(
                condition=Q(invalidated_at__isnull=True, invalidation_reason="")
                | (Q(invalidated_at__isnull=False) & ~Q(invalidation_reason="")),
                name="metric_pin_invalidation",
            ),
        ]
        indexes = [
            models.Index(
                fields=["manifest", "expires_at"], name="metric_pin_manifest_idx"
            ),
            models.Index(
                fields=["project", "expires_at"], name="metric_pin_project_idx"
            ),
        ]

    def is_usable(self, now: datetime | None = None) -> bool:
        return self.invalidated_at is None and self.expires_at > _now(now)

    @classmethod
    def pin_head(
        cls,
        project_id,
        *,
        consumer_kind: str,
        ttl: timedelta,
        consumer_ref: str = "",
        definition_version_id=None,
        now: datetime | None = None,
    ) -> MetricGenerationPin:
        """Pin the project's committed head for one request or snapshot."""

        now = _now(now)
        if not timedelta(0) < ttl <= PIN_MAX_LIFETIME:
            raise ValueError("pin ttl must be positive and at most 24 hours")
        head = (
            MetricProjectRefresh.objects.filter(project_id=project_id)
            .values_list("head_manifest_id", flat=True)
            .first()
        )
        if head is None:
            raise MetricStateConflict("no committed refresh to pin yet")
        return cls.objects.create(
            project_id=project_id,
            manifest_id=head,
            definition_version_id=definition_version_id,
            consumer_kind=consumer_kind,
            consumer_ref=consumer_ref,
            captured_at=now,
            expires_at=now + ttl,
        )

    @classmethod
    def invalidate(
        cls,
        project_id,
        *,
        reason: str,
        manifest_ids=None,
        definition_version_ids=None,
        now: datetime | None = None,
    ) -> int:
        """Invalidate live pins of a project (retention, erasure, version retired)."""

        if reason not in cls.Invalidation.values or not reason:
            raise ValueError("unknown invalidation reason")
        pins = cls.objects.filter(project_id=project_id, invalidated_at__isnull=True)
        if manifest_ids is not None:
            pins = pins.filter(manifest_id__in=list(manifest_ids))
        if definition_version_ids is not None:
            pins = pins.filter(definition_version_id__in=list(definition_version_ids))
        return pins.update(invalidated_at=_now(now), invalidation_reason=reason)

    @classmethod
    def manifest_in_use(cls, manifest_id, now: datetime | None = None) -> bool:
        """True while any usable pin holds the manifest (blocks its cleanup)."""

        return cls.objects.filter(
            manifest_id=manifest_id,
            invalidated_at__isnull=True,
            expires_at__gt=_now(now),
        ).exists()


class MetricErasureRequest(models.Model):
    """A durable erasure request for live metric rows and snapshots.

    ``subject_ids`` is private: it is never logged, never shown in ``repr`` and
    never quoted in error text. Work resumes after an interruption: a failed or
    abandoned request can be claimed again and keeps its per-store receipts.
    """

    class Scope(models.TextChoices):
        ORGANIZATION = "organization"
        PROJECT = "project"
        SESSIONS = "sessions"
        USERS = "users"

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        DONE = "done"
        FAILED = "failed"

    class Store(models.TextChoices):
        LIVE_METRIC_ROWS = "live_metric_rows"
        SNAPSHOT_INPUTS = "snapshot_inputs"
        SNAPSHOT_RESULTS = "snapshot_results"

    ID_SCOPES = (Scope.SESSIONS, Scope.USERS)

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "accounts.Organization", on_delete=models.CASCADE, related_name="+"
    )
    project = models.ForeignKey(
        "tracer.Project",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="+",
    )
    scope_kind = models.CharField(max_length=16, choices=Scope.choices)
    subject_ids = models.JSONField(default=list, blank=True)
    subject_count = models.PositiveIntegerField(default=0)
    subject_sha256 = models.CharField(max_length=64, blank=True, default="")
    requested_at = models.DateTimeField(default=timezone.now)
    requested_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.QUEUED
    )
    claim_token = models.UUIDField(null=True, blank=True)
    claim_expires_at = models.DateTimeField(null=True, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)
    # Per-store receipts: counts only, e.g. {"live_metric_rows": {"rows": 12}}.
    receipts = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    scope_kind__in=["organization", "project", "sessions", "users"]
                ),
                name="metric_erasure_scope",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["queued", "running", "done", "failed"]),
                name="metric_erasure_status",
            ),
            models.CheckConstraint(
                condition=Q(
                    scope_kind__in=["sessions", "users"],
                    subject_count__gte=1,
                    subject_count__lte=ERASURE_MAX_SUBJECT_IDS,
                )
                | Q(scope_kind__in=["organization", "project"], subject_count=0),
                name="metric_erasure_subjects",
            ),
            models.CheckConstraint(
                condition=~Q(scope_kind__in=["project", "sessions"])
                | Q(project__isnull=False),
                name="metric_erasure_project_scope",
            ),
            models.CheckConstraint(
                condition=~Q(status="running")
                | Q(claim_token__isnull=False, claim_expires_at__isnull=False),
                name="metric_erasure_claim",
            ),
            models.CheckConstraint(
                condition=~Q(status="done") | Q(completed_at__isnull=False),
                name="metric_erasure_done",
            ),
        ]
        indexes = [
            models.Index(
                fields=["status", "requested_at"], name="metric_erasure_queue_idx"
            )
        ]

    def __repr__(self):
        return (
            f"<MetricErasureRequest {self.id} {self.scope_kind} "
            f"subjects={self.subject_count} {self.status}>"
        )

    def __str__(self):
        return repr(self)

    @classmethod
    def record(
        cls,
        *,
        organization_id,
        scope_kind: str,
        project_id=None,
        subject_ids=(),
        requested_by_id=None,
        now: datetime | None = None,
    ) -> MetricErasureRequest:
        now = _now(now)
        if scope_kind not in cls.Scope.values:
            raise ValidationError({"scope_kind": "Unknown erasure scope."})
        ids = list(dict.fromkeys(subject_ids or ()))
        if scope_kind in cls.ID_SCOPES:
            if any(not isinstance(value, str) or not value for value in ids):
                raise ValidationError(
                    {"subject_ids": "Every id must be a non-empty string."}
                )
            if not 1 <= len(ids) <= ERASURE_MAX_SUBJECT_IDS:
                raise ValidationError(
                    {
                        "subject_ids": (
                            f"An erasure request names 1 to {ERASURE_MAX_SUBJECT_IDS} "
                            f"ids; got {len(ids)}."
                        )
                    }
                )
        elif ids:
            raise ValidationError(
                {"subject_ids": f"A {scope_kind} erasure takes no ids; got {len(ids)}."}
            )
        if scope_kind in (cls.Scope.PROJECT, cls.Scope.SESSIONS) and project_id is None:
            raise ValidationError(
                {"project": f"A {scope_kind} erasure needs a project."}
            )
        request = cls.objects.create(
            organization_id=organization_id,
            project_id=project_id,
            scope_kind=scope_kind,
            subject_ids=ids,
            subject_count=len(ids),
            subject_sha256=_sha256_json(sorted(ids)) if ids else "",
            requested_by_id=requested_by_id,
            requested_at=now,
            updated_at=now,
        )
        logger.info(
            "metric_erasure_requested",
            erasure_request_id=str(request.id),
            scope_kind=scope_kind,
            subject_count=len(ids),
        )
        return request

    @classmethod
    def claim(
        cls, request_id, *, ttl: timedelta, now: datetime | None = None
    ) -> uuid.UUID | None:
        """Start or resume work: queued, failed, or running with a lapsed claim."""

        now = _now(now)
        if not timedelta(0) < ttl <= ERASURE_MAX_CLAIM_TTL:
            raise ValueError("claim ttl must be positive and at most one hour")
        token = uuid.uuid4()
        moved = cls.objects.filter(
            Q(status__in=[cls.Status.QUEUED, cls.Status.FAILED])
            | Q(status=cls.Status.RUNNING, claim_expires_at__lte=now),
            id=request_id,
        ).update(
            status=cls.Status.RUNNING,
            claim_token=token,
            claim_expires_at=now + ttl,
            attempt_count=F("attempt_count") + 1,
            started_at=Coalesce(F("started_at"), Value(now)),
            error_code="",
            updated_at=now,
        )
        if moved != 1:
            return None
        logger.info("metric_erasure_claimed", erasure_request_id=str(request_id))
        return token

    @classmethod
    def record_receipt(
        cls,
        request_id,
        *,
        token,
        store: str,
        counts: dict,
        now: datetime | None = None,
    ) -> None:
        """Record one store's completed work. Receipts hold counts only."""

        now = _now(now)
        if store not in cls.Store.values:
            raise ValueError("unknown erasure store")
        if not counts or any(
            not _CODE_RE.match(str(key)) or type(value) is not int or value < 0
            for key, value in counts.items()
        ):
            raise ValueError(
                "receipt counts must be non-negative integers keyed by code"
            )
        with transaction.atomic():
            request = (
                cls.objects.select_for_update()
                .only("id", "status", "claim_token", "claim_expires_at", "receipts")
                .filter(id=request_id)
                .first()
            )
            if (
                request is None
                or request.status != cls.Status.RUNNING
                or request.claim_token != token
                or request.claim_expires_at <= now
            ):
                raise MetricStateConflict("the erasure claim is not held")
            receipts = dict(request.receipts or {})
            receipts[store] = dict(counts)
            cls.objects.filter(id=request_id).update(receipts=receipts, updated_at=now)
        logger.info(
            "metric_erasure_store_done",
            erasure_request_id=str(request_id),
            store=store,
            **{f"count_{key}": value for key, value in counts.items()},
        )

    @classmethod
    def finish(cls, request_id, *, token, now: datetime | None = None) -> None:
        now = _now(now)
        moved = cls.objects.filter(
            id=request_id,
            status=cls.Status.RUNNING,
            claim_token=token,
            receipts__has_keys=list(cls.Store.values),
        ).update(
            status=cls.Status.DONE,
            completed_at=now,
            claim_token=None,
            claim_expires_at=None,
            updated_at=now,
        )
        if moved != 1:
            raise MetricStateConflict(
                "the erasure claim is not held or a store has no receipt"
            )
        logger.info("metric_erasure_done", erasure_request_id=str(request_id))

    @classmethod
    def fail(
        cls, request_id, *, token, error_code: str, now: datetime | None = None
    ) -> None:
        now = _now(now)
        _require_code(error_code, "error_code")
        moved = cls.objects.filter(
            id=request_id, status=cls.Status.RUNNING, claim_token=token
        ).update(
            status=cls.Status.FAILED,
            error_code=error_code,
            claim_token=None,
            claim_expires_at=None,
            updated_at=now,
        )
        if moved != 1:
            raise MetricStateConflict("the erasure claim is not held")
        logger.warning(
            "metric_erasure_failed",
            erasure_request_id=str(request_id),
            error_code=error_code,
        )
