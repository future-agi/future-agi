# ruff: noqa: F811 -- shared isolated native fixtures
"""Physical source-retention tests against PostgreSQL and ClickHouse."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import MetricProjectRefresh, MetricRefreshAttempt
from tracer.services.derived_metrics import retention
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.storage import BUCKET_TOTALS, RETIRED_PARTS
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    current_fields, default_row_select, live_writer, metric_ch, publication_store,
)
from tracer.tests.test_derived_metric_retention_clickhouse import numeric_definition, seed_fields_and_buckets

pytestmark = pytest.mark.django_db(transaction=True)


def test_expiry_deletes_legacy_retired_labels_and_all_aggregate_revisions(live_writer):
    writer = live_writer
    now = timezone.now().replace(hour=12, minute=7, second=0, microsecond=0)
    definition = numeric_definition(uuid4())
    seed_fields_and_buckets(writer, definition, [
        ("expired", now - timedelta(days=3), 4, "private-user"),
        ("boundary", now - timedelta(days=1) + timedelta(minutes=1), 6, "boundary-user"),
        ("retained", now - timedelta(hours=1), 9, "retained-user"),
    ])
    # Migration fixture: identifier-bearing retired history from an old binary.
    attempt = writer.begin()
    for table in (RETIRED_PARTS, BUCKET_TOTALS):
        writer.freeze(attempt, table, default_row_select(table, {
            "metric_id": "toUUID(%(metric)s)", "metric_version": "toUInt32(1)",
            "bucket_start": "toDateTime(%(old)s, 'UTC')",
            "group_key": "'private-legacy-label'", "unit_count": "toUInt64(1)",
            "valid_count": "toUInt64(1)", "weighted_n": "toFloat64(42)",
            "weighted_d": "toFloat64(1)",
        }), {"metric": str(definition.metric_id), "old": now - timedelta(days=10)})
    writer.seal(attempt)
    writer.stage_and_publish(attempt)
    writer.compact()
    assert retention.expire_project(writer, [definition], retention_days=lambda: 1, now=now)["purged"]
    head = MetricProjectRefresh.objects.get(project_id=writer.lease.project_id).head_manifest
    assert {row[0] for row in current_fields(writer, head)} == {"boundary", "retained"}
    for table in (RETIRED_PARTS, BUCKET_TOTALS):
        sql, params = committed_rows_select(table, head, org_id=writer.org_id, project_id=writer.lease.project_id)
        rows = writer.store.execute(f"SELECT group_key, bucket_start FROM ({sql})", params)
        assert all(row[0] != "private-legacy-label" for row in rows)
        if table == RETIRED_PARTS:
            assert rows == []
        else:
            assert rows and all(row[1].replace(tzinfo=now.tzinfo) >= now - timedelta(days=1) for row in rows)
    for table in ("metric_bucket_retired_parts", "metric_bucket_totals", "metric_bucket_totals_checkpoint"):
        assert writer.store.execute(f"SELECT count() FROM {table} WHERE group_key = 'private-legacy-label'") == [(0,)]
    assert retention.expire_project(writer, [definition], retention_days=lambda: -1, now=now)["unlimited"]
    assert writer.store.execute("SELECT count() FROM metric_bucket_retired_parts") == [(0,)]


def test_failed_expiry_closes_prepared_attempt_and_retry_keeps_progress(live_writer, monkeypatch):
    writer = live_writer
    definition = numeric_definition(uuid4())
    now = timezone.now()
    seed_fields_and_buckets(writer, definition, [
        ("old", now - timedelta(days=3), 4, "old-user"),
        ("keep", now - timedelta(hours=1), 7, "keep-user"),
    ])
    native = writer.freeze_current
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic interrupted expiry")
    monkeypatch.setattr(writer, "freeze_current", fail)
    with pytest.raises(RuntimeError, match="interrupted expiry"):
        retention.expire_project(writer, [definition], retention_days=lambda: 1, now=now)
    assert not MetricRefreshAttempt.objects.filter(project_id=writer.lease.project_id, state__in=MetricRefreshAttempt.OPEN_STATES).exists()
    monkeypatch.setattr(writer, "freeze_current", native)
    assert retention.expire_project(writer, [definition], retention_days=lambda: 1, now=now)["purged"]
