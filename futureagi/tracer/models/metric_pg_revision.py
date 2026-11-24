"""Metrics-owned PG response fences, deliberately independent of source lifetimes.

No foreign keys: deleting/recreating a native project or User must not reset a
revision. These rows are NOT producer enrollment or ClickHouse visibility proof.
Only the transaction API should mutate them; deletion/reset is unsupported.
"""

import uuid

from django.db import models


class MetricPgResponseRevision(models.Model):
    project_id = models.UUIDField(primary_key=True)
    incarnation = models.UUIDField(default=uuid.uuid4, editable=False)
    revision = models.PositiveBigIntegerField(default=0)

    class Meta:
        db_table = "metric_pg_response_revision"


class MetricPgIdentityRevision(models.Model):
    singleton = models.PositiveSmallIntegerField(primary_key=True, default=1)
    incarnation = models.UUIDField(default=uuid.uuid4, editable=False)
    revision = models.PositiveBigIntegerField(default=0)

    class Meta:
        db_table = "metric_pg_identity_revision"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(singleton=1), name="metric_pg_identity_singleton"
            )
        ]
