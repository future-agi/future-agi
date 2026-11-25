# ruff: noqa: F811 -- shared isolated CH fixture
"""Real immutable-attempt visibility, stale writes, pins and checkpoint proofs."""

from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from uuid import uuid4

import pytest
from django.db import close_old_connections
from django.utils import timezone

from tracer.models.derived_metric import (
    MetricProjectRefresh,
    MetricReadManifest,
    MetricRefreshAttempt,
    MetricStateConflict,
)
from tracer.services.clickhouse.v2.apply_schema_rewriter import split_statements
from tracer.services.derived_metrics.deletion_protocol import (
    capture_pin,
    collect_history,
    revoke_generations,
    serialized_project,
)
from tracer.services.derived_metrics.lease import ProjectLease, project_lease
from tracer.services.derived_metrics.publication import (
    ClickHousePublicationStore,
    MetricVisibilityError,
    committed_rows_select,
    pinned_read,
)
from tracer.services.derived_metrics.refresh import RefreshWriter
from tracer.services.derived_metrics.storage import FIELD_VALUES, TABLES, UNIT_STATE
from tracer.tests.test_derived_metric_clickhouse import SCHEMA, metric_ch  # noqa: F401


@pytest.fixture
def publication_store(metric_ch):
    for filename in dict.fromkeys(table.schema_file for table in TABLES.values()):
        for sql in split_statements((SCHEMA / filename).read_text()):
            metric_ch.execute(sql)
    for sql in split_statements(
        (SCHEMA / "038_metric_snapshot_sketches.sql").read_text()
    ):
        metric_ch.execute(sql)
    return ClickHousePublicationStore(metric_ch)


@pytest.fixture
def live_writer(publication_store, observe_project, workspace):
    with project_lease(observe_project.id, holder="b07-live") as lease:
        yield RefreshWriter(
            lease,
            publication_store,
            org_id=workspace.organization_id,
            workspace_id=workspace.id,
            retention_days=lambda: 0,
        )


def field_query(
    unit_id="unit-a",
    *,
    n=3,
    start=datetime(2026, 1, 31, 23, 59, tzinfo=UTC),
    deleted=0,
    user="",
    group="region-a",
    metric=None,
    version=1,
):
    metric = metric or uuid4()
    replacements = {
        "field_id": "toUUID(%(test_metric)s)",
        "field_version": "toUInt32(%(test_version)s)",
        "level": "'conversation'",
        "unit_id": "%(test_unit)s",
        "session_id": "%(test_unit)s",
        "user_id": "%(test_user)s",
        "unit_start": "toDateTime64(%(test_start)s, 6, 'UTC')",
        "bucket_start": "toStartOfFifteenMinutes(toDateTime(%(test_start)s, 'UTC'))",
        "status": "'ok'",
        "value_number": "toNullable(toFloat64(%(test_n)s))",
        "part_n": "toNullable(toFloat64(%(test_n)s))",
        "part_d": "toNullable(toFloat64(1))",
        "inputs": "map('y', toNullable(toFloat64(%(test_n)s)))",
        "sample_probability": "toNullable(toFloat64(1))",
        "sampling_design": "'live'",
        "group_key": "%(test_group)s",
        "is_deleted": "toUInt8(%(test_deleted)s)",
    }
    columns = []
    # stamp metadata is overwritten by RefreshWriter.freeze, but giving defaults
    # keeps this query useful as an independent finite payload relation too.
    for name, kind in FIELD_VALUES.schema:
        expr = replacements.get(
            name,
            f"defaultValueOfTypeName('{kind.replace(chr(39), chr(92) + chr(39))}')",
        )
        columns.append(f"{expr} AS {name}")
    return "SELECT " + ", ".join(columns), {
        "test_metric": str(metric),
        "test_version": version,
        "test_unit": unit_id,
        "test_user": user,
        "test_start": start,
        "test_n": n,
        "test_group": group,
        "test_deleted": deleted,
    }


def prepare_field(writer, *, metric=None, unit_id="unit-a", n=3, **kwargs):
    attempt = writer.begin(
        ledger={
            "dirty_before": [{"unit_id": unit_id, "old_start": "2026-02-01"}],
            "dirty_after": [{"unit_id": unit_id, "new_start": "2026-01-31"}],
        }
    )
    sql, params = field_query(unit_id, n=n, metric=metric, **kwargs)
    payloads = writer.freeze(attempt, FIELD_VALUES, sql, params)
    writer.seal(attempt, cursors={"watermark_ns": attempt.generation})
    return attempt, payloads


def current_fields(writer, manifest):
    sql, params = committed_rows_select(
        FIELD_VALUES, manifest, org_id=writer.org_id, project_id=writer.lease.project_id
    )
    return writer.store.execute(
        f"SELECT unit_id, value_number, is_deleted, bucket_start FROM ({sql}) ORDER BY unit_id",
        params,
    )


@pytest.mark.django_db(transaction=True)
def test_delayed_real_insert_is_excluded_even_for_key_absent_in_replacement(
    live_writer,
):
    writer = live_writer
    metric = uuid4()
    initial, _ = prepare_field(writer, metric=metric)
    committed = writer.stage_and_publish(initial)
    old, late = prepare_field(writer, metric=metric, unit_id="only-aborted", n=900)
    future = timezone.now() + timedelta(minutes=6)
    grant = MetricProjectRefresh.take_lease(
        writer.lease.project_id, holder="new", ttl=timedelta(minutes=5), now=future
    )
    replacement = RefreshWriter(
        ProjectLease(grant, clock=lambda: future), writer.store, org_id=writer.org_id
    )
    replacement.abort_stale()
    fresh, _ = prepare_field(replacement, metric=metric, n=8)
    head = replacement.stage_and_publish(fresh)
    # This is a REAL delayed CH INSERT, submitted after replacement publication.
    # It carries the OLD epoch and an attempt/key no replacement ever wrote.
    writer.store.insert(FIELD_VALUES, late[0])
    assert [r[:3] for r in current_fields(replacement, head)] == [("unit-a", 8.0, 0)]
    assert [r[:3] for r in current_fields(replacement, committed)] == [
        ("unit-a", 3.0, 0)
    ]
    compacted = replacement.compact()
    assert [r[:3] for r in current_fields(replacement, compacted)] == [
        ("unit-a", 8.0, 0)
    ]
    MetricRefreshAttempt.objects.filter(project_id=writer.lease.project_id).exclude(
        id=compacted.published_by_id
    ).update(expires_at=future + timedelta(seconds=1))
    audit = MetricReadManifest.objects.get(id=compacted.id).manifest_sha256
    intent = collect_history(writer.lease.project_id, now=future + timedelta(seconds=2))
    assert intent and intent.scope_kind == "history"
    assert (
        MetricReadManifest.objects.filter(project_id=writer.lease.project_id).count()
        == 1
    )
    assert (
        MetricRefreshAttempt.objects.filter(project_id=writer.lease.project_id).count()
        == 1
    )
    assert MetricReadManifest.objects.get(id=compacted.id).manifest_sha256 == audit
    # A late write remains excluded even after PG collection removed its attempt.
    writer.store.insert(FIELD_VALUES, late[0])
    assert [r[:3] for r in current_fields(replacement, compacted)] == [
        ("unit-a", 8.0, 0)
    ]


@pytest.mark.django_db
def test_receipt_wrong_shard_and_expired_pins_fail_closed(live_writer):
    attempt, _ = prepare_field(live_writer)
    manifest = live_writer.stage_and_publish(attempt)
    pin = capture_pin(
        live_writer.lease.project_id, consumer_kind="snapshot", ttl=timedelta(minutes=1)
    )
    with pinned_read(live_writer.store, live_writer.lease.project_id, pin=pin) as (
        pinned,
        _,
    ):
        assert pinned.id == manifest.id
    old = live_writer.store.identity
    live_writer.store.identity = lambda: ["another-server", "another_database"]
    with pytest.raises(MetricVisibilityError):
        live_writer.store.assert_visible(manifest)
    live_writer.store.identity = old
    revoke_generations(live_writer.lease.project_id, reason="retention_expired")
    with pytest.raises(MetricStateConflict, match="expired"):
        with pinned_read(live_writer.store, live_writer.lease.project_id, pin=pin):
            pytest.fail("revoked capture entered")


@pytest.mark.django_db(transaction=True)
def test_pin_capture_waits_for_locked_revocation(live_writer):
    attempt, _ = prepare_field(live_writer)
    live_writer.stage_and_publish(attempt)
    entered, completed, failures = Event(), Event(), []
    project = live_writer.lease.project_id

    def capture():
        close_old_connections()
        entered.set()
        try:
            capture_pin(project, consumer_kind="snapshot", ttl=timedelta(minutes=1))
        except MetricStateConflict:
            failures.append("expired")
        finally:
            completed.set()
            close_old_connections()

    with serialized_project(project):
        thread = Thread(target=capture)
        thread.start()
        assert entered.wait(5)
        assert not completed.wait(0.1)
        revoke_generations(project, reason="erased")
    thread.join(10)
    assert completed.is_set() and failures == ["expired"]


@pytest.mark.django_db
def test_binary_roundtrip_is_lossless_and_duplicate_retries_verify(live_writer):
    attempt, payloads = prepare_field(live_writer, unit_id="ends-with-last-byte-z")
    manifest = live_writer.stage_and_publish(attempt)
    live_writer.store.insert(FIELD_VALUES, payloads[0])
    batch = attempt.batches.get()
    assert (
        live_writer.store.verify(FIELD_VALUES, attempt, batch)["sha256"]
        == payloads[0].digest
    )
    assert current_fields(live_writer, manifest)[0][0] == "ends-with-last-byte-z"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "cardinality", [0, 1, 16, 17, 37, 100, 250, 500, 750, 4095, 4096, 6000]
)
def test_sketch_receipts_preserve_full_states_and_merges(live_writer, cardinality):
    from tracer.services.derived_metrics.refresh import payload_select
    from tracer.services.derived_metrics.sketch_serialization import (
        canonical_sketch_row,
    )
    from tracer.services.derived_metrics.storage import BUCKET_TOTALS

    writer = live_writer
    attempt = writer.begin()
    source = default_row_select(
        BUCKET_TOTALS,
        {
            "uniq_state": f"(SELECT uniqCombined64State(toString(number)) FROM numbers({cardinality}))",
            "quantile_state": f"(SELECT quantileTDigestState(toFloat64(number) / 7) FROM numbers({cardinality}))",
        },
    )
    payloads = writer.freeze(attempt, BUCKET_TOTALS, source)
    writer.seal(attempt)
    writer.stage_and_publish(attempt)
    payload = payloads[0]
    batch = attempt.batches.get()
    # Repeated deserialization can alternate MEDIUM hash iteration orders. The
    # stored canonical payload and full-state receipt must survive every retry.
    for _ in range(3):
        writer.store.insert(BUCKET_TOTALS, payload)
        receipt = writer.store.verify(BUCKET_TOTALS, attempt, batch)
        assert receipt == {"count": 1, "sha256": payload.digest}
    columns = ", ".join(BUCKET_TOTALS.columns)
    actual = writer.store.execute(
        f"SELECT hex(formatRow('RowBinary', {columns})) FROM {BUCKET_TOTALS.name} "
        "WHERE attempt_id = %(attempt)s LIMIT 1",
        {"attempt": attempt.id},
    )[0][0]
    assert canonical_sketch_row(BUCKET_TOTALS, actual) == payload.rows[0]
    decoded, params = payload_select(BUCKET_TOTALS, payloads, prefix="roundtrip")
    # Check both finalization and merging with overlapping/disjoint other states,
    # including the transition from exact hash sets to HLL. Quantile bytes remain
    # exact, so this also covers EVERY configured percentile, not just these probes.
    finalizers = ["uniqCombined64Merge(u)"] + [
        f"hex(formatRow('RowBinary', quantileTDigestMerge({p})(q)))"
        for p in (0, 0.05, 0.5, 0.9, 0.95, 0.99, 1)
    ]
    for other in (0, 250, 6000):
        results = []
        for relation, bound in (
            (decoded, params),
            (
                f"SELECT * FROM {BUCKET_TOTALS.name} WHERE attempt_id = %(attempt)s LIMIT 1",
                {"attempt": attempt.id},
            ),
        ):
            states = (
                "SELECT uniq_state AS u, quantile_state AS q, 0 AS merge_order "
                f"FROM ({relation}) UNION ALL "
                "SELECT uniqCombined64State(toString(number + 125)) AS u, "
                "quantileTDigestState(toFloat64(number + 125) / 7) AS q, 1 AS merge_order "
                f"FROM numbers({other})"
            )
            results.append(
                writer.store.execute(
                    f"SELECT {', '.join(finalizers)} FROM ({states} ORDER BY merge_order) SETTINGS max_threads=1",
                    bound,
                )
            )
        assert results[0] == results[1], (cardinality, other)
    if cardinality == 250:
        # Same cardinality is NOT sufficient: replace the members but preserve
        # every scalar, row version, and the quantile state. Fail closed.
        corrupt = writer.store.freeze_select(
            BUCKET_TOTALS,
            f"SELECT * REPLACE((SELECT uniqCombined64State(concat('wrong-', toString(number))) "
            f"FROM numbers(250)) AS uniq_state) FROM {BUCKET_TOTALS.name} "
            "WHERE attempt_id = %(attempt)s LIMIT 1",
            {"attempt": attempt.id},
        )
        writer.store.execute(
            f"ALTER TABLE {BUCKET_TOTALS.name} DELETE WHERE attempt_id = %(attempt)s SETTINGS mutations_sync=2",
            {"attempt": attempt.id},
        )
        writer.store.insert(BUCKET_TOTALS, corrupt)
        with pytest.raises(MetricVisibilityError, match="frozen batch not visible"):
            writer.store.verify(BUCKET_TOTALS, attempt, batch)


def default_row_select(table, overrides):
    columns = []
    for name, kind in table.schema:
        escaped = kind.replace("'", "\\'")
        expression = overrides.get(name, f"defaultValueOfTypeName('{escaped}')")
        columns.append(f"{expression} AS {name}")
    return "SELECT " + ", ".join(columns)


@pytest.mark.django_db(transaction=True)
def test_outstanding_old_write_all_stores_and_actual_history_collection(live_writer):
    from tracer.models.derived_metric import MetricFencingError
    from tracer.services.derived_metrics.storage import (
        BUCKET_TOTALS,
        SOURCE_DEPENDENCIES,
    )

    writer = live_writer
    initial, _ = prepare_field(writer)
    writer.stage_and_publish(initial)
    old = writer.begin(ledger={"dirty_after": [{"unit_id": "only-old"}]})
    sql, p = field_query("only-old")
    late = {FIELD_VALUES: writer.freeze(old, FIELD_VALUES, sql, p)}
    for table, overrides in (
        (UNIT_STATE, {"level": "'conversation'", "unit_id": "'only-old'"}),
        (
            SOURCE_DEPENDENCIES,
            {"trace_id": "'only-old-trace'", "span_id": "'only-old-span'"},
        ),
        (
            BUCKET_TOTALS,
            {
                "metric_id": "toUUID('00000000-0000-0000-0000-000000000001')",
                "metric_version": "toUInt32(1)",
                "group_key": "'only-old-group'",
                "uniq_state": "initializeAggregation('uniqCombined64State', 'only-old')",
                "quantile_state": "initializeAggregation('quantileTDigestState', toFloat64(1))",
            },
        ),
    ):
        late[table] = writer.freeze(old, table, default_row_select(table, overrides))
    writer.seal(old)
    entered, release, finished, errors = Event(), Event(), Event(), []
    original_insert = writer.store.insert

    def delayed_insert(table, payload):
        entered.set()
        if not release.wait(15):
            raise RuntimeError("delayed test insert was not released")
        original_insert(table, payload)

    def old_worker():
        close_old_connections()
        try:
            writer.stage_and_publish(old)
        except MetricFencingError:
            errors.append("fenced")
        finally:
            close_old_connections()
            finished.set()

    writer.store.insert = delayed_insert
    thread = Thread(target=old_worker)
    thread.start()
    try:
        assert entered.wait(10)
        writer.store.insert = original_insert
        future = timezone.now() + timedelta(minutes=6)
        clock = [future]
        grant = MetricProjectRefresh.take_lease(
            writer.lease.project_id,
            holder="takeover",
            ttl=timedelta(minutes=10),
            now=future,
        )
        new = RefreshWriter(
            ProjectLease(grant, clock=lambda: clock[0]),
            writer.store,
            org_id=writer.org_id,
        )
        new.abort_stale()
        fresh, _ = prepare_field(new, n=8)
        head = new.stage_and_publish(fresh)
        release.set()
        assert finished.wait(15)
        thread.join(5)
        assert errors == ["fenced"]
        # The remaining tables also receive late immutable writes from that same
        # revoked attempt, including keys the replacement never mentioned.
        for table in (UNIT_STATE, SOURCE_DEPENDENCIES, BUCKET_TOTALS):
            original_insert(table, late[table][0])
        for table in (UNIT_STATE, SOURCE_DEPENDENCIES, BUCKET_TOTALS):
            sql, p = committed_rows_select(
                table, head, org_id=writer.org_id, project_id=writer.lease.project_id
            )
            assert writer.store.execute(f"SELECT count() FROM ({sql})", p) == [(0,)]
        checkpoint = new.compact()
        MetricRefreshAttempt.objects.filter(project_id=writer.lease.project_id).exclude(
            id=checkpoint.published_by_id
        ).update(expires_at=future + timedelta(seconds=1))
        clock[0] = future + timedelta(seconds=2)
        intent = new.collect_garbage()
        assert intent is not None
        assert (
            MetricReadManifest.objects.filter(
                project_id=writer.lease.project_id
            ).count()
            == 1
        )
        assert (
            MetricRefreshAttempt.objects.filter(
                project_id=writer.lease.project_id
            ).count()
            == 1
        )
        for table in late:
            assert writer.store.execute(
                f"SELECT count() FROM {table.name} WHERE attempt_id = %(attempt)s",
                {"attempt": old.id},
            ) == [(0,)]
            # Even an insert that arrives AFTER physical GC cannot become current.
            original_insert(table, late[table][0])
            sql, p = committed_rows_select(
                table,
                checkpoint,
                org_id=writer.org_id,
                project_id=writer.lease.project_id,
            )
            result = writer.store.execute(
                f"SELECT count() FROM ({sql}) WHERE attempt_id = %(attempt)s",
                {**p, "attempt": old.id},
            )
            assert result == [(0,)]
    finally:
        release.set()
        thread.join(20)
        writer.store.insert = original_insert


@pytest.mark.django_db(transaction=True)
def test_keyset_batches_and_checkpoint_stream_without_offset_or_payload_recopies(
    live_writer,
):
    writer = live_writer
    attempt = writer.begin()
    sql, params = field_query()
    expanded = f"SELECT * REPLACE(concat('unit-', leftPad(toString(number), 4, '0')) AS unit_id) FROM ({sql}) AS f CROSS JOIN numbers(1001) AS n"
    payloads = writer.freeze(attempt, FIELD_VALUES, expanded, params)
    assert [len(p.rows) for p in payloads] == [500, 500, 1]
    writer.seal(attempt)
    head = writer.stage_and_publish(attempt)
    assert len(current_fields(writer, head)) == 1001
    checkpoint = writer.compact()
    assert len(current_fields(writer, checkpoint)) == 1001
    batches = checkpoint.published_by.batches.filter(
        table_name="field_values"
    ).order_by("batch_no")
    # PERF1 separates checkpoint transport (50k rows / 4 MiB) from the
    # unchanged 500-row discovery cap asserted above. This payload fits once.
    assert list(batches.values_list("row_count", flat=True)) == [1001]
    from tracer.services.derived_metrics.publication import (
        MAX_COMPACTION_BYTES,
        MAX_COMPACTION_ROWS,
        FrozenRows,
    )

    plans = list(writer.plans(checkpoint.published_by, kind="field_values"))
    assert len(plans) == 1
    payload = FrozenRows.from_plan(plans[0])
    assert payload.row_limit == MAX_COMPACTION_ROWS
    assert len(payload.rows) == 1001
    assert sum(len(bytes.fromhex(row)) for row in payload.rows) <= MAX_COMPACTION_BYTES
    assert payload.digest == batches.get().payload_sha256
    with pytest.raises(ValueError, match="row cap exceeded"):
        FrozenRows(("00",) * (MAX_COMPACTION_ROWS + 1), row_limit=MAX_COMPACTION_ROWS)
    with pytest.raises(ValueError, match="byte cap exceeded"):
        FrozenRows(("00" * (MAX_COMPACTION_BYTES + 1),), row_limit=MAX_COMPACTION_ROWS)
    assert all(
        b.row_sequence_last - b.row_sequence_first + 1 == b.row_count for b in batches
    )
    with pytest.raises(ValueError, match="immutable"):
        committed_rows_select(
            FIELD_VALUES,
            checkpoint,
            org_id=writer.org_id,
            project_id=writer.lease.project_id,
            key_after={"status": "ok"},
        )


@pytest.mark.django_db(transaction=True)
def test_repeated_checkpoint_pin_revocation_and_gc_collects_real_pg_ancestry(
    live_writer,
):
    writer = live_writer
    clock = [timezone.now()]
    writer.lease.clock = lambda: clock[0]
    metric = uuid4()
    initial, delayed = prepare_field(writer, metric=metric, n=99)
    writer.stage_and_publish(initial)
    for _ in range(3):
        pin = capture_pin(
            writer.lease.project_id,
            consumer_kind="snapshot",
            ttl=timedelta(minutes=5),
            now=clock[0],
        )
        removal, _ = prepare_field(writer, metric=metric, deleted=1)
        writer.stage_and_publish(removal)
        checkpoint = writer.compact()
        original_hash = checkpoint.manifest_sha256
        MetricRefreshAttempt.objects.filter(project_id=writer.lease.project_id).exclude(
            id=checkpoint.published_by_id
        ).update(expires_at=clock[0] + timedelta(seconds=1))
        clock[0] += timedelta(seconds=2)
        writer.collect_garbage()
        assert pin.is_usable(clock[0])
        assert MetricReadManifest.objects.filter(id=pin.manifest_id).exists()
        revoke_generations(
            writer.lease.project_id,
            reason="erased",
            manifest_ids=[pin.manifest_id],
            now=clock[0],
        )
        writer.collect_garbage()
        assert (
            MetricReadManifest.objects.filter(
                project_id=writer.lease.project_id
            ).count()
            == 1
        )
        assert (
            MetricRefreshAttempt.objects.filter(
                project_id=writer.lease.project_id
            ).count()
            == 1
        )
        assert (
            MetricReadManifest.objects.get(id=checkpoint.id).manifest_sha256
            == original_hash
        )
        writer.store.insert(FIELD_VALUES, delayed[0])
        assert current_fields(writer, checkpoint)[0][2] == 1


def physical_rows(writer):
    """Rows the metric tables hold in active parts, deleted-but-masked included."""
    return dict(
        writer.store.execute(
            "SELECT table, sum(rows) FROM system.parts WHERE database = "
            "currentDatabase() AND active AND table IN %(tables)s GROUP BY table",
            {"tables": tuple(TABLES)},
        )
    )


def attempt_rows(writer, attempts):
    """Rows any reader can still see for these attempts, across every store."""
    total = 0
    for table in TABLES:
        total += writer.store.execute(
            f"SELECT count() FROM {table} WHERE attempt_id IN %(attempts)s",
            {"attempts": tuple(attempts) or ("00000000-0000-0000-0000-000000000000",)},
        )[0][0]
    return total


@pytest.mark.django_db(transaction=True)
def test_superseded_copies_are_collected_after_a_grace_not_after_seven_days(
    live_writer,
):
    grace = timedelta(minutes=30)  # METRICS_HISTORY_GC_GRACE_SECONDS default
    writer = live_writer
    metric = uuid4()
    initial, _ = prepare_field(writer, metric=metric, n=1)
    writer.stage_and_publish(initial)
    first = writer.compact()
    pin = capture_pin(
        writer.lease.project_id, consumer_kind="snapshot", ttl=timedelta(hours=1)
    )
    pinned = {str(a) for _, a in first.allowed_attempts()}
    checkpoints = []
    for n in (2, 3):
        attempt, _ = prepare_field(writer, metric=metric, n=n)
        writer.stage_and_publish(attempt)
        head = writer.compact()
        checkpoints.append((str(attempt.id), str(head.published_by_id)))
    (_, previous), (last_delta, newest) = checkpoints
    reachable = {str(a) for _, a in head.allowed_attempts()} | pinned
    closed = {
        str(a)
        for a in MetricRefreshAttempt.objects.filter(
            project_id=writer.lease.project_id, state__in=["committed", "aborted"]
        ).values_list("id", flat=True)
    }
    superseded = closed - reachable
    assert superseded and attempt_rows(writer, superseded)
    expected = current_fields(writer, head)
    attempts = MetricRefreshAttempt.objects.filter(
        project_id=writer.lease.project_id
    ).count()

    def closed_ago(age, *, except_id=None):
        at = writer.lease.clock() - age
        project = MetricRefreshAttempt.objects.filter(
            project_id=writer.lease.project_id
        ).exclude(id=except_id)
        project.filter(state="committed").update(committed_at=at)
        project.filter(state="aborted").update(aborted_at=at)

    # Inside the grace nothing is collected: a lease, a request pin or a
    # statement may still be reading a just-superseded copy.
    writer.collect_garbage()
    closed_ago(grace - timedelta(seconds=30))
    writer.collect_garbage()
    assert attempt_rows(writer, superseded) > 0

    # The grace runs from supersession, not from commit: the previous
    # checkpoint committed long ago, but the newest checkpoint replaced it (and
    # the last delta) only now, so a reader of that head may still be running.
    closed_ago(grace + timedelta(seconds=30), except_id=newest)
    writer.collect_garbage()
    assert attempt_rows(writer, {previous}) > 0
    assert attempt_rows(writer, {last_delta}) > 0
    assert attempt_rows(writer, superseded - {previous, last_delta}) == 0

    # Past the grace, the ordinary maintenance pass collects every superseded
    # copy, long before the seven-day ledger retention; PG keeps the ledger.
    closed_ago(grace + timedelta(seconds=30))
    writer.collect_garbage()
    assert attempt_rows(writer, superseded) == 0
    assert attempt_rows(writer, reachable) > 0
    assert current_fields(writer, head) == expected
    assert [r[:3] for r in current_fields(writer, first)] == [("unit-a", 1.0, 0)]
    assert pin.is_usable()
    assert (
        MetricRefreshAttempt.objects.filter(project_id=writer.lease.project_id).count()
        == attempts
    )
    assert {
        str(a)
        for a in MetricRefreshAttempt.objects.filter(
            project_id=writer.lease.project_id, rows_collected_at__isnull=False
        ).values_list("id", flat=True)
    } == superseded
    # Collected once: a later pass issues no further deletes for them.
    assert writer.collect_unreachable_rows() == 0

    # Lightweight: the copies are masked, not yet rewritten. The scheduled
    # cleanup's bounded reclaim rewrites the masked partitions; disk then holds
    # only rows a reader can reach, and no reachable row changed.
    from tracer.services.derived_metrics.history_gc import apply_deleted_masks

    assert sum(physical_rows(writer).values()) > attempt_rows(writer, reachable)
    done = apply_deleted_masks(writer.store.execute, limit=64)
    assert done and all(table in TABLES for table, _, _ in done)
    assert sum(physical_rows(writer).values()) == attempt_rows(writer, reachable)
    assert current_fields(writer, head) == expected
    assert apply_deleted_masks(writer.store.execute, limit=64) == []


@pytest.mark.django_db(transaction=True)
def test_compaction_takes_server_side_ranges_only_where_they_pay_and_are_safe(
    live_writer, settings, monkeypatch
):
    from tracer.services.derived_metrics import compaction_ranges

    writer = live_writer
    metric = uuid4()
    initial, _ = prepare_field(writer, metric=metric, n=1)  # unit_start 2026-01-31
    writer.stage_and_publish(initial)
    # A small head copies faster through the legacy path.
    assert writer.compaction_path() == "small"
    head = writer.compact()
    assert not head.published_by.source_ceilings.get("range_compaction")
    assert head.published_by.batches.exists()
    settings.METRICS_SERVER_SIDE_MIN_ROWS = 0
    replacement, _ = prepare_field(writer, metric=metric, n=2)
    writer.stage_and_publish(replacement)
    assert writer.compaction_path() == "server"
    head = writer.compact()
    assert head.published_by.source_ceilings.get("range_compaction") is True
    assert not head.published_by.batches.exists()
    assert [r[:3] for r in current_fields(writer, head)] == [("unit-a", 2.0, 0)]
    # An expiry is due (the head holds a row past the physical cutoff): the
    # legacy copy, as before, and this pass's expiry retires the row.
    writer.retention_days = lambda: 30
    assert writer.compaction_path() == "expiry_due"
    writer.retention_days = lambda: 0
    # No query log: the lost-acknowledgement proof is impossible; the legacy
    # copy, with a warning, never silently.
    monkeypatch.setattr(compaction_ranges, "query_log_available", lambda store: False)
    assert writer.compaction_path() == "query_log"
    monkeypatch.undo()
    assert compaction_ranges.query_log_available(writer.store) is True
    settings.METRICS_SERVER_SIDE_COMPACTION = 0
    assert writer.compaction_path() == "disabled"


@pytest.mark.django_db(transaction=True)
def test_checkpoint_byte_boundary_preserves_all_wide_payloads(live_writer):
    from tracer.services.derived_metrics.publication import (
        MAX_COMPACTION_BYTES,
        MAX_COMPACTION_ROWS,
        FrozenRows,
    )

    writer = live_writer
    attempt = writer.begin()
    sql, params = field_query(group="g" * 5000)
    expanded = f"SELECT * REPLACE(concat('unit-', leftPad(toString(number), 4, '0')) AS unit_id) FROM ({sql}) AS f CROSS JOIN numbers(1001) AS n"
    payloads = writer.freeze(attempt, FIELD_VALUES, expanded, params)
    assert [len(p.rows) for p in payloads] == [500, 500, 1]
    writer.seal(attempt)
    head = writer.stage_and_publish(attempt)
    expected = current_fields(writer, head)
    checkpoint = writer.compact()
    assert current_fields(writer, checkpoint) == expected
    assert len(expected) == 1001
    plans = list(writer.plans(checkpoint.published_by, kind="field_values"))
    payloads = [FrozenRows.from_plan(plan) for plan in plans]
    assert len(payloads) >= 2  # the native byte boundary, not discovery's row cap
    assert sum(len(p.rows) for p in payloads) == 1001
    sizes = [sum(len(bytes.fromhex(row)) for row in p.rows) for p in payloads]
    assert sum(sizes) > MAX_COMPACTION_BYTES
    assert all(0 < size <= MAX_COMPACTION_BYTES for size in sizes)
    assert all(p.row_limit == MAX_COMPACTION_ROWS for p in payloads)
    batches = list(
        checkpoint.published_by.batches.filter(table_name="field_values").order_by(
            "batch_no"
        )
    )
    assert [(b.row_count, b.payload_sha256) for b in batches] == [
        (len(p.rows), p.digest) for p in payloads
    ]
    assert all(
        b.row_sequence_last - b.row_sequence_first + 1 == b.row_count for b in batches
    )
    sql, params = committed_rows_select(
        FIELD_VALUES,
        checkpoint,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
    )
    assert writer.store.execute(
        f"SELECT count(), min(length(group_key)), max(length(group_key)) FROM ({sql})",
        params,
    ) == [(1001, 5000, 5000)]
