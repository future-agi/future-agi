# ruff: noqa: F401, F811 -- shared isolated native fixtures
"""Native server-side range payload parity and durable replay boundaries."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.db import IntegrityError, transaction

from tracer.models.derived_metric import (
    MetricCompactionRange,
    MetricErasureRequest,
    MetricLedgerConflict,
    MetricProjectRefresh,
    MetricRefreshAttempt,
    MetricStateConflict,
)
from tracer.services.derived_metrics.compaction_ranges import (
    ALGORITHM,
    BUCKETS,
    RANGE_ROWS,
    destination_select,
    digest_select,
    prepare_ranges,
    registry,
    validate_registry,
)
from tracer.services.derived_metrics.publication import (
    MetricVisibilityError,
    committed_rows_select,
)
from tracer.services.derived_metrics.refresh import TABLE_KINDS
from tracer.services.derived_metrics.storage import (
    BUCKET_TOTALS,
    FIELD_CHECKPOINT,
    FIELD_VALUES,
    RETIRED_PARTS,
)
from tracer.tests.test_derived_metric_clickhouse import metric_ch
from tracer.tests.test_derived_metric_publication_clickhouse import (
    current_fields,
    field_query,
    live_writer,
    prepare_field,
    publication_store,
)

pytestmark = pytest.mark.django_db(transaction=True)


def seed_all(writer, count=17):
    attempt = writer.begin(purpose="compaction")
    for table in TABLE_KINDS.values():
        columns = []
        for name, kind in table.schema:
            literal = kind.replace("'", "\\'")
            expression = f"defaultValueOfTypeName('{literal}')"
            if name in {"unit_id", "trace_id", "span_id", "group_key"}:
                expression = "concat('key-', toString(number))"
            elif name in {
                "unit_start",
                "bucket_start",
                "start_hour",
                "first_seen",
                "due_at",
            }:
                expression = "toDateTime('2026-10-20 00:00:00', 'UTC')"
            elif name == "level":
                expression = "'conversation'"
            elif name == "is_deleted":
                expression = "toUInt8(modulo(number, 3) = 0)"
            elif name == "inputs":
                expression = "map('n', toNullable(toFloat64(number)), 'missing', CAST(NULL, 'Nullable(Float64)'))"
            elif name == "inputs_text":
                expression = "map('wide', toNullable(repeat('v', 2048)), 'missing', CAST(NULL, 'Nullable(String)'))"
            elif name == "inputs_bool":
                expression = "map('b', toNullable(toUInt8(modulo(number, 2))))"
            elif name == "uniq_state":
                expression = "arrayReduce('uniqCombined64State', [concat('key-', toString(number))])"
            elif name == "quantile_state":
                expression = "arrayReduce('quantileTDigestState', [toFloat64(number)])"
            columns.append(f"{expression} AS {name}")
        writer.freeze(
            attempt, table, "SELECT " + ", ".join(columns) + f" FROM numbers({count})"
        )
    writer.seal(attempt)
    return writer.stage_and_publish(attempt)


def normalized(writer, manifest, table):
    sql, params = committed_rows_select(
        table, manifest, org_id=writer.org_id, project_id=writer.lease.project_id
    )
    columns = [
        c
        for c in table.columns
        if c
        not in {
            "generation",
            "attempt_id",
            "row_sequence",
            "fencing_epoch",
            table.version_column,
        }
    ]
    return writer.store.execute(
        f"SELECT hex(formatRow('RowBinary', {', '.join(columns)})) FROM ({sql}) ORDER BY {', '.join(table.logical_key)}",
        params,
    )


def prepare(writer, *, target_rows=RANGE_ROWS):
    head = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
    attempt = writer.begin(
        purpose="compaction",
        ceilings={
            "manifest": str(head.id),
            "range_compaction": True,
            "range_obsolete": [],
        },
    )
    root = prepare_ranges(writer, attempt, target_rows=target_rows)
    writer.seal(attempt, publication={"ranges": root})
    return attempt


def test_all_seven_stores_exact_payloads_and_no_pg_rowbinary(live_writer):
    writer = live_writer
    old = seed_all(writer)
    expected = {
        kind: normalized(writer, old, table) for kind, table in TABLE_KINDS.items()
    }
    manifest = writer.compact(server_side=True)
    for kind, table in TABLE_KINDS.items():
        assert normalized(writer, manifest, table) == expected[kind]
        assert normalized(writer, old, table) == expected[kind]
    attempt = manifest.published_by
    assert not attempt.batches.exists()
    assert not list(writer.plans(attempt))
    # One empty range per hash bucket per table kind is the floor.
    assert attempt.compaction_ranges.count() >= BUCKETS * len(TABLE_KINDS)
    assert set(attempt.compaction_ranges.values_list("state", flat=True)) == {
        "verified"
    }
    assert manifest.visibility_receipts["format"] == 2
    assert (
        manifest.visibility_receipts["ranges"][0]["registry"]["algorithm"] == ALGORITHM
    )
    assert all(
        "rows" not in entry
        for chunk in attempt.ledger_chunks.all()
        for entry in chunk.payload
    )
    assert all(
        part.active_parts == 1
        for part in writer.store.checkpoint_layout(attempt.project_id)
    )


def test_empty_ranges_have_complete_sealed_registry(live_writer):
    writer = live_writer
    seed_all(writer, count=0)
    attempt = prepare(writer)
    assert attempt.compaction_ranges.count() == BUCKETS * len(TABLE_KINDS)
    assert set(attempt.compaction_ranges.values_list("row_count", flat=True)) == {0}
    manifest = writer.stage_and_publish(attempt)
    assert manifest.checkpoint_attempt_id == attempt.id
    assert all(row.verified_count == 0 for row in attempt.compaction_ranges.all())


def test_small_children_are_disjoint_and_exhaustive(live_writer):
    writer = live_writer
    seed_all(writer, count=33)
    attempt = prepare(writer, target_rows=2)
    assert max(attempt.compaction_ranges.values_list("row_count", flat=True)) <= 2
    manifest = writer.stage_and_publish(attempt)
    assert all(
        len(normalized(writer, manifest, table)) == 33 for table in TABLE_KINDS.values()
    )


def test_duplicate_physical_prefixes_cannot_widen_checkpoint_children(live_writer):
    writer = live_writer
    checkpoint = seed_all(writer, count=33)
    writer.store.execute(f"SYSTEM STOP MERGES {FIELD_CHECKPOINT.name}")
    try:
        # Simulate an acknowledged legacy replay whose identical physical rows
        # have not merged yet. A later delta field sorts after all checkpoint
        # fields, but shares one of their immutable unit buckets.
        writer.store.execute(
            f"INSERT INTO {FIELD_CHECKPOINT.name} SELECT * FROM {FIELD_CHECKPOINT.name} "
            "WHERE attempt_id=toUUID(%(attempt)s)",
            {"attempt": str(checkpoint.checkpoint_attempt_id)},
        )
        draft, _ = prepare_field(writer, unit_id="key-0", metric=uuid4())
        parent = writer.stage_and_publish(draft)
        expected = {
            kind: normalized(writer, parent, table)
            for kind, table in TABLE_KINDS.items()
        }
        attempt = prepare(writer, target_rows=2)
        assert max(attempt.compaction_ranges.values_list("row_count", flat=True)) <= 2
    finally:
        writer.store.execute(f"SYSTEM START MERGES {FIELD_CHECKPOINT.name}")
    manifest = writer.stage_and_publish(attempt)
    for kind, table in TABLE_KINDS.items():
        assert normalized(writer, manifest, table) == expected[kind]
        assert normalized(writer, parent, table) == expected[kind]


@pytest.mark.parametrize(
    "corruption", ["missing", "bounds", "schema", "digest", "unfinished"]
)
def test_sealed_registry_corruption_cannot_transition_or_publish(
    live_writer, corruption
):
    writer = live_writer
    seed_all(writer, count=1)
    attempt = prepare(writer)
    row = attempt.compaction_ranges.order_by("id").first()
    if corruption == "missing":
        row.delete()
    else:
        changes = {
            "bounds": {"upper_key": ["different"]},
            "schema": {"schema_sha256": "0" * 64},
            "digest": {"digest": "0" * 64},
            "unfinished": {"state": "planned"},
        }[corruption]
        MetricCompactionRange.objects.filter(id=row.id).update(**changes)
    with pytest.raises(MetricLedgerConflict):
        MetricRefreshAttempt.transition(
            attempt.id,
            from_state="prepared",
            to_state="staging",
            **writer.lease.check(),
        )
    assert not attempt.batches.exists()


def test_zero_batches_do_not_allow_unverified_ranges(live_writer):
    seed_all(live_writer, count=0)
    attempt = prepare(live_writer)
    MetricRefreshAttempt.transition(
        attempt.id,
        from_state="prepared",
        to_state="staging",
        **live_writer.lease.check(),
    )
    with pytest.raises(MetricLedgerConflict, match="incomplete"):
        MetricRefreshAttempt.transition(
            attempt.id,
            from_state="staging",
            to_state="verified",
            **live_writer.lease.check(),
        )


def test_lost_insert_ack_aborts_namespace_without_blind_replay(
    live_writer, monkeypatch
):
    writer = live_writer
    original = seed_all(writer, count=1)
    attempt = prepare(writer)
    execute = writer.store.execute
    sends = []

    def lose_ack(sql, params=None, **kwargs):
        result = execute(sql, params, **kwargs)
        if sql.startswith("INSERT INTO") and kwargs.get("query_id", "").startswith(
            "metric-range-insert-"
        ):
            sends.append(kwargs["query_id"])
            raise ConnectionError("synthetic lost acknowledgement")
        return result

    monkeypatch.setattr(writer.store, "execute", lose_ack)
    with pytest.raises(ConnectionError):
        writer.stage_and_publish(attempt)

    def hide_terminal_log(sql, params=None, **kwargs):
        if "system.query_log" in sql:
            return [(0,)]
        return execute(sql, params, **kwargs)

    monkeypatch.setattr(writer.store, "execute", hide_terminal_log)
    with pytest.raises(MetricVisibilityError, match="unresolved"):
        writer.stage_and_publish(attempt)
    attempt.refresh_from_db()
    assert attempt.state == "aborted" and len(sends) == 1
    assert (
        MetricProjectRefresh.for_project(attempt.project_id).head_manifest_id
        == original.id
    )
    assert (
        MetricErasureRequest.objects.filter(store_payload__attempt=str(attempt.id))
        .exclude(status="done")
        .exists()
    )
    assert not attempt.compaction_ranges.filter(
        state="verified", row_count__gt=0
    ).exists()


def test_crash_after_receipt_reuses_destination_without_insert(
    live_writer, monkeypatch
):
    writer = live_writer
    seed_all(writer, count=1)
    attempt = prepare(writer)
    verify = MetricCompactionRange.verify
    failed = []

    def crash(range_id, actual, **kwargs):
        if actual["count"] and not failed:
            failed.append(range_id)
            raise RuntimeError("synthetic crash before PG receipt")
        return verify(range_id, actual, **kwargs)

    monkeypatch.setattr(MetricCompactionRange, "verify", crash)
    with pytest.raises(RuntimeError):
        writer.stage_and_publish(attempt)
    row = MetricCompactionRange.objects.get(id=failed[0])
    assert row.state == "inserting"
    monkeypatch.setattr(MetricCompactionRange, "verify", verify)
    manifest = writer.stage_and_publish(attempt)
    submissions = MetricErasureRequest.objects.filter(
        store_payload__attempt=str(attempt.id),
        store_payload__compaction_range_id=row.id,
    )
    assert submissions.count() == 1
    assert manifest.checkpoint_attempt_id == attempt.id


def test_inherited_range_receipts_survive_incremental_publication(live_writer):
    writer = live_writer
    seed_all(writer, count=1)
    checkpoint = writer.compact(server_side=True)
    draft, _ = prepare_field(writer, unit_id="new-unit")
    result = writer.stage_and_publish(draft)
    assert (
        result.visibility_receipts["ranges"] == checkpoint.visibility_receipts["ranges"]
    )
    assert result.visibility_receipts["batches"]
    assert result.visibility_receipts["format"] == 2


def test_source_revocation_fails_closed(live_writer):
    from tracer.services.derived_metrics.deletion_protocol import revoke_generations

    writer = live_writer
    head = seed_all(writer, count=1)
    attempt = prepare(writer)
    revoke_generations(
        attempt.project_id, reason="retention_expired", manifest_ids=[head.id]
    )
    with pytest.raises(MetricStateConflict):
        writer.stage_and_publish(attempt)
    assert not attempt.compaction_ranges.filter(state="verified").exists()


def test_native_destination_tampering_is_not_count_only_receipt(live_writer):
    writer = live_writer
    seed_all(writer, count=1)
    attempt = prepare(writer)
    writer.stage_and_publish(attempt)
    row = attempt.compaction_ranges.filter(
        table_kind="field_values", row_count__gt=0
    ).first()
    sql, params = destination_select(writer, attempt, row, FIELD_VALUES)
    actual = digest_select(writer.store, FIELD_VALUES, sql, params, row)
    assert actual["sha256"] == row.digest
    writer.store.execute(
        f"ALTER TABLE {FIELD_CHECKPOINT.name} UPDATE value_number = toNullable(999.) WHERE attempt_id = toUUID(%(attempt)s) SETTINGS mutations_sync=2",
        {"attempt": str(attempt.id)},
    )
    changed = digest_select(writer.store, FIELD_VALUES, sql, params, row)
    assert changed["count"] == actual["count"] and changed["sha256"] != actual["sha256"]


def test_verified_constraints_and_terminal_private_purge(live_writer):
    from tracer.services.derived_metrics.retention import _purge_terminal_payloads

    writer = live_writer
    seed_all(writer, count=0)
    attempt = prepare(writer)
    row = attempt.compaction_ranges.first()
    with pytest.raises(IntegrityError), transaction.atomic():
        MetricCompactionRange.objects.filter(id=row.id).update(state="verified")
    writer.stage_and_publish(attempt)
    _purge_terminal_payloads(attempt.project_id)
    assert not attempt.compaction_ranges.exists()


def test_preparation_crash_preserves_frozen_sequences_on_resume(
    live_writer, monkeypatch
):
    from tracer.services.derived_metrics import compaction_ranges

    writer = live_writer
    seed_all(writer, count=1)
    original = compaction_ranges.digest_select
    calls = []

    def crash(*args, **kwargs):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("synthetic preparation crash")
        return original(*args, **kwargs)

    monkeypatch.setattr(compaction_ranges, "digest_select", crash)
    with pytest.raises(RuntimeError, match="preparation crash"):
        writer.compact(server_side=True)
    attempt = MetricRefreshAttempt.objects.get(source_ceilings__range_compaction=True)
    before = list(
        attempt.compaction_ranges.filter(state="frozen").values_list(
            "id", "sequence_first", "sequence_last", "digest"
        )
    )
    assert before and not attempt.ledger_sealed_at
    monkeypatch.setattr(compaction_ranges, "digest_select", original)
    manifest = writer.compact(server_side=True)
    assert manifest.published_by_id == attempt.id
    after = {
        row[0]: row[1:]
        for row in attempt.compaction_ranges.values_list(
            "id", "sequence_first", "sequence_last", "digest"
        )
    }
    assert all(after[row[0]] == row[1:] for row in before)


def test_compaction_deadline_covers_preparation_before_seal(live_writer, monkeypatch):
    from tracer.services.derived_metrics import publication

    writer = live_writer
    head = seed_all(writer, count=1)
    # An already exhausted invocation must not submit even its schema query.
    monkeypatch.setattr(publication, "monotonic", lambda: 2)
    with pytest.raises(MetricVisibilityError, match="deadline"):
        with writer.store.compaction_budget(1):
            writer.compact(server_side=True)
    assert (
        MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest_id
        == head.id
    )
    assert not MetricRefreshAttempt.objects.filter(
        source_ceilings__range_compaction=True
    ).exists()
    assert writer.store._compaction_deadline is None


def test_budgeted_native_results_keep_rows_types_and_columnar_shape(publication_store):
    from time import monotonic

    store = publication_store
    sql = "SELECT number, toString(number) AS label FROM numbers(3) ORDER BY number"
    for options in ({}, {"with_column_types": True}, {"columnar": True}):
        expected = store.execute(sql, **options)
        with store.compaction_budget(monotonic() + 60):
            assert store.execute(sql, **options) == expected
    assert store._compaction_deadline is None


def test_policy_shrink_blocks_private_range_submission(live_writer):
    from tracer.services.derived_metrics.refresh import MetricRetentionChanged

    writer = live_writer
    head = seed_all(writer, count=1)
    attempt = prepare(writer)
    writer.retention_days = lambda: 1
    with pytest.raises(MetricRetentionChanged):
        writer.stage_and_publish(attempt)
    assert (
        not attempt.compaction_ranges.filter(
            table_kind="field_values", state__in=["inserting", "verified"]
        )
        .exclude(row_count=0)
        .exists()
    )
    assert writer.store.execute(
        f"SELECT count() FROM {FIELD_CHECKPOINT.name} WHERE attempt_id=toUUID(%(attempt)s)",
        {"attempt": str(attempt.id)},
    ) == [(0,)]
    assert (
        MetricProjectRefresh.for_project(attempt.project_id).head_manifest_id == head.id
    )


def test_last_range_retention_change_blocks_publication(live_writer, monkeypatch):
    from tracer.services.derived_metrics.refresh import MetricRetentionChanged

    writer = live_writer
    head = seed_all(writer, count=1)
    attempt = prepare(writer)
    # The seeded anchor is Oct 20; narrowing policy after the last destination
    # receipt must still win over the frozen expectation and old source pin.
    writer.retention_days = lambda: 0
    verify = MetricCompactionRange.verify
    remaining = attempt.compaction_ranges.count()

    def verified(range_id, actual, **kwargs):
        nonlocal remaining
        result = verify(range_id, actual, **kwargs)
        remaining -= 1
        if remaining == 0:
            writer.retention_days = lambda: 1
        return result

    monkeypatch.setattr(MetricCompactionRange, "verify", verified)
    with pytest.raises(MetricRetentionChanged):
        writer.stage_and_publish(attempt)
    assert remaining == 0
    assert (
        MetricProjectRefresh.for_project(attempt.project_id).head_manifest_id == head.id
    )


def test_sequence_exhaustion_never_seals_or_submits(live_writer):
    from tracer.models.derived_metric import MAX_ROW_SEQUENCE, MetricLedgerExhausted

    writer = live_writer
    seed_all(writer, count=1)
    MetricProjectRefresh.objects.filter(project_id=writer.lease.project_id).update(
        row_sequence_high_water=MAX_ROW_SEQUENCE
    )
    with pytest.raises(MetricLedgerExhausted):
        prepare(writer)
    attempt = MetricRefreshAttempt.objects.filter(
        source_ceilings__range_compaction=True
    ).latest("created_at")
    assert attempt.ledger_sealed_at is None
    assert not MetricErasureRequest.objects.filter(
        store_payload__attempt=str(attempt.id)
    ).exists()


def test_lost_ack_with_terminal_query_proof_records_receipt(live_writer, monkeypatch):
    writer = live_writer
    seed_all(writer, count=1)
    attempt = prepare(writer)
    execute = writer.store.execute
    lost = []

    def lose_ack(sql, params=None, **kwargs):
        result = execute(sql, params, **kwargs)
        if (
            sql.startswith("INSERT INTO")
            and kwargs.get("query_id", "").startswith("metric-range-insert-")
            and not lost
        ):
            lost.append(kwargs["query_id"])
            raise ConnectionError("synthetic lost acknowledgement")
        return result

    monkeypatch.setattr(writer.store, "execute", lose_ack)
    with pytest.raises(ConnectionError):
        writer.stage_and_publish(attempt)
    monkeypatch.setattr(writer.store, "execute", execute)
    writer.store.execute("SYSTEM FLUSH LOGS")
    submission = MetricErasureRequest.objects.get(store_payload__query_id=lost[0])
    range_id = submission.store_payload["compaction_range_id"]
    manifest = writer.stage_and_publish(attempt)
    submission.refresh_from_db()
    assert (
        submission.status == "done"
        and submission.receipts["terminal_query"]["drained"] == 1
    )
    assert (
        MetricErasureRequest.objects.filter(
            store_payload__attempt=str(attempt.id),
            store_payload__compaction_range_id=range_id,
        ).count()
        == 1
    )
    assert manifest.checkpoint_attempt_id == attempt.id


def test_terminated_partial_destination_is_cleaned_before_identical_replay(
    live_writer, monkeypatch
):
    writer = live_writer
    head = seed_all(writer, count=1)
    attempt = prepare(writer)
    verify = MetricCompactionRange.verify
    failed = []

    def crash(range_id, actual, **kwargs):
        if actual["count"] and not failed:
            failed.append(range_id)
            raise RuntimeError("synthetic crash before PG receipt")
        return verify(range_id, actual, **kwargs)

    monkeypatch.setattr(MetricCompactionRange, "verify", crash)
    with pytest.raises(RuntimeError):
        writer.stage_and_publish(attempt)
    monkeypatch.setattr(MetricCompactionRange, "verify", verify)
    row = MetricCompactionRange.objects.get(id=failed[0])
    from tracer.services.derived_metrics.publication import physical_table

    table = physical_table(TABLE_KINDS[row.table_kind], checkpoint=True)
    writer.store.execute(
        f"ALTER TABLE {table.name} DELETE WHERE attempt_id=toUUID(%(attempt)s) SETTINGS mutations_sync=2",
        {"attempt": str(attempt.id)},
    )
    manifest = writer.stage_and_publish(attempt)
    attempt.refresh_from_db()
    assert attempt.state == "committed"
    assert manifest.checkpoint_attempt_id == attempt.id
    assert normalized(writer, manifest, TABLE_KINDS[row.table_kind]) == normalized(
        writer, head, TABLE_KINDS[row.table_kind]
    )
    cleanup = MetricErasureRequest.objects.get(
        store_payload__attempt=str(attempt.id),
        store_payload__operation="range_cleanup",
    )
    assert cleanup.status == "done"
    assert cleanup.receipts == {"synchronous_response": {"drained": 1}}


@pytest.mark.parametrize("server_side", [False, True])
def test_layout_replay_follows_checkpoint_through_incremental_head(
    live_writer, monkeypatch, server_side
):
    writer = live_writer
    seed_all(writer, count=1)
    consolidate = writer.store.consolidate_checkpoint

    def fail(*args, **kwargs):
        raise MetricVisibilityError("synthetic preflight failure")

    monkeypatch.setattr(writer.store, "consolidate_checkpoint", fail)
    with pytest.raises(MetricVisibilityError, match="committed_layout_pending"):
        writer.compact(server_side=True)
    checkpoint = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
    incremental = writer.begin()
    writer.seal(incremental)
    head = writer.stage_and_publish(incremental)
    assert head.checkpoint_attempt_id == checkpoint.checkpoint_attempt_id
    count = MetricRefreshAttempt.objects.count()
    monkeypatch.setattr(writer.store, "consolidate_checkpoint", consolidate)
    compacted = writer.compact(server_side=server_side)
    assert compacted.id != head.id
    assert compacted.published_by.parent_manifest_id == head.id
    assert not compacted.delta_generations
    assert MetricRefreshAttempt.objects.count() == count + 1
    assert (
        not MetricErasureRequest.objects.filter(
            project_key=writer.lease.project_id,
            store_payload__protocol="metric_checkpoint_layout",
            store_payload__kind="checkpoint",
        )
        .exclude(status="done")
        .exists()
    )


def test_byte_overflow_replans_fresh_namespace_with_immutable_frozen_prefix(
    live_writer, monkeypatch
):
    from tracer.services.derived_metrics import compaction_ranges as ranges

    writer = live_writer
    head = seed_all(writer, count=33)
    expected = {
        kind: normalized(writer, head, table) for kind, table in TABLE_KINDS.items()
    }
    # Exercise the real RowBinary-byte calibration with a smaller target so the
    # regression remains fast; the production target stays at 128 MiB.
    monkeypatch.setattr(ranges, "RANGE_BYTES", 6000)
    originals = {}
    freeze = MetricCompactionRange.freeze

    def record(range_id, **kwargs):
        freeze(range_id, **kwargs)
        row = MetricCompactionRange.objects.get(id=range_id)
        originals[range_id] = (
            row.lower_key,
            row.upper_key,
            row.digest,
            row.sequence_first,
            row.sequence_last,
        )

    monkeypatch.setattr(MetricCompactionRange, "freeze", record)
    manifest = writer.compact(server_side=True)
    aborted = MetricRefreshAttempt.objects.filter(
        state="aborted", source_ceilings__range_compaction=True
    )
    assert aborted.exists()
    assert not MetricErasureRequest.objects.filter(
        store_payload__attempt__in=[
            str(x) for x in aborted.values_list("id", flat=True)
        ],
        store_payload__protocol="metric_submission",
    ).exists()
    for row in MetricCompactionRange.objects.filter(id__in=originals):
        assert (
            row.lower_key,
            row.upper_key,
            row.digest,
            row.sequence_first,
            row.sequence_last,
        ) == originals[row.id]
    for kind, table in TABLE_KINDS.items():
        assert normalized(writer, manifest, table) == expected[kind]
    assert (
        max(
            manifest.published_by.compaction_ranges.values_list(
                "expected_bytes", flat=True
            )
        )
        <= 6000
    )


@pytest.mark.parametrize("code", [159, 241])
def test_planning_statement_cap_aborts_before_replanning(
    live_writer, monkeypatch, code
):
    from clickhouse_driver.errors import ServerException

    from tracer.services.derived_metrics import compaction_ranges as ranges

    writer = live_writer
    seed_all(writer, count=1)
    prepare_original = ranges.prepare_ranges
    attempts = []

    def capped(writer, attempt, *, target_rows):
        attempts.append((attempt.id, target_rows))
        if len(attempts) == 1:
            raise ServerException("synthetic planning cap", code=code)
        return prepare_original(writer, attempt, target_rows=target_rows)

    monkeypatch.setattr(ranges, "prepare_ranges", capped)
    manifest = writer.compact(server_side=True)
    assert len(attempts) == 2
    assert attempts[0][0] != attempts[1][0] == manifest.checkpoint_attempt_id
    assert attempts[1][1] == attempts[0][1] // 2
    assert MetricRefreshAttempt.objects.get(id=attempts[0][0]).state == "aborted"


@pytest.mark.parametrize("terminal", [False, True])
def test_partial_cleanup_lost_response_requires_terminal_mutation_proof(
    live_writer, monkeypatch, terminal
):
    writer = live_writer
    head = seed_all(writer, count=17)
    expected = {
        kind: normalized(writer, head, table) for kind, table in TABLE_KINDS.items()
    }
    attempt = prepare(writer, target_rows=2)
    verify = MetricCompactionRange.verify
    interrupted = []

    def crash(range_id, actual, **kwargs):
        if actual["count"] and not interrupted:
            interrupted.append(range_id)
            raise RuntimeError("synthetic receipt crash")
        return verify(range_id, actual, **kwargs)

    monkeypatch.setattr(MetricCompactionRange, "verify", crash)
    with pytest.raises(RuntimeError, match="receipt crash"):
        writer.stage_and_publish(attempt)
    monkeypatch.setattr(MetricCompactionRange, "verify", verify)
    row = MetricCompactionRange.objects.get(id=interrupted[0])
    from tracer.services.derived_metrics.publication import physical_table

    table = physical_table(TABLE_KINDS[row.table_kind], checkpoint=True)
    writer.store.execute(
        f"ALTER TABLE {table.name} DELETE WHERE attempt_id=toUUID(%(attempt)s) AND row_sequence=%(sequence)s SETTINGS mutations_sync=2",
        {"attempt": str(attempt.id), "sequence": row.sequence_first},
    )
    execute = writer.store.execute
    mutation_ids = []

    def lost(sql, params=None, **kwargs):
        if (
            kwargs.get("query_id", "").startswith("metric-range-cleanup-")
            and not mutation_ids
        ):
            mutation_ids.append(kwargs["query_id"])
            if terminal:
                execute(sql, params, **kwargs)
            raise RuntimeError("synthetic mutation response lost")
        return execute(sql, params, **kwargs)

    monkeypatch.setattr(writer.store, "execute", lost)
    with pytest.raises(RuntimeError, match="mutation response lost"):
        writer.stage_and_publish(attempt)
    monkeypatch.setattr(writer.store, "execute", execute)
    writer.store.execute("SYSTEM FLUSH LOGS")
    if terminal:
        manifest = writer.stage_and_publish(attempt)
        for kind, table in TABLE_KINDS.items():
            assert normalized(writer, manifest, table) == expected[kind]
            assert normalized(writer, head, table) == expected[kind]
        assert (
            not MetricErasureRequest.objects.filter(
                store_payload__attempt=str(attempt.id)
            )
            .exclude(status="done")
            .exists()
        )
    else:
        with pytest.raises(MetricVisibilityError, match="unresolved"):
            writer.stage_and_publish(attempt)
        attempt.refresh_from_db()
        assert attempt.state == "aborted"
        assert MetricErasureRequest.objects.filter(
            store_payload__query_id=mutation_ids[0], status="queued"
        ).exists()
        assert (
            MetricProjectRefresh.for_project(attempt.project_id).head_manifest_id
            == head.id
        )


def test_hot_bucket_wide_rows_replan_under_unchanged_128mib_target(live_writer):
    from tracer.services.derived_metrics.compaction_ranges import RANGE_BYTES
    from tracer.services.derived_metrics.publication import MAX_COMPACTION_ROWS

    writer = live_writer
    attempt = writer.begin(purpose="compaction")
    sql, params = field_query("one-immutable-unit")
    sql = sql.replace(
        "toUUID(%(test_metric)s) AS field_id",
        "toUUID(concat('00000000-0000-0000-0000-', leftPad(toString(number + 1), 12, '0'))) AS field_id",
    )
    # Field IDs vary but every row belongs to the same immutable unit bucket.
    # Use a separate expression rather than relying on the fixture's map default.
    sql = (
        "SELECT * REPLACE (map('wide', toNullable(repeat('w', 2048))) AS inputs_text) FROM ("
        + sql
        + " FROM numbers(%(offset)s, 5000))"
    )
    for offset in range(0, 75_000, 5000):
        writer.freeze(
            attempt,
            FIELD_VALUES,
            sql,
            {**params, "offset": offset},
            row_limit=MAX_COMPACTION_ROWS,
            collect_payloads=False,
        )
    writer.seal(attempt)
    old = writer.stage_and_publish(attempt)
    manifest = writer.compact(server_side=True)
    assert MetricRefreshAttempt.objects.filter(
        state="aborted", source_ceilings__range_compaction=True
    ).exists()
    rows = manifest.published_by.compaction_ranges.filter(
        table_kind="field_values", row_count__gt=0
    )
    assert rows.count() > 1
    assert rows.values("bucket").distinct().count() == 1
    assert all(row.expected_bytes <= RANGE_BYTES for row in rows)
    assert sum(row.row_count for row in rows) == 75_000
    for snapshot in (old, manifest):
        current, bound = committed_rows_select(
            FIELD_VALUES, snapshot, org_id=writer.org_id, project_id=attempt.project_id
        )
        assert writer.store.execute(
            "SELECT count(), uniqExact(field_id), sum(length(inputs_text['wide'])) FROM ("
            + current
            + ")",
            bound,
        ) == [(75_000, 75_000, 75_000 * 2048)]


@pytest.mark.parametrize(
    "population,width", [(500, 16), (1, 17 * 1024 * 1024), (500, 40_000)]
)
def test_vectorized_source_capture_retains_count_and_byte_guards(
    live_writer, population, width
):
    from clickhouse_driver.errors import ServerException

    from tracer.services.derived_metrics.bounded_reads import (
        SOURCE_SCHEMA,
        capture_source_output,
    )

    columns = []
    for name, kind in SOURCE_SCHEMA:
        literal = kind.replace("'", "\\'")
        expression = f"defaultValueOfTypeName('{literal}')"
        if name == "level":
            expression = "'conversation'"
        elif name == "unit_id":
            expression = "concat('unit-', toString(number))"
        elif name == "inputs_text":
            expression = "map('wide', toNullable(repeat('x', %(width)s)))"
        columns.append(expression + " AS " + name)
    sql = "SELECT " + ", ".join(columns) + " FROM numbers(%(population)s)"

    def capture():
        return capture_source_output(
            live_writer.store,
            sql,
            {"width": width, "population": population},
            field_id=uuid4(),
            field_version=1,
            candidate_count=500,
        )

    if width == 16:
        assert len(capture().records) == 500
    else:
        with pytest.raises((ValueError, ServerException)):
            capture()
        # The failed stream cannot poison subsequent native queries.
        assert live_writer.store.execute("SELECT 1") == [(1,)]
