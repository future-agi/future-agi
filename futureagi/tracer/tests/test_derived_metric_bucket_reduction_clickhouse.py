# ruff: noqa: F811 -- shared isolated CH/PG fixtures
"""Anonymous bucket reduction under the unchanged publication statement caps."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tracer.services.derived_metrics.frozen_relations import (
    RELATION_ROWS,
    FrozenRelationOverflow,
    FrozenRelations,
)
from tracer.services.derived_metrics.publication import (
    WRITE_SETTINGS,
    FrozenRows,
)
from tracer.services.derived_metrics.refresh import payload_select
from tracer.services.derived_metrics.storage import BUCKET_TOTALS, FIELD_VALUES
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    field_query,
    live_writer,
    metric_ch,
    publication_store,
)


def test_bucket_subdivision_rejects_mutable_keys_and_invalid_buckets():
    from tracer.services.derived_metrics.bounded_reads import KeyBucket

    assert (
        KeyBucket("unit_id", 7).predicate(FIELD_VALUES)
        == "cityHash64(unit_id) %% 8 = 7"
    )
    for column, bucket in (
        ("bucket_start", 0),
        ("status", 0),
        ("unit_id", -1),
        ("unit_id", 8),
        ("unit_id", True),
    ):
        with pytest.raises(ValueError):
            KeyBucket(column, bucket).predicate(FIELD_VALUES)


def _group_key_sql(groups):
    # leftPad truncates longer input; width must cover the entire group domain.
    width = max(2, len(str(groups - 1)))
    return (
        f"concat('group-', leftPad(toString(modulo(number, {groups})), {width}, '0'))"
    )


@pytest.mark.parametrize("groups", [60, 6000])
def test_group_fixture_keeps_native_cardinality(metric_ch, record_property, groups):
    query_id = "bucket-fixture-" + uuid4().hex
    try:
        assert metric_ch.execute(
            f"SELECT count(), uniqExact({_group_key_sql(groups)}) FROM numbers({groups})",
            query_id=query_id,
            settings=WRITE_SETTINGS,
        ) == [(groups, groups)]
    finally:
        metric_ch.execute("SYSTEM FLUSH LOGS")
        receipts = metric_ch.execute(
            "SELECT query_id, type, query_duration_ms, memory_usage, read_rows, exception_code "
            "FROM system.query_log WHERE query_id = %(id)s AND type != 'QueryStart'",
            {"id": query_id},
        )
        record_property("fixture_query_receipts", json.dumps(receipts))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "rows,groups,retired_count",
    [(1_000_000, 1, 0), (6000, 60, 7), (6000, 6000, 7)],
)
def test_million_member_bucket_reduction(
    live_writer,
    record_property,
    monkeypatch,
    rows,
    groups,
    retired_count,
    shape="uniform",
):
    """The failed first-refresh path, without repeating unrelated 1M compaction.

    Direct synthetic checkpoint seeding is diagnostic, not publication evidence.
    The real _freeze_buckets/anonymous-parts/freeze_select path must retain every
    contribution, including the 500 proposals replacing committed fields.
    """
    writer = live_writer
    store = writer.store
    metric, checkpoint = uuid4(), uuid4()
    at = datetime(2026, 1, 1, tzinfo=UTC)
    definition = SimpleNamespace(
        metric_id=metric,
        version=1,
        kind="average",
        level="conversation",
        formula="mean(y)",
        inputs=[
            {
                "alias": "y",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "reducer": "latest",
            }
        ],
    )
    source, params = field_query(metric=metric, start=at, n=1, group="")
    identity = (
        "concat('00000000-0000-0000-0000-', leftPad(toString(number+1), 12, '0'))"
    )
    domain = f"numbers({rows})"
    if shape == "same-hash":
        domain = (
            f"(SELECT number FROM numbers({rows * 16}) WHERE cityHash64({identity}) %% 8 = 0 "
            f"ORDER BY number LIMIT {rows})"
        )
    if shape == "wide":
        identity = f"concat(repeat('x', 4096), {identity})"
    source = source.replace("%(test_unit)s AS unit_id", identity + " AS unit_id")
    source = source.replace("%(test_unit)s AS session_id", identity + " AS session_id")
    # ClickHouse leftPad truncates input wider than the requested width. Keep
    # every group distinct and preserve numeric order in the top-50 tie-break.
    group_width = max(2, len(str(groups - 1)))
    if groups > 1:
        source = source.replace(
            "%(test_group)s AS group_key",
            f"{_group_key_sql(groups)} AS group_key",
        )
    head = SimpleNamespace(
        project_id=writer.lease.project_id,
        checkpoint_attempt_id=checkpoint,
        allowed_attempts=lambda: [(0, checkpoint)],
    )
    for offset in range(0, rows, 50_000):
        store.execute(
            "INSERT INTO metric_field_values_checkpoint SELECT * REPLACE("
            "toUUID(%(seed_org)s) AS org_id, toUUID(%(seed_project)s) AS project_id, "
            "toUUID(%(seed_attempt)s) AS attempt_id) "
            f"FROM ({source} FROM (SELECT number FROM {domain} ORDER BY number LIMIT {offset}, {min(50000, rows - offset)}))",
            {
                **params,
                "seed_org": str(writer.org_id),
                "seed_project": str(writer.lease.project_id),
                "seed_attempt": str(checkpoint),
            },
        )
    assert store.execute(
        "SELECT count(), uniqExact(group_key) FROM metric_field_values_checkpoint "
        "WHERE org_id = %(org)s AND project_id = %(project)s "
        "AND field_id = %(metric)s AND attempt_id = %(attempt)s",
        {
            "org": writer.org_id,
            "project": writer.lease.project_id,
            "metric": metric,
            "attempt": checkpoint,
        },
    ) == [(rows, groups)]
    if shape == "same-hash":
        assert store.execute(
            "SELECT uniqExact(cityHash64(unit_id) %% 8), min(cityHash64(unit_id) %% 8) "
            "FROM metric_field_values_checkpoint WHERE attempt_id = %(attempt)s",
            {"attempt": checkpoint},
        ) == [(1, 0)]
    if retired_count:
        store.execute(
            "INSERT INTO metric_bucket_retired_parts (org_id, project_id, metric_id, metric_version, "
            "bucket_start, group_key, generation, attempt_id, unit_count, weighted_n, weighted_d) "
            "VALUES (%(org)s, %(project)s, %(metric)s, 1, %(at)s, 'old', 0, %(attempt)s, %(count)s, %(count)s, %(count)s)",
            {
                "org": writer.org_id,
                "project": writer.lease.project_id,
                "metric": metric,
                "at": at,
                "attempt": checkpoint,
                "count": retired_count,
            },
        )
    attempt = writer.begin(ledger={"dirty_before": [{"bucket_start": at.isoformat()}]})
    # Wide overlays use one finite proposal: their size must not turn this
    # input-range regression into a test of legacy RowBinary compiler constants.
    overlay_count = 1 if shape == "wide" else 500
    frozen = writer.freeze(
        attempt,
        FIELD_VALUES,
        source
        + f" FROM (SELECT number FROM {domain} ORDER BY number LIMIT {overlay_count})",
        params,
    )
    overlay, overlay_params = payload_select(FIELD_VALUES, frozen, prefix="overlay")
    execute = store.execute
    query_ids = []
    transient_limits, transient_page_counts, transient_row_counts = [], [], []
    materialize = FrozenRelations.materialize
    handles = []

    def record_materialization(self, *args, **kwargs):
        result = materialize(self, *args, **kwargs)
        handles.append(result)
        if kwargs.get("role") == "child":
            transient_limits.append(RELATION_ROWS)
            transient_page_counts.append(1)
            transient_row_counts.append(result.row_count)
        return result

    monkeypatch.setattr(FrozenRelations, "materialize", record_materialization)

    def recorded(sql, params=None, **kwargs):
        query_id = kwargs.setdefault("query_id", "bucket-reduction-" + uuid4().hex)
        query_ids.append(query_id)
        return execute(sql, params, **kwargs)

    from tracer.services.derived_metrics import refresh

    probe = refresh.field_window_requires_subdivision
    decisions = []

    def subdivide(*args, **kwargs):
        result = probe(*args, **kwargs)
        decisions.append(result)
        # Exercise global folding across subdivisions even for this smaller
        # oracle fixture; separately assert the real bounded probe's decision.
        return result or groups > 1

    monkeypatch.setattr(refresh, "field_window_requires_subdivision", subdivide)
    store.execute = recorded
    try:
        writer._freeze_buckets(attempt, head, definition, overlay, overlay_params)
        assert decisions == [rows > 50_000]
        if shape == "uniform" and rows == 6000:
            assert transient_limits == [RELATION_ROWS] * 8
        else:
            assert len(transient_limits) > 1
            assert set(transient_limits) == {RELATION_ROWS}
        if groups == 6000:
            assert transient_page_counts == [1] * 8
            assert min(transient_row_counts) > 500
        assert [h.role for h in handles].count("complete-folded") == 1
        if shape == "wide":
            assert [h.role for h in handles].count(
                "retired"
            ) > 1  # fresh namespace replan
        else:
            assert [h.role for h in handles].count("retired") == 1
        # Backing tables never outlive preparation, even on the success path.
        for handle in handles:
            assert store.execute(f"EXISTS TABLE {handle.namespace}") == [(0,)]
        final, bound = payload_select(
            BUCKET_TOTALS,
            [
                FrozenRows.from_plan(p)
                for p in writer.plans(attempt, kind="bucket_totals")
            ],
            prefix="result",
        )
        assert store.execute(
            f"SELECT sum(unit_count), sum(weighted_n), sum(weighted_d) FROM ({final}) WHERE is_deleted = 0",
            bound,
        ) == [
            (
                rows + retired_count,
                float(rows + retired_count),
                float(rows + retired_count),
            )
        ]
        if groups > 1:
            counts = dict(
                store.execute(
                    f"SELECT group_key, sum(unit_count) FROM ({final}) WHERE is_deleted = 0 GROUP BY group_key",
                    bound,
                )
            )
            per_group = rows // groups
            expected_groups = {
                f"group-{i:0{group_width}}": per_group for i in range(groups)
            }
            if retired_count:
                expected_groups["old"] = retired_count
            ranked = sorted(
                expected_groups.items(), key=lambda item: (-item[1], item[0])
            )
            # The retired group wins a top-50 slot when each live group has one
            # member. Ranking it into Other would hide a global-fold regression.
            assert counts == {
                **dict(ranked[:50]),
                '["other"]': sum(count for _, count in ranked[50:]),
            }
        assert list(writer.plans(attempt, kind="bucket_retired_parts")) == []
    finally:
        store.execute = execute
        execute("SYSTEM FLUSH LOGS")
        receipts = execute(
            "SELECT query_id, type, query_duration_ms, memory_usage, read_rows, exception_code "
            "FROM system.query_log WHERE query_id IN %(ids)s AND type != 'QueryStart'",
            {"ids": tuple(query_ids)},
        )
        record_property("bucket_query_receipts", json.dumps(receipts))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "shape,rows", [("same-hash", 120_000), ("same-hash", 1_000_128), ("wide", 6000)]
)
def test_bucket_input_adversaries(
    live_writer, record_property, monkeypatch, shape, rows
):
    """Native collisions and serialized bytes, with unchanged statement caps."""
    test_million_member_bucket_reduction(
        live_writer, record_property, monkeypatch, rows, 1, 7, shape=shape
    )


def test_revision_ranges_exhaust_empty_windows_and_reject_indivisible_history(
    publication_store,
):
    from tracer.services.derived_metrics.bounded_reads import (
        BUCKET_INPUT_ROWS,
        BucketFieldPlan,
        BucketRangeOverflow,
    )

    store = publication_store
    org, project, metric = uuid4(), uuid4(), uuid4()
    at = datetime(2026, 1, 1, tzinfo=UTC)
    source, params = field_query(metric=metric, start=at)
    source = source.replace(
        "%(test_unit)s AS unit_id", "toString(intDiv(number, 20)) AS unit_id"
    )
    source = f"SELECT * REPLACE(toUInt64(number) AS _version, if(intDiv(number, 20) < 5, bucket_start + INTERVAL 1 DAY, bucket_start) AS bucket_start) FROM ({source}, number FROM numbers(400))"
    plan = BucketFieldPlan(
        None,
        org,
        project,
        metric,
        1,
        "bucket_start = toDateTime(%(at)s, 'UTC')",
        {"at": at},
        (source, params),
        False,
    )
    scan = plan.scan(store, target_rows=21)
    seen, empty = [], 0
    for sql, bound in scan:
        values = store.execute(f"SELECT unit_id, _version FROM ({sql})", bound)
        empty += not values
        seen.extend(values)
    assert scan.complete and empty > 0
    assert dict(seen) == {str(i): i * 20 + 19 for i in range(5, 20)}
    assert len(seen) == len(dict(seen))
    assert sum(d[3] for d in scan.ranges) == 400
    assert scan.exhausted_routes == [None]
    with pytest.raises(ValueError, match="single-use"):
        list(scan)

    source, params = field_query(metric=metric, start=at)
    oversized = BucketFieldPlan(
        None,
        org,
        project,
        metric,
        1,
        "1",
        {},
        (source + f" FROM numbers({BUCKET_INPUT_ROWS + 1})", params),
        False,
    )
    scan = oversized.scan(store, target_rows=1)
    with pytest.raises(BucketRangeOverflow):
        list(scan)
    assert not scan.complete and scan.ranges == []


def test_checked_handle_identity_role_tamper_and_cleanup(publication_store):
    from dataclasses import replace

    from tracer.services.derived_metrics.buckets import live_parts_select
    from tracer.services.derived_metrics.publication import MetricVisibilityError

    store = publication_store
    definition = SimpleNamespace(
        metric_id=uuid4(),
        version=1,
        kind="average",
        level="conversation",
        formula="mean(y)",
        inputs=[
            {
                "alias": "y",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "reducer": "latest",
            }
        ],
    )
    source, params = field_query(metric=definition.metric_id)
    relations = FrozenRelations(
        store, org_id=uuid4(), project_id=uuid4(), attempt_id=uuid4(), source=["pinned"]
    )
    with relations:
        retired = relations.materialize(
            "SELECT * FROM metric_bucket_retired_parts WHERE 0", {}, role="retired"
        )
        child = relations.materialize(
            live_parts_select(definition, source), params, role="child"
        )
        with pytest.raises(MetricVisibilityError, match="identity"):
            relations.select(replace(child), role="child")
        with pytest.raises(MetricVisibilityError, match="identity"):
            relations.select(child, role="complete-folded")
        with pytest.raises(MetricVisibilityError, match="incomplete"):
            relations.finish(expected_children=2)
        final = relations.finish(expected_children=1)
        assert store.execute(
            f"SELECT sum(unit_count) FROM ({relations.select(final, role='complete-folded')})"
        ) == [(1,)]
        with pytest.raises(MetricVisibilityError, match="sealed"):
            relations.materialize(
                live_parts_select(definition, source), params, role="child"
            )
        store.execute(f"INSERT INTO {child.namespace} SELECT * FROM {child.namespace}")
        with pytest.raises(MetricVisibilityError, match="changed"):
            relations.select(child, role="child")
    for handle in (retired, child, final):
        assert store.execute(f"EXISTS TABLE {handle.namespace}") == [(0,)]
    with pytest.raises(MetricVisibilityError, match="session"):
        relations.select(final, role="complete-folded")


def test_handle_physical_budget_fails_before_insert_and_cleans_up(
    publication_store, monkeypatch
):
    from clickhouse_driver.errors import ServerException

    from tracer.services.derived_metrics import frozen_relations
    from tracer.services.derived_metrics.buckets import live_parts_select

    store = publication_store
    definition = SimpleNamespace(
        metric_id=uuid4(),
        version=1,
        kind="average",
        level="conversation",
        formula="mean(y)",
        inputs=[
            {
                "alias": "y",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "reducer": "latest",
            }
        ],
    )
    source, params = field_query(metric=definition.metric_id, group="x" * 8192)
    # Reduce the capture budget to exercise its boundary, never the statement caps.
    monkeypatch.setattr(frozen_relations, "RELATION_BYTES", 4096)
    relations = FrozenRelations(
        store, org_id=uuid4(), project_id=uuid4(), attempt_id=uuid4(), source=[]
    )
    with pytest.raises(FrozenRelationOverflow, match="capture budget") as raised:
        with relations:
            relations.materialize(
                live_parts_select(definition, source), params, role="child"
            )
    assert isinstance(raised.value.__cause__, ServerException)
    assert raised.value.__cause__.code == 395
    assert "metric_frozen_relation_budget" in raised.value.__cause__.message
    assert relations.closed and not relations.handles
    for name in relations.names:
        assert store.execute(f"EXISTS TABLE {name}") == [(0,)]


@pytest.mark.parametrize(
    "code,message",
    [
        (
            395,
            "DB::Exception: unrelated_capture_failure: metric_frozen_relation_budget",
        ),
        (241, "DB::Exception: metric_frozen_relation_budget"),
        (159, "DB::Exception: timeout"),
        (60, "DB::Exception: missing table"),
    ],
)
def test_capture_does_not_normalize_unrelated_server_failures(
    publication_store, monkeypatch, code, message
):
    from clickhouse_driver.errors import ServerException

    store = publication_store
    execute = store.execute
    failure = ServerException(message, code=code)

    def fail_insert(sql, *args, **kwargs):
        if sql.startswith("INSERT INTO metric_frozen_"):
            raise failure
        return execute(sql, *args, **kwargs)

    monkeypatch.setattr(store, "execute", fail_insert)
    relations = FrozenRelations(
        store, org_id=uuid4(), project_id=uuid4(), attempt_id=uuid4(), source=[]
    )
    with pytest.raises(ServerException) as raised:
        with relations:
            relations.materialize(
                "SELECT * FROM metric_bucket_retired_parts WHERE 0", {}, role="retired"
            )
    assert raised.value is failure
    assert relations.closed and not relations.handles
    for name in relations.names:
        assert store.execute(f"EXISTS TABLE {name}") == [(0,)]
