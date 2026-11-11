# ruff: noqa: F811 -- shared isolated PostgreSQL/ClickHouse fixtures
"""An erasure completion invalidates cached discovery even before registration."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import (
    MetricAttemptLedgerChunk,
    MetricFencingError,
    MetricProjectRefresh,
    MetricRefreshAttempt,
    MetricStateConflict,
)
from tracer.services.derived_metrics import admission, backfill, erasure, retention
from tracer.services.derived_metrics.changes import (
    Dependency,
    MetricReadBudget,
    _batch,
    _scope,
)
from tracer.services.derived_metrics.deletion_protocol import capture_pin
from tracer.services.derived_metrics.lease import ProjectLease
from tracer.services.derived_metrics.publication import pinned_read
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.refresh import RefreshWriter
from tracer.services.derived_metrics.storage import TABLES
from tracer.tests.test_derived_metric_backfill import make_version
from tracer.tests.test_derived_metric_changes_clickhouse import Analytics
from tracer.tests.test_derived_metric_clickhouse import seed_span
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_refresh_clickhouse import change_for

pytestmark = pytest.mark.django_db(transaction=True)


def discovery_case(writer, workspace, project, *, with_head):
    scope = Scope(writer.org_id, workspace.id, project.id)
    writer.store.execute(
        "ALTER TABLE spans ADD COLUMN observation_type String DEFAULT ''"
    )
    definition = make_version(
        project,
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
    now = timezone.now()
    at = now - timedelta(hours=2)
    session, user, trace = uuid4(), uuid4(), uuid4()
    seed_span(
        writer.store.client, scope, "private-span", session, user, at, trace_id=trace
    )
    pin = None
    if with_head:
        dependency = Dependency(
            str(trace),
            "private-span",
            str(session),
            str(session),
            str(user),
            str(user),
            at,
        )
        writer.refresh(
            change_for(session, dependencies=[dependency]),
            [definition],
            scope,
            start=at - timedelta(days=1),
            end=now,
            as_of=now,
        )
        pin = capture_pin(project.id, consumer_kind="request", ttl=timedelta(minutes=5))
    return scope, definition, now, user, pin


def private_state(project_id):
    return (
        set(
            MetricRefreshAttempt.objects.filter(project_id=project_id).values_list(
                "id", flat=True
            )
        ),
        set(
            MetricAttemptLedgerChunk.objects.filter(
                attempt__project_id=project_id
            ).values_list("id", flat=True)
        ),
    )


def assert_purged(writer, before, pin):
    assert private_state(writer.lease.project_id) == before
    assert not MetricAttemptLedgerChunk.objects.filter(
        attempt__project_id=writer.lease.project_id
    ).exists()
    assert not MetricRefreshAttempt.objects.filter(
        project_id=writer.lease.project_id, state__in=MetricRefreshAttempt.OPEN_STATES
    ).exists()
    if pin:
        assert not pin.is_usable()
        with pytest.raises(MetricStateConflict, match="expired"):
            with pinned_read(writer.store, writer.lease.project_id, pin=pin):
                pytest.fail("erased discovery became readable")
    for table in TABLES.values():
        assert writer.store.execute(
            f"SELECT count() FROM {table.name} WHERE project_id = %(project)s",
            {"project": str(writer.lease.project_id)},
        ) == [(0,)]


@pytest.mark.parametrize("scope_kind", ["project", "organization"])
@pytest.mark.parametrize("with_head", [False, True])
def test_erasure_fences_discovery_before_initial_registration(
    live_writer, workspace, observe_project, monkeypatch, scope_kind, with_head
):
    writer = live_writer
    scope, _, now, private_user, pin = discovery_case(
        writer, workspace, observe_project, with_head=with_head
    )
    refresh, observed = writer.refresh, []
    state = MetricProjectRefresh.for_project(observe_project.id)

    def erase_after_discovery(batch, *args, **kwargs):
        assert str(private_user) in str(batch.dependencies)
        if with_head:
            assert str(private_user) in str(batch.prior_dependencies)
        else:
            assert not batch.prior_dependencies and not state.head_manifest_id
        # The source is authoritatively erased BEFORE the derived-store job.
        writer.store.execute(
            "ALTER TABLE spans DELETE WHERE 1 SETTINGS mutations_sync=2"
        )
        job = erasure.erase_metric_data(
            organization_id=writer.org_id,
            project_id=scope.project if scope_kind == "project" else None,
            scope_kind=scope_kind,
        )
        assert erasure.process_erasure(job.id, store=writer.store)
        job.refresh_from_db()
        assert job.status == "done" and job.completed_at
        before = private_state(scope.project)
        observed.append(before)
        with pytest.raises(MetricFencingError):
            writer.lease.check()
        # In particular, there must be no independently committed membership
        # preimage chunk after this call rejects the pre-erasure discovery epoch.
        return refresh(batch, *args, **kwargs)

    monkeypatch.setattr(writer, "refresh", erase_after_discovery)
    with pytest.raises(MetricFencingError):
        admission.run_incremental(writer, Analytics(writer.store.client), scope, state)
    assert len(observed) == 1
    assert_purged(writer, observed[0], pin)


@pytest.mark.parametrize("scope_kind", ["project", "organization"])
def test_erasure_completion_fences_replacement_lease_discovery_during_purge(
    live_writer, workspace, observe_project, monkeypatch, scope_kind
):
    writer = live_writer
    scope, definition, now, private_user, pin = discovery_case(
        writer, workspace, observe_project, with_head=True
    )
    # Capture the pre-purge committed allowlist, just as run_project_pass does.
    head = MetricProjectRefresh.for_project(scope.project).head_manifest
    initial = backfill.incremental_changes(
        Analytics(writer.store.client), writer, head, [definition], now=now
    )
    assert initial.source_keys
    writer.store.execute("ALTER TABLE spans DELETE WHERE 1 SETTINGS mutations_sync=2")
    job = erasure.erase_metric_data(
        organization_id=writer.org_id,
        project_id=scope.project if scope_kind == "project" else None,
        scope_kind=scope_kind,
    )
    delete, discovered = retention._delete, []

    def discover_on_replacement_lease(*args, **kwargs):
        if not discovered:
            grant = MetricProjectRefresh.take_lease(
                scope.project, holder="during-erasure", ttl=timedelta(minutes=5)
            )
            assert grant is not None
            replacement = RefreshWriter(
                ProjectLease(grant),
                writer.store,
                org_id=writer.org_id,
                workspace_id=workspace.id,
                retention_days=lambda: 0,
            )
            # Nominate the erased identity through the retained committed
            # dependency, not through an upstream row that no longer exists.
            batch = _batch(
                Analytics(writer.store.client),
                initial.source_keys,
                _scope(
                    str(writer.org_id),
                    str(scope.project),
                    [str(a) for _, a in head.allowed_attempts()],
                ),
                MetricReadBudget(),
                {},
            )
            assert str(private_user) in str(batch.prior_dependencies)
            discovered.append((replacement, batch))
            with pytest.raises(MetricStateConflict, match="erasure"):
                replacement.begin(
                    ledger={
                        "membership_preimage": [
                            d.payload() for d in batch.prior_dependencies
                        ]
                    }
                )
        return delete(*args, **kwargs)

    monkeypatch.setattr(retention, "_delete", discover_on_replacement_lease)
    assert erasure.process_erasure(job.id, store=writer.store)
    job.refresh_from_db()
    assert job.status == "done" and job.completed_at
    assert len(discovered) == 1
    replacement, batch = discovered[0]
    before = private_state(scope.project)
    with pytest.raises(MetricFencingError):
        replacement.refresh(
            batch,
            [definition],
            scope,
            start=now - timedelta(days=1),
            end=now,
            as_of=now,
        )
    assert_purged(replacement, before, pin)


@pytest.mark.parametrize("scope_kind", ["project", "organization"])
@pytest.mark.parametrize("with_head", [False, True])
def test_erasure_fences_initial_sweep_page_before_registration(
    live_writer, workspace, observe_project, monkeypatch, scope_kind, with_head
):
    """The paged initial-history sweep is a discovery path of its own.

    Its source page reads private memberships (and, after the first page,
    committed preimages) before writer.refresh registers an attempt. A project
    erasure completing in that window must fence registration exactly like
    ordinary incremental discovery: no new attempt or private chunk survives.
    """
    writer = live_writer
    writer.store.execute(
        "ALTER TABLE spans ADD COLUMN observation_type String DEFAULT ''"
    )
    scope = Scope(writer.org_id, workspace.id, observe_project.id)
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
    at = timezone.now() - timedelta(hours=2)
    session, user, trace = uuid4(), uuid4(), uuid4()
    for span in ("private-span-a", "private-span-b"):
        seed_span(writer.store.client, scope, span, session, user, at, trace_id=trace)
    # Initial history whose exhaustive discovery overflows the admission cap.
    writer.incremental_unit_limit = 0
    runner = backfill.BackfillRunner(
        writer, Analytics(writer.store.client), scope, page_size=1
    )
    if with_head:
        runner.run_pass(max_chunks=1)
        state = MetricProjectRefresh.for_project(observe_project.id)
        assert state.head_manifest_id and state.watermark_ns == 0
        assert backfill.sweep_participants(observe_project.id)
    refresh, observed = writer.refresh, []

    def erase_after_discovery(batch, *args, **kwargs):
        assert str(user) in str(batch.dependencies)
        assert kwargs["publication"]["backfills"]
        writer.store.execute(
            "ALTER TABLE spans DELETE WHERE 1 SETTINGS mutations_sync=2"
        )
        job = erasure.erase_metric_data(
            organization_id=writer.org_id,
            project_id=scope.project if scope_kind == "project" else None,
            scope_kind=scope_kind,
        )
        assert erasure.process_erasure(job.id, store=writer.store)
        job.refresh_from_db()
        assert job.status == "done" and job.completed_at
        observed.append(private_state(scope.project))
        with pytest.raises(MetricFencingError):
            writer.lease.check()
        return refresh(batch, *args, **kwargs)

    monkeypatch.setattr(writer, "refresh", erase_after_discovery)
    with pytest.raises(MetricFencingError):
        runner.run_pass(max_chunks=1)
    assert len(observed) == 1
    assert_purged(writer, observed[0], None)
