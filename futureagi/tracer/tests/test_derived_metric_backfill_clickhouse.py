# ruff: noqa: F811 -- shared isolated CH/PG fixtures
"""Live discovery, dual-write, publication, activation and physical cleanup proofs."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tracer.models.derived_metric import (
    MetricGenerationPin,
    MetricProjectRefresh,
    MetricRefreshAttempt,
    MetricStateConflict,
)
from tracer.services.derived_metrics.backfill import BackfillRunner, queue_bulk_change
from tracer.services.derived_metrics.deletion_protocol import capture_pin
from tracer.services.derived_metrics.publication import (
    committed_rows_select,
    pinned_read,
)
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.refresh import MetricAdmissionUnproven
from tracer.services.derived_metrics.storage import (
    BUCKET_TOTALS,
    FIELD_VALUES,
    SOURCE_DEPENDENCIES,
)
from tracer.tests.test_derived_metric_backfill import (
    assert_scrubbed,
    make_version,
)
from tracer.tests.test_derived_metric_changes_clickhouse import Analytics
from tracer.tests.test_derived_metric_clickhouse import seed_span
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_refresh_clickhouse import (
    change_for,
    seed_span_revision,
)


@pytest.fixture
def history_writer(live_writer):
    # The shared minimal spans fixture predates B04's physical scan column.
    live_writer.store.execute(
        "ALTER TABLE spans ADD COLUMN observation_type String DEFAULT ''"
    )
    return live_writer


def context(writer, workspace, project, *, retention=2, page_size=1000):
    writer.retention_days = lambda: retention
    scope = Scope(workspace.organization_id, workspace.id, project.id)
    return scope, BackfillRunner(
        writer, Analytics(writer.store.client), scope, page_size=page_size
    )


def fields(writer, head, version):
    sql, params = committed_rows_select(
        FIELD_VALUES, head, org_id=writer.org_id, project_id=writer.lease.project_id
    )
    return writer.store.execute(
        f"SELECT unit_id, user_id, value_number, value_bool, status, group_key FROM ({sql}) "
        "WHERE field_id = toUUID(%(test_field)s) AND field_version = %(test_version)s AND is_deleted = 0 ORDER BY unit_id",
        {
            **params,
            "test_field": str(version.metric_id),
            "test_version": version.version,
        },
    )


def bucket_value(writer, head, version):
    sql, params = committed_rows_select(
        BUCKET_TOTALS, head, org_id=writer.org_id, project_id=writer.lease.project_id
    )
    n, d = writer.store.execute(
        f"SELECT sum(weighted_n), sum(weighted_d) FROM ({sql}) "
        "WHERE metric_id = toUUID(%(test_metric)s) AND metric_version = %(test_version)s AND is_deleted = 0",
        {
            **params,
            "test_metric": str(version.metric_id),
            "test_version": version.version,
        },
    )[0]
    return n / d if d else None


def active_version(metric):
    metric.refresh_from_db()
    return metric.active_version


def finish(runner, *, max_passes=12):
    for _ in range(max_passes):
        if not MetricProjectRefresh.for_project(
            runner.writer.lease.project_id
        ).pending_backfill:
            return MetricProjectRefresh.for_project(
                runner.writer.lease.project_id
            ).head_manifest
        runner.run_pass()
    pytest.fail("bounded backfill did not finish")


@pytest.mark.django_db(transaction=True)
def test_real_backfill_keeps_old_reads_dualwrites_and_atomically_switches_to_oracle(
    history_writer,
    workspace,
    observe_project,
    monkeypatch,
):
    writer = history_writer
    scope, runner = context(writer, workspace, observe_project)
    now = datetime.now(UTC).replace(microsecond=0)
    old = make_version(
        observe_project,
        workspace,
        active=True,
        inputs=[
            {
                "alias": "x",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "reducer": "latest",
            }
        ],
    )
    sessions, user = [uuid4(), uuid4()], uuid4()
    starts = [now - timedelta(hours=36), now - timedelta(hours=2)]
    traces = [uuid4(), uuid4()]
    for i, (session, at) in enumerate(zip(sessions, starts, strict=True)):
        seed_span(
            writer.store.client,
            scope,
            f"history-{i}",
            session,
            user,
            at,
            resolved=4 + 2 * i,
            escalated=10 + 4 * i,
            trace_id=traces[i],
        )
    head = writer.refresh(
        change_for(sessions[0]),
        [old],
        scope,
        start=now - timedelta(days=2),
        end=now,
        as_of=now,
    )
    from tracer.services.derived_metrics.changes import ns

    head = writer.refresh(
        change_for(sessions[1], cursor=ns(now - timedelta(minutes=5))),
        [old],
        scope,
        start=now - timedelta(days=2),
        end=now,
        as_of=now,
    )
    assert bucket_value(writer, head, old) == 5
    pin = capture_pin(
        observe_project.id,
        metric_id=old.metric_id,
        consumer_kind="snapshot",
        ttl=timedelta(hours=1),
    )
    draft = make_version(
        observe_project,
        workspace,
        metric=old.metric,
        group_dims=["model"],
        value_labels={"match": "Changed"},
        inputs=[
            {
                "alias": "x",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "escalated",
                "reducer": "latest",
            }
        ],
    )
    head = runner.run_pass(max_chunks=1)[0]
    draft.refresh_from_db()
    assert draft.backfill_days_done == 1 and draft.backfill_days_total == 2
    assert len(fields(writer, head, draft)) == 1
    assert active_version(old.metric).id == old.id
    assert bucket_value(writer, head, active_version(old.metric)) == 5
    assert old.metric.active_semantics()["group_dims"] == []
    assert old.metric.active_semantics()["value_labels"] == {}
    # A touch in an ALREADY older history chunk must still update BOTH recipes.
    seed_span(
        writer.store.client,
        scope,
        "changed",
        sessions[0],
        user,
        starts[0] + timedelta(minutes=1),
        resolved=8,
        escalated=20,
        trace_id=traces[0],
    )
    head = writer.refresh(
        change_for(
            sessions[0],
            cursor=MetricProjectRefresh.for_project(observe_project.id).watermark_ns,
        ),
        [old],
        scope,
        start=now - timedelta(days=2),
        end=now,
        as_of=now,
    )
    assert bucket_value(writer, head, old) == 7
    assert {row[2] for row in fields(writer, head, draft)} == {14.0, 20.0}
    assert active_version(old.metric).id == old.id
    # Observe the real INSERT boundary: new physical rows cannot move meaning.
    original, observations = writer.store.insert, []

    def observe(table, payload):
        observations.append(active_version(old.metric).id)
        original(table, payload)

    monkeypatch.setattr(writer.store, "insert", observe)
    head = finish(runner)
    assert observations and set(observations) == {old.id}
    assert active_version(old.metric).id == draft.id
    assert old.metric.active_semantics()["group_dims"] == ["model"]
    assert old.metric.active_semantics()["value_labels"] == {"match": "Changed"}
    assert bucket_value(writer, head, draft) == 17  # independent (20 + 14) / 2
    assert {row[2] for row in fields(writer, head, draft)} == {14.0, 20.0}
    dependencies, params = committed_rows_select(
        SOURCE_DEPENDENCIES, head, org_id=writer.org_id, project_id=observe_project.id
    )
    assert (
        writer.store.execute(
            f"SELECT count() FROM ({dependencies}) WHERE is_deleted = 0", params
        )[0][0]
        == 3
    )
    with pinned_read(writer.store, observe_project.id, pin=pin) as (captured, _):
        assert bucket_value(writer, captured, old) == 5
        assert {row[2] for row in fields(writer, captured, old)} == {4.0, 6.0}
    # Compact while the queued old-version capture is live: it remains present.
    monkeypatch.setattr(writer.store, "insert", original)
    head = writer.compact()
    assert bucket_value(writer, head, old) == 7
    assert MetricGenerationPin.objects.get(id=pin.id).is_usable()
    MetricGenerationPin.objects.filter(id=pin.id).update(
        invalidated_at=now, invalidation_reason="version_retired"
    )
    with pytest.raises(MetricStateConflict, match="expired"):
        with pinned_read(writer.store, observe_project.id, pin=pin):
            pytest.fail("invalidated queued capture entered")
    head = writer.compact()
    assert fields(writer, head, old) == []
    assert bucket_value(writer, head, draft) == 17
    # Old attempt namespaces are physically removed only through existing GC.
    cleanup_now = datetime.now(UTC) + timedelta(seconds=2)
    MetricRefreshAttempt.objects.exclude(id=head.published_by_id).update(
        expires_at=cleanup_now - timedelta(seconds=1)
    )
    writer.lease.clock = lambda: cleanup_now
    writer.collect_garbage()
    assert (
        writer.store.execute(
            "SELECT count() FROM metric_field_values WHERE field_version = 1"
        )[0][0]
        == 0
    )
    assert (
        writer.store.execute(
            "SELECT count() FROM metric_field_values_checkpoint WHERE field_version = 1"
        )[0][0]
        == 0
    )
    from tracer.models.derived_metric import DerivedMetric

    # Collection commits the permanent capture/activation fence without changing
    # the historical COMPLETE status required by the activation constraint.
    with pytest.raises(MetricStateConflict):
        DerivedMetric.activate_version(
            old.metric_id, old.id, expected_active_id=draft.id
        )


@pytest.mark.django_db(transaction=True)
def test_conversation_to_span_edit_preserves_old_grain_groups_and_labels_until_switch(
    history_writer,
    workspace,
    observe_project,
):
    writer = history_writer
    scope, runner = context(writer, workspace, observe_project)
    now = datetime.now(UTC).replace(microsecond=0)
    old = make_version(
        observe_project,
        workspace,
        active=True,
        inputs=[
            {
                "alias": "x",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "reducer": "latest",
            }
        ],
        group_dims=["model"],
        value_labels={"match": "Original"},
    )
    session, trace = uuid4(), uuid4()
    for i, n in enumerate([2, 8]):
        seed_span_revision(
            writer,
            scope,
            trace,
            f"span-{i}",
            session,
            now - timedelta(hours=3) + timedelta(minutes=i),
            n,
            revision=i + 1,
        )
    head = writer.refresh(
        change_for(session),
        [old],
        scope,
        start=now - timedelta(days=2),
        end=now,
        as_of=now,
    )
    assert bucket_value(writer, head, old) == 8
    draft = make_version(
        observe_project,
        workspace,
        metric=old.metric,
        **{
            **old.semantics(),
            "level": "span",
            "selection_unit": "span",
            "group_dims": ["provider"],
            "value_labels": {"match": "New grain"},
        },
    )
    head = runner.run_pass(max_chunks=1)[0]
    assert active_version(old.metric).level == "conversation"
    assert active_version(old.metric).group_dims == ["model"]
    assert active_version(old.metric).value_labels == {"match": "Original"}
    assert bucket_value(writer, head, old) == 8
    head = finish(runner)
    assert active_version(old.metric).id == draft.id
    assert active_version(old.metric).level == "span"
    assert bucket_value(writer, head, draft) == 5  # two ended spans, (2 + 8) / 2
    # Span evidence is committed FIELD_VALUES evidence (span_evidence), now
    # published as an ordinary delta when no privacy transition is needed.
    # After ordinary GC the ONLY physical span rows are the head's own evidence
    # of the new span version: the old conversation grain never wrote any, and
    # no superseded or abandoned span row survives outside the head.
    assert sorted(row[2] for row in fields(writer, head, draft)) == [2.0, 8.0]
    cleanup_now = datetime.now(UTC) + timedelta(seconds=2)
    MetricRefreshAttempt.objects.exclude(id=head.published_by_id).update(
        expires_at=cleanup_now - timedelta(seconds=1)
    )
    writer.lease.clock = lambda: cleanup_now
    writer.collect_garbage()
    allowed = tuple(str(attempt) for _, attempt in head.allowed_attempts())
    for name in (FIELD_VALUES.name, FIELD_VALUES.name + "_checkpoint"):
        assert writer.store.execute(
            f"SELECT count() FROM {name} WHERE level = 'span' "
            "AND (attempt_id NOT IN %(allowed)s OR field_version != %(version)s)",
            {"allowed": allowed, "version": draft.version},
        ) == [(0,)]


@pytest.mark.django_db(transaction=True)
def test_user_any_to_every_edit_keeps_old_user_rule_and_writes_no_buckets(
    history_writer,
    workspace,
    observe_project,
):
    writer = history_writer
    scope, runner = context(writer, workspace, observe_project)
    now = datetime.now(UTC).replace(microsecond=0)
    old = make_version(
        observe_project,
        workspace,
        active=True,
        kind="share",
        level="user",
        user_rule="any",
        value_type="boolean",
        inputs=[
            {
                "alias": "x",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "reducer": "latest",
            }
        ],
        field_spec={"conditions": [{"alias": "x", "operator": "gt", "value": 0}]},
    )
    user = uuid4()
    for i in range(2):
        session = uuid4()
        seed_span(
            writer.store.client,
            scope,
            f"user-{i}",
            session,
            user,
            now - timedelta(hours=i + 2),
            resolved=i,
        )
        writer.refresh(
            change_for(session),
            [old],
            scope,
            start=now - timedelta(days=2),
            end=now,
            as_of=now,
        )
    draft = make_version(
        observe_project,
        workspace,
        metric=old.metric,
        **{**old.semantics(), "user_rule": "every"},
    )
    head = runner.run_pass(max_chunks=1)[0]
    assert active_version(old.metric).user_rule == "any"
    assert max(row[3] for row in fields(writer, head, active_version(old.metric))) == 1
    head = finish(runner)
    assert (
        active_version(old.metric).id == draft.id
        and active_version(old.metric).user_rule == "every"
    )
    assert min(row[3] for row in fields(writer, head, draft)) == 0
    assert {row[1] for row in fields(writer, head, draft)} == {str(user)}
    for table in [
        "metric_bucket_totals",
        "metric_bucket_totals_checkpoint",
        "metric_bucket_retired_parts",
    ]:
        assert writer.store.execute(f"SELECT count() FROM {table}")[0][0] == 0
    assert all(row[4] == "ok" for row in fields(writer, head, draft))


@pytest.mark.django_db(transaction=True)
def test_live_backfill_crash_replays_frozen_rows_before_progress(
    history_writer, workspace, observe_project, monkeypatch
):
    writer = history_writer
    scope, runner = context(writer, workspace, observe_project)
    now = datetime.now(UTC).replace(microsecond=0)
    draft = make_version(
        observe_project,
        workspace,
        inputs=[
            {
                "alias": "x",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "reducer": "latest",
            }
        ],
    )
    session, user = uuid4(), uuid4()
    seed_span(
        writer.store.client,
        scope,
        "crash",
        session,
        user,
        now - timedelta(hours=3),
        resolved=9,
    )
    original, failed = writer.store.insert, []

    def crash(table, payload):
        original(table, payload)
        if not failed:
            failed.append(True)
            raise RuntimeError("lost INSERT acknowledgement")

    monkeypatch.setattr(writer.store, "insert", crash)
    with pytest.raises(RuntimeError, match="acknowledgement"):
        runner.run_pass(max_chunks=1)
    draft.refresh_from_db()
    assert draft.backfill_cursor is None and draft.backfill_days_done == 0
    assert MetricProjectRefresh.for_project(observe_project.id).head_manifest_id is None
    monkeypatch.setattr(writer.store, "insert", original)
    head = runner.run_pass(max_chunks=1)[0]
    draft.refresh_from_db()
    assert draft.backfill_days_done == 1
    assert [row[2] for row in fields(writer, head, draft)] == [9.0]
    assert writer.store.execute("SELECT count() FROM metric_field_values")[0][0] in {
        1,
        2,
    }


@pytest.mark.django_db(transaction=True)
def test_bulk_repair_covers_old_history_with_independent_persisted_cursor(
    history_writer, workspace, observe_project
):
    writer = history_writer
    scope, runner = context(
        writer, workspace, observe_project, retention=0, page_size=1
    )
    now = datetime.now(UTC).replace(microsecond=0)
    old = make_version(
        observe_project,
        workspace,
        active=True,
        inputs=[
            {
                "alias": "x",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "reducer": "latest",
            }
        ],
    )
    session, user = uuid4(), uuid4()
    seed_span(
        writer.store.client,
        scope,
        "old",
        session,
        user,
        now - timedelta(days=45),
        resolved=11,
    )
    queue_bulk_change(observe_project.id, now=now)
    phases = []
    for _ in range(12):
        runner.run_repair_page()
        state = MetricProjectRefresh.for_project(observe_project.id)
        phases.append(state.reconciliation_cursor["phase"])
        if not state.pending_repair:
            break
    else:
        pytest.fail("full-history repair failed to complete")
    assert "sources" in phases and phases[-1] == "complete"
    assert state.reconciliation_cursor["completed_passes"] == 1
    assert [row[2] for row in fields(writer, state.head_manifest, old)] == [11.0]
    assert bucket_value(writer, state.head_manifest, old) == 11
    assert old.backfill_cursor is None  # repair does not reuse a version/day cursor


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("known_anchor", [False, True])
def test_backfill_after_private_expiry_requires_identical_committed_anchor(
    history_writer, workspace, observe_project, known_anchor
):
    writer = history_writer
    scope, runner = context(writer, workspace, observe_project, retention=0)
    old = make_version(
        observe_project,
        workspace,
        active=True,
        inputs=[
            {
                "alias": "x",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "reducer": "latest",
            }
        ],
    )
    now = datetime.now(UTC).replace(microsecond=0)
    session, user = uuid4(), uuid4()
    initial = writer.begin(
        purpose="compaction",
        ceilings={"retention_floor": (now - timedelta(days=3)).isoformat()},
    )
    if known_anchor:
        from tracer.tests.test_derived_metric_publication_clickhouse import field_query

        query, params = field_query(
            str(session), metric=old.metric_id, start=now - timedelta(hours=2)
        )
        writer.freeze(initial, FIELD_VALUES, query, params)
    writer.seal(initial)
    head = writer.stage_and_publish(initial)
    draft = make_version(
        observe_project, workspace, metric=old.metric, **old.semantics()
    )
    seed_span(
        writer.store.client,
        scope,
        "unproven",
        session,
        user,
        now - timedelta(hours=2),
        resolved=1,
    )
    if known_anchor:
        head = runner.run_pass(max_chunks=1)[0]
        assert [row[2] for row in fields(writer, head, draft)] == [1.0]
        draft.refresh_from_db()
        assert draft.backfill_days_done == 1
    else:
        with pytest.raises(MetricAdmissionUnproven):
            runner.run_pass(max_chunks=1)
        draft.refresh_from_db()
        assert draft.backfill_cursor is None and draft.activated_at is None
        state = MetricProjectRefresh.for_project(observe_project.id)
        assert state.pending_backfill and state.pending_repair
        assert state.head_manifest_id == head.id and state.watermark_ns == 0


@pytest.mark.django_db(transaction=True)
def test_retention_overrides_old_version_pin_using_its_own_recipe(
    history_writer, workspace, observe_project
):
    from tracer.services.derived_metrics.changes import ChangeBatch
    from tracer.tests.test_derived_metric_publication_clickhouse import field_query

    writer = history_writer
    scope, _ = context(writer, workspace, observe_project, retention=0)
    now = datetime.now(UTC).replace(microsecond=0)
    old = make_version(observe_project, workspace, active=True)
    session = str(uuid4())
    attempt = writer.begin(purpose="compaction")
    query, params = field_query(
        session, metric=old.metric_id, start=now - timedelta(days=3), n=5
    )
    from tracer.services.derived_metrics.refresh import payload_select

    frozen = writer.freeze(
        attempt,
        FIELD_VALUES,
        query,
        params,
        extra={"inputs": "map('x', toNullable(toFloat64(5)))"},
    )
    bucket = (now - timedelta(days=3)).replace(
        minute=now.minute // 15 * 15, second=0, microsecond=0
    )
    writer.append(attempt, "dirty_after", [{"bucket_start": bucket.isoformat()}])
    overlay, bound = payload_select(FIELD_VALUES, frozen, prefix="old_expiry_fields")
    writer._freeze_buckets(attempt, None, old, overlay, bound)
    writer.seal(attempt)
    writer.stage_and_publish(attempt)
    pin = capture_pin(
        observe_project.id,
        metric_id=old.metric_id,
        consumer_kind="snapshot",
        ttl=timedelta(hours=1),
    )
    current = make_version(observe_project, workspace, metric=old.metric, active=True)
    attempt = writer.begin()
    query, params = field_query(
        session, metric=old.metric_id, start=now - timedelta(days=3), n=9
    )
    frozen = writer.freeze(
        attempt,
        FIELD_VALUES,
        query,
        params,
        extra={
            "field_version": "toUInt32(2)",
            "inputs": "map('x', toNullable(toFloat64(9)))",
        },
    )
    writer.append(attempt, "dirty_after", [{"bucket_start": bucket.isoformat()}])
    overlay, bound = payload_select(FIELD_VALUES, frozen, prefix="new_expiry_fields")
    writer._freeze_buckets(
        attempt,
        MetricProjectRefresh.for_project(observe_project.id).head_manifest,
        current,
        overlay,
        bound,
    )
    writer.seal(attempt)
    before = writer.stage_and_publish(attempt)
    assert bucket_value(writer, before, old) == 5
    assert bucket_value(writer, before, current) == 9
    writer.retention_days = lambda: 2
    head = writer.refresh(
        ChangeBatch((), (), (), (), (), {}),
        [current],
        scope,
        start=now - timedelta(days=4),
        end=now,
        as_of=now,
    )
    assert fields(writer, head, old) == fields(writer, head, current) == []
    assert bucket_value(writer, head, old) == 5
    assert bucket_value(writer, head, current) == 9
    assert not MetricGenerationPin.objects.get(id=pin.id).is_usable()
    with pytest.raises(MetricStateConflict, match="expired"):
        with pinned_read(writer.store, observe_project.id, pin=pin):
            pytest.fail("retention-invalidated old capture entered")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("capture_during_staging", [False, True])
def test_reactivation_or_pin_during_compaction_aborts_omission_without_losing_rows(
    history_writer, workspace, observe_project, monkeypatch, capture_during_staging
):
    from tracer.models.derived_metric import DerivedMetric
    from tracer.tests.test_derived_metric_publication_clickhouse import field_query

    writer = history_writer
    old = make_version(observe_project, workspace, active=True)
    attempt = writer.begin()
    query, params = field_query(
        "old-unit", metric=old.metric_id, start=datetime.now(UTC), n=8
    )
    writer.freeze(attempt, FIELD_VALUES, query, params)
    writer.seal(attempt)
    before = writer.stage_and_publish(attempt)
    current = make_version(observe_project, workspace, metric=old.metric, active=True)
    publish, pins = writer.stage_and_publish, []

    def activate_after_freeze(attempt):
        DerivedMetric.activate_version(
            old.metric_id, old.id, expected_active_id=current.id
        )
        if capture_during_staging:
            pins.append(
                capture_pin(
                    observe_project.id,
                    metric_id=old.metric_id,
                    consumer_kind="snapshot",
                    ttl=timedelta(minutes=5),
                )
            )
            DerivedMetric.activate_version(
                old.metric_id, current.id, expected_active_id=old.id
            )
        return publish(attempt)

    monkeypatch.setattr(writer, "stage_and_publish", activate_after_freeze)
    with pytest.raises(MetricStateConflict, match="eligibility changed"):
        writer.compact()
    assert (
        MetricProjectRefresh.for_project(observe_project.id).head_manifest_id
        == before.id
    )
    assert [row[2] for row in fields(writer, before, old)] == [8]
    old.refresh_from_db()
    assert old.pins_revoked_at is None
    if pins:
        assert pins[0].is_usable()
        with pinned_read(writer.store, observe_project.id, pin=pins[0]) as (head, _):
            assert [row[2] for row in fields(writer, head, old)] == [8]
    else:
        assert active_version(old.metric).id == old.id


@pytest.mark.django_db(transaction=True)
def test_repeated_superseded_drafts_physically_collect_fields_and_buckets(
    history_writer, workspace, observe_project
):
    from tracer.models.derived_metric import DerivedMetric
    from tracer.services.derived_metrics.refresh import payload_select
    from tracer.tests.test_derived_metric_publication_clickhouse import field_query

    writer = history_writer
    old = make_version(observe_project, workspace, active=True)
    draft = make_version(observe_project, workspace, metric=old.metric)
    abandoned = []
    for number in range(3):
        now = datetime.now(UTC)
        attempt = writer.begin()
        query, params = field_query(
            "superseded-unit", metric=old.metric_id, start=now, n=8 + number
        )
        frozen = writer.freeze(
            attempt,
            FIELD_VALUES,
            query,
            params,
            extra={
                "field_version": f"toUInt32({draft.version})",
                "inputs": "map('x', toNullable(toFloat64(%(test_n)s)))",
            },
        )
        bucket = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
        writer.append(attempt, "dirty_after", [{"bucket_start": bucket.isoformat()}])
        overlay, bound = payload_select(FIELD_VALUES, frozen, prefix="draft_fields")
        head = MetricProjectRefresh.for_project(observe_project.id).head_manifest
        writer._freeze_buckets(attempt, head, draft, overlay, bound)
        writer.seal(attempt)
        head = writer.stage_and_publish(attempt)
        assert (
            fields(writer, head, draft)
            and bucket_value(writer, head, draft) == 8 + number
        )
        abandoned.append(draft)
        draft = make_version(observe_project, workspace, metric=old.metric)
        head = writer.compact()
        for version in abandoned:
            version.refresh_from_db()
            assert version.activated_at is None and version.pins_revoked_at is not None
            assert fields(writer, head, version) == []
            assert bucket_value(writer, head, version) is None
            with pytest.raises(MetricStateConflict):
                DerivedMetric.activate_version(
                    old.metric_id, version.id, expected_active_id=old.id
                )
        assert active_version(old.metric).id == old.id
        cleanup_now = datetime.now(UTC) + timedelta(seconds=2)
        MetricRefreshAttempt.objects.exclude(id=head.published_by_id).update(
            expires_at=cleanup_now - timedelta(seconds=1)
        )
        writer.lease.clock = lambda at=cleanup_now: at
        writer.collect_garbage()
        for table, prefix in ((FIELD_VALUES, "field"), (BUCKET_TOTALS, "metric")):
            for name in (table.name, table.name + "_checkpoint"):
                if table == BUCKET_TOTALS and name.endswith("_checkpoint"):
                    continue  # bucket totals already use immutable checkpoint namespaces
                assert writer.store.execute(
                    f"SELECT count() FROM {name} WHERE {prefix}_version IN %(obsolete)s",
                    {"obsolete": tuple(v.version for v in abandoned)},
                ) == [(0,)]


@pytest.mark.django_db(transaction=True)
def test_expiry_scrubs_superseded_and_nested_progress_after_real_partial_page(
    history_writer, workspace, observe_project
):
    from tracer.models.derived_metric import DerivedMetricVersion
    from tracer.services.derived_metrics.changes import ChangeBatch
    from tracer.services.derived_metrics.lease import ProjectLease

    writer = history_writer
    scope, runner = context(
        writer, workspace, observe_project, retention=2, page_size=1
    )
    now = datetime.now(UTC).replace(microsecond=0)
    old = make_version(observe_project, workspace, active=True)
    draft = make_version(observe_project, workspace, metric=old.metric)
    trace, session, user = uuid4(), uuid4(), uuid4()
    seed_span(
        writer.store.client,
        scope,
        "private-keyset-span",
        session,
        user,
        now - timedelta(hours=6),
        trace_id=trace,
    )
    runner.run_pass(max_chunks=1)
    draft.refresh_from_db()
    assert str(trace) in str(draft.backfill_cursor)
    newest = make_version(observe_project, workspace, metric=old.metric)
    draft.refresh_from_db()
    assert draft.backfill_cursor is None
    nested = {
        "start": now.isoformat(),
        "day_end": now.isoformat(),
        "total": 2,
        "phase": "coverage",
        "coverage": {"after": [str(trace), "private-keyset-span"]},
    }
    DerivedMetricVersion.objects.filter(id=newest.id).update(backfill_cursor=nested)
    future = now + timedelta(days=3)
    grant = MetricProjectRefresh.take_lease(
        observe_project.id,
        holder="backfill-expiry",
        ttl=timedelta(minutes=5),
        now=future,
    )
    writer.lease = ProjectLease(grant, clock=lambda: future)
    writer.retention_days = lambda: 1
    head = writer.refresh(
        ChangeBatch((), (), (), (), (), {}),
        [old],
        scope,
        start=now - timedelta(days=4),
        end=future,
        as_of=future,
    )
    assert fields(writer, head, draft) == []
    draft.refresh_from_db()
    newest.refresh_from_db()
    assert draft.backfill_cursor is None  # abandoned: dropped whole at the edit
    # The live draft keeps its coverage progress without the undated keyset.
    assert newest.backfill_cursor == {
        **nested,
        "coverage": {"after": None, "keyset_start": None},
    }
    assert str(trace) not in str(
        list(DerivedMetricVersion.objects.values_list("backfill_cursor", flat=True))
    )


@pytest.mark.django_db(transaction=True)
def test_subject_erasure_scrubs_partial_and_repair_keysets(
    history_writer, workspace, observe_project
):
    from types import SimpleNamespace

    from tracer.services.derived_metrics.retention import purge_by_scope

    writer = history_writer
    scope, runner = context(
        writer, workspace, observe_project, retention=2, page_size=1
    )
    now = datetime.now(UTC)
    old = make_version(observe_project, workspace, active=True)
    draft = make_version(observe_project, workspace, metric=old.metric)
    trace, session, user = uuid4(), uuid4(), uuid4()
    seed_span(
        writer.store.client,
        scope,
        "erased-keyset-span",
        session,
        user,
        now - timedelta(hours=6),
        trace_id=trace,
    )
    runner.run_pass(max_chunks=1)
    draft.refresh_from_db()
    assert str(trace) in str(draft.backfill_cursor)
    partial = draft.backfill_cursor
    repair = {"phase": "units", "after": ["conversation", str(session)]}
    MetricProjectRefresh.objects.filter(project_id=observe_project.id).update(
        reconciliation_cursor=repair,
        pending_repair=True,
    )
    request = SimpleNamespace(
        id=uuid4(),
        organization_key=writer.org_id,
        project_key=observe_project.id,
        scope_kind="sessions",
        subject_ids=[str(session)],
    )
    result = purge_by_scope(writer.store, request, writer=writer)
    head = MetricProjectRefresh.for_project(observe_project.id).head_manifest
    assert result["manifest"] == str(head.id)
    assert fields(writer, head, draft) == []
    draft.refresh_from_db()
    state = MetricProjectRefresh.for_project(observe_project.id)
    # Erasure cannot tell whose keyset it is: every position goes, progress stays.
    assert draft.backfill_cursor == {**partial, "after": None, "keyset_start": None}
    assert state.reconciliation_cursor == {
        **repair,
        "after": None,
        "keyset_start": None,
    }
    assert str(trace) not in str(draft.backfill_cursor)
    assert str(session) not in str(state.reconciliation_cursor)
    assert state.pending_backfill and state.pending_repair


@pytest.mark.django_db(transaction=True)
def test_erasure_scrubs_repair_ledger_and_legacy_attempt_metadata(
    history_writer, workspace, observe_project
):
    import json
    from types import SimpleNamespace

    from tracer.models.derived_metric import MetricAttemptLedgerChunk
    from tracer.services.derived_metrics.retention import purge_by_scope

    writer = history_writer
    scope, runner = context(
        writer, workspace, observe_project, retention=0, page_size=1
    )
    now = datetime.now(UTC)
    version = make_version(observe_project, workspace, active=True)
    session, trace = uuid4(), uuid4()
    seed_span(
        writer.store.client,
        scope,
        "private-repair-span",
        session,
        uuid4(),
        now - timedelta(hours=2),
        trace_id=trace,
    )
    queue_bulk_change(observe_project.id, now=now)
    head = runner.run_repair_page()
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert str(trace) in json.dumps(state.reconciliation_cursor)
    partial = state.reconciliation_cursor
    assert str(trace) not in json.dumps(head.published_by.source_ceilings)
    assert str(trace) in json.dumps(
        list(MetricAttemptLedgerChunk.objects.values_list("payload", flat=True))
    )
    # Simulate a persisted pre-fix duplicate; its audit hash must not be rewritten.
    legacy = head.published_by
    ceilings = {
        **legacy.source_ceilings,
        "cursor_updates": {
            "watermark_ns": 42,
            "reconciliation_cursor": state.reconciliation_cursor,
        },
    }
    MetricRefreshAttempt.objects.filter(id=legacy.id).update(source_ceilings=ceilings)
    original_hash = legacy.prepared_sha256
    request = SimpleNamespace(
        id=uuid4(),
        organization_key=writer.org_id,
        project_key=observe_project.id,
        scope_kind="sessions",
        subject_ids=[str(session)],
    )
    purge_by_scope(writer.store, request, writer=writer)
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert_scrubbed(state.reconciliation_cursor, partial)
    assert str(trace) not in json.dumps(state.reconciliation_cursor)
    assert fields(writer, state.head_manifest, version) == []
    assert str(trace) not in json.dumps(
        list(MetricRefreshAttempt.objects.values_list("source_ceilings", flat=True))
    )
    assert str(trace) not in json.dumps(
        list(MetricAttemptLedgerChunk.objects.values_list("payload", flat=True))
    )
    legacy.refresh_from_db()
    assert legacy.prepared_sha256 == original_hash
    assert legacy.source_ceilings["cursor_updates"] == {"watermark_ns": 42}


@pytest.mark.django_db(transaction=True)
def test_finite_retention_daily_and_multipage_coverage_finish_without_reset(
    history_writer, workspace, observe_project
):
    writer = history_writer
    scope, runner = context(
        writer, workspace, observe_project, retention=1, page_size=1
    )
    now = datetime.now(UTC)
    draft = make_version(observe_project, workspace)
    session, trace = uuid4(), uuid4()
    seed_span(
        writer.store.client,
        scope,
        "retained-coverage-span",
        session,
        uuid4(),
        now - timedelta(hours=2),
        trace_id=trace,
    )
    phases, saw_private_source, days = set(), False, []
    for _ in range(24):
        head = runner.run_pass(max_chunks=1)[0]
        draft.refresh_from_db()
        days.append(draft.backfill_days_done)
        cp = draft.backfill_cursor
        assert cp is not None
        if cp["coverage"]:
            phases.add(cp["coverage"]["phase"])
            saw_private_source |= bool(
                cp["coverage"]["phase"] == "sources" and cp["coverage"]["after"]
            )
        if draft.backfill_status == "complete":
            break
    else:
        pytest.fail("retained paged coverage never activated")
    assert saw_private_source
    assert {"dependencies", "units", "complete"} <= phases
    assert days == sorted(days) and days[-1] == 1
    assert (
        draft.activated_at is not None and active_version(draft.metric).id == draft.id
    )
    assert len(fields(writer, head, draft)) == 1


@pytest.mark.django_db(transaction=True)
def test_version_coverage_is_bounded_but_reads_whole_boundary_conversation(
    history_writer, workspace, observe_project
):
    from tracer.models.derived_metric import DerivedMetricVersion
    from tracer.services.derived_metrics import backfill, changes

    writer = history_writer
    scope, runner = context(writer, workspace, observe_project, retention=0)
    now = datetime.now(UTC)
    draft = make_version(
        observe_project,
        workspace,
        inputs=[
            {
                "alias": "x",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "reducer": "sum",
            }
        ],
    )
    cursor = backfill._initial_cursor(draft, now=now, retention_days=0)
    cursor.update(phase="coverage", done=90, day_end=cursor["start"])
    DerivedMetricVersion.objects.filter(id=draft.id).update(
        backfill_cursor=cursor,
        backfill_status="running",
        backfill_days_done=90,
        backfill_days_total=90,
    )
    MetricProjectRefresh.objects.filter(project_id=observe_project.id).update(
        watermark_ns=changes.ns(now),
        eval_watermark_ns=changes.ns(now),
        remap_cursor_ns=changes.ns(now),
    )
    outside, spanning, user = uuid4(), uuid4(), uuid4()
    for span, session, days, value in (
        ("outside-history", outside, 120, 8),
        ("boundary-old-member", spanning, 120, 3),
        ("boundary-new-member", spanning, 0.1, 7),
    ):
        seed_span(
            writer.store.client,
            scope,
            span,
            session,
            user,
            now - timedelta(days=days),
            resolved=value,
        )
    head = finish(runner)
    assert [(row[0], row[2]) for row in fields(writer, head, draft)] == [
        (str(spanning), 10.0)
    ]
    assert draft.metric.active_version_id is None  # stale object, not a partial switch
    assert active_version(draft.metric).id == draft.id
    # Independent project repair remains full retained history, not 90-day work.
    queue_bulk_change(observe_project.id, now=now)
    for _ in range(12):
        head = runner.run_repair_page()
        if not MetricProjectRefresh.for_project(observe_project.id).pending_repair:
            break
    else:
        pytest.fail("full-history repair failed to finish")
    assert {row[0]: row[2] for row in fields(writer, head, draft)} == {
        str(spanning): 10.0,
        str(outside): 8.0,
    }


@pytest.mark.django_db(transaction=True)
def test_omitted_payload_expires_before_live_attempt_history_ttl(
    history_writer, workspace, observe_project
):
    from tracer.models.derived_metric import MetricErasureRequest
    from tracer.services.derived_metrics.lease import ProjectLease
    from tracer.services.derived_metrics.retention import expire_project
    from tracer.tests.test_derived_metric_publication_clickhouse import field_query

    writer = history_writer
    writer.retention_days = lambda: 2
    now = datetime.now(UTC)
    old = make_version(observe_project, workspace, active=True)
    draft = make_version(observe_project, workspace, metric=old.metric)
    attempt = writer.begin()
    sql, params = field_query(
        str(uuid4()), metric=old.metric_id, start=now - timedelta(hours=6), n=8
    )
    writer.freeze(
        attempt,
        FIELD_VALUES,
        sql,
        params,
        extra={"field_version": f"toUInt32({draft.version})"},
    )
    writer.seal(attempt)
    before = writer.stage_and_publish(attempt)
    assert len(fields(writer, before, draft)) == 1
    newest = make_version(observe_project, workspace, metric=old.metric)
    head = writer.compact()
    assert fields(writer, head, draft) == []
    debt = MetricErasureRequest.objects.get(store_payload__protocol="metric_omission")
    assert debt.status != "done" and "unit_id" not in debt.store_payload
    assert writer.store.execute(
        "SELECT count() FROM metric_field_values WHERE field_version = %(v)s",
        {"v": draft.version},
    ) == [(1,)]
    future = now + timedelta(days=3)
    assert MetricRefreshAttempt.objects.get(id=attempt.id).expires_at > future
    grant = MetricProjectRefresh.take_lease(
        observe_project.id,
        holder="omitted-expiry",
        ttl=timedelta(minutes=5),
        now=future,
    )
    writer.lease = ProjectLease(grant, clock=lambda: future)
    writer.retention_days = lambda: 1
    result = expire_project(
        writer, [old, newest], retention_days=writer.retention_days, now=future
    )
    assert result["purged"]
    writer.collect_garbage()
    assert MetricRefreshAttempt.objects.filter(id=attempt.id).exists()
    for table in ("metric_field_values", "metric_field_values_checkpoint"):
        assert writer.store.execute(
            f"SELECT count() FROM {table} WHERE field_version = %(v)s",
            {"v": draft.version},
        ) == [(0,)]
    debt.refresh_from_db()
    assert debt.status == "done"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("work_kind", ["version", "repair"])
def test_retained_key_in_cutoff_hour_advances_daily_and_coverage_pages(
    history_writer, workspace, observe_project, work_kind
):
    from tracer.services.derived_metrics.lease import ProjectLease

    writer = history_writer
    now = (datetime.now(UTC) + timedelta(days=2)).replace(
        hour=12, minute=30, second=0, microsecond=0
    )
    grant = MetricProjectRefresh.take_lease(
        observe_project.id, holder="cutoff-hour", ttl=timedelta(minutes=5), now=now
    )
    writer.lease = ProjectLease(grant, clock=lambda: now)
    scope, runner = context(
        writer, workspace, observe_project, retention=1, page_size=1
    )
    version = make_version(observe_project, workspace, active=work_kind == "repair")
    trace, session = uuid4(), uuid4()
    retained = now - timedelta(days=1) + timedelta(minutes=5)
    seed_span(
        writer.store.client,
        scope,
        "retained-cutoff-hour",
        session,
        uuid4(),
        retained,
        trace_id=trace,
    )
    if work_kind == "repair":
        queue_bulk_change(observe_project.id, now=now)
    saw_exact_position = False
    for _ in range(24):
        head = (
            runner.run_pass(max_chunks=1)[0]
            if work_kind == "version"
            else runner.run_repair_page()
        )
        version.refresh_from_db()
        state = MetricProjectRefresh.for_project(observe_project.id)
        cursor = (
            version.backfill_cursor
            if work_kind == "version"
            else state.reconciliation_cursor
        )
        assert cursor is not None
        for position in (cursor, cursor.get("coverage") or {}):
            if position.get("phase") == "sources" and position.get("after"):
                assert datetime.fromisoformat(position["after"][2]).replace(
                    tzinfo=UTC
                ) < now - timedelta(days=1)
                assert datetime.fromisoformat(position["keyset_start"]) == retained
                saw_exact_position = True
        pending = (
            state.pending_backfill if work_kind == "version" else state.pending_repair
        )
        if not pending:
            break
    else:
        pytest.fail("retained cutoff-hour keyset kept restarting")
    assert saw_exact_position
    assert len(fields(writer, head, version)) == 1
    assert active_version(version.metric).id == version.id


STEADY_INPUT = [
    {
        "alias": "x",
        "source": "span",
        "field": "attribute_number",
        "attribute_key": "resolved",
        "reducer": "latest",
    }
]


def steady_aging(writer, workspace, project, *, units=10):
    """One retained conversation crosses the moving one-day cutoff per step."""
    from tracer.services.derived_metrics.changes import ChangeBatch, UnitKey, ns

    scope, runner = context(writer, workspace, project, retention=1, page_size=1000)
    t0 = datetime.now(UTC).replace(microsecond=0)
    clock = [t0]
    writer.lease.clock = lambda: clock[0]
    writer.lease.renew(ttl=timedelta(hours=1))
    old = make_version(project, workspace, active=True, inputs=STEADY_INPUT)
    sessions = []
    for i in range(1, units + 1):
        sessions.append(uuid4())
        seed_span(
            writer.store.client,
            scope,
            f"steady-{i}",
            sessions[-1],
            uuid4(),
            t0 - timedelta(days=1) + timedelta(minutes=10 * (i - 1) + 1),
            resolved=i,
        )
    sessions.append(uuid4())
    seed_span(
        writer.store.client,
        scope,
        "steady-recent",
        sessions[-1],
        uuid4(),
        t0 - timedelta(hours=1),
        resolved=100,
    )
    writer.refresh(
        ChangeBatch(
            (),
            tuple(UnitKey("conversation", str(s)) for s in sessions),
            (),
            (),
            (),
            {"watermark_ns": ns(t0)},
        ),
        [old],
        scope,
        start=t0 - timedelta(days=2),
        end=t0,
        as_of=t0,
    )

    def step():
        clock[0] += timedelta(minutes=10)
        writer.lease.renew(ttl=timedelta(hours=1))
        return round((clock[0] - t0) / timedelta(minutes=10))

    return runner, old, step


def retained_values(steps, units=10):
    # Conversation i starts 10(i - 1) + 1 minutes after t0 - 1 day, so after k
    # ten-minute steps exactly conversations 1..k have crossed the cutoff.
    return sorted([float(i) for i in range(steps + 1, units + 1)] + [100.0])


@pytest.mark.django_db(transaction=True)
def test_steady_aging_backfill_activates_and_collects_superseded_draft(
    history_writer, workspace, observe_project, settings
):
    # Each ten-minute step ages one conversation out; expire on every pass
    # (physical expiry is otherwise batched per METRICS_EXPIRY_INTERVAL_SECONDS).
    settings.METRICS_EXPIRY_INTERVAL_SECONDS = 60
    writer = history_writer
    runner, old, step = steady_aging(writer, workspace, observe_project)
    draft = make_version(
        observe_project,
        workspace,
        metric=old.metric,
        inputs=STEADY_INPUT,
        group_dims=["model"],
    )
    step()
    head = runner.run_pass(max_chunks=1)[0]
    assert head.published_by.source_ceilings.get("logical_expiry")
    assert fields(writer, head, draft)  # abandoned below with published payload
    newest = make_version(
        observe_project,
        workspace,
        metric=old.metric,
        inputs=STEADY_INPUT,
        value_labels={"match": "Newest"},
    )
    trail = []
    for _ in range(10):
        steps = step()
        head = runner.run_pass(max_chunks=1)[0]
        newest.refresh_from_db()
        cursor = newest.backfill_cursor or {}
        trail.append(
            (
                newest.backfill_days_done,
                cursor.get("phase"),
                (cursor.get("coverage") or {}).get("phase"),
                bool(head.published_by.source_ceilings.get("logical_expiry")),
                len(head.delta_attempt_ids),
            )
        )
        if newest.backfill_status == "complete":
            break
        assert cursor, trail  # an expiry never discards progress
    else:
        pytest.fail(f"steady aging blocked activation: {trail}")
    assert len(trail) <= 5, trail  # the non-aging control path is 4-5 chunks
    assert sum(item[3] for item in trail) >= len(trail) - 1, trail
    assert [item[0] for item in trail] == sorted(item[0] for item in trail)
    assert active_version(old.metric).id == newest.id
    assert sorted(row[2] for row in fields(writer, head, newest)) == retained_values(
        steps
    )
    # Retention checkpoints reset the delta chain every pass; the abandoned draft
    # was still omitted and fenced, by the pending-omission trigger.
    assert max(item[4] for item in trail) == 0, trail
    draft.refresh_from_db()
    assert draft.pins_revoked_at is not None and draft.activated_at is None
    assert fields(writer, head, draft) == []
    assert bucket_value(writer, head, draft) is None


@pytest.mark.django_db(transaction=True)
def test_steady_aging_full_history_repair_completes_without_restarting(
    history_writer, workspace, observe_project, settings
):
    # Each ten-minute step ages one conversation out; expire on every pass
    # (physical expiry is otherwise batched per METRICS_EXPIRY_INTERVAL_SECONDS).
    settings.METRICS_EXPIRY_INTERVAL_SECONDS = 60
    writer = history_writer
    runner, old, step = steady_aging(writer, workspace, observe_project)
    queue_bulk_change(observe_project.id, now=writer.lease.clock())
    order = ["sources", "dependencies", "units", "complete"]
    trail = []
    for _ in range(8):
        steps = step()
        head = runner.run_repair_page()
        state = MetricProjectRefresh.for_project(observe_project.id)
        cursor = state.reconciliation_cursor
        assert cursor is not None, trail
        trail.append(
            (
                cursor["phase"],
                bool(head.published_by.source_ceilings.get("logical_expiry")),
            )
        )
        if not state.pending_repair:
            break
    else:
        pytest.fail(f"steady aging blocked the repair sweep: {trail}")
    assert [order.index(phase) for phase, _ in trail] == sorted(
        order.index(phase) for phase, _ in trail
    ), trail
    assert sum(expired for _, expired in trail) >= len(trail) - 1, trail
    assert cursor["completed_passes"] == 1 and cursor["phase"] == "complete"
    assert sorted(row[2] for row in fields(writer, head, old)) == retained_values(steps)


@pytest.mark.django_db(transaction=True)
def test_retained_conversation_tombstone_does_not_retrigger_expiry(
    history_writer, workspace, observe_project
):
    from tracer.services.derived_metrics.changes import Dependency
    from tracer.services.derived_metrics.retention import expire_project
    from tracer.tests.test_derived_metric_publication_clickhouse import field_query

    writer = history_writer
    writer.retention_days = lambda: 1
    now = datetime.now(UTC)
    version = make_version(observe_project, workspace, active=True)
    session = str(uuid4())
    attempt = writer.begin(purpose="compaction")
    sql, params = field_query(
        session, metric=version.metric_id, start=now - timedelta(hours=2), n=4
    )
    writer.freeze(attempt, FIELD_VALUES, sql, params)
    # The deleted earliest member keeps its own older start; the conversation's
    # live anchor is recent, so the head deliberately retains this tombstone.
    writer.freeze_dependencies(
        attempt,
        [
            Dependency(
                str(uuid4()),
                "deleted-earliest",
                session,
                session,
                "",
                "",
                now - timedelta(days=2),
                is_deleted=True,
            ),
            Dependency(
                str(uuid4()),
                "live-member",
                session,
                session,
                "",
                "",
                now - timedelta(hours=2),
            ),
        ],
    )
    writer.seal(attempt)
    head = writer.stage_and_publish(attempt)
    pin = capture_pin(
        observe_project.id,
        metric_id=version.metric_id,
        consumer_kind="snapshot",
        ttl=timedelta(hours=1),
    )
    for _ in range(2):
        result = expire_project(
            writer, [version], retention_days=writer.retention_days, now=now
        )
        assert result == {"unlimited": False, "purged": False}
        assert (
            MetricProjectRefresh.for_project(observe_project.id).head_manifest_id
            == head.id
        )
    assert MetricGenerationPin.objects.get(id=pin.id).is_usable()
    dependencies, bound = committed_rows_select(
        SOURCE_DEPENDENCIES, head, org_id=writer.org_id, project_id=observe_project.id
    )
    assert writer.store.execute(
        f"SELECT count() FROM ({dependencies}) WHERE is_deleted = 1", bound
    ) == [(1,)]
    assert [row[2] for row in fields(writer, head, version)] == [4.0]
