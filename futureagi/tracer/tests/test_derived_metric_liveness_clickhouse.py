# ruff: noqa: F811 -- shared isolated ClickHouse / PostgreSQL fixtures
"""Initial history must progress across real fenced admissions, not zombie work."""

from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import uuid4

import pytest

from tracer.models.derived_metric import MetricProjectRefresh
from tracer.services.clickhouse import v2
from tracer.services.derived_metrics import admission, backfill, reads
from tracer.services.derived_metrics.publication import ClickHousePublicationStore
from tracer.services.derived_metrics.queries import Scope
from tracer.tests.metric_read_fixtures import ninety_day_source_policy  # noqa: F401
from tracer.tests.test_derived_metric_backfill import make_version
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    metric_ch,
    publication_store,
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("ninety_day_source_policy")
@pytest.mark.parametrize("definition_count", [1, 3])
def test_review_sized_initial_history_finishes_in_bounded_reservations(
    live_writer, workspace, observe_project, settings, monkeypatch, definition_count
):
    settings.METRICS_ENABLED = 1
    settings.METRICS_MAX_CONCURRENT_PROJECTS = 1
    store = live_writer.store
    store.execute("ALTER TABLE spans ADD COLUMN observation_type String DEFAULT ''")
    # Span-only discovery/publication/KPI reads must work without the evaluation
    # logger; eval-backed definitions still require the configured source table.
    store.execute("DROP TABLE tracer_eval_logger_v2")
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    definition = make_version(
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
    definitions = [definition]
    for index in range(definition_count):
        current = (
            definition
            if index == 0
            else make_version(
                observe_project,
                workspace,
                inputs=definition.inputs,
            )
        )
        current.metric.name = f"Initial history {index + 1}"
        current.metric.save(update_fields=["name", "updated_at"])
        if index:
            definitions.append(current)
    now = datetime.now(UTC).replace(microsecond=0)
    # 350 conversations, 2,800 traces and 5,600 spans, spread over seven days.
    # One insert avoids measuring the fixture loader instead of the writer.
    store.execute(
        "INSERT INTO spans (id, project_id, org_id, trace_id, trace_session_id, "
        "end_user_id, start_time, end_time, created_at, updated_at, attrs_number) "
        "SELECT toString(number), toUUID(%(project)s), toUUID(%(org)s), "
        "toString(intDiv(number, 2)), reinterpretAsUUID(toFixedString(toString(intDiv(number, 16)), 16)), "
        "toUUID(%(user)s), toDateTime64(%(now)s, 6, 'UTC') - toIntervalMinute(120 + intDiv(number, 16) * 28) AS start_time, "
        "start_time + toIntervalSecond(1), start_time, start_time, map('resolved', toFloat64(2)) "
        "FROM numbers(5600)",
        {
            "project": str(scope.project),
            "org": str(scope.organization),
            "user": str(uuid4()),
            "now": now,
        },
    )
    live_writer.lease.release()
    started = monotonic()
    execute = ClickHousePublicationStore.execute

    def bounded_execute(self, *args, **kwargs):
        assert monotonic() - started < 45, (
            "initial backfill exceeded 45 seconds before publishing usable history"
        )
        return execute(self, *args, **kwargs)

    monkeypatch.setattr(ClickHousePublicationStore, "execute", bounded_execute)
    freeze_select = ClickHousePublicationStore.freeze_select
    transactions = []

    def unlocked_freeze(self, *args, **kwargs):
        from django.db import connection

        transactions.append(connection.in_atomic_block)
        return freeze_select(self, *args, **kwargs)

    monkeypatch.setattr(ClickHousePublicationStore, "freeze_select", unlocked_freeze)
    native = store.client.connection
    monkeypatch.setattr(
        v2,
        "get_v2_config",
        lambda: {
            "host": native.host,
            "tcp_port": native.port,
            "user": native.user,
            "password": native.password,
            "database": native.database,
        },
    )

    for _reservation_no in range(12):
        (reservation,) = admission.reserve_projects()
        assert admission.execute_reservation(reservation.payload())
        assert not any(transactions), (
            "ClickHouse preparation held a PostgreSQL transaction"
        )
        state = MetricProjectRefresh.for_project(scope.project)
        assert state.head_manifest_id and state.backfill_last_progress_at
        assert state.lease_token is None
        definition.refresh_from_db()
        if definition.backfill_status == "complete":
            break
    else:
        pytest.fail("initial backfill did not finish within twelve reservations")
    for definition in definitions:
        definition.refresh_from_db()
        assert definition.backfill_status == "complete"
        metric = definition.metric
        metric.refresh_from_db()
        assert metric.active_version_id == definition.id
        result = reads.read_kpi(
            metric,
            {"window_start": now - timedelta(days=7), "window_end": now},
            store=store,
        )
        assert result["current"]["value"] == pytest.approx(2)
        assert result["current"]["eligible_count"] == 350
    elapsed = monotonic() - started
    assert elapsed < 45
    print(
        f"INITIAL_HISTORY definitions={definition_count} seconds={elapsed:.3f} reservations={_reservation_no + 1}"
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("ninety_day_source_policy")
def test_large_initial_history_yields_after_checkpoint_and_resumes_next_reservation(
    live_writer, workspace, observe_project, settings, monkeypatch
):
    from tracer.services.derived_metrics.refresh import RefreshWriter

    settings.METRICS_ENABLED = 1
    settings.METRICS_MAX_CONCURRENT_PROJECTS = 1
    store = live_writer.store
    store.execute("ALTER TABLE spans ADD COLUMN observation_type String DEFAULT ''")
    native = store.client.connection
    monkeypatch.setattr(
        v2,
        "get_v2_config",
        lambda: {
            "host": native.host,
            "tcp_port": native.port,
            "user": native.user,
            "password": native.password,
            "database": native.database,
        },
    )
    make_version(
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
    now = datetime.now(UTC).replace(microsecond=0)
    store.execute(
        "INSERT INTO spans (id, project_id, org_id, trace_id, trace_session_id, "
        "start_time, end_time, created_at, updated_at, attrs_number) "
        "SELECT toString(number), toUUID(%(project)s), toUUID(%(org)s), toString(number), "
        "reinterpretAsUUID(toFixedString(toString(number), 16)), "
        "toDateTime64(%(now)s, 6, 'UTC') - toIntervalMinute(120 + number * 7) AS start_time, "
        "start_time + toIntervalSecond(1), start_time, start_time, map('resolved', toFloat64(2)) "
        "FROM numbers(1400)",
        {
            "project": str(observe_project.id),
            "org": str(workspace.organization_id),
            "now": now,
        },
    )
    live_writer.lease.release()
    publish = RefreshWriter.stage_and_publish

    def yield_after_history_checkpoint(self, attempt, **kwargs):
        head = publish(self, attempt, **kwargs)
        if attempt.purpose == "backfill":
            self.lease.deadline = monotonic() - 1
        return head

    monkeypatch.setattr(
        admission._ScheduledRefreshWriter,
        "stage_and_publish",
        yield_after_history_checkpoint,
    )
    definition = backfill.pending_versions(observe_project.id).get()
    positions, heads = [], []
    for reservation_number in range(2):
        started = monotonic()
        (reservation,) = admission.reserve_projects()
        assert not admission.execute_reservation(reservation.payload())
        state = MetricProjectRefresh.for_project(observe_project.id)
        definition.refresh_from_db()
        # The 1,400 owners overflow the 1,000-item admission cap: the initial
        # history is a paged backfill sweep, never a diverted repair.
        assert state.head_manifest_id and not state.pending_repair
        assert state.reconciliation_cursor is None
        assert state.lease_token is None and state.consecutive_failures == 0
        assert state.head_manifest.published_by.purpose == "backfill"
        positions.append(definition.backfill_cursor["coverage"]["scanned"])
        heads.append(state.head_manifest_id)
        if reservation_number == 0:
            # Durable progress, nothing consumed or activated before completion.
            assert definition.backfill_status == "running"
            assert definition.activated_at is None and state.pending_backfill
            assert state.watermark_ns == 0
        else:
            definition.metric.refresh_from_db()
            assert definition.backfill_status == "complete"
            assert definition.metric.active_version_id == definition.id
            assert not state.pending_backfill and state.watermark_ns
        assert monotonic() - started < 60
    assert positions == [1000, 1400]
    assert heads[0] != heads[1]
    print(f"LARGE_HISTORY checkpoints={positions}")


@pytest.mark.django_db(transaction=True)
def test_empty_history_skip_keeps_a_nominee_on_the_exclusive_day_boundary(
    live_writer, workspace, observe_project
):
    from tracer.services.derived_metrics.backfill import _initial_cursor, day_page
    from tracer.services.derived_metrics.changes import UnitKey
    from tracer.tests.test_derived_metric_changes_clickhouse import Analytics
    from tracer.tests.test_derived_metric_clickhouse import seed_span

    writer = live_writer
    writer.store.execute(
        "ALTER TABLE spans ADD COLUMN observation_type String DEFAULT ''"
    )
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    definition = make_version(observe_project, workspace)
    now = datetime.now(UTC).replace(microsecond=0)
    session = uuid4()
    seed_span(
        writer.store.client,
        scope,
        "boundary",
        session,
        uuid4(),
        now - timedelta(days=3),
    )
    cursor = _initial_cursor(definition, now=now, retention_days=90)
    analytics = Analytics(writer.store.client)
    empty, proposed = day_page(analytics, writer, None, cursor, page_size=1000)
    assert not empty.units
    assert proposed["done"] == 2
    assert datetime.fromisoformat(proposed["day_end"]) == now - timedelta(days=2)
    page, _ = day_page(analytics, writer, None, proposed, page_size=1000)
    assert UnitKey("conversation", str(session)) in page.units


@pytest.mark.django_db(transaction=True)
def test_bucket_batch_overflow_splits_without_duplicate_output(
    live_writer, workspace, observe_project, settings, monkeypatch
):
    from contextlib import contextmanager

    from tracer.services.derived_metrics.changes import ChangeBatch, UnitKey
    from tracer.services.derived_metrics.frozen_relations import FrozenRelationOverflow
    from tracer.tests.test_derived_metric_clickhouse import seed_span

    settings.METRICS_ENABLED = 1
    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    definition = make_version(
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
    sessions = [uuid4(), uuid4()]
    for index, session in enumerate(sessions):
        seed_span(
            writer.store.client,
            scope,
            f"split-{index}",
            session,
            uuid4(),
            now - timedelta(hours=index + 2),
            resolved=index * 2 + 1,
        )
    capture, sizes = writer._anonymous_bucket_parts, []

    @contextmanager
    def bounded_capture(attempt, definition, fields, retired, params):
        sizes.append(len(params["dirty_starts"]))
        if sizes[-1] > 1:
            raise FrozenRelationOverflow("simulated bounded input overflow")
        with capture(attempt, definition, fields, retired, params) as relation:
            yield relation

    monkeypatch.setattr(writer, "_anonymous_bucket_parts", bounded_capture)
    writer.refresh(
        ChangeBatch(
            (),
            tuple(UnitKey("conversation", str(s)) for s in sessions),
            (),
            (),
            (),
            {"watermark_ns": int(now.timestamp()) * 10**9},
        ),
        [definition],
        scope,
        start=now - timedelta(days=7),
        end=now,
        as_of=now,
    )
    assert sizes == [2, 1, 1]
    result = reads.read_kpi(
        definition.metric,
        {"window_start": now - timedelta(days=7), "window_end": now},
        store=writer.store,
    )
    assert result["current"]["value"] == pytest.approx(2)
    assert result["current"]["eligible_count"] == 2


@pytest.mark.django_db(transaction=True)
def test_real_bucket_budget_overflow_subdivides_under_unchanged_cap(
    live_writer, workspace, observe_project, settings, monkeypatch
):
    from contextlib import contextmanager

    from clickhouse_driver.errors import ServerException

    from tracer.services.derived_metrics import frozen_relations
    from tracer.services.derived_metrics.changes import ChangeBatch, UnitKey
    from tracer.services.derived_metrics.frozen_relations import FrozenRelationOverflow
    from tracer.tests.test_derived_metric_clickhouse import seed_span
    from tracer.tests.test_derived_metric_refresh_clickhouse import bucket_rows

    settings.METRICS_ENABLED = 1
    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    definition = make_version(
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
    sessions = [uuid4(), uuid4()]
    for index, session in enumerate(sessions):
        seed_span(
            writer.store.client,
            scope,
            f"real-split-{index}",
            session,
            uuid4(),
            now - timedelta(hours=index + 2),
            resolved=index * 2 + 1,
        )
    # Each quarter occupies three rows (child, merged, folded); together they
    # need six. Keep the reduced cap fixed for the failed group AND both halves.
    monkeypatch.setattr(frozen_relations, "RELATION_ROWS", 5)
    capture, sizes, overflows = writer._anonymous_bucket_parts, [], []

    @contextmanager
    def observed_capture(attempt, definition, fields, retired, params):
        assert frozen_relations.RELATION_ROWS == 5
        sizes.append(len(params["dirty_starts"]))
        try:
            with capture(attempt, definition, fields, retired, params) as relation:
                yield relation
        except FrozenRelationOverflow as exc:
            assert isinstance(exc.__cause__, ServerException)
            assert exc.__cause__.code == 395
            assert "metric_frozen_relation_budget" in exc.__cause__.message
            overflows.append(exc)
            raise

    monkeypatch.setattr(writer, "_anonymous_bucket_parts", observed_capture)
    head = writer.refresh(
        ChangeBatch(
            (),
            tuple(UnitKey("conversation", str(s)) for s in sessions),
            (),
            (),
            (),
            {"watermark_ns": int(now.timestamp()) * 10**9},
        ),
        [definition],
        scope,
        start=now - timedelta(days=7),
        end=now,
        as_of=now,
    )
    assert sizes == [2, 1, 1] and len(overflows) == 1
    assert frozen_relations.RELATION_ROWS == 5
    rows = [row for row in bucket_rows(writer, head) if not row[-1]]
    assert len(rows) == 2 and sum(row[2] for row in rows) == 2
    result = reads.read_kpi(
        definition.metric,
        {"window_start": now - timedelta(days=7), "window_end": now},
        store=writer.store,
    )
    assert result["current"]["value"] == pytest.approx(2)
    assert result["current"]["eligible_count"] == 2
    assert writer.store.execute(
        "SELECT count() FROM system.tables WHERE startsWith(name, 'metric_frozen_')"
    ) == [(0,)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "interleaving",
    [
        "head_revoked",
        "head_replaced",
        "definition_revoked",
        "metric_deleted",
        "metric_hard_deleted",
    ],
)
def test_expiry_rechecks_lifecycle_after_unlocked_classification(
    live_writer, workspace, observe_project, monkeypatch, interleaving
):
    from django.db import connection

    from tracer.models.derived_metric import (
        DerivedMetric,
        MetricReadManifest,
        MetricRefreshAttempt,
        MetricStateConflict,
    )
    from tracer.services.derived_metrics import retention
    from tracer.services.derived_metrics.deletion_protocol import (
        capture_pin,
        revoke_generations,
    )
    from tracer.services.derived_metrics.publication import pinned_read
    from tracer.tests.test_derived_metric_publication_clickhouse import current_fields
    from tracer.tests.test_derived_metric_retention_clickhouse import (
        seed_fields_and_buckets,
    )

    writer = live_writer
    project_id = writer.lease.project_id
    definition = make_version(observe_project, workspace, active=True)
    now = datetime.now(UTC).replace(microsecond=0)
    head = seed_fields_and_buckets(
        writer,
        definition,
        [
            ("expired", now - timedelta(days=3), 4, "old-user"),
            ("retained", now - timedelta(hours=2), 6, "live-user"),
        ],
    )
    assert len(current_fields(writer, head)) == 2
    pin = capture_pin(
        project_id,
        metric_id=definition.metric_id,
        consumer_kind="snapshot",
        ttl=timedelta(minutes=5),
    )
    classify = retention._classify_recovery
    expected_head, manifest_ids, attempt_ids = [], [], []

    def interleave(*args, **kwargs):
        assert not connection.in_atomic_block
        assert not MetricRefreshAttempt.objects.filter(
            project_id=project_id, state__in=MetricRefreshAttempt.OPEN_STATES
        ).exists(), "the regression requires no predecessor for revocation to abort"
        classified = classify(*args, **kwargs)
        if interleaving == "head_revoked":
            revoke_generations(project_id, reason="erased", manifest_ids=[head.id])
        elif interleaving == "head_replaced":
            writer.compact()
        elif interleaving == "definition_revoked":
            revoke_generations(
                project_id, reason="erased", definition_version_ids=[definition.id]
            )
        elif interleaving == "metric_deleted":
            DerivedMetric._base_manager.filter(id=definition.metric_id).update(
                deleted=True
            )
        else:
            DerivedMetric._base_manager.filter(id=definition.metric_id).delete()
        # Neither an unchanged head id nor a still-valid lease proves that the
        # snapshotted payload may be copied. Check the lease survives revocation.
        writer.lease.check()
        expected_head.append(
            MetricProjectRefresh.for_project(project_id).head_manifest_id
        )
        manifest_ids.extend(
            MetricReadManifest.objects.filter(project_id=project_id).values_list(
                "id", flat=True
            )
        )
        attempt_ids.extend(
            MetricRefreshAttempt.objects.filter(project_id=project_id).values_list(
                "id", flat=True
            )
        )
        return classified

    monkeypatch.setattr(retention, "_classify_recovery", interleave)
    with pytest.raises(MetricStateConflict, match="retention (base|definition)"):
        retention.expire_project(
            writer, [definition], retention_days=lambda: 1, now=now
        )
    assert (
        MetricProjectRefresh.for_project(project_id).head_manifest_id
        == expected_head[0]
    )
    assert set(
        MetricReadManifest.objects.filter(project_id=project_id).values_list(
            "id", flat=True
        )
    ) == set(manifest_ids)
    assert set(
        MetricRefreshAttempt.objects.filter(project_id=project_id).values_list(
            "id", flat=True
        )
    ) == set(attempt_ids), "expiry must not even register a stale successor"
    if interleaving in {"head_revoked", "definition_revoked"}:
        assert not pin.is_usable()
        with pytest.raises(MetricStateConflict, match="expired"):
            with pinned_read(writer.store, project_id, pin=pin):
                pytest.fail("revoked evidence became readable")
        with pytest.raises(MetricStateConflict, match="expired"):
            capture_pin(
                project_id,
                metric_id=definition.metric_id,
                consumer_kind="request",
                ttl=timedelta(minutes=1),
            )


@pytest.mark.django_db(transaction=True)
def test_bucket_consumer_overflow_never_replays_a_captured_group(
    live_writer, monkeypatch
):
    from contextlib import contextmanager

    from tracer.services.derived_metrics.frozen_relations import FrozenRelationOverflow
    from tracer.services.derived_metrics.publication import committed_rows_select
    from tracer.services.derived_metrics.storage import BUCKET_TOTALS, FIELD_VALUES
    from tracer.tests.test_derived_metric_retention_clickhouse import (
        numeric_definition,
        seed_fields_and_buckets,
    )

    writer = live_writer
    definition = numeric_definition(uuid4())
    now = datetime.now(UTC).replace(microsecond=0)
    starts = [now - timedelta(hours=2), now - timedelta(hours=3)]
    head = seed_fields_and_buckets(
        writer,
        definition,
        [(f"consumer-{i}", at, i + 1, "user") for i, at in enumerate(starts)],
    )
    attempt = writer.begin(
        ledger={
            "dirty_after": [
                {"bucket_start": at.replace(minute=at.minute // 15 * 15)}
                for at in starts
            ]
        }
    )
    fields, params = committed_rows_select(
        FIELD_VALUES, head, org_id=writer.org_id, project_id=writer.lease.project_id
    )
    capture, sizes = writer._anonymous_bucket_parts, []
    freeze = writer.freeze
    failure = FrozenRelationOverflow("consumer failure after capture")

    @contextmanager
    def observed_capture(attempt, definition, fields, retired, params):
        sizes.append(len(params["dirty_starts"]))
        with capture(attempt, definition, fields, retired, params) as relation:
            yield relation

    def fail_consumer(attempt, table, *args, **kwargs):
        if table == BUCKET_TOTALS:
            raise failure
        return freeze(attempt, table, *args, **kwargs)

    monkeypatch.setattr(writer, "_anonymous_bucket_parts", observed_capture)
    monkeypatch.setattr(writer, "freeze", fail_consumer)
    with pytest.raises(FrozenRelationOverflow) as raised:
        writer._freeze_buckets(
            attempt, head, definition, fields, params, complete_fields=True
        )
    assert raised.value is failure and sizes == [2]
    assert (
        MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest_id
        == head.id
    )
    assert not list(writer.plans(attempt, kind="bucket_totals"))
