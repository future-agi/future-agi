# ruff: noqa: F811 -- shared isolated CH/PG fixtures
"""Refresh integration, whole buckets, source merges, crashes and oracle changes."""

from datetime import UTC, datetime, timedelta
from random import Random
from uuid import uuid4

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
from tracer.services.derived_metrics.publication import committed_rows_select
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
        if (
            table.name == BUCKET_TOTALS.name
            and len(bucket_writes) == kill_at
            and not failed
        ):
            failed.append(True)
            raise RuntimeError("injected refresh crash")
        original_insert(table, payload)
        if table.name == BUCKET_TOTALS.name:
            bucket_writes.append(True)
        pinned = MetricProjectRefresh.objects.get(
            project_id=observe_project.id
        ).head_manifest
        # Check the committed request snapshot THROUGHOUT staging, not only at
        # eventual completion: staged fields and either bucket are still hidden.
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
