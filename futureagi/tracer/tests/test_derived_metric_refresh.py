"""Postgres/fake recorder proofs. Live races are in the ClickHouse suites."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from tracer.models.derived_metric import (
    MetricAttemptBatch,
    MetricFencingError,
    MetricProjectRefresh,
    MetricReadManifest,
    MetricStateConflict,
)
from tracer.services.derived_metrics.lease import ProjectLease, project_lease
from tracer.services.derived_metrics.publication import (
    FrozenRows,
    committed_rows_select,
)
from tracer.services.derived_metrics.refresh import RefreshWriter, dependency_select
from tracer.services.derived_metrics.retention import retention_cutoff
from tracer.services.derived_metrics.storage import FIELD_VALUES, UNIT_STATE


class Recorder:
    def __init__(self):
        self.writes = []
        self.fail_after = None
        self.payload = FrozenRows((b"private payload".hex(),))

    def identity(self):
        return ["test-shard", "test_database"]

    def freeze_select(self, *args, **kwargs):
        return self.payload

    def insert(self, table, payload):
        if self.fail_after == len(self.writes):
            raise RuntimeError("injected write failure")
        self.writes.append((table.name, payload.digest))

    def verify(self, table, attempt, batch):
        return {"count": batch.row_count, "sha256": batch.payload_sha256}

    def assert_visible(self, manifest):
        assert manifest.visibility_receipts["shard"] == self.identity()


@pytest.fixture
def writer(observe_project, workspace):
    with project_lease(observe_project.id, holder="b07-test") as lease:
        yield RefreshWriter(
            lease,
            Recorder(),
            org_id=workspace.organization_id,
            workspace_id=workspace.id,
        )


@pytest.mark.django_db
def test_freeze_all_preimages_before_first_write_and_atomic_cursors(writer):
    attempt = writer.begin(
        ledger={
            "dirty_before": [{"bucket": "old"}],
            "dirty_after": [{"bucket": "new"}],
            "vanished": [{"bucket": "gone"}],
            "membership_preimage": [{"source": "old-membership"}],
        }
    )
    writer.freeze(attempt, FIELD_VALUES, "SELECT private_payload")
    writer.freeze(attempt, UNIT_STATE, "SELECT private_payload")
    assert not writer.store.writes
    assert not MetricAttemptBatch.objects.filter(attempt=attempt).exists()
    with pytest.raises(MetricStateConflict):
        writer.stage_and_publish(attempt)
    writer.seal(
        attempt,
        cursors={
            "watermark_ns": 42,
            "eval_watermark_ns": 19,
            "pending_backfill": False,
        },
    )
    manifest = writer.stage_and_publish(attempt)
    refresh = MetricProjectRefresh.objects.get(project_id=writer.lease.project_id)
    assert refresh.head_manifest_id == manifest.id
    assert (refresh.watermark_ns, refresh.eval_watermark_ns) == (42, 19)
    assert len(writer.store.writes) == 2
    assert manifest.visibility_receipts["batches"][0]["count"] == 1
    attempt.refresh_from_db()
    assert attempt.state == "committed"
    assert attempt.ledger_key_count == 7


@pytest.mark.django_db
def test_crash_resumes_frozen_payload_not_new_sources(writer):
    attempt = writer.begin(ledger={"dirty_before": [{"bucket": "month-before"}]})
    writer.freeze(attempt, FIELD_VALUES, "SELECT old_source")
    writer.freeze(attempt, UNIT_STATE, "SELECT old_source")
    writer.seal(attempt, cursors={"watermark_ns": 99})
    writer.store.fail_after = 1
    with pytest.raises(RuntimeError):
        writer.stage_and_publish(attempt)
    refresh = MetricProjectRefresh.objects.get(project_id=writer.lease.project_id)
    assert refresh.watermark_ns == 0 and refresh.head_manifest_id is None
    assert (
        MetricAttemptBatch.objects.filter(
            attempt=attempt, verified_at__isnull=False
        ).count()
        == 1
    )
    writer.store.payload = FrozenRows((b"changed source".hex(),))
    writer.store.fail_after = None
    manifest = writer.stage_and_publish(attempt)
    assert len(writer.store.writes) == 2  # verified batch was not resubmitted
    assert writer.store.writes[0][1] == writer.store.writes[1][1]
    # A publish-before-ack retry returns the SAME committed manifest.
    assert writer.stage_and_publish(attempt).id == manifest.id
    assert MetricReadManifest.objects.count() == 1


@pytest.mark.django_db
def test_expired_old_writer_cannot_publish_and_takeover_keeps_dirty_set(writer):
    attempt = writer.begin(
        ledger={
            "dirty_before": [{"unit_id": "only-old", "bucket": "old-month"}],
            "membership_preimage": [{"unit_start": "old-anchor"}],
        }
    )
    writer.freeze(attempt, FIELD_VALUES, "SELECT private_payload")
    writer.seal(attempt)
    future = timezone.now() + timedelta(minutes=6)
    grant = MetricProjectRefresh.take_lease(
        writer.lease.project_id,
        holder="replacement",
        ttl=timedelta(minutes=5),
        now=future,
    )
    replacement = RefreshWriter(
        ProjectLease(grant, clock=lambda: future), writer.store, org_id=writer.org_id
    )
    recovered = replacement.abort_stale()
    assert recovered["dirty_before"][0]["unit_id"] == "only-old"
    assert recovered["membership_preimage"][0]["unit_start"] == "old-anchor"
    with pytest.raises(MetricFencingError):
        writer.stage_and_publish(attempt)
    assert not writer.store.writes


@pytest.mark.django_db
def test_complete_ledger_is_append_only_and_chunks_bounded(writer):
    attempt = writer.begin(
        ledger={"dirty_before": [{"unit_id": str(i)} for i in range(10_001)]}
    )
    assert list(
        attempt.ledger_chunks.order_by("chunk_no").values_list("key_count", flat=True)
    ) == [10_000, 1]
    writer.seal(attempt)
    with pytest.raises(MetricStateConflict):
        writer.append(attempt, "dirty_after", [{"unit_id": "too-late"}])


def test_manifest_sql_filters_attempts_before_collapse_and_time():
    from uuid import uuid4

    project, org, allowed, aborted = (uuid4() for _ in range(4))
    manifest = SimpleNamespace(
        project_id=project,
        checkpoint_attempt_id=None,
        allowed_attempts=lambda: [(7, allowed)],
    )
    sql, params = committed_rows_select(
        FIELD_VALUES, manifest, org_id=org, project_id=project
    )
    assert "FINAL" not in sql and "generation <=" not in sql
    assert sql.index("attempt_id) IN") < sql.index("LIMIT 1 BY")
    assert params["pub_deltas"] == ((7, allowed),)
    assert str(aborted) not in str(params)


@pytest.mark.parametrize("days", [0, -1])
def test_unlimited_retention(days):
    assert retention_cutoff(days) is None


@pytest.mark.parametrize("count", [0, 501, 10_000])
def test_dependency_relation_rejects_unbounded_input_before_sql_construction(count):
    with pytest.raises(ValueError, match="one publication batch"):
        dependency_select([None] * count)


def test_binary_payload_errors_do_not_echo_private_data():
    with pytest.raises(ValueError, match="invalid frozen metric payload"):
        FrozenRows(("private-not-hex",))
    assert "private" not in repr(FrozenRows((b"private".hex(),)))
