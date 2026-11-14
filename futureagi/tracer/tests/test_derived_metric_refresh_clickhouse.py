# ruff: noqa: F811 -- shared isolated CH/PG fixtures
"""Refresh integration, whole buckets, source merges, crashes and oracle changes."""

import math
from datetime import UTC, datetime, timedelta
from random import Random
from uuid import UUID, uuid4

import pytest

from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricVersion,
    MetricProjectRefresh,
)
from tracer.services.derived_metrics.changes import (
    ChangeBatch,
    Dependency,
    SourceKey,
    UnitKey,
)
from tracer.services.derived_metrics.publication import (
    FrozenRows,
    committed_rows_select,
)
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.storage import (
    BUCKET_TOTALS,
    FIELD_VALUES,
    SOURCE_DEPENDENCIES,
)
from tracer.tests.test_derived_metric_clickhouse import seed_span
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    current_fields,
    field_query,
    live_writer,
    metric_ch,
    prepare_field,
    publication_store,
)


def definition_for(writer, workspace, project, *, level="conversation"):
    metric = DerivedMetric.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        project=project,
        name="Test metric",
    )
    return DerivedMetricVersion.objects.create(
        metric=metric,
        version=1,
        kind="average",
        level=level,
        user_rule="average" if level == "user" else "",
        formula="mean(resolved)",
        inputs=[
            {
                "alias": "resolved",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "reducer": "latest",
            }
        ],
        selection_unit="session",
        weighting="none",
        cohort_key="region",
        definition_hash="a" * 64,
    )


def span_definition_for(writer, workspace, project):
    base = definition_for(writer, workspace, project)
    return DerivedMetricVersion.objects.create(
        metric=base.metric,
        version=2,
        **{**base.semantics(), "level": "span", "selection_unit": "span"},
    )


def seed_span_revision(
    writer,
    scope,
    trace,
    span,
    session,
    at,
    value,
    *,
    revision,
    deleted=0,
    ended=True,
    group="west",
    arrival=None,
):
    writer.store.execute(
        "INSERT INTO spans (id, trace_id, project_id, org_id, trace_session_id, start_time, end_time, created_at, updated_at, _version, is_deleted, attrs_number, attrs_string) "
        "VALUES (%(span)s, %(trace)s, %(project_id)s, %(org_id)s, %(session)s, %(at)s, %(ended)s, %(at)s, %(arrival)s, %(revision)s, %(deleted)s, map('resolved', %(value)s), map('region', %(group)s))",
        {
            **scope.parameters(),
            "span": span,
            "trace": str(trace),
            "session": str(session),
            "at": at,
            "ended": at + timedelta(seconds=1) if ended else None,
            "arrival": arrival or at,
            "revision": revision,
            "deleted": deleted,
            "value": value,
            "group": group,
        },
    )


def metric_bucket_rows(writer, head, definition):
    sql, params = committed_rows_select(
        BUCKET_TOTALS, head, org_id=writer.org_id, project_id=writer.lease.project_id
    )
    return writer.store.execute(
        f"SELECT bucket_start, group_key, unit_count, weighted_n, weighted_d FROM ({sql}) "
        "WHERE metric_id = toUUID(%(oracle_metric)s) AND metric_version = %(oracle_version)s AND is_deleted = 0 ORDER BY bucket_start, group_key",
        {
            **params,
            "oracle_metric": str(definition.metric_id),
            "oracle_version": definition.version,
        },
    )


def change_for(session, *, cursor=1, dependencies=(), prior=()):
    keys = tuple(SourceKey(d.trace_id, d.span_id) for d in dependencies)
    return ChangeBatch(
        keys,
        (UnitKey("conversation", str(session)),),
        (),
        tuple(dependencies),
        tuple(prior),
        {"watermark_ns": cursor},
    )


def bucket_rows(writer, manifest):
    sql, params = committed_rows_select(
        BUCKET_TOTALS,
        manifest,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
    )
    return writer.store.execute(
        f"SELECT bucket_start, group_key, unit_count, weighted_n, weighted_d, is_deleted FROM ({sql}) ORDER BY bucket_start, group_key",
        params,
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("level", ["conversation", "user"])
def test_real_source_refresh_fields_and_user_metrics_have_zero_buckets(
    live_writer, workspace, observe_project, level
):
    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    definition = definition_for(writer, workspace, observe_project, level=level)
    session, user, trace = uuid4(), uuid4(), uuid4()
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
    seed_span(
        writer.store.client,
        scope,
        "span-a",
        session,
        user,
        at,
        resolved=4,
        trace_id=trace,
    )
    dependency = Dependency(
        str(trace), "span-a", str(session), str(session), str(user), str(user), at
    )
    head = writer.refresh(
        change_for(session, dependencies=[dependency]),
        [definition],
        scope,
        start=at - timedelta(days=1),
        end=at + timedelta(days=1),
        as_of=at + timedelta(hours=1),
    )
    values = current_fields(writer, head)
    assert len(values) == 1 and values[0][1] == 4
    if level == "user":
        assert bucket_rows(writer, head) == []
        assert (
            writer.store.execute("SELECT count() FROM metric_bucket_totals")[0][0] == 0
        )
    else:
        buckets = bucket_rows(writer, head)
        assert len(buckets) == 1 and buckets[0][2:5] == (1, 4.0, 1.0)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("configured", [None, 1, 4])
def test_a_head_compacts_only_once_it_carries_the_configured_deltas(
    live_writer, workspace, observe_project, settings, configured
):
    from tracer.models.derived_metric import MAX_MANIFEST_DELTAS
    from tracer.services.derived_metrics.refresh import (
        DEFAULT_DELTAS_BEFORE_COMPACTION,
    )

    if configured is None:
        if hasattr(settings, "METRICS_MAX_DELTAS_BEFORE_COMPACTION"):
            del settings.METRICS_MAX_DELTAS_BEFORE_COMPACTION
        threshold = DEFAULT_DELTAS_BEFORE_COMPACTION
    else:
        settings.METRICS_MAX_DELTAS_BEFORE_COMPACTION = configured
        threshold = configured
    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    definition = definition_for(writer, workspace, observe_project)
    session, user, trace = uuid4(), uuid4(), uuid4()
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=2)
    seed_span(
        writer.store.client,
        scope,
        "span-a",
        session,
        user,
        at,
        resolved=4,
        trace_id=trace,
    )
    dependency = Dependency(
        str(trace), "span-a", str(session), str(session), str(user), str(user), at
    )
    compactions = []
    compact = writer.compact

    def counted(*args, **kwargs):
        compactions.append(
            len(
                MetricProjectRefresh.for_project(
                    observe_project.id
                ).head_manifest.delta_attempt_ids
            )
        )
        return compact(*args, **kwargs)

    writer.compact = counted
    chain, expected, carried = [], [], 0
    for cursor in range(1, threshold + 3):
        head = writer.refresh(
            change_for(session, cursor=cursor, dependencies=[dependency]),
            [definition],
            scope,
            start=at - timedelta(days=1),
            end=at + timedelta(days=1),
            as_of=at + timedelta(hours=1),
        )
        chain.append(len(head.delta_attempt_ids))
        carried = (0 if carried >= threshold else carried) + 1
        expected.append(carried)
        values = current_fields(writer, head)
        assert len(values) == 1 and values[0][1] == 4
    # Each pass compacts only once the head holds the threshold, before its own
    # attempt begins, so no head ever carries more than the threshold.
    assert chain == expected, chain
    assert max(chain) == threshold <= MAX_MANIFEST_DELTAS - 1
    assert compactions == [threshold] * (2 if threshold == 1 else 1), compactions
    assert head.checkpoint_attempt_id is not None


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "old_start,new_start",
    [
        (
            datetime(2026, 1, 31, 23, 59, tzinfo=UTC),
            datetime(2026, 1, 31, 23, 44, tzinfo=UTC),
        ),
        (
            datetime(2026, 2, 1, 0, 1, tzinfo=UTC),
            datetime(2026, 1, 31, 23, 59, tzinfo=UTC),
        ),
    ],
)
@pytest.mark.parametrize("crash_point", ["after_fields", "mid_buckets"])
def test_source_and_field_merges_then_crash_bucket_move(
    live_writer, workspace, observe_project, old_start, new_start, crash_point
):
    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    definition = definition_for(writer, workspace, observe_project)
    session, user, trace = uuid4(), uuid4(), uuid4()
    seed_span(
        writer.store.client,
        scope,
        "old-span",
        session,
        user,
        old_start,
        resolved=4,
        trace_id=trace,
    )
    before = Dependency(
        str(trace),
        "old-span",
        str(session),
        str(session),
        str(user),
        str(user),
        old_start,
    )
    writer.refresh(
        change_for(session, dependencies=[before]),
        [definition],
        scope,
        start=new_start - timedelta(days=1),
        end=old_start + timedelta(days=1),
        as_of=old_start + timedelta(hours=2),
    )
    seed_span(
        writer.store.client,
        scope,
        "earlier-span",
        session,
        user,
        new_start,
        resolved=8,
        trace_id=trace,
    )
    newer = Dependency(
        str(trace),
        "earlier-span",
        str(session),
        str(session),
        str(user),
        str(user),
        new_start,
    )
    writer.store.client.execute("OPTIMIZE TABLE spans FINAL")
    writer.store.client.execute("OPTIMIZE TABLE metric_field_values FINAL")
    original_insert = writer.store.insert
    failed, bucket_writes = [], []

    def crash(table, payload):
        kill_at = 0 if crash_point == "after_fields" else 1
        # Folds now batch multiple quarters. Inject at the same logical bucket
        # boundary, including a partial batch, rather than counting INSERT calls.
        payloads = (
            [FrozenRows((row,), row_limit=payload.row_limit) for row in payload.rows]
            if table.name == BUCKET_TOTALS.name
            else [payload]
        )
        for piece in payloads:
            if (
                table.name == BUCKET_TOTALS.name
                and len(bucket_writes) == kill_at
                and not failed
            ):
                failed.append(True)
                raise RuntimeError("injected refresh crash")
            original_insert(table, piece)
            if table.name == BUCKET_TOTALS.name:
                bucket_writes.append(True)
            pinned = MetricProjectRefresh.objects.get(
                project_id=observe_project.id
            ).head_manifest
            # Check the committed request snapshot THROUGHOUT staging, not only
            # at completion: staged fields and either bucket are still hidden.
            assert current_fields(writer, pinned)[0][1] == 4.0
            assert len([r for r in bucket_rows(writer, pinned) if not r[-1]]) == 1

    writer.store.insert = crash
    with pytest.raises(RuntimeError):
        writer.refresh(
            change_for(session, cursor=2, dependencies=[newer], prior=[before]),
            [definition],
            scope,
            start=new_start - timedelta(days=1),
            end=old_start + timedelta(days=1),
            as_of=old_start + timedelta(hours=2),
        )
    assert failed == [True]
    assert len(bucket_writes) == (0 if crash_point == "after_fields" else 1)
    current = MetricProjectRefresh.objects.get(project_id=observe_project.id)
    assert current.watermark_ns == 1
    assert [r for r in bucket_rows(writer, current.head_manifest) if not r[-1]][0][
        0
    ] == old_start.replace(minute=old_start.minute // 15 * 15, second=0)
    attempt = observe_project.metric_refresh_attempts.get(state="staging")
    # Source can disappear/merge entirely after the durable payload was frozen.
    writer.store.client.execute("TRUNCATE TABLE spans")
    writer.store.insert = original_insert
    h2 = writer.stage_and_publish(attempt)
    rows = bucket_rows(writer, h2)
    live = [r for r in rows if not r[-1]]
    gone = [r for r in rows if r[-1]]
    assert len(live) == 1 and len(gone) == 1
    assert live[0][0] == new_start.replace(minute=new_start.minute // 15 * 15, second=0)
    assert gone[0][0] == old_start.replace(minute=old_start.minute // 15 * 15, second=0)
    assert live[0][2] == 1


@pytest.mark.django_db(transaction=True)
def test_205_independent_randomized_committed_read_sequences(live_writer):
    """Independent two-state histories, with reads both before and after stage.

    This is a field/publication oracle, NOT a claim of the entire P1-P6 product
    probe matrix; source discovery/sampling have their own real-CH suites.
    """
    writer, rng = live_writer, Random(71209)
    for _ in range(205):
        unit_id, metric = str(uuid4()), uuid4()
        n1, n2 = rng.randint(-100, 100), rng.randint(-100, 100)
        first, _ = prepare_field(writer, metric=metric, unit_id=unit_id, n=n1)
        h1 = writer.stage_and_publish(first)
        deleted = rng.randint(0, 1)
        second, frozen = prepare_field(
            writer, metric=metric, unit_id=unit_id, n=n2, deleted=deleted
        )
        writer.store.insert(
            FIELD_VALUES, frozen[0]
        )  # speculative stage still invisible
        old = {r[0]: r[1:3] for r in current_fields(writer, h1)}
        assert old[unit_id] == (float(n1), 0)
        h2 = writer.stage_and_publish(second)
        newest = {r[0]: r[1:3] for r in current_fields(writer, h2)}
        assert newest[unit_id] == (float(n2), deleted)
        assert {r[0]: r[1:3] for r in current_fields(writer, h1)}[unit_id] == (
            float(n1),
            0,
        )
        # Keep the randomized run bounded physically and in PG without claiming a
        # scale benchmark. This uses the real checkpoint protocol, not raw FINAL.
        if len(h2.delta_attempt_ids) >= 10:
            writer.compact()


@pytest.mark.django_db(transaction=True)
def test_discovery_maximum_dependencies_freezes_bounded_batches_before_staging(
    live_writer,
):
    writer = live_writer
    at = datetime.now(UTC).replace(microsecond=123456)
    dependencies = [
        Dependency(
            "trace",
            f"span-{i:05d}",
            "session",
            "session",
            "user",
            "user",
            at,
            is_deleted=bool(i % 2),
        )
        for i in range(10_000)
    ]
    attempt = writer.begin()
    writer.freeze_dependencies(attempt, dependencies)
    plans = list(writer.plans(attempt, kind="source_dependencies"))
    assert len(plans) == 20 and all(len(p["rows"]) == 500 for p in plans)
    assert not attempt.batches.exists()
    assert writer.store.execute("SELECT count() FROM metric_source_dependencies") == [
        (0,)
    ]
    writer.seal(attempt, cursors={"watermark_ns": 42})
    head = writer.stage_and_publish(attempt)
    sql, params = committed_rows_select(
        SOURCE_DEPENDENCIES,
        head,
        org_id=writer.org_id,
        project_id=writer.lease.project_id,
    )
    assert writer.store.execute(
        f"SELECT count(), uniqExact(span_id), countIf(is_deleted), min(unit_start), max(unit_start) FROM ({sql})",
        params,
    ) == [(10_000, 10_000, 5_000, at, at)]
    assert (
        MetricProjectRefresh.objects.get(
            project_id=writer.lease.project_id
        ).watermark_ns
        == 42
    )


@pytest.mark.django_db(transaction=True)
def test_settle_reopen_deleted_arrival_floor_and_final_delete(
    live_writer, workspace, observe_project
):
    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    definition = definition_for(writer, workspace, observe_project)
    session, user, trace = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC).replace(microsecond=0)
    at = now - timedelta(hours=2)
    seed_span(
        writer.store.client,
        scope,
        "stable",
        session,
        user,
        at,
        resolved=4,
        trace_id=trace,
    )
    h1 = writer.refresh(
        change_for(session),
        [definition],
        scope,
        start=at - timedelta(days=1),
        end=now + timedelta(days=1),
        as_of=now,
    )
    assert sum(r[2] for r in bucket_rows(writer, h1) if not r[-1]) == 1
    seed_span(
        writer.store.client,
        scope,
        "latest",
        session,
        user,
        now,
        resolved=8,
        trace_id=trace,
    )
    h2 = writer.refresh(
        change_for(session, cursor=2),
        [definition],
        scope,
        start=at - timedelta(days=1),
        end=now + timedelta(days=1),
        as_of=now + timedelta(minutes=1),
    )
    assert current_fields(writer, h2)[0][1] is None
    assert all(r[-1] for r in bucket_rows(writer, h2))
    # Losing the live latest span cannot lower the committed arrival floor.
    writer.store.execute(
        "ALTER TABLE spans DELETE WHERE id = 'latest' SETTINGS mutations_sync=2"
    )
    h3 = writer.refresh(
        change_for(session, cursor=3),
        [definition],
        scope,
        start=at - timedelta(days=1),
        end=now + timedelta(days=1),
        as_of=now + timedelta(minutes=5),
    )
    assert current_fields(writer, h3)[0][1] is None
    assert all(r[-1] for r in bucket_rows(writer, h3))
    h4 = writer.refresh(
        change_for(session, cursor=4),
        [definition],
        scope,
        start=at - timedelta(days=1),
        end=now + timedelta(days=1),
        as_of=now + timedelta(minutes=31),
    )
    assert current_fields(writer, h4)[0][1] == 4.0
    assert sum(r[2] for r in bucket_rows(writer, h4) if not r[-1]) == 1
    writer.store.execute("TRUNCATE TABLE spans")
    h5 = writer.refresh(
        change_for(session, cursor=5),
        [definition],
        scope,
        start=at - timedelta(days=1),
        end=now + timedelta(days=1),
        as_of=now + timedelta(minutes=32),
    )
    assert current_fields(writer, h5)[0][2] == 1
    assert all(r[-1] for r in bucket_rows(writer, h5))


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("crash_point", [0, 1])
def test_direct_span_totals_dedup_moves_deletes_and_atomic_retry(
    live_writer, workspace, observe_project, mixed, crash_point
):
    writer = live_writer
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    span = span_definition_for(writer, workspace, observe_project)
    definitions = [span]
    if mixed:
        definitions.append(
            DerivedMetricVersion.objects.get(metric=span.metric, version=1)
        )
    session, trace = uuid4(), uuid4()
    at = datetime.now(UTC).replace(minute=7, second=0, microsecond=0) - timedelta(
        hours=3
    )
    earlier = at - timedelta(minutes=30)
    for name, value in [("moving", 4), ("stable", 6)]:
        seed_span_revision(writer, scope, trace, name, session, at, value, revision=1)
    before = Dependency(str(trace), "moving", str(session), str(session), "", "", at)
    stable = Dependency(str(trace), "stable", str(session), str(session), "", "", at)
    kwargs = {
        "start": earlier - timedelta(days=1),
        "end": at + timedelta(days=1),
        "as_of": at + timedelta(hours=1),
    }
    head = writer.refresh(
        change_for(session, dependencies=[before, stable]), definitions, scope, **kwargs
    )
    initial = metric_bucket_rows(writer, head, span)
    assert len(initial) == 1 and initial[0][2:] == (2, 10.0, 2.0)
    seed_span_revision(writer, scope, trace, "moving", session, earlier, 8, revision=2)
    after = Dependency(
        str(trace), "moving", str(session), str(session), "", "", earlier
    )
    writer.store.execute("OPTIMIZE TABLE spans FINAL")
    original = writer.store.insert
    writes, failed = [], []

    def crash(table, payload):
        # Crash after the same number of logical bucket writes even when both
        # quarters share a frozen batch; keep the in-staging visibility oracle.
        payloads = (
            [FrozenRows((row,), row_limit=payload.row_limit) for row in payload.rows]
            if table.name == BUCKET_TOTALS.name
            else [payload]
        )
        for piece in payloads:
            if (
                table.name == BUCKET_TOTALS.name
                and len(writes) == crash_point
                and not failed
            ):
                failed.append(True)
                raise RuntimeError("injected direct span publication failure")
            original(table, piece)
            if table.name == BUCKET_TOTALS.name:
                writes.append(True)
            pinned = MetricProjectRefresh.objects.get(
                project_id=observe_project.id
            ).head_manifest
            assert metric_bucket_rows(writer, pinned, span) == initial

    writer.store.insert = crash
    with pytest.raises(RuntimeError, match="direct span publication"):
        writer.refresh(
            change_for(session, cursor=2, dependencies=[after], prior=[before]),
            definitions,
            scope,
            **kwargs,
        )
    assert failed == [True] and len(writes) == crash_point
    writer.store.insert = original
    assert (
        MetricProjectRefresh.objects.get(project_id=observe_project.id).watermark_ns
        == 1
    )
    attempt = observe_project.metric_refresh_attempts.get(state="staging")
    # Retry does not re-read mutable telemetry, even for direct span totals.
    writer.store.execute("TRUNCATE TABLE spans")
    head = writer.stage_and_publish(attempt)
    live = metric_bucket_rows(writer, head, span)
    assert (
        len(live) == 2
        and sum(r[2] for r in live) == 2
        and sum(r[3] for r in live) == 14
    )
    seed_span_revision(
        writer, scope, trace, "moving", session, earlier, 8, revision=3, deleted=1
    )
    seed_span_revision(writer, scope, trace, "stable", session, at, 6, revision=3)
    deleted = Dependency(
        str(trace),
        "moving",
        str(session),
        str(session),
        "",
        "",
        earlier,
        is_deleted=True,
    )
    head = writer.refresh(
        change_for(session, cursor=3, dependencies=[deleted], prior=[after]),
        definitions,
        scope,
        **kwargs,
    )
    live = metric_bucket_rows(writer, head, span)
    assert len(live) == 1 and live[0][2:] == (1, 6.0, 1.0)
    evidence, params = committed_rows_select(
        FIELD_VALUES,
        head,
        org_id=writer.org_id,
        project_id=observe_project.id,
        key_in={
            "field_id": (span.metric_id,),
            "field_version": (span.version,),
            "level": ("span",),
        },
    )
    assert writer.store.execute(
        f"SELECT countIf(is_deleted = 0), sumIf(value_number, is_deleted = 0) FROM ({evidence})",
        params,
    ) == [(1, 6.0)]
    assert writer.store.execute(
        f"SELECT countIf(is_deleted = 1 AND value_number IS NULL) FROM ({evidence})",
        params,
    ) == [(1,)]
    # Span readiness depends on end_time, not the conversation settle timer.
    assert writer.store.execute(
        "SELECT count() FROM metric_unit_state WHERE level = 'span'"
    ) == [(0,)]


@pytest.mark.django_db(transaction=True)
def test_direct_span_settling_and_partial_bucket_retirement(
    live_writer, workspace, observe_project
):
    from tracer.services.derived_metrics.retention import expire_project

    writer = live_writer
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    definition = span_definition_for(writer, workspace, observe_project)
    wall = datetime.now(UTC).replace(second=0, microsecond=0)
    now = wall.replace(minute=wall.minute // 15 * 15 + 7)
    if now > wall:
        now -= timedelta(minutes=15)
    writer.lease.clock = lambda: now
    cutoff = now - timedelta(days=1)
    session, trace = uuid4(), uuid4()
    times = {
        "old": cutoff - timedelta(minutes=2),
        "live": cutoff + timedelta(minutes=2),
        "open": cutoff + timedelta(minutes=3),
    }
    dependencies = []
    for name, at in times.items():
        seed_span_revision(
            writer,
            scope,
            trace,
            name,
            session,
            at,
            4 if name == "old" else 6,
            revision=1,
            ended=name != "open",
        )
        dependencies.append(
            Dependency(str(trace), name, str(session), str(session), "", "", at)
        )
    kwargs = {
        "start": cutoff - timedelta(days=1),
        "end": now + timedelta(days=1),
        "as_of": now,
    }
    head = writer.refresh(
        change_for(session, dependencies=dependencies), [definition], scope, **kwargs
    )
    assert sum(r[2] for r in metric_bucket_rows(writer, head, definition)) == 2
    expire_project(writer, [definition], retention_days=lambda: 1, now=now)
    writer.retention_days = lambda: 1
    writer.store.execute(
        "ALTER TABLE spans DELETE WHERE id = 'old' SETTINGS mutations_sync=2"
    )
    head = writer.refresh(
        change_for(
            session, cursor=2, dependencies=dependencies[1:], prior=dependencies
        ),
        [definition],
        scope,
        **kwargs,
    )
    rows = metric_bucket_rows(writer, head, definition)
    assert len(rows) == 1 and rows[0][2:] == (2, 10.0, 2.0)
    seed_span_revision(
        writer, scope, trace, "open", session, times["open"], 6, revision=2
    )
    head = writer.refresh(
        change_for(session, cursor=3, dependencies=dependencies[2:]),
        [definition],
        scope,
        **kwargs,
    )
    rows = metric_bucket_rows(writer, head, definition)
    assert len(rows) == 1 and rows[0][2:] == (3, 16.0, 3.0)
    assert writer.store.execute(
        "SELECT count() FROM metric_source_dependencies WHERE canonical_session_id != ''"
    ) == [(0,)]


@pytest.mark.django_db(transaction=True)
def test_batched_whole_writer_histories_with_independent_source_oracle(
    live_writer, workspace, observe_project
):
    """205 independent unit histories advance together through the real writer.

    The oracle knows only generated source events, not compiler SQL/field plans.
    It independently reduces live sources, sampling weights, top-50/Other and
    sketch populations, and checks committed reads throughout both crash points.
    """
    import json
    from collections import defaultdict
    from uuid import UUID

    from tracer.services.derived_metrics.storage import RETIRED_PARTS

    writer, rng = live_writer, Random(707205)
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    wall = datetime.now(UTC).replace(second=0, microsecond=0)
    now = wall.replace(minute=wall.minute // 15 * 15 + 7)
    if now > wall:
        now -= timedelta(minutes=15)
    writer.lease.clock = lambda: now
    base = definition_for(writer, workspace, observe_project)
    config = UUID(int=rng.getrandbits(128))
    definitions = []
    for index, kind in enumerate(("average", "unique_count", "percentile"), start=2):
        definitions.append(
            DerivedMetricVersion.objects.create(
                metric=base.metric,
                version=index,
                **{
                    **base.semantics(),
                    "kind": kind,
                    "weighting": "inverse_probability" if kind == "average" else "none",
                    "inputs": [
                        {
                            "alias": "resolved",
                            "source": "eval",
                            "config_id": str(config),
                            "config_hash": "a" * 64,
                            "reducer": "latest",
                        }
                    ],
                },
            )
        )
    records, requested, historical_starts = [], set(), {}
    for index in range(205):
        session = UUID(int=rng.getrandbits(128))
        records.append(
            {
                "session": session,
                "raw_session": session,
                "trace": UUID(int=rng.getrandbits(128)),
                "span": f"oracle-{index}",
                "eval_id": UUID(int=rng.getrandbits(128)),
                "at": now - timedelta(days=1, minutes=rng.randint(-4, 4)),
                "value": rng.randint(-10, 30),
                "p": rng.choice((0.25, 0.5, 1.0)),
                "group": f"g-{index % 61:02d}",
                "scope": "11111111-1111-1111-1111-111111111111",
                "stratum": "s-0",
                "deleted": False,
            }
        )
        requested.add(str(session))
    kwargs = {"start": now - timedelta(days=2), "end": now + timedelta(hours=1)}
    previous_expected, previous_buckets = None, None

    def source_events(revision, *, reopen=False):
        deps, spans, evals = [], [], []
        for record in records:
            at = record["at"]
            spans.append(
                (
                    record["span"],
                    str(record["trace"]),
                    scope.project,
                    scope.organization,
                    record["raw_session"],
                    at,
                    at + timedelta(seconds=1),
                    at,
                    now if reopen else at,
                    revision,
                    int(record["deleted"]),
                    {"resolved": 1.0},
                    {"region": record["group"]},
                )
            )
            sampling = {
                "inclusion_probability": record["p"],
                "sampling_unit": "session",
                "sampling_unit_id": "session:" + str(record["session"]),
                "cohort_id": record["scope"],
                "policy_version": "c" * 64,
                "stratum": record["stratum"],
                "membership_mode": "live",
                "cohort_frozen_at": None,
                "population_size": None,
                "window": {"start": (now - timedelta(days=3)).isoformat(), "end": None},
            }
            when = now - timedelta(minutes=40)
            evals.append(
                (
                    record["eval_id"],
                    config,
                    "a" * 64,
                    None,
                    None,
                    record["session"],
                    "session",
                    when,
                    when,
                    revision,
                    0,
                    float(record["value"]),
                    json.dumps({"_task_selection": {"sampling": sampling}}),
                    "completed",
                    0,
                )
            )
            deps.append(
                Dependency(
                    str(record["trace"]),
                    record["span"],
                    str(record["raw_session"]),
                    str(record["session"]),
                    "",
                    "",
                    record["at"],
                    is_deleted=record["deleted"],
                )
            )
        # Batch transport only; every history still has its own immutable
        # source/eval identities and independent generated state transitions.
        writer.store.client.execute(
            "INSERT INTO spans (id, trace_id, project_id, org_id, trace_session_id, start_time, end_time, created_at, updated_at, _version, is_deleted, attrs_number, attrs_string) VALUES",
            spans,
        )
        writer.store.client.execute(
            "INSERT INTO tracer_eval_logger_v2 (id, custom_eval_config_id, config_hash, trace_id, observation_span_id, trace_session_id, target_type, created_at, updated_at, _version, is_deleted, output_float, output_metadata, status, error) VALUES",
            evals,
        )
        return deps

    def oracle(opened, *, retained=False):
        live = {str(r["session"]): r for r in records if not r["deleted"]}
        expected, raw_groups = {}, defaultdict(list)
        for unit in sorted(requested):
            record = live.get(unit)
            if record:
                historical_starts[unit] = record["at"]
            for definition in definitions:
                anchor = record["at"] if record else historical_starts.get(unit)
                if retained and (anchor is None or anchor < now - timedelta(days=1)):
                    continue
                expected[(str(definition.metric_id), definition.version, unit)] = (
                    None if opened or not record else float(record["value"]),
                    "no_value" if not record else "open" if opened else "ok",
                    int(not record),
                    historical_starts.get(unit),
                )
        for record in live.values():
            if opened:
                continue
            bucket = record["at"].replace(
                minute=record["at"].minute // 15 * 15, second=0, microsecond=0
            )
            group = json.dumps(["value", record["group"]], separators=(",", ":"))
            for definition in definitions:
                weighted = definition.weighting == "inverse_probability"
                key = (
                    str(definition.metric_id),
                    definition.version,
                    bucket,
                    group,
                    record["scope"] if weighted else "",
                    record["stratum"] if weighted else "",
                )
                raw_groups[key].append(
                    (record["value"], record["p"] if weighted else 1.0)
                )
        populations = defaultdict(lambda: defaultdict(int))
        for key, values in raw_groups.items():
            populations[key[:3]][key[3]] += len(values)
        winners = {
            key: set(sorted(groups, key=lambda g: (-groups[g], g))[:50])
            for key, groups in populations.items()
        }
        folded = defaultdict(list)
        for key, values in raw_groups.items():
            folded[
                (
                    *key[:3],
                    key[3] if key[3] in winners[key[:3]] else '["other"]',
                    *key[4:],
                )
            ].extend(values)
        buckets = {}
        by_version = {d.version: d for d in definitions}
        for key, pairs in folded.items():
            definition = by_version[key[1]]
            values = [value for value, _ in pairs]
            numerator = sum(
                (1 if definition.kind == "unique_count" else value) / p
                for value, p in pairs
            )
            buckets[key] = (
                len(pairs),
                numerator,
                sum(1 / p for _, p in pairs),
                # A sketch metric keeps only ITS sketch; the other physical
                # column is deliberately empty (distinct count 0, no quantile).
                0 if definition.kind == "percentile" else len(set(values)),
                # Small t-digest populations use the lower observed .5 rank,
                # not the interpolated arithmetic median of an even population.
                None
                if definition.kind == "unique_count"
                else sorted(values)[(len(values) - 1) // 2],
            )
        return expected, buckets

    def assert_manifest(head, expected, expected_buckets):
        fields, params = committed_rows_select(
            FIELD_VALUES, head, org_id=writer.org_id, project_id=scope.project
        )
        rows = writer.store.execute(
            f"SELECT toString(field_id), field_version, unit_id, value_number, status, is_deleted, unit_start FROM ({fields})",
            params,
        )
        actual = {(r[0], r[1], r[2]): r[3:] for r in rows}
        assert set(actual) == set(expected)
        for key, value in expected.items():
            # Absent brand-new candidates have the compiler's epoch tombstone;
            # all previously live units must preserve their real old anchor.
            if actual[key][:3] != value[:3]:
                detail = writer.store.execute(
                    f"SELECT no_value_reason, inputs, sample_probability, sampling_scope, sampling_stratum FROM ({fields}) "
                    "WHERE field_version = %(debug_version)s AND unit_id = %(debug_unit)s",
                    {**params, "debug_version": key[1], "debug_unit": key[2]},
                )
                pytest.fail(
                    f"oracle mismatch at step {step}, {key}: {actual[key]} != {value}; evidence={detail}"
                )
            if value[3] is not None:
                assert actual[key][3] == value[3]
        buckets, bp = committed_rows_select(
            BUCKET_TOTALS, head, org_id=writer.org_id, project_id=scope.project
        )
        rows = writer.store.execute(
            f"SELECT toString(metric_id), metric_version, bucket_start, group_key, splitByChar(':', scope_key)[1], stratum_key, "
            f"unit_count, weighted_n, weighted_d, finalizeAggregation(uniq_state), finalizeAggregation(quantile_state) FROM ({buckets}) WHERE is_deleted = 0",
            bp,
        )
        actual = {tuple(r[:6]): r[6:] for r in rows}
        assert set(actual) == set(expected_buckets)
        for key, values in expected_buckets.items():
            assert actual[key][:4] == pytest.approx(values[:4])
            if values[4] is None:
                assert math.isnan(actual[key][4]), (key, actual[key])
            else:
                assert actual[key][4] == pytest.approx(values[4], abs=0.05)

    prior = ()
    for step in range(4):
        if step == 1:
            for index, record in enumerate(records):
                record["value"] = rng.randint(-20, 40)
                record["p"] = rng.choice((0.25, 0.5, 1.0))
                record["group"] = f"g-{rng.randrange(61):02d}"
                record["scope"] = (
                    "22222222-2222-2222-2222-222222222222"
                    if index % 3 == 0
                    else record["scope"]
                )
                record["stratum"] = f"s-{rng.randrange(3)}"
                record["deleted"] = rng.random() < 0.17
                if rng.random() < 0.3:
                    destination = UUID(int=rng.getrandbits(128))
                    record["raw_session"] = destination
                    if index % 2:
                        record["session"] = destination
                        requested.add(str(destination))
                    else:
                        # Real remap: the old alias remains the canonical
                        # survivor while the physical source moves to its target.
                        writer.store.execute(
                            "INSERT INTO trace_session_id_remap (old_id, new_id) VALUES (%(old)s, %(new)s)",
                            {"old": str(record["session"]), "new": str(destination)},
                        )
                record["at"] += timedelta(minutes=rng.choice((-30, 0, 30)))
        if step < 3:
            deps = source_events(step + 1, reopen=step == 2)
        if step == 1:
            writer.store.execute("OPTIMIZE TABLE spans FINAL")
            writer.store.execute("OPTIMIZE TABLE metric_field_values FINAL")
        expected, buckets = oracle(step == 2)
        change = ChangeBatch(
            tuple(SourceKey(str(r["trace"]), r["span"]) for r in records),
            tuple(UnitKey("conversation", unit) for unit in sorted(requested)),
            (),
            tuple(deps),
            tuple(prior),
            {"watermark_ns": step + 1},
        )
        original = writer.store.insert
        failed, bucket_writes = [], []
        if step in {1, 2}:

            def crash(
                table,
                payload,
                *,
                bucket_writes=bucket_writes,
                step=step,
                failed=failed,
                original=original,
                previous_expected=previous_expected,
                previous_buckets=previous_buckets,
            ):
                if (
                    table.name == BUCKET_TOTALS.name
                    and len(bucket_writes) == step - 1
                    and not failed
                ):
                    failed.append(True)
                    raise RuntimeError("injected randomized whole-writer crash")
                original(table, payload)
                if table.name == BUCKET_TOTALS.name:
                    bucket_writes.append(True)
                pinned = MetricProjectRefresh.objects.get(
                    project_id=scope.project
                ).head_manifest
                assert_manifest(pinned, previous_expected, previous_buckets)

            writer.store.insert = crash
            with pytest.raises(RuntimeError, match="randomized whole-writer"):
                writer.refresh(
                    change,
                    definitions,
                    scope,
                    as_of=now + timedelta(minutes=step),
                    **kwargs,
                )
            writer.store.insert = original
            pending = observe_project.metric_refresh_attempts.get(state="staging")
            head = writer.stage_and_publish(pending)
        else:
            head = writer.refresh(
                change,
                definitions,
                scope,
                as_of=now + timedelta(minutes=31 if step == 3 else 0),
                **kwargs,
            )
        assert_manifest(head, expected, buckets)
        previous_expected, previous_buckets, prior = expected, buckets, tuple(deps)
    # Retire a cutoff that splits buckets. Anonymous pre-fold groups must refold
    # to exactly the same complete total, even with mixed sampling scopes.
    writer.retention_days = lambda: 1
    expected, buckets = oracle(False, retained=True)
    head = writer.refresh(
        change, definitions, scope, as_of=now + timedelta(minutes=31), **kwargs
    )
    assert_manifest(head, expected, buckets)
    retired, rp = committed_rows_select(
        RETIRED_PARTS, head, org_id=writer.org_id, project_id=scope.project
    )
    assert (
        writer.store.execute(
            f"SELECT sum(unit_count) FROM ({retired}) WHERE is_deleted = 0", rp
        )[0][0]
        > 0
    )
    assert len(records) == 205


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("seed", range(205))
def test_205_randomized_whole_writer_histories_with_independent_source_oracle(
    live_writer, workspace, observe_project, seed
):
    """Each seed has RESET PG/CH state, actual discovery and a source-only oracle.

    This independent core matrix complements the multi-unit sampled/sketch/group
    matrix above. Neither is credited with the separately held admission gaps or
    with the entire R08 coverage contract (see the build handoff).
    """
    from dataclasses import replace

    from tracer.models.derived_metric import MetricRefreshAttempt
    from tracer.services.derived_metrics.changes import discover_changes, ns
    from tracer.services.derived_metrics.settle import UnitSnapshot, settle_due_page
    from tracer.services.derived_metrics.storage import UNIT_STATE
    from tracer.services.derived_metrics.unit_reads import unit_values_select
    from tracer.tests.test_derived_metric_changes_clickhouse import Analytics

    writer, rng = live_writer, Random(707000 + seed)
    scope = Scope(writer.org_id, writer.workspace_id, writer.lease.project_id)
    now = datetime.now(UTC).replace(microsecond=0)
    writer.lease.clock = lambda: now
    base = definition_for(writer, workspace, observe_project)
    definition = DerivedMetricVersion.objects.create(
        metric=base.metric,
        version=2,
        **{
            **base.semantics(),
            "inputs": [
                {**base.inputs[0], "value_type": "number", "ordering": "sentence"}
            ],
        },
    )
    sessions = [UUID(int=rng.getrandbits(128)) for _ in range(rng.randint(1, 3))]
    records = []
    for session in sessions:
        for _index in range(rng.randint(1, 4)):
            at = now - timedelta(hours=2, minutes=rng.randrange(60))
            records.append(
                {
                    "session": session,
                    "trace": UUID(int=rng.getrandbits(128)),
                    "span": f"source-{len(records)}",
                    "at": at,
                    "arrival": at,
                    "value": rng.randint(-10, 20),
                    "deleted": False,
                }
            )
    old_anchors, known = {}, set()
    analytics = Analytics(writer.store.client)
    coverage = dict.fromkeys(
        (
            "source_update",
            "source_delete",
            "owner_move",
            "reopen",
            "due_settle",
            "revision_collapse",
            "staging_boundary",
        ),
        0,
    )

    def insert(record, revision):
        seed_span_revision(
            writer,
            scope,
            record["trace"],
            record["span"],
            record["session"],
            record["at"],
            record["value"],
            revision=revision,
            deleted=int(record["deleted"]),
            arrival=record["arrival"],
        )

    def oracle(as_of):
        # No production compiler/bucket builder is used for expected values.
        result = {}
        for unit in sorted(known):
            members = [
                r for r in records if str(r["session"]) == unit and not r["deleted"]
            ]
            if not members:
                result[unit] = (None, "no_value", 1, old_anchors[unit])
                continue
            anchor = min(r["at"] for r in members)
            old_anchors[unit] = anchor
            opened = max(r["arrival"] for r in members) + timedelta(minutes=30) > as_of
            latest = max(members, key=lambda r: (r["at"], str(r["trace"]), r["span"]))
            result[unit] = (
                None if opened else float(latest["value"]),
                "open" if opened else "ok",
                0,
                anchor,
            )
        return result

    def assert_state(manifest, expected, *, sources=None):
        fields, params = committed_rows_select(
            FIELD_VALUES, manifest, org_id=writer.org_id, project_id=scope.project
        )
        rows = writer.store.execute(
            f"SELECT unit_id, value_number, status, is_deleted, unit_start FROM ({fields})",
            params,
        )
        assert {r[0]: r[1:] for r in rows} == expected
        expected_buckets = {}
        for value, status, deleted, anchor in expected.values():
            if status != "ok" or deleted:
                continue
            bucket = anchor.replace(
                minute=anchor.minute // 15 * 15, second=0, microsecond=0
            )
            count, total = expected_buckets.get(bucket, (0, 0.0))
            expected_buckets[bucket] = count + 1, total + value
        rows = bucket_rows(writer, manifest)
        actual = {r[0]: (r[2], r[3], r[4]) for r in rows if not r[-1]}
        assert actual == {
            bucket: (count, total, float(count))
            for bucket, (count, total) in expected_buckets.items()
        }
        state, sp = committed_rows_select(
            UNIT_STATE, manifest, org_id=writer.org_id, project_id=scope.project
        )
        queued = {
            r[0]: r[1:]
            for r in writer.store.execute(
                f"SELECT unit_id, due_at, is_deleted FROM ({state})", sp
            )
        }
        for unit, (_, status, deleted, _) in expected.items():
            assert queued[unit][1] == int(deleted or status != "open")
        deps, dp = committed_rows_select(
            SOURCE_DEPENDENCIES,
            manifest,
            org_id=writer.org_id,
            project_id=scope.project,
        )
        physical = writer.store.execute(
            f"SELECT trace_id, span_id, canonical_session_id, unit_start, is_deleted FROM ({deps})",
            dp,
        )
        assert {(r[0], r[1]): r[2:] for r in physical} == {
            (str(r["trace"]), r["span"]): (
                str(r["session"]),
                r["at"],
                int(r["deleted"]),
            )
            for r in (sources if sources is not None else records)
        }

    def discover(as_of):
        current = MetricProjectRefresh.for_project(scope.project)
        allowed = (
            [str(a) for _, a in current.head_manifest.allowed_attempts()]
            if current.head_manifest_id
            else []
        )
        return discover_changes(
            analytics,
            org_id=str(scope.organization),
            project_id=str(scope.project),
            committed_attempts=allowed,
            watermark_ns=current.watermark_ns,
            eval_watermark_ns=current.eval_watermark_ns,
            remap_cursor_ns=current.remap_cursor_ns,
            ceiling=as_of,
        )

    for record in records:
        insert(record, ns(record["arrival"]))
        known.add(str(record["session"]))
    start, end = now - timedelta(days=2), now + timedelta(days=1)
    head = writer.refresh(
        discover(now), [definition], scope, start=start, end=end, as_of=now
    )
    expected = oracle(now)
    assert_state(head, expected)
    before_sources = [dict(r) for r in records]
    target = rng.choice(records)
    target["value"] = rng.randint(-50, 50)
    if seed % 3 == 0:
        target["deleted"] = True
        coverage["source_delete"] += 1
    elif seed % 3 == 1:
        target["session"] = UUID(int=rng.getrandbits(128))
        known.add(str(target["session"]))
        coverage["owner_move"] += 1
    else:
        target["arrival"] = now + timedelta(minutes=1)
        coverage["reopen"] += 1
    coverage["source_update"] += 1
    insert(target, ns(now + timedelta(minutes=1)))
    writer.store.execute("OPTIMIZE TABLE spans FINAL")
    coverage["revision_collapse"] += 1
    change = discover(now + timedelta(minutes=2))
    discovered = {(d.trace_id, d.span_id): d for d in change.dependencies}
    assert (str(target["trace"]), target["span"]) in discovered
    assert (
        discovered[(str(target["trace"]), target["span"])].is_deleted
        == target["deleted"]
    )
    # Isolate the crash point to the source refresh, not its preparatory copy.
    # A checkpoint-only retry must not be mistaken for consumed discovery work.
    writer.compact()
    insert_original, interrupted = writer.store.insert, []
    before_watermark = MetricProjectRefresh.for_project(scope.project).watermark_ns

    def boundary(table, payload):
        insert_original(table, payload)
        assert_state(
            MetricProjectRefresh.for_project(scope.project).head_manifest,
            expected,
            sources=before_sources,
        )
        assert (
            MetricProjectRefresh.for_project(scope.project).watermark_ns
            == before_watermark
        )
        coverage["staging_boundary"] += 1
        if seed % 11 == 0 and not interrupted:
            interrupted.append(True)
            raise RuntimeError("independent history staging interruption")

    writer.store.insert = boundary
    try:
        if seed % 11 == 0:
            with pytest.raises(RuntimeError, match="independent history staging"):
                writer.refresh(
                    change,
                    [definition],
                    scope,
                    start=start,
                    end=end,
                    as_of=now + timedelta(minutes=2),
                )
            writer.store.insert = insert_original
            head = writer.stage_and_publish(
                MetricRefreshAttempt.objects.get(
                    project_id=scope.project, state="staging"
                )
            )
        else:
            head = writer.refresh(
                change,
                [definition],
                scope,
                start=start,
                end=end,
                as_of=now + timedelta(minutes=2),
            )
    finally:
        writer.store.insert = insert_original
    expected = oracle(now + timedelta(minutes=2))
    assert_state(head, expected)
    if coverage["reopen"]:

        def load_latest(keys, budget):
            sql, params = unit_values_select(
                definition,
                scope,
                [k.unit_id for k in keys],
                start=start,
                end=end,
                as_of=now + timedelta(minutes=31),
                manifest=head,
            )
            rows = writer.store.execute(
                f"SELECT unit_id, last_arrival_ns, unit_start, source_rows FROM ({sql})",
                params,
            )
            return {
                UnitKey("conversation", r[0]): UnitSnapshot(
                    UnitKey("conversation", r[0]), r[1], r[2], exists=r[3] > 0
                )
                for r in rows
            }

        page = settle_due_page(
            analytics,
            org_id=str(scope.organization),
            project_id=str(scope.project),
            committed_attempts=[str(a) for _, a in head.allowed_attempts()],
            now=now + timedelta(minutes=31),
            settle_minutes=30,
            load_latest=load_latest,
        )
        assert page.updates
        proposal = replace(
            discover(now + timedelta(minutes=31)),
            units=tuple(u.state.key for u in page.updates),
        )
        head = writer.refresh(
            proposal,
            [definition],
            scope,
            start=start,
            end=end,
            as_of=now + timedelta(minutes=31),
        )
        expected = oracle(now + timedelta(minutes=31))
        assert_state(head, expected)
        coverage["due_settle"] += 1
    assert coverage["source_update"] == coverage["revision_collapse"] == 1
    assert coverage["staging_boundary"] > 0
