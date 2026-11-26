"""Postgres/fake recorder proofs. Live races are in the ClickHouse suites."""

import hashlib
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from tracer.models.derived_metric import (
    MetricAttemptBatch,
    MetricAttemptLedgerChunk,
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

    def assert_checkpoint_schema(self):
        pass

    def checkpoint_group(self, project_id):
        return 0

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
def test_sealed_ledger_stream_preserves_original_digest_across_parts(writer):
    attempt = writer.begin()
    for part, entries in (
        ("vanished", [{"unit_id": "last"}]),
        ("dirty_before", [{"unit_id": "first"}, {"unit_id": "second"}]),
        ("dirty_after", [{"unit_id": "middle"}]),
    ):
        writer.append(attempt, part, entries)
    writer.seal(attempt)
    attempt.refresh_from_db()
    chunks = list(
        MetricAttemptLedgerChunk.objects.filter(attempt=attempt)
        .order_by("part", "chunk_no")
        .values_list("part", "chunk_no", "payload_sha256")
    )
    expected = hashlib.sha256(
        "\n".join(f"{part}:{no}:{sha}" for part, no, sha in chunks).encode()
    ).hexdigest()
    assert attempt.ledger_sha256 == expected
    assert attempt.ledger_chunk_count == 4
    assert attempt.ledger_key_count == 5


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
def test_large_frozen_page_cannot_bypass_normal_attempt_batch_cap(writer):
    from tracer.models.derived_metric import MetricLedgerConflict
    from tracer.services.derived_metrics.publication import MAX_COMPACTION_ROWS

    attempt = writer.begin()
    payload = FrozenRows(("00",) * 501, row_limit=MAX_COMPACTION_ROWS)
    writer.append(
        attempt,
        "cleanup_intent",
        [
            {
                "kind": "frozen_batch",
                "table": "field_values",
                "batch_no": 0,
                "rows": list(payload.rows),
                "row_limit": MAX_COMPACTION_ROWS,
                "sha256": payload.digest,
                "first": 1,
                "last": 501,
            }
        ],
    )
    writer.seal(attempt)
    with pytest.raises(MetricLedgerConflict, match="requires compaction"):
        writer.stage_and_publish(attempt)
    assert not writer.store.writes


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


def legacy_definition():
    return SimpleNamespace(
        selection_unit="user", level="conversation", kind="custom", user_rule=""
    )


@pytest.mark.django_db
def test_native_refresh_rejects_needs_rebuild_before_expiry_or_attempts(writer):
    from tracer.models.derived_metric import MetricRefreshAttempt
    from tracer.services.derived_metrics.changes import ChangeBatch
    from tracer.services.derived_metrics.errors import MetricInputError
    from tracer.services.derived_metrics.queries import Scope

    def forbidden():
        pytest.fail("legacy refresh reached retention policy loading")

    writer.retention_days = forbidden
    with pytest.raises(MetricInputError, match="needs_rebuild"):
        writer.refresh(
            ChangeBatch((), (), (), (), (), {}),
            [legacy_definition()],
            Scope(writer.org_id, writer.workspace_id, writer.lease.project_id),
            start=timezone.now() - timedelta(days=1),
            end=timezone.now(),
            as_of=timezone.now(),
        )
    assert not MetricRefreshAttempt.objects.exists()
    assert not writer.store.writes


def test_native_compiler_rejects_needs_rebuild_before_source_compilation(monkeypatch):
    from uuid import uuid4

    from tracer.services.derived_metrics import unit_reads
    from tracer.services.derived_metrics.errors import MetricInputError
    from tracer.services.derived_metrics.queries import Scope

    def forbidden(*args, **kwargs):
        pytest.fail("legacy compiler reached source-plan compilation")

    monkeypatch.setattr(unit_reads, "_source_plan", forbidden)
    with pytest.raises(MetricInputError, match="needs_rebuild"):
        unit_reads.unit_values_select(
            legacy_definition(),
            Scope(uuid4(), uuid4(), uuid4()),
            [uuid4()],
            start=timezone.now() - timedelta(days=1),
            end=timezone.now(),
            as_of=timezone.now(),
        )


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["pending", "complete"])
def test_direct_activation_cannot_activate_legacy_version(
    observe_project, workspace, status
):
    from tracer.models.derived_metric import DerivedMetric, DerivedMetricVersion
    from tracer.services.derived_metrics.errors import MetricInputError

    metric = DerivedMetric.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        project=observe_project,
        name="Legacy recipe",
    )
    version = DerivedMetricVersion.objects.create(
        metric=metric,
        version=1,
        formula="mean(value)",
        inputs=[{"alias": "value", "source": "span", "field": "latency_ms"}],
        selection_unit="user",
        weighting="none",
        definition_hash="a" * 64,
        backfill_status=status,
    )
    expected_error = MetricInputError if status == "complete" else MetricStateConflict
    with pytest.raises(expected_error):
        DerivedMetric.activate_version(metric.id, version.id, expected_active_id=None)
    metric.refresh_from_db()
    version.refresh_from_db()
    assert metric.active_version_id is None and version.activated_at is None


@pytest.mark.parametrize("filters", [None, [], [{"field": "provider", "value": "p"}]])
def test_native_compiler_saved_filters_are_mandatory_with_optional_narrowing(filters):
    from copy import deepcopy
    from uuid import uuid4

    from tracer.services.derived_metrics.queries import Scope
    from tracer.services.derived_metrics.unit_reads import unit_values_select
    from tracer.tests.test_derived_metric_clickhouse import recipe

    definition = recipe()
    definition.filters = [{"field": "model", "value": "m"}]
    before = deepcopy(definition.filters)
    _, params = unit_values_select(
        definition,
        Scope(uuid4(), uuid4(), uuid4()),
        [uuid4()],
        start=timezone.now() - timedelta(days=1),
        end=timezone.now(),
        as_of=timezone.now(),
        filters=filters,
    )
    assert params["filter_0"] == "m"
    if filters:
        assert params["filter_1"] == "p"
    assert definition.filters == before


@pytest.mark.django_db
@pytest.mark.parametrize("rollback", [False, True])
def test_logical_expiry_manifest_revocation_and_cleanup_intent_are_one_transaction(
    writer, monkeypatch, rollback
):
    from tracer.models.derived_metric import MetricErasureRequest, MetricRefreshAttempt
    from tracer.services.derived_metrics import deletion_protocol
    from tracer.services.derived_metrics.deletion_protocol import capture_pin

    initial = writer.begin()
    writer.freeze(initial, FIELD_VALUES, "SELECT original")
    writer.seal(initial, cursors={"watermark_ns": 1})
    original = writer.stage_and_publish(initial)
    pin = capture_pin(
        writer.lease.project_id, consumer_kind="snapshot", ttl=timedelta(hours=1)
    )
    stale = writer.begin()
    checkpoint = writer.begin(purpose="compaction", ceilings={"logical_expiry": True})
    writer.freeze(checkpoint, FIELD_VALUES, "SELECT safe")
    writer.seal(checkpoint, cursors={"watermark_ns": 2})
    commit = deletion_protocol.commit_logical_expiry

    def interrupt(*args, **kwargs):
        commit(*args, **kwargs)
        raise RuntimeError("rollback expiry transaction")

    if rollback:
        monkeypatch.setattr(deletion_protocol, "commit_logical_expiry", interrupt)
        with pytest.raises(RuntimeError, match="rollback expiry"):
            writer.stage_and_publish(checkpoint)
    else:
        writer.stage_and_publish(checkpoint)
    original.refresh_from_db()
    stale.refresh_from_db()
    current = MetricProjectRefresh.for_project(writer.lease.project_id)
    assert (current.watermark_ns, original.revoked_at is not None, pin.is_usable()) == (
        (1, False, True) if rollback else (2, True, False)
    )
    assert (stale.state == MetricRefreshAttempt.State.ABORTED) is not rollback
    assert (
        MetricErasureRequest.objects.filter(
            store_payload__protocol="metric_expiry"
        ).exists()
        is not rollback
    )


@pytest.mark.django_db
def test_expiry_fences_later_allocated_namespaces_and_keeps_idle_cleanup_scheduled(
    writer,
):
    from tracer.models.derived_metric import MetricErasureRequest

    checkpoint = writer.begin(purpose="compaction", ceilings={"logical_expiry": True})
    writer.freeze(checkpoint, FIELD_VALUES, "SELECT safe")
    writer.seal(checkpoint)
    later = writer.begin()
    writer.freeze(later, FIELD_VALUES, "SELECT obsolete_private")
    writer.seal(later)
    writer.stage_and_publish(checkpoint)
    later.refresh_from_db()
    assert later.generation > checkpoint.generation and later.state == "aborted"
    obligation = MetricErasureRequest.objects.get(
        store_payload__protocol="metric_expiry"
    )
    assert obligation.store_payload["through_generation"] >= later.generation
    assert MetricProjectRefresh.for_project(writer.lease.project_id).pending_expiry
    # B18's generic multi-store worker cannot claim a writer barrier as erasure.
    assert MetricErasureRequest.claim(obligation.id, ttl=timedelta(minutes=1)) is None
    with pytest.raises(MetricStateConflict):
        writer.stage_and_publish(later)


@pytest.mark.django_db
def test_published_checkpoint_schedules_gc_when_layout_maintenance_fails(
    writer, monkeypatch
):
    from django.db import transaction

    from tracer.services.derived_metrics.publication import MetricVisibilityError

    initial = writer.begin()
    writer.freeze(initial, FIELD_VALUES, "SELECT original")
    writer.seal(initial)
    writer.stage_and_publish(initial)

    # Isolate post-publication maintenance from this test's source reader.
    monkeypatch.setattr(writer, "freeze_current", lambda *_args, **_kwargs: None)
    callbacks = []
    monkeypatch.setattr(transaction, "on_commit", callbacks.append)

    def reject_shared_partition(_project, *, deadline=None):
        raise MetricVisibilityError("checkpoint partition consolidation cap exceeded")

    monkeypatch.setattr(
        writer.store, "consolidate_checkpoint", reject_shared_partition, raising=False
    )
    with pytest.raises(MetricVisibilityError, match="committed_layout_pending"):
        writer.compact()
    published = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
    assert published.published_by.purpose == "compaction"
    assert len(callbacks) == 1
    assert callbacks[0].__self__ is writer
    assert callbacks[0].__func__ is writer.collect_garbage.__func__
    from tracer.models.derived_metric import MetricErasureRequest, MetricRefreshAttempt

    pending = MetricErasureRequest.objects.get(
        deletion_key=f"checkpoint-layout:{published.published_by_id}"
    )
    assert pending.status == "queued"
    # An incremental head must retain the checkpoint's maintenance obligation.
    incremental = writer.begin()
    writer.seal(incremental)
    descendant = writer.stage_and_publish(incremental)
    assert descendant.checkpoint_attempt_id == published.checkpoint_attempt_id
    attempts = MetricRefreshAttempt.objects.count()
    monkeypatch.setattr(
        writer.store, "consolidate_checkpoint", lambda *_args, **_kwargs: None
    )
    compacted = writer.compact()
    assert compacted.id != descendant.id
    assert compacted.published_by.parent_manifest_id == descendant.id
    assert (
        MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest_id
        == compacted.id
    )
    assert MetricRefreshAttempt.objects.count() == attempts + 1
    pending.refresh_from_db()
    assert pending.status == "done"
    assert len(callbacks) == 3


@pytest.mark.django_db
def test_batch_number_seed_is_once_per_table_and_resume_uses_durable_max(writer):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    attempt = writer.begin()
    with CaptureQueriesContext(connection) as queries:
        for _ in range(6):
            writer.freeze(attempt, FIELD_VALUES, "SELECT frozen")
        for _ in range(3):
            writer.freeze(attempt, UNIT_STATE, "SELECT frozen")
    seeds = [
        q["sql"] for q in queries if "MAX(" in q["sql"] and '"payload"' in q["sql"]
    ]
    assert len(seeds) == 2
    assert not any("COUNT(" in q["sql"] and '"payload"' in q["sql"] for q in queries)
    assert [p["batch_no"] for p in writer.plans(attempt, kind="field_values")] == list(
        range(6)
    )
    assert [p["batch_no"] for p in writer.plans(attempt, kind="unit_state")] == list(
        range(3)
    )
    # A durable historical gap must not make COUNT allocate an existing number.
    prior = next(writer.plans(attempt, kind="field_values"))
    writer.append(attempt, "cleanup_intent", [{**prior, "batch_no": 20}])
    resumed = RefreshWriter(writer.lease, writer.store, org_id=writer.org_id)
    resumed.freeze(attempt, FIELD_VALUES, "SELECT frozen")
    assert [p["batch_no"] for p in resumed.plans(attempt, kind="field_values")] == [
        *range(6),
        20,
        21,
    ]
    resumed.freeze(attempt, UNIT_STATE, "SELECT frozen")
    assert [p["batch_no"] for p in resumed.plans(attempt, kind="unit_state")] == list(
        range(4)
    )


@pytest.mark.django_db
def test_batch_number_lost_append_acknowledgement_reseeds_same_writer(
    writer, monkeypatch
):
    attempt = writer.begin()
    original = writer.append

    def lose_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("lost append acknowledgement")

    with monkeypatch.context() as patch:
        patch.setattr(writer, "append", lose_ack)
        with pytest.raises(RuntimeError, match="lost append"):
            writer.freeze(attempt, FIELD_VALUES, "SELECT frozen")
    writer.freeze(attempt, FIELD_VALUES, "SELECT frozen")
    assert [p["batch_no"] for p in writer.plans(attempt)] == [0, 1]
    writer.seal(attempt)
    writer.stage_and_publish(attempt)
    # A process restart at the staging boundary replays only the sealed bytes.
    resumed = RefreshWriter(writer.lease, writer.store, org_id=writer.org_id)
    writes = list(writer.store.writes)
    resumed.stage_and_publish(attempt)
    assert writer.store.writes == writes


@pytest.mark.django_db
def test_batch_counter_does_not_survive_rolled_back_preparation(writer):
    from django.db import transaction

    attempt = writer.begin()
    writer.freeze(attempt, FIELD_VALUES, "SELECT frozen")
    with pytest.raises(RuntimeError, match="rollback"):
        with transaction.atomic():
            writer.freeze(attempt, FIELD_VALUES, "SELECT frozen")
            writer.freeze(attempt, FIELD_VALUES, "SELECT frozen")
            raise RuntimeError("rollback")
    writer.freeze(attempt, FIELD_VALUES, "SELECT frozen")
    assert [p["batch_no"] for p in writer.plans(attempt)] == [0, 1]
