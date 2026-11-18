"""Private, metrics-owned source intent journal; no source/navigation FKs.

Only base_list_current_source may mutate these records. A settled journal is
NOT producer enrollment, retained bootstrap, PG revision or serving authority.
Pending records have no TTL. Privacy cleanup must join the existing PERF2
protocol; deleting a source Project cannot silently delete this history.
"""

import uuid

from django.db import models
from django.db.models import F, Q


class MetricSourceScope(models.Model):
    epoch = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization_id = models.UUIDField()
    project_id = models.UUIDField()
    revision = models.PositiveBigIntegerField(default=0)
    closed_revision = models.PositiveBigIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("organization_id", "project_id"), name="metric_src_scope"
            ),
            models.CheckConstraint(
                condition=Q(closed_revision__lte=F("revision")),
                name="metric_src_cut_order",
            ),
        ]


class MetricSourceIntent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    scope = models.ForeignKey(MetricSourceScope, on_delete=models.PROTECT)
    operation_key = models.UUIDField()
    revision = models.PositiveBigIntegerField()
    manifest_sha256 = models.CharField(max_length=64)
    page_count = models.PositiveSmallIntegerField()
    target_count = models.PositiveSmallIntegerField()
    complete = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("scope", "operation_key"), name="metric_src_operation"
            ),
            models.UniqueConstraint(
                fields=("scope", "revision"), name="metric_src_revision"
            ),
            models.CheckConstraint(
                condition=Q(revision__gt=0), name="metric_src_revision_positive"
            ),
        ]


class MetricSourcePage(models.Model):
    intent = models.ForeignKey(MetricSourceIntent, on_delete=models.PROTECT)
    ordinal = models.PositiveSmallIntegerField()
    sha256 = models.CharField(max_length=64)
    # Canonical JSON bytes, not JSONField reserialization. Bounded on insertion.
    payload = models.BinaryField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("intent", "ordinal"), name="metric_src_page"
            ),
        ]


class MetricSourceDelivery(models.Model):
    class State(models.TextChoices):
        PREPARED = "prepared"
        UNCERTAIN = "uncertain"
        ACKNOWLEDGED = "acknowledged"

    receipt_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    intent = models.ForeignKey(MetricSourceIntent, on_delete=models.PROTECT)
    server_id = models.UUIDField()
    database = models.CharField(max_length=128)
    store = models.CharField(max_length=64)
    schema_sha256 = models.CharField(max_length=64)
    payload_sha256 = models.CharField(max_length=64)
    state = models.CharField(
        max_length=16, choices=State.choices, default=State.PREPARED
    )
    acknowledgement_sha256 = models.CharField(max_length=64, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("intent", "server_id", "database", "store"),
                name="metric_src_target",
            ),
            models.CheckConstraint(
                condition=(Q(state="acknowledged") & ~Q(acknowledgement_sha256=""))
                | (
                    Q(state__in=("prepared", "uncertain"))
                    & Q(acknowledgement_sha256="")
                ),
                name="metric_src_ack_state",
            ),
        ]
