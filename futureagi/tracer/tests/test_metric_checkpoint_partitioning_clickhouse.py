# ruff: noqa: F401, F811 -- shared isolated native ClickHouse fixtures
"""Checkpoint tuple layout, immutable-key parity and maintenance replay proofs."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import uuid4

import pytest

from tracer.models.derived_metric import MetricErasureRequest
from tracer.services.derived_metrics.compaction import (
    LAYOUT_PROTOCOL,
    checkpoint_gate,
    layout_obligations,
)
from tracer.services.derived_metrics.deletion_protocol import capture_pin
from tracer.services.derived_metrics.publication import (
    MetricCheckpointLayoutPending,
    MetricVisibilityError,
    committed_rows_select,
)
from tracer.services.derived_metrics.storage import FIELD_CHECKPOINT, FIELD_VALUES
from tracer.tests.test_derived_metric_clickhouse import metric_ch
from tracer.tests.test_derived_metric_publication_clickhouse import (
    current_fields,
    field_query,
    live_writer,
    prepare_field,
    publication_store,
)

pytestmark = pytest.mark.django_db(transaction=True)


def checkpoint(writer, *, count=128, metric=None):
    """Synthetic complete checkpoint using the unchanged frozen-payload path."""
    attempt = writer.begin(purpose="compaction")
    sql, params = field_query(metric=metric)
    sql = sql.replace("%(test_unit)s", "concat('unit-', toString(number))")
    writer.freeze(attempt, FIELD_VALUES, sql + f" FROM numbers({count})", params)
    writer.seal(attempt)
    return writer.stage_and_publish(attempt)


def fragmented(writer):
    # Deterministic fragmentation: this isolated fixture disables automatic
    # merge selection, not the explicit bounded FINAL being tested.
    writer.store.execute(
        f"ALTER TABLE {FIELD_CHECKPOINT.name} MODIFY SETTING "
        "max_bytes_to_merge_at_max_space_in_pool=1, max_bytes_to_merge_at_min_space_in_pool=1"
    )
    metric = uuid4()
    first = checkpoint(writer, metric=metric)
    second = checkpoint(writer, metric=metric)
    return first, second


def test_subpartitions_preserve_pinned_history_and_exact_cells(live_writer):
    writer = live_writer
    metric = uuid4()
    before = checkpoint(writer, metric=metric)
    pin = capture_pin(
        writer.lease.project_id, consumer_kind="request", ttl=timedelta(minutes=5)
    )
    head = checkpoint(writer, metric=metric)
    assert pin.manifest_id == before.id and pin.manifest_id != head.id
    original = current_fields(writer, before)
    current = current_fields(writer, head)
    assert len(original) == len(current) == 128
    writer.store.consolidate_checkpoint(writer.lease.project_id)
    layout = writer.store.checkpoint_layout(writer.lease.project_id)
    assert len(layout) == 8
    assert all(part.active_parts == 1 and part.rows > 0 for part in layout)
    assert sum(part.rows for part in layout) == 256
    assert current_fields(writer, before) == original
    from tracer.models.derived_metric import MetricRefreshAttempt

    MetricRefreshAttempt.objects.filter(id=before.published_by_id).update(
        expires_at=before.published_by.created_at + timedelta(microseconds=1)
    )
    writer.collect_garbage()
    assert pin.is_usable()
    assert current_fields(writer, pin.manifest) == original
    selected = tuple(f"unit-{index}" for index in range(0, 100, 2))
    sql, params = committed_rows_select(
        FIELD_VALUES,
        head,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
        key_in={"unit_id": selected},
    )
    assert "cityHash64(unit_id) %% 8" in sql
    actual = writer.store.execute(f"SELECT unit_id FROM ({sql})", params)
    assert {row[0] for row in actual} == set(selected)


def test_moved_owner_start_and_tombstone_keep_one_immutable_tuple(live_writer):
    writer = live_writer
    metric = uuid4()
    first = checkpoint(writer, count=1, metric=metric)
    attempt, _ = prepare_field(
        writer,
        metric=metric,
        unit_id="unit-0",
        user="moved-user",
        start=datetime(2026, 3, 2, tzinfo=UTC),
        deleted=1,
    )
    delta = writer.stage_and_publish(attempt)
    expected = current_fields(writer, delta)
    assert expected[0][2] == 1
    result = writer.compact()
    assert current_fields(writer, result) == expected
    assert current_fields(writer, first)[0][2] == 0
    rows = writer.store.execute(
        f"SELECT uniqExact(_partition_id) FROM {FIELD_CHECKPOINT.name} "
        "WHERE project_id = toUUID(%(project)s) AND unit_id = 'unit-0'",
        {"project": str(writer.lease.project_id)},
    )
    assert rows == [(1,)]


@pytest.mark.parametrize("guard", ["bytes", "parts"])
def test_tuple_budget_rejects_before_optimize(live_writer, monkeypatch, guard):
    writer = live_writer
    fragmented(writer)
    layout = writer.store.checkpoint_layout(writer.lease.project_id)
    changed = replace(
        layout[0],
        active_parts=513 if guard == "parts" else 2,
        bytes_on_disk=2 * 1024**3 + 1 if guard == "bytes" else 10,
    )
    monkeypatch.setattr(writer.store, "checkpoint_layout", lambda _: (changed,))
    calls = []
    monkeypatch.setattr(
        writer.store, "optimize_checkpoint", lambda *args: calls.append(args)
    )
    with pytest.raises(MetricVisibilityError, match="cap exceeded"):
        writer.store.consolidate_checkpoint(writer.lease.project_id)
    assert not calls
    assert (
        not MetricErasureRequest.objects.filter(store_payload__protocol=LAYOUT_PROTOCOL)
        .exclude(status="done")
        .exists()
    )


def test_empty_and_single_part_layouts_do_not_optimize(live_writer, monkeypatch):
    writer = live_writer
    calls = []
    monkeypatch.setattr(
        writer.store, "optimize_checkpoint", lambda *args: calls.append(args)
    )
    writer.store.consolidate_checkpoint(writer.lease.project_id)
    assert writer.store.checkpoint_layout(writer.lease.project_id) == ()
    checkpoint(writer)
    writer.store.consolidate_checkpoint(writer.lease.project_id)
    assert not calls


def test_lost_optimize_ack_requires_terminal_query_receipt(live_writer, monkeypatch):
    writer = live_writer
    fragmented(writer)
    native = writer.store.optimize_checkpoint
    submitted_ids = []

    def lose_ack(partition, query_id, seconds):
        native(partition, query_id, seconds)
        submitted_ids.append(query_id)
        raise ConnectionError("synthetic lost response")

    monkeypatch.setattr(writer.store, "optimize_checkpoint", lose_ack)
    with pytest.raises(ConnectionError):
        writer.store.consolidate_checkpoint(writer.lease.project_id)
    pending = MetricErasureRequest.objects.get(
        store_payload__protocol=LAYOUT_PROTOCOL, status="queued"
    )
    assert MetricErasureRequest.claim(pending.id, ttl=timedelta(minutes=1)) is None
    writer.store.execute("SYSTEM FLUSH LOGS")
    monkeypatch.setattr(writer.store, "optimize_checkpoint", native)
    writer.store.consolidate_checkpoint(writer.lease.project_id)
    pending.refresh_from_db()
    assert pending.status == "done"
    assert len(submitted_ids) == 1
    assert all(
        p.active_parts == 1
        for p in writer.store.checkpoint_layout(writer.lease.project_id)
    )


def test_unproven_submission_cannot_be_retried_from_absence(live_writer, monkeypatch):
    writer = live_writer
    fragmented(writer)
    calls = []

    def lost_before_send(*args):
        calls.append(args)
        raise ConnectionError("synthetic unproven transport")

    monkeypatch.setattr(writer.store, "optimize_checkpoint", lost_before_send)
    with pytest.raises(ConnectionError):
        writer.store.consolidate_checkpoint(writer.lease.project_id)
    with pytest.raises(MetricVisibilityError, match="reconciliation required"):
        writer.store.consolidate_checkpoint(writer.lease.project_id)
    assert len(calls) == 1
    # Another checkpoint may not insert into a tuple with an unresolved merge.
    before = MetricErasureRequest.objects.filter(
        store_payload__protocol="metric_submission"
    ).count()
    with pytest.raises(MetricVisibilityError, match="reconciliation required"):
        checkpoint(writer)
    assert (
        MetricErasureRequest.objects.filter(
            store_payload__protocol="metric_submission"
        ).count()
        == before
    )


def test_expired_wall_budget_does_not_submit_maintenance(live_writer):
    writer = live_writer
    fragmented(writer)
    with pytest.raises(MetricVisibilityError, match="deadline"):
        writer.store.consolidate_checkpoint(
            writer.lease.project_id, deadline=monotonic() - 1
        )
    assert not MetricErasureRequest.objects.filter(
        store_payload__protocol=LAYOUT_PROTOCOL
    ).exists()


def test_existing_old_partition_schema_requires_explicit_cutover(live_writer):
    writer = live_writer
    # Isolated fixture table, empty here. Simulate an installation predating the
    # new CREATE, then populate it to prove the guard never erases old data.
    writer.store.execute(f"DROP TABLE {FIELD_CHECKPOINT.name}")
    writer.store.execute(
        f"CREATE TABLE {FIELD_CHECKPOINT.name} AS {FIELD_VALUES.name} "
        "ENGINE = ReplacingMergeTree(_version) PARTITION BY cityHash64(project_id) %% 32 "
        "ORDER BY (org_id, project_id, field_id, field_version, level, unit_id, generation, attempt_id)"
    )
    sql, params = field_query()
    writer.store.execute(f"INSERT INTO {FIELD_CHECKPOINT.name} {sql}", params)
    with pytest.raises(MetricVisibilityError, match="reviewed cutover"):
        writer.store.assert_checkpoint_schema()
    assert writer.store.execute(f"SELECT count() FROM {FIELD_CHECKPOINT.name}") == [
        (1,)
    ]


def test_layout_counts_foreign_project_rows_in_shared_tuple(live_writer):
    writer = live_writer
    head = checkpoint(writer)
    group = writer.store.checkpoint_group(writer.lease.project_id)
    candidates = tuple(str(uuid4()) for _ in range(256))
    other = writer.store.execute(
        "SELECT value FROM (SELECT arrayJoin(%(ids)s) AS value) "
        "WHERE cityHash64(toUUID(value)) %% 32 = %(group)s LIMIT 1",
        {"ids": list(candidates), "group": group},
    )[0][0]
    writer.store.execute(
        f"INSERT INTO {FIELD_CHECKPOINT.name} SELECT * REPLACE ("
        "toUUID(%(other)s) AS project_id, toUUID(%(other_org)s) AS org_id) "
        f"FROM {FIELD_CHECKPOINT.name} WHERE project_id = toUUID(%(project)s)",
        {
            "other": other,
            "other_org": str(uuid4()),
            "project": str(writer.lease.project_id),
        },
    )
    assert (
        sum(p.rows for p in writer.store.checkpoint_layout(writer.lease.project_id))
        == 256
    )
    writer.store.consolidate_checkpoint(writer.lease.project_id)
    assert writer.store.execute(
        f"SELECT count() FROM {FIELD_CHECKPOINT.name} WHERE project_id = toUUID(%(other)s)",
        {"other": other},
    ) == [(128,)]
    assert len(current_fields(writer, head)) == 128


@pytest.mark.parametrize("exclusive", [False, True])
def test_group_gate_prevents_maintenance_during_other_writers(live_writer, exclusive):
    from threading import Event, Thread

    from django.db import close_old_connections

    writer = live_writer
    fragmented(writer)
    group = writer.store.checkpoint_group(writer.lease.project_id)
    shard = writer.store.identity()
    entered, release = Event(), Event()
    failures = []

    def holding_connection():
        close_old_connections()
        try:
            with checkpoint_gate(group, shard, exclusive=exclusive):
                entered.set()
                if not release.wait(20):
                    raise AssertionError("gate test did not release")
        except BaseException as exc:
            failures.append(exc)
            entered.set()
        finally:
            close_old_connections()

    thread = Thread(target=holding_connection)
    thread.start()
    try:
        assert entered.wait(10) and not failures
        with pytest.raises(MetricVisibilityError, match="gate is busy"):
            writer.store.consolidate_checkpoint(writer.lease.project_id)
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and not failures
    writer.store.consolidate_checkpoint(writer.lease.project_id)


def test_client_deadline_disconnect_keeps_durable_obligation(live_writer, monkeypatch):
    from tracer.services.derived_metrics import publication

    writer = live_writer
    fragmented(writer)
    clock = iter((0, 2))
    monkeypatch.setattr(publication, "monotonic", lambda: next(clock))
    native = writer.store.optimize_checkpoint
    monkeypatch.setattr(
        writer.store,
        "optimize_checkpoint",
        lambda part, query, seconds: native(part, query, 1),
    )
    with pytest.raises(MetricVisibilityError, match="timed out"):
        writer.store.consolidate_checkpoint(writer.lease.project_id)
    assert writer.store.client.connection.socket is None
    assert (
        MetricErasureRequest.objects.filter(
            store_payload__protocol=LAYOUT_PROTOCOL, status="queued"
        ).count()
        == 1
    )
