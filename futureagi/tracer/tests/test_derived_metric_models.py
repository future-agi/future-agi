"""Postgres models for metric definitions, refresh publication and snapshots.

PG-only: no ClickHouse is needed. Every check runs against real Postgres
constraints and compare-and-set updates.
"""

import hashlib
import logging
from datetime import timedelta
from uuid import uuid4

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tracer.models.derived_metric import (
    LEDGER_CHUNK_MAX_KEYS,
    MAX_FENCING_EPOCH,
    MAX_MANIFEST_DELTAS,
    MAX_ROW_SEQUENCE,
    PIN_MAX_LIFETIME,
    DerivedMetric,
    DerivedMetricMonitorDelivery,
    DerivedMetricRun,
    DerivedMetricVersion,
    MetricAttemptBatch,
    MetricAttemptLedgerChunk,
    MetricErasureRequest,
    MetricFencingError,
    MetricGenerationPin,
    MetricLedgerConflict,
    MetricLedgerExhausted,
    MetricProjectRefresh,
    MetricReadManifest,
    MetricRefreshAttempt,
    MetricStateConflict,
    row_version,
)
from tracer.models.monitor import UserAlertMonitor

LEASE = timedelta(minutes=5)
LEDGER_TABLE = MetricAttemptLedgerChunk._meta.db_table


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _metric(workspace, project, name="Resolution rate"):
    return DerivedMetric.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        project=project,
        name=name,
    )


def _version(metric, number=1, **fields):
    defaults = {
        "formula": "ratio(resolved == 1, resolved >= 0)",
        "inputs": [{"alias": "resolved", "source": "span"}],
        "selection_unit": "session",
        "weighting": "none",
        "definition_hash": _sha(f"{metric.id}:{number}"),
    }
    defaults.update(fields)
    return DerivedMetricVersion.objects.create(
        metric=metric, version=number, **defaults
    )


def _lease(project, now, holder="writer-a"):
    grant = MetricProjectRefresh.take_lease(
        project.id, holder=holder, ttl=LEASE, now=now
    )
    assert grant is not None
    return grant


def _prepare(project, grant, now, purpose="incremental"):
    return MetricRefreshAttempt.prepare(
        project.id,
        epoch=grant.fencing_epoch,
        token=grant.lease_token,
        purpose=purpose,
        source_ceilings={"watermark_ns": 10},
        metric_versions=[{"version": "v1"}],
        now=now,
    )


def _stage_and_verify(attempt, grant, now, keys=("c1", "c2")):
    fence = {"epoch": grant.fencing_epoch, "token": grant.lease_token, "now": now}
    MetricRefreshAttempt.append_ledger_chunk(
        attempt.id, part="dirty_after", chunk_no=0, keys=list(keys), **fence
    )
    MetricRefreshAttempt.seal_ledger(
        attempt.id, expected_chunks=1, expected_keys=len(keys), **fence
    )
    MetricRefreshAttempt.transition(
        attempt.id, from_state="prepared", to_state="staging", **fence
    )
    batch = MetricAttemptBatch.freeze(
        attempt.id,
        table_name="field_values",
        batch_no=0,
        row_count=len(keys),
        payload_sha256=_sha(f"batch:{attempt.id}"),
        **fence,
    )
    MetricAttemptBatch.record_receipt(
        batch.id,
        visible_row_count=len(keys),
        visible_sha256=_sha(f"batch:{attempt.id}"),
        **fence,
    )
    MetricRefreshAttempt.transition(
        attempt.id, from_state="staging", to_state="verified", **fence
    )


def _commit(project, grant, now, purpose="incremental", cursors=None):
    attempt = _prepare(project, grant, now, purpose)
    _stage_and_verify(attempt, grant, now)
    manifest = MetricReadManifest.publish(
        attempt.id,
        epoch=grant.fencing_epoch,
        token=grant.lease_token,
        cursors=cursors,
        now=now,
    )
    return attempt, manifest


# --- defaults and constraints -------------------------------------------------


@pytest.mark.django_db
def test_defaults(workspace, observe_project):
    refresh = MetricProjectRefresh.for_project(observe_project.id)
    assert (refresh.settle_minutes, refresh.cadence_seconds) == (30, 900)
    assert (refresh.fencing_epoch, refresh.generation_high_water) == (0, 0)
    assert refresh.row_sequence_high_water == 0
    assert refresh.head_manifest_id is None
    assert not any(
        [
            refresh.pending_backfill,
            refresh.pending_expiry,
            refresh.pending_repair,
            refresh.pending_erasure,
        ]
    )
    assert MetricProjectRefresh.for_project(observe_project.id).id == refresh.id

    metric = _metric(workspace, observe_project)
    version = _version(metric)
    assert metric.active_version_id is None
    assert metric.active_semantics() is None
    assert (version.kind, version.level, version.user_rule) == (
        "custom",
        "conversation",
        "",
    )
    assert version.group_dims == [] and version.value_labels == {}
    assert version.backfill_status == "pending" and version.activated_at is None

    now = timezone.now()
    run = DerivedMetricRun.objects.create(
        definition=version,
        window_start=now - timedelta(days=1),
        window_end=now,
        as_of=now,
        fingerprint=uuid4().hex,
    )
    assert (run.kind, run.cleanup_status, run.removed_unit_count) == ("run", "", 0)


@pytest.mark.django_db
def test_group_dims_are_capped_at_two(workspace, observe_project):
    metric = _metric(workspace, observe_project)
    _version(metric, 1, group_dims=["region", "plan"])
    with pytest.raises(IntegrityError), transaction.atomic():
        _version(metric, 2, group_dims=["region", "plan", "locale"])
    draft = DerivedMetricVersion(
        metric=metric,
        version=3,
        formula="x",
        inputs=[],
        selection_unit="session",
        weighting="none",
        definition_hash="0" * 64,
        group_dims=["a", "b", "c"],
    )
    with pytest.raises(ValidationError) as error:
        draft.clean()
    assert "group_dims" in error.value.message_dict


@pytest.mark.django_db
@pytest.mark.parametrize(
    "minutes,ok", [(0, False), (1, True), (1440, True), (1441, False)]
)
def test_settle_minutes_range(observe_project, minutes, ok):
    refresh = MetricProjectRefresh.for_project(observe_project.id)
    update = MetricProjectRefresh.objects.filter(id=refresh.id)
    if ok:
        update.update(settle_minutes=minutes)
        refresh.refresh_from_db()
        assert refresh.settle_minutes == minutes
    else:
        with pytest.raises(IntegrityError), transaction.atomic():
            update.update(settle_minutes=minutes)


@pytest.mark.django_db
def test_one_refresh_row_per_project(observe_project):
    MetricProjectRefresh.for_project(observe_project.id)
    with pytest.raises(IntegrityError), transaction.atomic():
        MetricProjectRefresh.objects.create(project=observe_project)


@pytest.mark.django_db
def test_ledger_key_is_unique_and_write_once(observe_project):
    now = timezone.now()
    grant = _lease(observe_project, now)
    attempt = _prepare(observe_project, grant, now)
    fence = {"epoch": grant.fencing_epoch, "token": grant.lease_token, "now": now}
    first = MetricRefreshAttempt.append_ledger_chunk(
        attempt.id, part="dirty_before", chunk_no=0, keys=["a", "b"], **fence
    )
    # An identical retry is a no-op; different content under the same key is refused.
    again = MetricRefreshAttempt.append_ledger_chunk(
        attempt.id, part="dirty_before", chunk_no=0, keys=["a", "b"], **fence
    )
    assert again.id == first.id
    with pytest.raises(MetricLedgerConflict):
        MetricRefreshAttempt.append_ledger_chunk(
            attempt.id, part="dirty_before", chunk_no=0, keys=["a", "c"], **fence
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        MetricAttemptLedgerChunk.objects.create(
            attempt=attempt,
            part="dirty_before",
            chunk_no=0,
            key_count=1,
            payload=["z"],
            payload_sha256=_sha("z"),
        )
    with pytest.raises(MetricLedgerConflict):
        first.payload = ["rewritten"]
        first.save()
    with pytest.raises(ValueError):
        MetricRefreshAttempt.append_ledger_chunk(
            attempt.id,
            part="dirty_before",
            chunk_no=1,
            keys=["k"] * (LEDGER_CHUNK_MAX_KEYS + 1),
            **fence,
        )
    first.refresh_from_db()
    assert first.payload == ["a", "b"]


@pytest.mark.django_db(transaction=True)
def test_migration_rolls_back_to_0125_and_forward():
    executor = MigrationExecutor(connection)
    previous = [("tracer", "0125_merge_metrics_and_dev_tracer_leaf")]
    current = [("tracer", "0126_derived_metric_refresh")]
    new_tables = {
        "tracer_metricprojectrefresh",
        "tracer_metricrefreshattempt",
        "tracer_metricattemptledgerchunk",
        "tracer_metricattemptbatch",
        "tracer_metricreadmanifest",
        "tracer_metricgenerationpin",
        "tracer_metricerasurerequest",
    }

    def columns(table):
        with connection.cursor() as cursor:
            return {
                column.name
                for column in connection.introspection.get_table_description(
                    cursor, table
                )
            }

    try:
        executor.migrate(previous)
        tables = set(connection.introspection.table_names())
        assert not new_tables & tables
        assert "active_version_id" not in columns("tracer_derivedmetric")
        assert "user_rule" not in columns("tracer_derivedmetricversion")

        executor.loader.build_graph()
        executor.migrate(current)
        assert new_tables <= set(connection.introspection.table_names())
        assert "active_version_id" in columns("tracer_derivedmetric")
        assert {"kind", "level", "user_rule", "group_dims"} <= columns(
            "tracer_derivedmetricversion"
        )
    finally:
        executor.loader.build_graph()
        executor.migrate(current)


# --- generation, epoch and sequence allocation --------------------------------


@pytest.mark.django_db
def test_generation_and_epoch_are_monotone_and_fail_closed(observe_project):
    now = timezone.now()
    first = _lease(observe_project, now)
    assert first.fencing_epoch == 1
    # Held: nobody else gets it.
    assert (
        MetricProjectRefresh.take_lease(
            observe_project.id, holder="writer-b", ttl=LEASE, now=now
        )
        is None
    )
    generations = [_prepare(observe_project, first, now).generation for _ in range(3)]
    assert generations == [1, 2, 3]

    # After expiry a takeover gets the next epoch; generations keep growing.
    later = now + LEASE + timedelta(seconds=1)
    second = _lease(observe_project, later, holder="writer-b")
    assert second.fencing_epoch == 2
    assert _prepare(observe_project, second, later).generation == 4
    with pytest.raises(MetricFencingError):
        _prepare(observe_project, first, later)

    # Row sequences are epoch-wide and never reset inside an epoch.
    fence = {"epoch": second.fencing_epoch, "token": second.lease_token, "now": later}
    assert MetricProjectRefresh.allocate_row_sequences(
        observe_project.id, count=10, **fence
    ) == (1, 10)
    assert MetricProjectRefresh.allocate_row_sequences(
        observe_project.id, count=5, **fence
    ) == (11, 15)
    MetricProjectRefresh.objects.filter(project_id=observe_project.id).update(
        row_sequence_high_water=MAX_ROW_SEQUENCE - 1
    )
    assert MetricProjectRefresh.allocate_row_sequences(
        observe_project.id, count=1, **fence
    ) == (MAX_ROW_SEQUENCE, MAX_ROW_SEQUENCE)
    with pytest.raises(MetricLedgerExhausted):
        MetricProjectRefresh.allocate_row_sequences(
            observe_project.id, count=1, **fence
        )

    # A new epoch restarts the sequence; the epoch itself cannot wrap.
    third = _lease(observe_project, later + LEASE + timedelta(seconds=1), "writer-c")
    assert third.fencing_epoch == 3
    assert MetricProjectRefresh.allocate_row_sequences(
        observe_project.id,
        count=1,
        epoch=third.fencing_epoch,
        token=third.lease_token,
        now=later + LEASE + timedelta(seconds=1),
    ) == (1, 1)
    MetricProjectRefresh.objects.filter(project_id=observe_project.id).update(
        fencing_epoch=MAX_FENCING_EPOCH, lease_token=None, lease_expires_at=None
    )
    with pytest.raises(MetricLedgerExhausted):
        _lease(observe_project, later + timedelta(hours=1))
    with pytest.raises(IntegrityError), transaction.atomic():
        MetricProjectRefresh.objects.filter(project_id=observe_project.id).update(
            fencing_epoch=MAX_FENCING_EPOCH + 1
        )

    assert row_version(2, 15) == (2 << 32) | 15
    for epoch, sequence in [
        (0, 1),
        (MAX_FENCING_EPOCH + 1, 1),
        (1, 0),
        (1, MAX_ROW_SEQUENCE + 1),
    ]:
        with pytest.raises(MetricLedgerExhausted):
            row_version(epoch, sequence)


# --- attempt ledger and state machine ------------------------------------------


@pytest.mark.django_db
def test_attempt_hashes_are_immutable(observe_project):
    now = timezone.now()
    grant = _lease(observe_project, now)
    attempt = _prepare(observe_project, grant, now)
    prepared_hash = attempt.prepared_sha256
    assert len(prepared_hash) == 64
    attempt.prepared_sha256 = "0" * 64
    with pytest.raises(MetricLedgerConflict):
        attempt.save()

    fence = {"epoch": grant.fencing_epoch, "token": grant.lease_token, "now": now}
    MetricRefreshAttempt.append_ledger_chunk(
        attempt.id, part="dirty_after", chunk_no=0, keys=["c1"], **fence
    )
    ledger_hash = MetricRefreshAttempt.seal_ledger(
        attempt.id, expected_chunks=1, expected_keys=1, **fence
    )
    with pytest.raises(MetricStateConflict):
        MetricRefreshAttempt.seal_ledger(
            attempt.id, expected_chunks=1, expected_keys=1, **fence
        )
    MetricRefreshAttempt.transition(
        attempt.id, from_state="prepared", to_state="staging", **fence
    )
    batch = MetricAttemptBatch.freeze(
        attempt.id,
        table_name="bucket_totals",
        batch_no=0,
        row_count=3,
        payload_sha256=_sha("p"),
        **fence,
    )
    # An equal retry returns the frozen batch; a different payload is refused.
    assert (
        MetricAttemptBatch.freeze(
            attempt.id,
            table_name="bucket_totals",
            batch_no=0,
            row_count=3,
            payload_sha256=_sha("p"),
            **fence,
        ).id
        == batch.id
    )
    with pytest.raises(MetricLedgerConflict):
        MetricAttemptBatch.freeze(
            attempt.id,
            table_name="bucket_totals",
            batch_no=0,
            row_count=3,
            payload_sha256=_sha("q"),
            **fence,
        )
    with pytest.raises(MetricLedgerConflict):
        MetricAttemptBatch.record_receipt(
            batch.id, visible_row_count=2, visible_sha256=_sha("p"), **fence
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        MetricAttemptBatch.objects.filter(id=batch.id).update(
            visible_row_count=2, visible_sha256=_sha("p"), verified_at=now
        )

    attempt.refresh_from_db()
    assert attempt.prepared_sha256 == prepared_hash
    assert attempt.ledger_sha256 == ledger_hash
    assert (attempt.ledger_chunk_count, attempt.ledger_key_count) == (1, 1)


@pytest.mark.django_db
def test_complete_ledger_is_required_before_staging(observe_project):
    now = timezone.now()
    grant = _lease(observe_project, now)
    attempt = _prepare(observe_project, grant, now)
    fence = {"epoch": grant.fencing_epoch, "token": grant.lease_token, "now": now}

    with pytest.raises(MetricStateConflict, match="not sealed"):
        MetricRefreshAttempt.transition(
            attempt.id, from_state="prepared", to_state="staging", **fence
        )
    # Batches cannot be frozen (so nothing can be submitted) before staging.
    with pytest.raises(MetricStateConflict):
        MetricAttemptBatch.freeze(
            attempt.id,
            table_name="field_values",
            batch_no=0,
            row_count=1,
            payload_sha256=_sha("x"),
            **fence,
        )
    # The database refuses a staged attempt without a sealed ledger.
    with pytest.raises(IntegrityError), transaction.atomic():
        MetricRefreshAttempt.objects.filter(id=attempt.id).update(state="staging")

    for part, chunk_no, keys in [
        ("dirty_before", 0, ["b1", "b2"]),
        ("dirty_after", 0, ["a1"]),
        ("vanished", 0, ["v1"]),
        ("membership_preimage", 0, ["s1->c1"]),
    ]:
        MetricRefreshAttempt.append_ledger_chunk(
            attempt.id, part=part, chunk_no=chunk_no, keys=keys, **fence
        )
    with pytest.raises(MetricLedgerConflict, match="incomplete"):
        MetricRefreshAttempt.seal_ledger(
            attempt.id, expected_chunks=5, expected_keys=6, **fence
        )
    MetricRefreshAttempt.append_ledger_chunk(
        attempt.id, part="dirty_before", chunk_no=2, keys=["b3"], **fence
    )
    with pytest.raises(MetricLedgerConflict, match="contiguous"):
        MetricRefreshAttempt.seal_ledger(
            attempt.id, expected_chunks=5, expected_keys=6, **fence
        )
    MetricRefreshAttempt.append_ledger_chunk(
        attempt.id, part="dirty_before", chunk_no=1, keys=["b4"], **fence
    )
    MetricRefreshAttempt.seal_ledger(
        attempt.id, expected_chunks=6, expected_keys=7, **fence
    )
    with pytest.raises(MetricStateConflict, match="sealed"):
        MetricRefreshAttempt.append_ledger_chunk(
            attempt.id, part="dirty_after", chunk_no=1, keys=["late"], **fence
        )
    MetricRefreshAttempt.transition(
        attempt.id, from_state="prepared", to_state="staging", **fence
    )
    batch = MetricAttemptBatch.freeze(
        attempt.id,
        table_name="field_values",
        batch_no=0,
        row_count=1,
        payload_sha256=_sha("x"),
        **fence,
    )
    # Verified needs a receipt for every frozen batch.
    with pytest.raises(MetricStateConflict, match="1 batches"):
        MetricRefreshAttempt.transition(
            attempt.id, from_state="staging", to_state="verified", **fence
        )
    MetricAttemptBatch.record_receipt(
        batch.id, visible_row_count=1, visible_sha256=_sha("x"), **fence
    )
    MetricRefreshAttempt.transition(
        attempt.id, from_state="staging", to_state="verified", **fence
    )
    attempt.refresh_from_db()
    assert attempt.state == "verified" and attempt.ledger_key_count == 7


@pytest.mark.django_db
def test_state_changes_never_touch_the_dirty_key_ledger(observe_project):
    """DCM1: transitions move a small row; the key set is never copied or re-read."""

    now = timezone.now()
    grant = _lease(observe_project, now)
    attempt = _prepare(observe_project, grant, now)
    fence = {"epoch": grant.fencing_epoch, "token": grant.lease_token, "now": now}
    for chunk_no in range(3):
        MetricRefreshAttempt.append_ledger_chunk(
            attempt.id,
            part="dirty_after",
            chunk_no=chunk_no,
            keys=[f"k{chunk_no}-{i}" for i in range(500)],
            **fence,
        )
    MetricRefreshAttempt.seal_ledger(
        attempt.id, expected_chunks=3, expected_keys=1500, **fence
    )
    with CaptureQueriesContext(connection) as queries:
        MetricRefreshAttempt.transition(
            attempt.id, from_state="prepared", to_state="staging", **fence
        )
        batch = MetricAttemptBatch.freeze(
            attempt.id,
            table_name="field_values",
            batch_no=0,
            row_count=1500,
            payload_sha256=_sha("rows"),
            **fence,
        )
        MetricAttemptBatch.record_receipt(
            batch.id, visible_row_count=1500, visible_sha256=_sha("rows"), **fence
        )
        MetricRefreshAttempt.transition(
            attempt.id, from_state="staging", to_state="verified", **fence
        )
        MetricReadManifest.publish(
            attempt.id, epoch=grant.fencing_epoch, token=grant.lease_token, now=now
        )
    sql = [query["sql"] for query in queries.captured_queries]
    assert sql, "expected the state changes to run queries"
    assert not [statement for statement in sql if LEDGER_TABLE in statement]
    attempt_updates = [
        statement
        for statement in sql
        if statement.startswith('UPDATE "tracer_metricrefreshattempt"')
    ]
    assert len(attempt_updates) == 3
    assert all('"payload"' not in statement for statement in attempt_updates)
    assert MetricAttemptLedgerChunk.objects.filter(attempt=attempt).count() == 3


@pytest.mark.django_db
def test_project_hard_delete_removes_the_whole_ledger(workspace, observe_project):
    now = timezone.now()
    grant = _lease(observe_project, now)
    _commit(observe_project, grant, now)
    _commit(observe_project, grant, now, purpose="compaction")
    _commit(observe_project, grant, now)
    open_attempt = _prepare(observe_project, grant, now)
    MetricRefreshAttempt.append_ledger_chunk(
        open_attempt.id,
        part="dirty_before",
        chunk_no=0,
        keys=["k"],
        epoch=grant.fencing_epoch,
        token=grant.lease_token,
        now=now,
    )
    MetricGenerationPin.pin_head(
        observe_project.id, consumer_kind="request", ttl=timedelta(minutes=1), now=now
    )
    MetricErasureRequest.record(
        organization_id=workspace.organization_id,
        project_id=observe_project.id,
        scope_kind="project",
        now=now,
    )
    metric = _metric(workspace, observe_project)
    version = _version(metric, 1, backfill_status="complete")
    DerivedMetric.activate_version(
        metric.id, version.id, expected_active_id=None, now=now
    )

    type(observe_project).objects.filter(id=observe_project.id).delete()

    for model in (
        MetricProjectRefresh,
        MetricRefreshAttempt,
        MetricAttemptLedgerChunk,
        MetricAttemptBatch,
        MetricReadManifest,
        MetricGenerationPin,
        MetricErasureRequest,
        DerivedMetricVersion,
    ):
        assert not model.objects.exists(), model.__name__
    assert not DerivedMetric.all_objects.filter(id=metric.id).exists()


# --- fenced publication, manifests and pins ------------------------------------


@pytest.mark.django_db
def test_fenced_head_publication(observe_project):
    now = timezone.now()
    grant = _lease(observe_project, now)
    first, manifest_1 = _commit(
        observe_project,
        grant,
        now,
        cursors={"watermark_ns": 100, "pending_backfill": True},
    )
    refresh = MetricProjectRefresh.objects.get(project_id=observe_project.id)
    assert refresh.head_manifest_id == manifest_1.id
    assert (refresh.watermark_ns, refresh.pending_backfill) == (100, True)
    assert manifest_1.allowed_attempts() == [(first.generation, first.id)]
    assert manifest_1.parent_id is None

    # An aborted attempt leaves a generation gap and is never visible.
    aborted = _prepare(observe_project, grant, now)
    MetricRefreshAttempt.abort(
        aborted.id,
        epoch=grant.fencing_epoch,
        token=grant.lease_token,
        reason="writer_crashed",
        now=now,
    )
    second, manifest_2 = _commit(
        observe_project, grant, now, cursors={"watermark_ns": 200}
    )
    assert manifest_2.allowed_attempts() == [
        (first.generation, first.id),
        (second.generation, second.id),
    ]
    assert aborted.generation not in [g for g, _ in manifest_2.allowed_attempts()]
    assert manifest_2.parent_id == manifest_1.id

    # Cursors never move backwards, and unknown fields are refused.
    stale_cursor = _prepare(observe_project, grant, now)
    _stage_and_verify(stale_cursor, grant, now)
    with pytest.raises(MetricStateConflict, match="backwards"):
        MetricReadManifest.publish(
            stale_cursor.id,
            epoch=grant.fencing_epoch,
            token=grant.lease_token,
            cursors={"watermark_ns": 150},
            now=now,
        )
    with pytest.raises(ValueError):
        MetricReadManifest.publish(
            stale_cursor.id,
            epoch=grant.fencing_epoch,
            token=grant.lease_token,
            cursors={"head_manifest": None},
            now=now,
        )
    MetricRefreshAttempt.abort(
        stale_cursor.id,
        epoch=grant.fencing_epoch,
        token=grant.lease_token,
        reason="cursor_rejected",
        now=now,
    )

    # A stale writer: its lease lapses, a new epoch takes over and publishes.
    in_flight = _prepare(observe_project, grant, now)
    _stage_and_verify(in_flight, grant, now)
    later = now + LEASE + timedelta(seconds=1)
    takeover = _lease(observe_project, later, holder="writer-b")
    with pytest.raises(MetricFencingError):
        MetricReadManifest.publish(
            in_flight.id, epoch=grant.fencing_epoch, token=grant.lease_token, now=later
        )
    with pytest.raises(MetricFencingError):
        MetricRefreshAttempt.transition(
            _prepare_raw(observe_project, grant, now).id,
            epoch=grant.fencing_epoch,
            token=grant.lease_token,
            from_state="prepared",
            to_state="staging",
            now=later,
        )
    # The new epoch may not publish the old epoch's attempt either.
    with pytest.raises(MetricFencingError):
        MetricReadManifest.publish(
            in_flight.id,
            epoch=takeover.fencing_epoch,
            token=takeover.lease_token,
            now=later,
        )
    MetricRefreshAttempt.abort(
        in_flight.id,
        epoch=takeover.fencing_epoch,
        token=takeover.lease_token,
        reason="lease_lost",
        now=later,
    )
    third, manifest_3 = _commit(observe_project, takeover, later)
    assert in_flight.id not in [a for _, a in manifest_3.allowed_attempts()]
    in_flight.refresh_from_db()
    assert in_flight.state == "aborted" and in_flight.abort_reason == "lease_lost"

    # An attempt prepared against an older head cannot publish over a newer one.
    behind = _prepare(observe_project, takeover, later)
    ahead, _ = _commit(observe_project, takeover, later)
    _stage_and_verify(behind, takeover, later)
    with pytest.raises(MetricStateConflict, match="head moved"):
        MetricReadManifest.publish(
            behind.id,
            epoch=takeover.fencing_epoch,
            token=takeover.lease_token,
            now=later,
        )

    # Manifests are immutable.
    manifest_3.visibility_receipts = {"rewritten": True}
    with pytest.raises(MetricLedgerConflict):
        manifest_3.save()


def _prepare_raw(project, grant, now):
    # Prepared while the lease was still valid (now), used later by a stale writer.
    return MetricRefreshAttempt.objects.create(
        project_id=project.id,
        generation=MetricProjectRefresh.objects.get(
            project_id=project.id
        ).generation_high_water
        + 1000,
        fencing_epoch=grant.fencing_epoch,
        lease_token=grant.lease_token,
        purpose="incremental",
        prepared_sha256=_sha("raw"),
        created_at=now,
        expires_at=now + timedelta(days=1),
    )


@pytest.mark.django_db
def test_compaction_resets_the_delta_chain_and_the_chain_is_bounded(observe_project):
    now = timezone.now()
    grant = _lease(observe_project, now)
    committed = [_commit(observe_project, grant, now)[0] for _ in range(3)]
    compaction, checkpoint = _commit(observe_project, grant, now, purpose="compaction")
    assert checkpoint.allowed_attempts() == [(compaction.generation, compaction.id)]
    after, manifest = _commit(observe_project, grant, now)
    assert manifest.allowed_attempts() == [
        (compaction.generation, compaction.id),
        (after.generation, after.id),
    ]
    assert not {a.id for a in committed} & {a for _, a in manifest.allowed_attempts()}

    for _ in range(MAX_MANIFEST_DELTAS - 1):
        _commit(observe_project, grant, now)
    attempt = _prepare(observe_project, grant, now)
    _stage_and_verify(attempt, grant, now)
    with pytest.raises(MetricLedgerExhausted, match="compact first"):
        MetricReadManifest.publish(
            attempt.id, epoch=grant.fencing_epoch, token=grant.lease_token, now=now
        )
    attempt.refresh_from_db()
    assert attempt.state == "verified"


@pytest.mark.django_db
def test_pins_expire_and_erasure_invalidates_them(workspace, observe_project):
    now = timezone.now()
    with pytest.raises(MetricStateConflict):
        MetricGenerationPin.pin_head(
            observe_project.id,
            consumer_kind="request",
            ttl=timedelta(minutes=1),
            now=now,
        )
    grant = _lease(observe_project, now)
    _, manifest = _commit(observe_project, grant, now)
    version = _version(_metric(workspace, observe_project))

    request_pin = MetricGenerationPin.pin_head(
        observe_project.id, consumer_kind="request", ttl=timedelta(minutes=1), now=now
    )
    snapshot_pin = MetricGenerationPin.pin_head(
        observe_project.id,
        consumer_kind="snapshot",
        ttl=timedelta(hours=1),
        consumer_ref="snapshot-run",
        definition_version_id=version.id,
        now=now,
    )
    assert request_pin.manifest_id == snapshot_pin.manifest_id == manifest.id
    assert request_pin.is_usable(now) and snapshot_pin.is_usable(now)
    assert not request_pin.is_usable(now + timedelta(minutes=2))
    assert MetricGenerationPin.manifest_in_use(manifest.id, now + timedelta(minutes=2))

    assert (
        MetricGenerationPin.invalidate(observe_project.id, reason="erased", now=now)
        == 2
    )
    snapshot_pin.refresh_from_db()
    assert not snapshot_pin.is_usable(now)
    assert snapshot_pin.invalidation_reason == "erased"
    assert not MetricGenerationPin.manifest_in_use(manifest.id, now)
    # Invalidation is not undone by a later, different reason.
    assert (
        MetricGenerationPin.invalidate(
            observe_project.id, reason="retention_expired", now=now
        )
        == 0
    )

    with pytest.raises(ValueError):
        MetricGenerationPin.pin_head(
            observe_project.id,
            consumer_kind="request",
            ttl=PIN_MAX_LIFETIME + timedelta(seconds=1),
            now=now,
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        MetricGenerationPin.objects.create(
            project=observe_project,
            manifest=manifest,
            consumer_kind="request",
            captured_at=now,
            expires_at=now + PIN_MAX_LIFETIME + timedelta(seconds=1),
        )


# --- versions: active pointer, immutability, user rules ------------------------


@pytest.mark.django_db
def test_active_version_semantics_stay_old_during_an_edit(workspace, observe_project):
    now = timezone.now()
    metric = _metric(workspace, observe_project)
    v1 = _version(metric, 1, kind="share", value_type="boolean")
    with pytest.raises(MetricStateConflict):
        DerivedMetric.activate_version(
            metric.id, v1.id, expected_active_id=None, now=now
        )
    DerivedMetricVersion.objects.filter(id=v1.id).update(
        backfill_status="complete", backfill_days_done=30, backfill_days_total=30
    )
    DerivedMetric.activate_version(metric.id, v1.id, expected_active_id=None, now=now)
    metric.refresh_from_db()
    before = metric.active_semantics()
    assert before["level"] == "conversation" and before["group_dims"] == []

    # Edit grain, grouping and labels: a new version, backfilling.
    v2 = _version(
        metric,
        2,
        kind="share",
        value_type="boolean",
        level="span",
        selection_unit="span",
        group_dims=["region"],
        value_labels={"match": "Resolved", "other": "Not resolved"},
    )
    DerivedMetric._base_manager.filter(id=metric.id).update(current_version=2)
    for done in (0, 12, 29):
        v2.backfill_status = "running"
        v2.backfill_days_done, v2.backfill_days_total = done, 30
        v2.save(
            update_fields=[
                "backfill_status",
                "backfill_days_done",
                "backfill_days_total",
            ]
        )
        metric.refresh_from_db()
        assert metric.active_version_id == v1.id
        assert metric.active_semantics() == before
        with pytest.raises(MetricStateConflict):
            DerivedMetric.activate_version(
                metric.id, v2.id, expected_active_id=v1.id, now=now
            )

    v2.backfill_status, v2.backfill_days_done = "complete", 30
    v2.save(update_fields=["backfill_status", "backfill_days_done"])
    DerivedMetric.activate_version(metric.id, v2.id, expected_active_id=v1.id, now=now)
    metric.refresh_from_db()
    after = metric.active_semantics()
    assert (after["level"], after["group_dims"]) == ("span", ["region"])
    assert after["value_labels"] == {"match": "Resolved", "other": "Not resolved"}
    v2.refresh_from_db()
    assert v2.activated_at == now
    # A stale activation (still expecting v1) changes nothing.
    with pytest.raises(MetricStateConflict, match="changed"):
        DerivedMetric.activate_version(
            metric.id, v1.id, expected_active_id=v1.id, now=now
        )
    # Another metric's version can never become this metric's active version.
    other = _version(_metric(workspace, observe_project, "Other"), 1)
    DerivedMetricVersion.objects.filter(id=other.id).update(backfill_status="complete")
    with pytest.raises(MetricStateConflict):
        DerivedMetric.activate_version(
            metric.id, other.id, expected_active_id=v2.id, now=now
        )

    # Versions are immutable; only backfill progress and activation change.
    v1.formula = "sum(resolved)"
    with pytest.raises(MetricLedgerConflict):
        v1.save()
    with pytest.raises(MetricLedgerConflict):
        v1.save(update_fields=["formula"])
    # The active version cannot be deleted on its own.
    with pytest.raises(Exception, match="(?i)restrict|protect"):
        DerivedMetricVersion.objects.filter(id=v2.id).delete()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "kind,rule",
    [
        ("share", "any"),
        ("share", "every"),
        ("count", "any"),
        ("count", "every"),
        ("average", "average"),
        ("average", "total"),
    ],
)
def test_user_rules_that_match_their_kind_are_accepted(
    workspace, observe_project, kind, rule
):
    metric = _metric(workspace, observe_project)
    version = _version(metric, 1, kind=kind, level="user", user_rule=rule)
    version.clean()
    assert (version.level, version.user_rule) == ("user", rule)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "fields,error_field",
    [
        ({"kind": "share", "level": "conversation", "user_rule": "any"}, "user_rule"),
        ({"kind": "custom", "level": "user", "user_rule": "any"}, "kind"),
        ({"kind": "custom", "level": "user", "user_rule": ""}, "kind"),
        ({"kind": "total", "level": "user", "user_rule": "total"}, "kind"),
        ({"kind": "share", "level": "user", "user_rule": "average"}, "user_rule"),
        ({"kind": "average", "level": "user", "user_rule": "any"}, "user_rule"),
        ({"kind": "count", "level": "user", "user_rule": ""}, "user_rule"),
    ],
)
def test_user_rule_needs_level_user_and_a_matching_kind(
    workspace, observe_project, fields, error_field
):
    metric = _metric(workspace, observe_project)
    with pytest.raises(IntegrityError), transaction.atomic():
        _version(metric, 1, **fields)
    draft = DerivedMetricVersion(
        metric=metric,
        version=1,
        formula="x",
        inputs=[],
        selection_unit="session",
        weighting="none",
        definition_hash="0" * 64,
        **fields,
    )
    with pytest.raises(ValidationError) as error:
        draft.clean()
    assert error_field in error.value.message_dict


# --- snapshots and monitor deliveries ------------------------------------------


@pytest.mark.django_db
def test_snapshot_changed_and_deleted_transitions(workspace, observe_project, user):
    now = timezone.now()
    version = _version(_metric(workspace, observe_project))

    def snapshot(**fields):
        return DerivedMetricRun.objects.create(
            definition=version,
            window_start=now - timedelta(days=7),
            window_end=now,
            as_of=now,
            fingerprint=uuid4().hex,
            kind="snapshot",
            supported_grain="conversation",
            **fields,
        )

    queued = snapshot()
    with pytest.raises(MetricStateConflict):
        DerivedMetricRun.mark_snapshot_changed(
            queued.id, reason="erasure", removed_units=1, now=now
        )

    done = snapshot(status="complete", captured_at=now)
    DerivedMetricRun.mark_snapshot_changed(
        done.id, reason="erasure", removed_units=12, now=now
    )
    DerivedMetricRun.mark_snapshot_changed(
        done.id, reason="erasure", removed_units=3, now=now
    )
    done.refresh_from_db()
    assert (done.change_reason, done.removed_unit_count, done.changed_at) == (
        "erasure",
        15,
        now,
    )
    with pytest.raises(ValueError):
        DerivedMetricRun.mark_snapshot_changed(
            done.id, reason="Removed user abc", removed_units=1, now=now
        )

    DerivedMetricRun.mark_snapshot_deleted(done.id, user_id=user.id, now=now)
    done.refresh_from_db()
    assert (done.deleted_by_id, done.cleanup_status) == (user.id, "pending")
    with pytest.raises(MetricStateConflict):
        DerivedMetricRun.mark_snapshot_deleted(done.id, user_id=user.id, now=now)
    with pytest.raises(MetricStateConflict):
        DerivedMetricRun.mark_snapshot_changed(
            done.id, reason="erasure", removed_units=1, now=now
        )

    for start, end in [
        ("pending", "running"),
        ("running", "failed"),
        ("failed", "running"),
        ("running", "done"),
    ]:
        DerivedMetricRun.advance_snapshot_cleanup(
            done.id, from_status=start, to_status=end
        )
    done.refresh_from_db()
    assert done.cleanup_status == "done"
    with pytest.raises(ValueError):
        DerivedMetricRun.advance_snapshot_cleanup(
            done.id, from_status="done", to_status="running"
        )
    with pytest.raises(MetricStateConflict):
        DerivedMetricRun.advance_snapshot_cleanup(
            done.id, from_status="pending", to_status="running"
        )

    # Span and user metrics cannot be snapshotted; runs carry no grain.
    for kind, grain in [
        ("snapshot", "span"),
        ("snapshot", "user"),
        ("snapshot", ""),
        ("run", "conversation"),
    ]:
        with pytest.raises(IntegrityError), transaction.atomic():
            DerivedMetricRun.objects.create(
                definition=version,
                window_start=now - timedelta(days=1),
                window_end=now,
                as_of=now,
                fingerprint=uuid4().hex,
                kind=kind,
                supported_grain=grain,
            )
    with pytest.raises(IntegrityError), transaction.atomic():
        DerivedMetricRun.objects.filter(id=queued.id).update(deleted_at=now)


@pytest.mark.django_db
def test_refresh_evaluated_deliveries_are_unique_per_window(workspace, observe_project):
    now = timezone.now()
    metric = _metric(workspace, observe_project)
    version = _version(metric)
    monitor = UserAlertMonitor.objects.create(
        name="Resolution monitor",
        organization=workspace.organization,
        workspace=workspace,
        project=observe_project,
        metric_type="derived_metric",
        metric=str(metric.id),
        threshold_operator="less_than",
        threshold_type="static",
        critical_threshold_value=0.75,
        notification_emails=[],
        alert_frequency=60,
    )
    window = {"window_start": now - timedelta(hours=1), "window_end": now}
    DerivedMetricMonitorDelivery.objects.create(
        monitor=monitor,
        metric_version=version,
        value=0.62,
        fired_at=now,
        alert_type="critical",
        **window,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        DerivedMetricMonitorDelivery.objects.create(
            monitor=monitor, metric_version=version, alert_type="", **window
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        DerivedMetricMonitorDelivery.objects.create(monitor=monitor, alert_type="")
    with pytest.raises(IntegrityError), transaction.atomic():
        DerivedMetricMonitorDelivery.objects.create(
            monitor=monitor,
            metric_version=version,
            alert_type="",
            window_start=now,
            window_end=now - timedelta(hours=1),
        )
    DerivedMetricMonitorDelivery.objects.create(
        monitor=monitor,
        metric_version=version,
        alert_type="",
        window_start=now,
        window_end=now + timedelta(hours=1),
    )
    assert monitor.derived_metric_deliveries.count() == 2


# --- erasure requests ------------------------------------------------------------


@pytest.mark.django_db
def test_erasure_request_lifecycle_is_resumable_and_never_leaks_ids(
    workspace, observe_project, user, caplog, capsys
):
    caplog.set_level(logging.DEBUG)
    secret_ids = [f"end-user-{uuid4()}" for _ in range(3)]
    now = timezone.now()
    request = MetricErasureRequest.record(
        organization_id=workspace.organization_id,
        project_id=observe_project.id,
        scope_kind="users",
        subject_ids=[*secret_ids, secret_ids[0]],
        requested_by_id=user.id,
        now=now,
    )
    assert (request.status, request.subject_count) == ("queued", 3)
    assert len(request.subject_sha256) == 64

    token = MetricErasureRequest.claim(request.id, ttl=timedelta(minutes=5), now=now)
    assert token is not None
    assert (
        MetricErasureRequest.claim(request.id, ttl=timedelta(minutes=5), now=now)
        is None
    )
    MetricErasureRequest.record_receipt(
        request.id, token=token, store="live_metric_rows", counts={"rows": 40}, now=now
    )
    with pytest.raises(MetricStateConflict):
        MetricErasureRequest.finish(request.id, token=token, now=now)
    errors = []
    try:
        MetricErasureRequest.fail(
            request.id, token=token, error_code=f"failed {secret_ids[1]}", now=now
        )
    except ValueError as error:
        errors.append(str(error))
    MetricErasureRequest.fail(
        request.id, token=token, error_code="snapshot_store_down", now=now
    )
    request.refresh_from_db()
    assert (request.status, request.error_code) == ("failed", "snapshot_store_down")

    # Resume: claim again, keep the earlier receipt, finish the remaining stores.
    later = now + timedelta(minutes=1)
    resumed = MetricErasureRequest.claim(
        request.id, ttl=timedelta(minutes=5), now=later
    )
    request.refresh_from_db()
    assert (request.status, request.attempt_count) == ("running", 2)
    assert request.receipts == {"live_metric_rows": {"rows": 40}}
    # A crashed worker's lapsed claim can be taken over.
    crashed_at = later + timedelta(minutes=6)
    takeover = MetricErasureRequest.claim(
        request.id, ttl=timedelta(minutes=5), now=crashed_at
    )
    assert takeover not in (None, resumed)
    with pytest.raises(MetricStateConflict):
        MetricErasureRequest.record_receipt(
            request.id,
            token=resumed,
            store="snapshot_inputs",
            counts={"rows": 1},
            now=crashed_at,
        )
    with pytest.raises(ValueError):
        MetricErasureRequest.record_receipt(
            request.id,
            token=takeover,
            store="snapshot_inputs",
            counts={"removed": secret_ids[2]},
            now=crashed_at,
        )
    for store in ("snapshot_inputs", "snapshot_results"):
        MetricErasureRequest.record_receipt(
            request.id, token=takeover, store=store, counts={"rows": 2}, now=crashed_at
        )
    MetricErasureRequest.finish(request.id, token=takeover, now=crashed_at)
    request.refresh_from_db()
    assert (request.status, request.completed_at, request.attempt_count) == (
        "done",
        crashed_at,
        3,
    )
    assert (
        MetricErasureRequest.claim(request.id, ttl=timedelta(minutes=5), now=crashed_at)
        is None
    )

    try:
        MetricErasureRequest.record(
            organization_id=workspace.organization_id,
            scope_kind="organization",
            subject_ids=[secret_ids[0]],
        )
    except ValidationError as error:
        errors.append(str(error))
    try:
        MetricErasureRequest.record(
            organization_id=workspace.organization_id,
            scope_kind="sessions",
            subject_ids=secret_ids,
        )
    except ValidationError as error:
        errors.append(str(error))
    try:
        MetricErasureRequest.record(
            organization_id=workspace.organization_id,
            project_id=observe_project.id,
            scope_kind="users",
            subject_ids=[f"{secret_ids[0]}-{i}" for i in range(10_001)],
        )
    except ValidationError as error:
        errors.append(str(error))
    assert len(errors) == 4

    captured = capsys.readouterr()
    visible = "\n".join(
        [caplog.text, captured.out, captured.err, repr(request), str(request), *errors]
    )
    assert "metric_erasure" in caplog.text + captured.out + captured.err
    for secret in secret_ids:
        assert secret not in visible
