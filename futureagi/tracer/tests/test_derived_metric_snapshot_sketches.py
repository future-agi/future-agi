# ruff: noqa: F811
"""Pinned opaque states, typed contributions and physical sketch erasure on CH."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricRun,
    DerivedMetricVersion,
    MetricProjectRefresh,
)
from tracer.services.derived_metrics import erasure, snapshots
from tracer.services.derived_metrics.deletion_protocol import _intent
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.reads import (
    _aggregate,
    _aggregate_select,
    _rows,
    bucket_select,
)
from tracer.services.derived_metrics.refresh import payload_select
from tracer.services.derived_metrics.snapshot_sketches import (
    BUCKET_COLUMNS,
    BUCKET_TABLE,
    UNIT_TABLE,
)
from tracer.services.derived_metrics.storage import FIELD_VALUES, TABLES
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    field_query,
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_snapshot_erasure_clickhouse import (
    execute,
    overall,
)

pytestmark = pytest.mark.django_db(transaction=True)
CASES = [
    ("unique_count", "text", None),
    ("unique_count", "boolean", None),
    ("unique_count", "number", None),
    ("percentile", "number", 0.5),
    ("percentile", "number", 0.9),
    ("percentile", "number", 0.99),
]


@pytest.mark.parametrize("percentile", [0.5, 0.9, 0.99])
def test_percentile_finalizer_orders_after_winner_selection(metric_ch, percentile):
    """A full-range sort barrier must beat upstream ORDER BY ... LIMIT 1 BY."""
    from tracer.services.derived_metrics.reads import MetricReadStore

    metric_ch.execute(
        "CREATE TABLE percentile_order_parts ("
        "bucket_start DateTime('UTC'), group_key String, scope_key String, stratum_key String, "
        "unit_count UInt64, valid_count UInt64, missing_count UInt64, invalid_count UInt64, "
        "known_sampled UInt64, nonempty_scope_count UInt64, scope_min String, scope_max String, "
        "quantile_state AggregateFunction(quantileTDigest, Float64)) ENGINE=MergeTree "
        "ORDER BY (bucket_start, group_key, scope_key, stratum_key)"
    )
    value = "toFloat64(modulo(number * 7919, 100003)) / 7 + if(modulo(number, 3) = 0, 100000., 0.)"
    metric_ch.execute(
        "INSERT INTO percentile_order_parts SELECT "
        "toDateTime('2026-01-01 00:00:00', 'UTC') + toIntervalMinute(15 * modulo(number, 8)) AS bucket_start, "
        "concat('cohort-', toString(modulo(intDiv(number, 8), 3))) AS group_key, "
        "'' AS scope_key, '' AS stratum_key, count(), countIf(number != 1), "
        "countIf(number = 1), 0, 0, 0, '', '', "
        f"quantileTDigestStateIf({value}, number != 1) FROM numbers(6000) "
        "GROUP BY bucket_start, group_key, scope_key, stratum_key "
        "ORDER BY bucket_start, group_key",
        settings={"max_threads": 1},
    )
    keys = "bucket_start, group_key, scope_key, stratum_key"
    sources = (
        "SELECT * FROM percentile_order_parts",
        "SELECT * FROM (SELECT *, toUInt64(1) AS generation, "
        "toUInt64(toUnixTimestamp(bucket_start) * 4 + toUInt64(right(group_key, 1))) AS version "
        "FROM percentile_order_parts) ORDER BY generation DESC, version DESC "
        f"LIMIT 1 BY {keys}",
    )
    # The independent oracle sorts materialized keyed states before folding them.
    # Its 24-state array is only a test oracle; production keeps streaming merges.
    expected = metric_ch.execute(
        f"SELECT toFloat64(arrayReduce('quantileTDigestMerge({percentile})', "
        "arrayMap(t -> t.2, arraySort(t -> t.1, "
        f"groupArray(tuple(tuple({keys}), quantile_state)))))) "
        "FROM percentile_order_parts",
        settings={"max_threads": 1},
    )[0][0]
    definition = SimpleNamespace(
        kind="percentile",
        inputs=[{"alias": "y", "value_type": "number", "reducer": "latest"}],
        field_spec={"percentile": percentile},
    )
    rows = []
    for source in sources:
        params = {}
        rows.append(
            _rows(
                MetricReadStore(metric_ch),
                _aggregate_select(source, definition, params),
                params,
            )[0]
        )
    assert rows[0] == rows[1]
    assert signature(rows[0]) == (expected, 6000, 5999, 1, 0, "ok")


def test_schema_038_is_additive_and_reapplicable(metric_ch):
    from tracer.services.clickhouse.v2.apply_schema_rewriter import split_statements
    from tracer.tests.test_derived_metric_clickhouse import SCHEMA

    before = metric_ch.execute("DESCRIBE TABLE derived_metric_inputs")
    for _ in range(2):
        for sql in split_statements(
            (SCHEMA / "038_metric_snapshot_sketches.sql").read_text()
        ):
            metric_ch.execute(sql)
    after = metric_ch.execute("DESCRIBE TABLE derived_metric_inputs")
    assert after[: len(before)] == before
    assert [row[0] for row in after[len(before) :]] == [
        "unit_start",
        "bucket_start",
        "status",
        "value_bool",
        "value_number",
        "value_text",
    ]
    assert metric_ch.execute(f"SELECT count() FROM {BUCKET_TABLE}") == [(0,)]


@pytest.fixture(params=CASES)
def sketch_case(request, live_writer, workspace, observe_project, user):
    kind, value_type, percentile, *options = request.param
    # Exercise the approximate HLL representation with 6,000 numeric identities
    # in one cell; text/boolean and percentile cases exercise multi-bucket merges.
    numeric_distinct = kind == "unique_count" and value_type == "number"
    groups = options[0] if options else (1 if numeric_distinct else 3)
    invalid_survivor = len(options) > 1 and options[1]
    sample_count = 8 if invalid_survivor else 6000
    buckets = 1 if numeric_distinct else 8
    metric = DerivedMetric.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        project=observe_project,
        name="Frozen sketch",
    )
    descriptor = {
        "alias": "y",
        "source": "span",
        "field": {
            "number": "attribute_number",
            "text": "attribute_string",
            "boolean": "attribute_bool",
        }[value_type],
        "attribute_key": "answer",
        "reducer": "latest",
        "value_type": value_type,
    }
    definition = DerivedMetricVersion.objects.create(
        metric=metric,
        version=1,
        kind=kind,
        level="conversation",
        inputs=[descriptor],
        field_spec={"percentile": percentile} if percentile else {},
        selection_unit="session",
        weighting="none",
        definition_hash="d" * 64,
        backfill_status="complete",
    )
    metric.active_version = definition
    metric.save(update_fields=["active_version"])
    # End on a fully settled day even if this fixture runs just after midnight.
    end = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=1
    )
    start = end - timedelta(days=2)
    dirty = [start + timedelta(minutes=15 * i) for i in range(buckets)]
    attempt = live_writer.begin(
        ledger={"dirty_after": [{"bucket_start": t.isoformat()} for t in dirty]}
    )
    sql, params = field_query(start=start, metric=metric.id)
    # Distinct values unique to erased owners, duplicate surviving values, false,
    # and non-integer numbers all survive typed capture without map coercion.
    n = "if(number = 1, NULL, toFloat64(modulo(number * 7919, 100003)) / 7 + if(modulo(number, 3) = 0, 100000., 0.))"
    sql = f"""SELECT * REPLACE(
        concat('unit-', toString(number)) AS unit_id,
        concat('unit-', toString(number)) AS session_id,
        if(modulo(number, 3) = 0, 'erase-me', 'keep-me') AS user_id,
        toDateTime(%(test_start)s, 'UTC') + toIntervalMinute(15 * modulo(number, {buckets})) AS bucket_start,
        toDateTime64(%(test_start)s, 6, 'UTC') + toIntervalMinute(15 * modulo(number, {buckets})) + toIntervalMicrosecond(number) AS unit_start,
        concat('cohort-', toString(modulo(intDiv(number, 8), {groups}))) AS group_key,
        if({int(invalid_survivor)} AND number = 2, NULL, toNullable({n})) AS value_number,
        toUInt8({int(invalid_survivor)} AND number = 2) AS invalid_probability,
        if({int(invalid_survivor)} AND number = 2, 'no_value', if(number = 1, 'missing', 'ok')) AS status,
        multiIf(number = 1, NULL, number = 0, '', modulo(number, 3) = 0, 'erase-only', concat('label-', toString(modulo(number, 37)))) AS value_text,
        if(number = 1, NULL, toUInt8(modulo(number, 3) = 0)) AS value_bool,
        map('y', toNullable({n})) AS inputs
    ) FROM ({sql}) AS template CROSS JOIN numbers({sample_count}) AS samples"""
    payloads = live_writer.freeze(attempt, FIELD_VALUES, sql, params)
    overlay, bound = payload_select(FIELD_VALUES, payloads, prefix="sketch_fixture")
    live_writer._freeze_buckets(
        attempt, None, definition, overlay, bound, complete_fields=True
    )
    # This synthetic source is exhausted through now, not the epoch. Persist
    # the consumed cut through the same publication protocol as a real capture.
    live_writer.seal(
        attempt, cursors={"watermark_ns": int(timezone.now().timestamp()) * 10**9}
    )
    live_writer.stage_and_publish(attempt)
    # Capture from checkpoints as well as immutable deltas.
    live_writer.compact()
    data = {"window_start": start, "window_end": end, "idempotency_key": str(uuid4())}
    with patch.object(snapshots, "enqueue_capture"):
        saved = snapshots.request_snapshot(metric, data, user)
    return metric, DerivedMetricRun.objects.get(pk=saved["id"]), data


def result(store, run):
    return store.execute(
        "SELECT value, unit_count, valid_count, missing_count, invalid_probability_count, status "
        "FROM derived_metric_results WHERE run_id = %(run)s AND result_id = %(result)s AND rollup = 'overall'",
        {"run": str(run.id), "result": str(run.result_id)},
    )[0]


def signature(row):
    return tuple(
        row[k]
        for k in (
            "value",
            "unit_count",
            "valid_count",
            "missing_count",
            "invalid_probability_count",
            "status",
        )
    )


def reference_survivors(writer, metric, run, predicate):
    sql, params = committed_rows_select(
        FIELD_VALUES,
        run.pin.manifest,
        org_id=metric.organization_id,
        project_id=metric.project_id,
        key_in={"field_id": (metric.id,), "field_version": (run.definition.version,)},
    )
    buckets = writer.store.execute(
        f"SELECT DISTINCT bucket_start FROM ({sql}) ORDER BY bucket_start", params
    )
    head = MetricProjectRefresh.for_project(metric.project_id).head_manifest
    dirty = [{"bucket_start": start.isoformat()} for (start,) in buckets]
    attempt = writer.begin(ledger={"dirty_after": dirty})
    # Use the REAL live publication path, not the frozen-unit reconstruction.
    # TDigest serialization compacts pending centroids. Directly reading scratch
    # Memory states skips that lossy wire boundary and is not a published KPI.
    # The snapshot pin still names the original generation, independent of this
    # survivor-only reference generation and all later reference publications.
    writer._freeze_buckets(
        attempt,
        head,
        run.definition,
        f"SELECT * FROM ({sql}) WHERE {predicate} ORDER BY bucket_start, unit_id",
        params,
        complete_fields=True,
    )
    writer.seal(attempt)
    published = writer.stage_and_publish(attempt)
    return signature(
        _aggregate(
            writer.store,
            metric,
            run.definition,
            published,
            {"window_start": run.window_start, "window_end": run.window_end},
        )
    )


def test_exact_pinned_kpi_and_state_copy(sketch_case, live_writer, monkeypatch):
    metric, run, data = sketch_case
    store = live_writer.store
    expected = signature(
        _aggregate(store, metric, run.definition, run.pin.manifest, data)
    )
    # A newer published generation cannot change what the queued capture reads.
    draft = live_writer.begin()
    sql, params = field_query(
        "unit-0", n=999999, start=data["window_start"], metric=metric.id
    )
    live_writer.freeze(draft, FIELD_VALUES, sql, params)
    live_writer.seal(draft)
    live_writer.stage_and_publish(draft)
    monkeypatch.setattr(snapshots, "ROLLUP_PAGE_ROWS", 1024)
    pinned, bound = bucket_select(metric, run.definition, run.pin.manifest, data)
    columns = ", ".join(BUCKET_COLUMNS)
    before = store.execute(
        f"SELECT formatRow('RowBinary', {columns}) FROM ({pinned}) ORDER BY bucket_start, group_key, scope_key, stratum_key",
        bound,
    )
    execute(run, store)
    assert run.status == "complete", run.error
    assert result(store, run) == expected
    if run.definition.kind == "unique_count":
        source_type = run.definition.inputs[0]["value_type"]
        if source_type in {"text", "boolean"}:
            assert expected[0] == {"text": 39, "boolean": 2}[source_type]
    assert expected[2:4] == (5999, 1)
    unused = (
        "sum(finalizeAggregation(uniq_state))"
        if run.definition.kind == "percentile"
        else "countIf(isFinite(finalizeAggregation(quantile_state)))"
    )
    assert store.execute(
        f"SELECT {unused} FROM {BUCKET_TABLE} WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [(0,)]
    assert (
        store.execute(
            f"SELECT formatRow('RowBinary', {columns}) FROM {BUCKET_TABLE} WHERE snapshot_id = %(s)s ORDER BY bucket_start, group_key, scope_key, stratum_key",
            {"s": str(run.snapshot_id)},
        )
        == before
    )
    assert store.execute(
        "SELECT count(), countIf(toUnixTimestamp64Micro(unit_start) != toUnixTimestamp64Micro(toDateTime64(bucket_start, 6))) "
        "FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [(6000, 5999)]
    assert store.execute(
        "SELECT count(), sum(unit_count) FROM derived_metric_results WHERE run_id = %(r)s AND result_id = %(v)s AND rollup = 'session'",
        {"r": str(run.id), "v": str(run.result_id)},
    ) == [(6000, 6000)]


@pytest.mark.parametrize("sketch_case", [("unique_count", "text", None)], indirect=True)
def test_incomplete_bucket_capture_fails_and_purges_typed_and_sketch_rows(
    sketch_case, live_writer
):
    from tracer.models.derived_metric import MetricErasureRequest

    metric, run, _ = sketch_case
    original = snapshots.capture_buckets_select

    def omit_bucket(*args):
        sql, params = original(*args)
        return (
            sql
            + " WHERE bucket_start != (SELECT min(bucket_start) FROM derived_metric_inputs WHERE snapshot_id = %(snapshot_id)s)",
            params,
        )

    with patch.object(snapshots, "capture_buckets_select", side_effect=omit_bucket):
        execute(run, live_writer.store)
    assert run.status == "failed" and run.error == "expired"
    assert run.result_id is None
    # The failure obligation is attempt-qualified: it owns exactly this attempt's
    # fresh snapshot namespace, including its typed units and sketch states.
    job = MetricErasureRequest.objects.get(
        deletion_key=f"capture-attempt:{run.id}:{run.attempt_id}"
    )
    assert job.snapshot_key == run.snapshot_id and job.result_key == run.attempt_id
    assert erasure.process_erasure(job.id, store=live_writer.store)
    for table in ("derived_metric_inputs", BUCKET_TABLE, UNIT_TABLE):
        assert live_writer.store.execute(
            f"SELECT count() FROM {table} WHERE snapshot_id = %(s)s",
            {"s": str(run.snapshot_id)},
        ) == [(0,)]


def redaction(metric, run, kind, subjects, count):
    return _intent(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope="run",
        run_key=run.id,
        snapshot_key=run.snapshot_id,
        metric_key=metric.id,
        store_payload={
            "protocol": "metric_snapshot_redaction",
            "subject_kind": kind,
            "subjects": subjects,
            "removed_units": count,
        },
    )


def test_erasure_rebuild_needs_no_live_data_and_resumes(
    sketch_case, live_writer, monkeypatch
):
    metric, run, _ = sketch_case
    store = live_writer.store
    expected = reference_survivors(live_writer, metric, run, "user_id != 'erase-me'")
    execute(run, store)
    assert run.status == "complete", run.error
    old_result = run.result_id
    # Neither raw source nor pinned/live fields remain available for recomputation.
    for table in TABLES:
        store.execute(f"TRUNCATE TABLE {table}")
    child = redaction(metric, run, "users", ["erase-me"], 2000)
    original = erasure.rebuild_buckets
    from tracer.services.derived_metrics.frozen_relations import (
        FrozenRelationOverflow,
        FrozenRelations,
    )

    materialize = FrozenRelations.materialize
    splits = []

    def constrained(relations, sql, params, **kwargs):
        width = len(params.get("buckets", ()))
        if kwargs.get("role") == "child" and width > 1:
            splits.append(width)
            raise FrozenRelationOverflow("test capture budget")
        return materialize(relations, sql, params, **kwargs)

    monkeypatch.setattr(FrozenRelations, "materialize", constrained)

    def interrupted(*args):
        original(*args)
        raise RuntimeError("lost acknowledgement after bucket rebuild")

    with patch.object(erasure, "rebuild_buckets", side_effect=interrupted):
        assert not erasure.process_erasure(child.id, store=store)
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s AND user_id = 'erase-me'",
        {"s": str(run.snapshot_id)},
    ) == [(0,)]
    monkeypatch.setattr(snapshots, "ROLLUP_PAGE_ROWS", 1024)
    assert erasure.process_erasure(child.id, store=store)
    assert erasure.process_erasure(child.id, store=store)
    run.refresh_from_db()
    assert result(store, run) == expected
    if run.definition.kind == "unique_count":
        source_type = run.definition.inputs[0]["value_type"]
        if source_type in {"text", "boolean"}:
            assert expected[0] == {"text": 37, "boolean": 1}[source_type]
    assert run.removed_unit_count == 2000
    if not (
        run.definition.kind == "unique_count"
        and run.definition.inputs[0]["value_type"] == "number"
    ):
        assert max(splits) == 8
    assert store.execute(
        "SELECT count() FROM derived_metric_results WHERE result_id = %(r)s",
        {"r": str(old_result)},
    ) == [(0,)]
    assert store.execute(
        f"SELECT sum(unit_count) FROM {BUCKET_TABLE} WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [(4000,)]
    # A project erasure clears ALL typed data and opaque states, not just results.
    child = redaction(metric, run, "project", [], 4000)
    assert erasure.process_erasure(child.id, store=store)
    run.refresh_from_db()
    assert overall(store, run) == [(None, 0)]
    for table in ("derived_metric_inputs", BUCKET_TABLE, UNIT_TABLE):
        assert store.execute(
            f"SELECT count() FROM {table} WHERE snapshot_id = %(s)s",
            {"s": str(run.snapshot_id)},
        ) == [(0,)]


@pytest.mark.parametrize(
    "sketch_case", [("percentile", "number", 0.95, 60)], indirect=True
)
def test_folded_cohorts_and_paged_scope_guard(sketch_case, live_writer, monkeypatch):
    metric, run, data = sketch_case
    store = live_writer.store
    expected = signature(
        _aggregate(store, metric, run.definition, run.pin.manifest, data)
    )
    survivor = reference_survivors(live_writer, metric, run, "user_id != 'erase-me'")
    # Duplicate cohort keys cross page boundaries. Only full groups may merge.
    monkeypatch.setattr(snapshots, "ROLLUPS", ("overall", "cohort", "user", "day"))
    monkeypatch.setattr(snapshots, "ROLLUP_PAGE_ROWS", 7)
    execute(run, store)
    assert run.status == "complete", run.error
    assert result(store, run) == expected
    params = {"r": str(run.id), "v": str(run.result_id)}
    assert store.execute(
        "SELECT count(), sum(unit_count), countIf(group_key = '[\"other\"]') "
        "FROM derived_metric_results WHERE run_id = %(r)s AND result_id = %(v)s AND rollup = 'cohort'",
        params,
    ) == [(51, 6000, 1)]
    child = redaction(metric, run, "users", ["erase-me"], 2000)
    assert erasure.process_erasure(child.id, store=store)
    run.refresh_from_db()
    assert result(store, run) == survivor
    # A page containing a single sampling scope must not undo the window guard.
    for table in ("derived_metric_inputs", UNIT_TABLE):
        store.execute(
            f"ALTER TABLE {table} UPDATE sampling_scope = unit_id "
            "WHERE snapshot_id = %(s)s SETTINGS mutations_sync=2",
            {"s": str(run.snapshot_id)},
        )
    DerivedMetricRun.objects.filter(pk=run.id).update(
        status="running", attempt_id=uuid4()
    )
    run.refresh_from_db()
    snapshots.delete_rows(
        store, BUCKET_TABLE, "snapshot_id = %(s)s", {"s": str(run.snapshot_id)}
    )
    erasure.rebuild_buckets(store, run)
    snapshots.commit_results(store, run)
    run.refresh_from_db()
    assert result(store, run)[-1] == "invalid_probability"
    assert store.execute(
        "SELECT countIf(status != 'invalid_probability') FROM derived_metric_results "
        "WHERE run_id = %(r)s AND result_id = %(v)s",
        {"r": str(run.id), "v": str(run.result_id)},
    ) == [(0,)]


@pytest.mark.parametrize(
    "sketch_case", [("percentile", "number", 0.95, 1, True)], indirect=True
)
def test_invalid_probability_survivor_preserves_completeness_across_rollups(
    sketch_case, live_writer
):
    metric, run, data = sketch_case
    store = live_writer.store
    survivor = "user_id != 'erase-me'"
    expected = reference_survivors(live_writer, metric, run, survivor)
    assert expected == (None, 5, 3, 1, 1, "invalid_probability")
    initial = signature(
        _aggregate(store, metric, run.definition, run.pin.manifest, data)
    )
    # Successful capture releases its pin. Freeze independent live-writer
    # references first, before capture or removing their source.
    references = {}
    for rollup, groups in {
        "overall": {"": "1"},
        "cohort": {"cohort-0": "group_key = 'cohort-0'"},
        "day": {data["window_start"].date().isoformat(): "1"},
        "user": {"keep-me": "user_id = 'keep-me'"},
        "session": {f"unit-{i}": f"unit_id = 'unit-{i}'" for i in (1, 2, 4, 5, 7)},
    }.items():
        references[rollup] = {
            group: reference_survivors(
                live_writer, metric, run, f"({survivor}) AND ({predicate})"
            )
            for group, predicate in groups.items()
        }
    execute(run, store)
    assert run.status == "complete", run.error
    assert result(store, run) == initial
    assert store.execute(
        f"SELECT value_number, input_number, invalid_probability FROM {UNIT_TABLE} "
        "WHERE snapshot_id = %(s)s AND unit_id = 'unit-2'",
        {"s": str(run.snapshot_id)},
    ) == [(None, (2 * 7919 % 100003) / 7, 1)]
    for table in TABLES:
        store.execute(f"TRUNCATE TABLE {table}")
    child = redaction(metric, run, "users", ["erase-me"], 3)
    assert erasure.process_erasure(child.id, store=store)
    run.refresh_from_db()
    assert result(store, run) == expected
    for rollup, reference in references.items():
        rows = store.execute(
            "SELECT group_key, value, unit_count, valid_count, missing_count, "
            "invalid_probability_count, status FROM derived_metric_results "
            "WHERE run_id = %(r)s AND result_id = %(v)s AND rollup = %(rollup)s",
            {"r": str(run.id), "v": str(run.result_id), "rollup": rollup},
        )
        assert {row[0]: row[1:] for row in rows} == reference, rollup
        assert tuple(sum(row[i] for row in rows) for i in (2, 3, 4, 5)) == expected[1:5]
    assert run.removed_unit_count == 3


def test_session_and_org_erasure_use_existing_live_path(sketch_case, live_writer):
    metric, run, _ = sketch_case
    store = live_writer.store
    expected = reference_survivors(live_writer, metric, run, "unit_id != 'unit-0'")
    execute(run, store)
    request = erasure.erase_metric_data(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope_kind="sessions",
        subject_ids=["unit-0"],
    )
    assert erasure.process_erasure(request.id, store=store, writer=live_writer)
    run.refresh_from_db()
    assert result(store, run) == expected
    assert run.removed_unit_count == 1
    request = erasure.erase_metric_data(
        organization_id=metric.organization_id, scope_kind="organization"
    )
    assert erasure.process_erasure(request.id, store=store)
    run.refresh_from_db()
    assert overall(store, run) == [(None, 0)]
    for table in ("derived_metric_inputs", BUCKET_TABLE, UNIT_TABLE):
        assert store.execute(
            f"SELECT count() FROM {table} WHERE org_id = %(o)s",
            {"o": str(metric.organization_id)},
        ) == [(0,)]
