# ruff: noqa: F811 -- shared isolated PG/CH fixtures
"""Checkpoint regressions for observed-work admission and no-head cleanup."""

from datetime import UTC, datetime, timedelta
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tracer.models.derived_metric import (
    MetricAttemptLedgerChunk,
    MetricErasureRequest,
    MetricProjectRefresh,
    MetricRefreshAttempt,
)
from tracer.services.derived_metrics import admission, busy_guard, refresh, retention
from tracer.services.derived_metrics.lease import MetricPassYield, project_lease
from tracer.services.derived_metrics.publication import ClickHousePublicationStore
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.storage import TABLES
from tracer.tests._metric_busy_fixture import _batch, _definitions, _seed, _truth_check
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    metric_ch,
    publication_store,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_resume_attempts_fitting_native_work_even_with_pessimistic_capacity(
    publication_store, workspace, observe_project, user, settings
):
    settings.METRICS_ENABLED = 1
    settings.METRICS_BUSY_CAPTURE_MS = 60000
    scope = Scope(workspace.organization_id, workspace.pk, observe_project.pk)
    definitions = _definitions(observe_project, workspace, user)
    now = datetime.now(UTC).replace(microsecond=0)
    state = MetricProjectRefresh.for_project(scope.project)
    MetricProjectRefresh.objects.filter(pk=state.pk).update(
        paused_reason="too_busy",
        paused_at=now,
        busy_yields=3,
        last_started_at=now - timedelta(minutes=2),
    )
    assert admission.request_refresh(scope.project)
    with project_lease(scope.project, holder="guard-fitting-resume") as lease:
        lease.deadline = monotonic() + admission.PASS_SECONDS
        writer = refresh.RefreshWriter(
            lease,
            ClickHousePublicationStore(publication_store.client, lease=lease),
            org_id=scope.organization,
            workspace_id=scope.workspace,
            retention_days=lambda: 90,
        )
        dependencies, truth = _seed(
            writer, scope, now - timedelta(hours=2), 1, label="fit"
        )
        head = writer.refresh(
            _batch(dependencies, 1),
            definitions,
            scope,
            start=now - timedelta(days=2),
            end=now,
            as_of=now,
            settle_minutes=1,
        )
        assert _truth_check(writer.store, head, definitions, scope, truth)["all_match"]
        state.refresh_from_db()
        assert state.watermark_ns == 1 and not state.paused_reason
        assert state.busy_yields == 0 and writer.pass_captures == 25


@pytest.mark.parametrize("scope_kind", ["expiry", "users", "sessions"])
def test_paused_no_head_frozen_span_draft_is_physically_scrubbed(
    live_writer, workspace, observe_project, user, settings, monkeypatch, scope_kind
):
    """Real span capture, soft yield, no head and no pre-existing policy debt."""
    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.pk, observe_project.pk)
    definitions = _definitions(observe_project, workspace, user)
    now = datetime.now(UTC).replace(microsecond=0)
    writer.retention_days = lambda: 90
    deps, _ = _seed(writer, scope, now - timedelta(days=2), 1, label="draft")
    original = writer.yield_if_late

    def yield_after_span(attempt):
        if getattr(writer, "pass_span_captures", 0):
            writer.lease.deadline = monotonic() + 100
        original(attempt)

    monkeypatch.setattr(writer, "yield_if_late", yield_after_span)
    with pytest.raises(MetricPassYield):
        writer.refresh(
            _batch(deps, 1),
            definitions,
            scope,
            start=now - timedelta(days=3),
            end=now,
            as_of=now,
            settle_minutes=1,
        )
    attempt = MetricRefreshAttempt.objects.filter(project_id=scope.project).latest(
        "generation"
    )
    assert attempt.abort_reason == refresh.PASS_YIELD
    chunks = MetricAttemptLedgerChunk.objects.filter(attempt=attempt)
    assert chunks.filter(part="cleanup_intent").exists()
    assert not MetricErasureRequest.objects.filter(
        project_key=scope.project, store_payload__protocol="metric_policy_replan"
    ).exists()
    # A synchronous late write already accepted by CH is another durable copy.
    # Cleanup must erase native bytes as well as the frozen PG span payload.
    writer.lease.deadline = monotonic() + admission.PASS_SECONDS
    params = {
        "org": str(scope.organization),
        "project": str(scope.project),
        "attempt": str(attempt.pk),
        "generation": attempt.generation,
    }
    insert = "INSERT INTO metric_field_values (org_id, project_id, attempt_id, generation, unit_id, user_id) VALUES (%(org)s, %(project)s, %(attempt)s, %(generation)s, 'draft-private', 'draft-user')"
    writer.store.execute(insert, params)
    MetricProjectRefresh.objects.filter(project_id=scope.project).update(
        paused_reason="too_busy",
        paused_at=now,
        busy_yields=3,
    )
    settings.METRICS_ENABLED = 0
    writer.retention_days = lambda: 1
    if scope_kind == "expiry":
        admission.run_expiry_pass(writer)
    else:
        result = retention.purge_by_scope(
            writer.store,
            SimpleNamespace(
                id=uuid4(),
                organization_key=scope.organization,
                project_key=scope.project,
                scope_kind=scope_kind,
                subject_ids=[
                    "draft-user"
                    if scope_kind == "users"
                    else deps[0].canonical_session_id
                ],
            ),
            writer=writer,
        )
        assert result["anonymous_history_dropped"]
    assert not chunks.exists()
    for table in TABLES.values():
        assert writer.store.execute(
            f"SELECT count() FROM {table.name} WHERE org_id = toUUID(%(org)s) AND project_id = toUUID(%(project)s)",
            params,
        ) == [(0,)]
    state = MetricProjectRefresh.for_project(scope.project)
    assert state.head_manifest_id is None and state.watermark_ns == 0
    assert state.paused_reason == "too_busy" and state.pending_repair
    assert not MetricRefreshAttempt.objects.filter(
        project_id=scope.project,
        state__in=MetricRefreshAttempt.OPEN_STATES,
    ).exists()
    assert (
        not MetricErasureRequest.objects.filter(
            project_key=scope.project,
            store_payload__protocol="metric_policy_replan",
        )
        .exclude(status="done")
        .exists()
    )


def test_dense_native_nonprogress_pauses_then_reduced_work_resumes_exactly_once(
    publication_store, workspace, observe_project, user, settings
):
    """Real pass deadlines and native IO: 400 captures within the dependency cap."""
    settings.METRICS_ENABLED = 1
    settings.METRICS_MAX_CONCURRENT_PROJECTS = 1
    scope = Scope(workspace.organization_id, workspace.pk, observe_project.pk)
    definitions = _definitions(observe_project, workspace, user)
    now = datetime.now(UTC).replace(microsecond=0)
    kwargs = {
        "start": now - timedelta(days=2),
        "end": now,
        "as_of": now,
        "settle_minutes": 1,
    }
    with project_lease(scope.project, holder="guard-stall-seed") as lease:
        writer = refresh.RefreshWriter(
            lease,
            ClickHousePublicationStore(publication_store.client, lease=lease),
            org_id=scope.organization,
            workspace_id=scope.workspace,
            retention_days=lambda: 90,
        )
        deps, truth = _seed(
            writer, scope, now - timedelta(hours=2), 8000, label="stalled"
        )
    captures = []
    for number in range(busy_guard.max_yields()):
        (reservation,) = admission.reserve_projects()

        def run(lease, purpose):
            writer = refresh.RefreshWriter(
                lease,
                ClickHousePublicationStore(publication_store.client, lease=lease),
                org_id=scope.organization,
                workspace_id=scope.workspace,
                retention_days=lambda: 90,
            )
            try:
                writer.refresh(_batch(deps[:1], 1), definitions, scope, **kwargs)
            finally:
                captures.append(getattr(writer, "pass_captures", 0))

        assert not admission.execute_reservation(reservation.payload(), runner=run)
        state = MetricProjectRefresh.for_project(scope.project)
        assert captures[-1] > 0, "must attempt work, not reject a fixed owner count"
        assert state.watermark_ns == 0 and state.head_manifest_id is None
        assert state.busy_yields == number + 1
        assert bool(state.paused_reason) == (number + 1 == busy_guard.max_yields())
    assert state.paused_reason == "too_busy"
    assert not admission.reserve_projects()
    # A real workload reduction: remove 24 conversation definitions. The span
    # and remaining conversation metric still include EVERY original owner.
    for definition in definitions[1:-1]:
        type(definition.metric).objects.filter(pk=definition.metric_id).update(
            deleted=True
        )
    definitions = [definitions[0], definitions[-1]]
    resume_at = (
        state.last_started_at + admission.RUN_NOW_THROTTLE + timedelta(seconds=1)
    )
    assert admission.request_refresh(scope.project, clock=lambda: resume_at)
    with project_lease(scope.project, holder="guard-stall-resume") as lease:
        lease.deadline = monotonic() + admission.PASS_SECONDS
        writer = refresh.RefreshWriter(
            lease,
            ClickHousePublicationStore(publication_store.client, lease=lease),
            org_id=scope.organization,
            workspace_id=scope.workspace,
            retention_days=lambda: 90,
        )
        # Only the old recovery ledger nominates the remaining 7,999 owners.
        head = writer.refresh(_batch(deps[:1], 1), definitions, scope, **kwargs)
        assert _truth_check(writer.store, head, definitions, scope, truth)["all_match"]
    state.refresh_from_db()
    assert (
        state.watermark_ns == 1 and not state.paused_reason and state.busy_yields == 0
    )
    print(
        "BUSY_NATIVE_STALL",
        {"captures_per_pass": captures, "owners": len(truth)},
        flush=True,
    )


def test_no_head_busy_expiry_keeps_debt_until_late_writes_drain(live_writer):
    from tracer.services.derived_metrics.deletion_protocol import _intent
    from tracer.tests.test_derived_metric_publication_clickhouse import prepare_field

    writer = live_writer
    now = datetime.now(UTC)
    attempt, _ = prepare_field(writer, start=now - timedelta(days=2))
    MetricRefreshAttempt.abort(
        attempt.pk, reason=refresh.PASS_YIELD, **writer.lease.check()
    )
    MetricProjectRefresh.objects.filter(project_id=writer.lease.project_id).update(
        paused_reason="too_busy",
        paused_at=now,
    )
    writer.retention_days = lambda: 1
    submission = _intent(
        organization_id=writer.org_id,
        project_id=writer.lease.project_id,
        scope="history",
        store_payload={
            "protocol": "metric_submission",
            "generation": attempt.generation,
            "attempt": str(attempt.pk),
            "shard": writer.store.identity(),
        },
    )
    admission.run_expiry_pass(writer)
    debt = MetricErasureRequest.objects.get(
        deletion_key=f"retention-replan:{attempt.pk}"
    )
    assert debt.status != "done"
    assert MetricProjectRefresh.for_project(writer.lease.project_id).pending_expiry
    assert not MetricAttemptLedgerChunk.objects.filter(attempt=attempt).exists()
    params = {
        "org": str(writer.org_id),
        "project": str(writer.lease.project_id),
        "attempt": str(attempt.pk),
        "generation": attempt.generation,
    }
    insert = "INSERT INTO metric_field_values (org_id, project_id, attempt_id, generation, unit_id) VALUES (%(org)s, %(project)s, %(attempt)s, %(generation)s, 'late-private')"
    # The old write lands after its first scrub. A done flag alone cannot drain it.
    writer.store.execute(insert, params)
    MetricErasureRequest.objects.filter(pk=submission.pk).update(
        status="done", completed_at=now
    )
    admission.run_expiry_pass(writer)
    debt.refresh_from_db()
    assert debt.status != "done"
    writer.store.execute(insert, params)
    for control in (
        {**params, "project": str(uuid4())},
        {**params, "org": str(uuid4())},
        {**params, "generation": attempt.generation + 1},
    ):
        writer.store.execute(insert, control)
    MetricErasureRequest.objects.filter(pk=submission.pk).update(
        receipts={"synchronous_response": {"drained": 1}},
    )
    admission.run_expiry_pass(writer)
    debt.refresh_from_db()
    assert debt.status == "done"
    assert writer.store.execute(
        "SELECT count() FROM metric_field_values WHERE unit_id = 'late-private' "
        "AND org_id = %(org)s AND project_id = %(project)s AND generation <= %(generation)s",
        params,
    ) == [(0,)]
    assert writer.store.execute(
        "SELECT count() FROM metric_field_values WHERE unit_id = 'late-private'"
    ) == [(3,)]
    state = MetricProjectRefresh.for_project(writer.lease.project_id)
    assert state.head_manifest_id is None and state.watermark_ns == 0
    assert (
        state.pending_repair
        and not state.pending_expiry
        and state.paused_reason == "too_busy"
    )
