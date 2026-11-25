# ruff: noqa: F811 -- shared isolated PG/CH fixtures
"""Busy-slot acceptance with real PG/CH and the unchanged real pass deadline."""

import json
from datetime import UTC, datetime, timedelta
from time import monotonic

import pytest

from tracer.models.derived_metric import MetricProjectRefresh, MetricRefreshAttempt
from tracer.services.derived_metrics import refresh
from tracer.services.derived_metrics.admission import PASS_SECONDS
from tracer.services.derived_metrics.lease import MetricPassYield, project_lease
from tracer.services.derived_metrics.publication import ClickHousePublicationStore
from tracer.services.derived_metrics.queries import Scope
from tracer.tests._metric_busy_fixture import _batch, _definitions, _seed, _truth_check
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    metric_ch,
    publication_store,
)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("owners", [3200, 1601])
def test_dense_mixed_work_is_bounded_without_false_pause(
    owners, publication_store, workspace, observe_project, user
):
    assert PASS_SECONDS == 540
    assert refresh.pass_soft_margin_seconds() == 200
    scope = Scope(workspace.organization_id, workspace.pk, observe_project.pk)
    definitions = _definitions(observe_project, workspace, user)
    now = datetime.now(UTC).replace(microsecond=0)
    quarter = (now - timedelta(hours=2)).replace(minute=0, second=0)
    kwargs = {
        "start": now - timedelta(days=2),
        "end": now,
        "as_of": now,
        "settle_minutes": 1,
    }

    def writer_for(lease):
        lease.deadline = monotonic() + PASS_SECONDS
        return refresh.RefreshWriter(
            lease,
            ClickHousePublicationStore(publication_store.client, lease=lease),
            org_id=scope.organization,
            workspace_id=scope.workspace,
            retention_days=lambda: 90,
        )

    with project_lease(scope.project, holder="guard-initial") as lease:
        writer = writer_for(lease)
        initial, initial_truth = _seed(
            writer, scope, quarter - timedelta(hours=1), 1, label="initial"
        )
        head = writer.refresh(_batch(initial, 1), definitions, scope, **kwargs)
        assert _truth_check(writer.store, head, definitions, scope, initial_truth)[
            "all_match"
        ]
        dependencies, dense_truth = _seed(writer, scope, quarter, owners, label="dense")

    from tracer.services.derived_metrics import busy_guard

    began = monotonic()
    for number in range(busy_guard.max_yields()):
        with project_lease(scope.project, holder="guard-dense") as lease:
            writer = writer_for(lease)
            before = busy_guard.frontier(
                MetricProjectRefresh.for_project(scope.project)
            )
            try:
                writer.refresh(
                    _batch(dependencies[:1], 2), definitions, scope, **kwargs
                )
            except MetricPassYield:
                busy_guard.record_yield(lease, before)
        state = MetricProjectRefresh.for_project(scope.project)
        assert getattr(writer, "pass_captures", 0) > 0
        if state.watermark_ns == 2:
            break
        assert state.busy_yields == number + 1
        assert bool(state.paused_reason) == (number + 1 == busy_guard.max_yields())
    assert owners != 1601 or state.watermark_ns == 2
    print(
        "BUSY_DENSE "
        + json.dumps(
            {
                "owners": owners,
                "seconds": monotonic() - began,
                "paused": state.paused_reason,
                "watermark": state.watermark_ns,
                "conversation_captures": getattr(writer, "pass_captures", 0),
            }
        ),
        flush=True,
    )
    if state.paused_reason:
        assert owners == 3200 and state.paused_reason == "too_busy"
        assert state.head_manifest_id == head.id and state.watermark_ns == 1
        # All bounded attempts ran real work. The prior cut remains untouched.
        latest = MetricRefreshAttempt.objects.filter(project_id=scope.project).latest(
            "generation"
        )
        assert latest.state == MetricRefreshAttempt.State.ABORTED
        assert latest.abort_reason == refresh.PASS_YIELD
        assert {
            entry["unit_id"]
            for chunk in latest.ledger_chunks.filter(part="dirty_after")
            for entry in chunk.payload
            if entry.get("unit_id")
        } == set(dense_truth)
        assert state.busy_counts["owners"] == owners
        assert state.busy_counts["captures"] > 0
        assert state.busy_yields == busy_guard.max_yields()
        # Compare the frozen prior values to their independent seed oracle.
        # New raw spans are deliberately NOT yet part of the serving head.
        from tracer.services.derived_metrics.publication import committed_rows_select
        from tracer.services.derived_metrics.storage import FIELD_VALUES

        sql, params = committed_rows_select(
            FIELD_VALUES,
            state.head_manifest,
            org_id=scope.organization,
            project_id=scope.project,
        )
        values = publication_store.execute(
            f"SELECT toString(field_id), session_id, value_number FROM ({sql}) WHERE is_deleted = 0",
            params,
        )
        assert len(values) == 26
        for index, definition in enumerate(definitions):
            assert [
                (session, value)
                for field, session, value in values
                if field == str(definition.metric_id)
            ] == [
                (session, source[f"value_{index}"])
                for session, source in initial_truth.items()
            ]
    else:
        assert not state.paused_reason
        assert state.head_manifest_id != head.id and state.watermark_ns == 2
        assert _truth_check(
            publication_store,
            state.head_manifest,
            definitions,
            scope,
            {
                **initial_truth,
                **dense_truth,
            },
        )["all_match"]


@pytest.mark.django_db(transaction=True)
def test_busy_pause_keeps_real_expiry_and_user_erasure_running(
    live_writer, workspace, observe_project
):
    from types import SimpleNamespace
    from uuid import uuid4

    from tracer.models.derived_metric import DerivedMetricVersion
    from tracer.services.derived_metrics import admission, retention
    from tracer.tests.test_derived_metric_refresh_clickhouse import definition_for
    from tracer.tests.test_derived_metric_retention_clickhouse import (
        current_fields,
        numeric_definition,
        seed_fields_and_buckets,
    )

    writer = live_writer
    definition = definition_for(writer, workspace, observe_project)
    template = numeric_definition(definition.metric_id)
    DerivedMetricVersion.objects.filter(pk=definition.pk).update(
        formula=template.formula,
        inputs=template.inputs,
    )
    definition.refresh_from_db()
    now = datetime.now(UTC)
    old, erased, kept, subject = [str(uuid4()) for _ in range(4)]
    seed_fields_and_buckets(
        writer,
        definition,
        [
            (old, now - timedelta(days=2), 4, "old"),
            (erased, now - timedelta(hours=1), 6, subject),
            (kept, now - timedelta(hours=1), 8, "other"),
        ],
    )
    writer.compact()
    MetricProjectRefresh.objects.filter(project_id=observe_project.pk).update(
        paused_reason="too_busy",
        paused_at=now,
        busy_counts={
            "owners": 3200,
            "estimated_captures": 176,
            "capture_capacity": 136,
        },
    )
    before = MetricProjectRefresh.for_project(observe_project.pk)
    writer.retention_days = lambda: 1
    admission.run_expiry_pass(writer)
    state = MetricProjectRefresh.for_project(observe_project.pk)
    assert (
        state.paused_reason == "too_busy" and state.watermark_ns == before.watermark_ns
    )
    assert {
        row[0] for row in current_fields(writer, state.head_manifest) if not row[2]
    } == {erased, kept}
    for table in ("metric_field_values", "metric_field_values_checkpoint"):
        assert writer.store.execute(
            f"SELECT count() FROM {table} WHERE unit_id = %(unit)s", {"unit": old}
        ) == [(0,)]
    request = SimpleNamespace(
        id=uuid4(),
        organization_key=writer.org_id,
        project_key=observe_project.pk,
        scope_kind="users",
        subject_ids=[subject],
    )
    result = retention.purge_by_scope(writer.store, request, writer=writer)
    assert result["anonymous_history_dropped"]
    state = MetricProjectRefresh.for_project(observe_project.pk)
    assert (
        state.paused_reason == "too_busy" and state.watermark_ns == before.watermark_ns
    )
    assert {
        row[0] for row in current_fields(writer, state.head_manifest) if not row[2]
    } == {kept}
    for table in ("metric_field_values", "metric_field_values_checkpoint"):
        assert writer.store.execute(
            f"SELECT count() FROM {table} WHERE user_id = %(user)s", {"user": subject}
        ) == [(0,)]


@pytest.mark.django_db(transaction=True)
def test_native_publication_resets_streak_and_preserves_busy_finality(
    live_writer, workspace, observe_project, user
):
    from tracer.services.derived_metrics import changes, reads

    scope = Scope(workspace.organization_id, workspace.pk, observe_project.pk)
    definitions = _definitions(observe_project, workspace, user)
    now = datetime.now(UTC).replace(microsecond=0)
    at = (now - timedelta(hours=2)).replace(minute=0, second=0)
    writer = live_writer
    writer.retention_days = lambda: 90
    deps, truth = _seed(writer, scope, at, 1, label="resumed")
    MetricProjectRefresh.objects.filter(project_id=scope.project).update(busy_yields=2)
    head = writer.refresh(
        _batch(deps, changes.ns(now - timedelta(minutes=30))),
        definitions,
        scope,
        start=now - timedelta(days=2),
        end=now,
        as_of=now,
        settle_minutes=1,
    )
    assert _truth_check(writer.store, head, definitions, scope, truth)["all_match"]
    state = MetricProjectRefresh.for_project(scope.project)
    assert not state.paused_reason and state.busy_yields == 0
    MetricProjectRefresh.objects.filter(pk=state.pk).update(
        paused_reason="too_busy", paused_at=now
    )
    finality = reads.read_finality(
        writer.store,
        definitions[0].metric,
        definitions[0],
        head,
        {"window_start": at, "window_end": now},
    )
    assert finality.final(at)
    assert finality.reason({"window_start": now}) == "too_busy"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("level", ["conversation", "span"])
@pytest.mark.parametrize("publishes_first", [False, True])
def test_native_yield_backstop_counts_only_nonprogressing_passes(
    live_writer, workspace, observe_project, settings, level, publishes_first
):
    """Fault injection at the soft boundary; the dense sizing test is unforced.

    Every turn gets a real lease and writer against PG/CH. A native publication
    before the injected yield must break the streak, for either metric level.
    """
    from uuid import uuid4

    from tracer.services.derived_metrics import admission, changes
    from tracer.tests.test_derived_metric_refresh_clickhouse import (
        definition_for,
        seed_span_revision,
        span_definition_for,
    )

    settings.METRICS_ENABLED = 1
    settings.METRICS_MAX_CONCURRENT_PROJECTS = 1
    scope = Scope(workspace.organization_id, workspace.pk, observe_project.pk)
    definition = (
        span_definition_for(live_writer, workspace, observe_project)
        if level == "span"
        else definition_for(live_writer, workspace, observe_project)
    )
    now = datetime.now(UTC)
    at = now - timedelta(hours=2)
    trace, session = uuid4(), uuid4()
    seed_span_revision(
        live_writer, scope, trace, "busy", session, at, 7, revision=changes.ns(at)
    )
    dependencies = [
        changes.Dependency(str(trace), "busy", str(session), str(session), "", "", at)
    ]
    cursor = changes.ns(now - timedelta(minutes=30))
    options = {
        "start": at - timedelta(hours=1),
        "end": now,
        "as_of": now,
        "settle_minutes": 1,
    }
    live_writer.retention_days = lambda: 90
    initial = live_writer.refresh(
        _batch(dependencies, cursor), [definition], scope, **options
    )
    live_writer.lease.release()
    for number in range(3):
        (reservation,) = admission.reserve_projects()
        assert reservation.grant.project_id == scope.project

        def run(lease, purpose, *, number=number):
            writer = refresh.RefreshWriter(
                lease,
                ClickHousePublicationStore(live_writer.store.client, lease=lease),
                org_id=scope.organization,
                workspace_id=scope.workspace,
                retention_days=lambda: 90,
            )
            if publishes_first:
                writer.refresh(
                    _batch(dependencies, cursor + number + 1),
                    [definition],
                    scope,
                    **options,
                )
                attempt = writer.begin()
                lease.deadline = monotonic() + 100
                writer.yield_if_late(attempt)
            else:
                lease.deadline = monotonic() + 100
                writer.refresh(
                    _batch(dependencies, cursor + number + 1),
                    [definition],
                    scope,
                    **options,
                )

        assert not admission.execute_reservation(reservation.payload(), runner=run)
        state = MetricProjectRefresh.for_project(scope.project)
        if publishes_first:
            assert state.head_manifest_id != initial.id
            assert state.watermark_ns == cursor + number + 1
            assert not state.paused_reason and state.busy_yields == 0
        else:
            assert state.head_manifest_id == initial.id and state.watermark_ns == cursor
            assert state.busy_yields == number + 1
    assert bool(state.paused_reason) is (not publishes_first)
    if not publishes_first:
        assert state.paused_reason == "too_busy"
        assert state.busy_counts["captures"] == 0
