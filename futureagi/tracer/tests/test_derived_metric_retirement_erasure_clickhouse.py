# ruff: noqa: F811 -- shared isolated ClickHouse/PostgreSQL fixtures
"""A copied retirement proposal cannot resurrect private bytes after erasure."""

from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from tracer.models.derived_metric import (
    DerivedMetric,
    MetricAttemptLedgerChunk,
    MetricFencingError,
    MetricProjectRefresh,
    MetricRefreshAttempt,
    MetricStateConflict,
)
from tracer.services.derived_metrics import erasure, retention
from tracer.services.derived_metrics.changes import Dependency
from tracer.services.derived_metrics.deletion_protocol import (
    capture_pin,
    revoke_generations,
)
from tracer.services.derived_metrics.publication import pinned_read
from tracer.services.derived_metrics.storage import (
    FIELD_CHECKPOINT,
    FIELD_VALUES,
    SOURCE_DEPENDENCIES,
    UNIT_STATE,
)
from tracer.tests.test_derived_metric_backfill import make_version
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    field_query,
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_retention_clickhouse import (
    seed_fields_and_buckets,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "operation", ["retention", "subject", "compaction", "ranges", "gc"]
)
def test_live_native_operations_do_not_hold_lifecycle(
    live_writer, workspace, observe_project, monkeypatch, operation
):
    from tracer.tests.test_derived_metric_integration_fences import (
        assert_lifecycle_available,
    )

    writer, now = live_writer, timezone.now()
    definition = make_version(observe_project, workspace, active=True)
    seed_fields_and_buckets(
        writer,
        definition,
        [
            ("old", now - timedelta(days=3), 4, "erase-user"),
            ("keep", now - timedelta(hours=2), 6, "keep-user"),
        ],
    )
    native, calls = writer.store.execute, []

    def unlocked_native(sql, *args, **kwargs):
        assert_lifecycle_available(writer.lease.project_id)
        calls.append(sql)
        return native(sql, *args, **kwargs)

    monkeypatch.setattr(writer.store, "execute", unlocked_native)
    if operation == "retention":
        result = retention.expire_project(
            writer, [definition], retention_days=lambda: 1, now=now
        )
        assert result["purged"]
    elif operation == "subject":
        request = erasure.erase_metric_data(
            organization_id=writer.org_id,
            project_id=writer.lease.project_id,
            scope_kind="users",
            subject_ids=["erase-user"],
        )
        result = retention.purge_by_scope(writer.store, request, writer=writer)
        assert result["anonymous_history_dropped"]
    elif operation in {"compaction", "ranges"}:
        assert writer.compact(server_side=operation == "ranges")
    else:
        writer.scrub_orphans()
    assert calls


@pytest.mark.parametrize(
    "interleaving",
    [
        "erased_after_classification",
        "erased_after_registration",
        "head_replaced",
        "draft_aborted",
        "definition_revoked",
        "metric_deleted",
        "metric_hard_deleted",
    ],
)
def test_retirement_handoff_revalidates_copied_private_recovery(
    live_writer, workspace, observe_project, monkeypatch, interleaving
):
    writer, now = live_writer, timezone.now()
    project_id = writer.lease.project_id
    definition = make_version(observe_project, workspace, active=True)
    head = seed_fields_and_buckets(
        writer,
        definition,
        [
            ("expired-owner", now - timedelta(days=3), 4, "old-user"),
            ("retained-owner", now - timedelta(hours=2), 6, "private-user"),
        ],
    )
    pin = capture_pin(project_id, consumer_kind="snapshot", ttl=timedelta(minutes=5))
    draft = writer.begin(
        ledger={
            "dirty_before": [{"unit_id": "retained-owner"}],
            "membership_preimage": [
                Dependency(
                    "private-trace",
                    "private-span",
                    "retained-owner",
                    "retained-owner",
                    "private-user",
                    "private-user",
                    now - timedelta(hours=2),
                ).payload()
            ],
        }
    )
    sql, params = field_query(
        "retained-owner",
        n=8,
        start=now - timedelta(hours=2),
        user="private-user",
        metric=definition.metric_id,
    )
    writer.freeze(draft, FIELD_VALUES, sql, params)
    classify, freeze = retention._classify_recovery, writer.freeze
    attempts_after_interleaving, chunks_after_interleaving = [], []
    completed = []

    def invalidate():
        assert not connection.in_atomic_block
        if interleaving.startswith("erased_"):
            job = erasure.erase_metric_data(
                organization_id=writer.org_id,
                project_id=project_id,
                scope_kind="project",
            )
            assert erasure.process_erasure(job.id, store=writer.store)
            job.refresh_from_db()
            assert job.status == "done" and job.completed_at is not None
            completed.append(job.id)
            assert not MetricAttemptLedgerChunk.objects.filter(
                attempt__project_id=project_id
            ).exists()
        elif interleaving == "head_replaced":
            writer.compact()
        elif interleaving == "draft_aborted":
            MetricRefreshAttempt.abort(
                draft.id, reason="interleaved", **writer.lease.check()
            )
        elif interleaving == "definition_revoked":
            revoke_generations(
                project_id, reason="erased", definition_version_ids=[definition.id]
            )
        elif interleaving == "metric_deleted":
            DerivedMetric._base_manager.filter(pk=definition.metric_id).update(
                deleted=True
            )
        else:
            DerivedMetric._base_manager.filter(pk=definition.metric_id).delete()
        if interleaving.startswith("erased_"):
            # Whole-project erasure also fences discovery that had no draft yet.
            with pytest.raises(MetricFencingError):
                writer.lease.check()
        else:
            writer.lease.check()
        attempts_after_interleaving.extend(
            MetricRefreshAttempt.objects.filter(project_id=project_id).values_list(
                "id", flat=True
            )
        )
        chunks_after_interleaving.extend(
            MetricAttemptLedgerChunk.objects.filter(
                attempt__project_id=project_id
            ).values_list("id", flat=True)
        )

    def classified_then_invalidated(*args, **kwargs):
        classified = classify(*args, **kwargs)
        # Make the race discriminating: the stale in-memory ledger really holds
        # a retained subject/preimage, not merely anonymous bucket identifiers.
        assert "private-user" in str(classified[0]["membership_preimage"])
        if interleaving != "erased_after_registration":
            invalidate()
        return classified

    def registered_then_erased(attempt, *args, **kwargs):
        assert attempt.id != draft.id
        assert attempt.ledger_chunks.filter(part="membership_preimage").exists()
        invalidate()
        return freeze(attempt, *args, **kwargs)

    monkeypatch.setattr(retention, "_classify_recovery", classified_then_invalidated)
    if interleaving == "erased_after_registration":
        monkeypatch.setattr(writer, "freeze", registered_then_erased)
    expected = (
        MetricFencingError
        if interleaving == "erased_after_registration"
        else MetricStateConflict
    )
    with pytest.raises(expected):
        retention.retirement_plan(
            writer, draft, head, [definition], now - timedelta(days=1)
        )
    assert attempts_after_interleaving
    assert set(
        MetricRefreshAttempt.objects.filter(project_id=project_id).values_list(
            "id", flat=True
        )
    ) == set(attempts_after_interleaving), "a new attempt appeared after invalidation"
    assert set(
        MetricAttemptLedgerChunk.objects.filter(
            attempt__project_id=project_id
        ).values_list("id", flat=True)
    ) == set(chunks_after_interleaving), "private bytes appeared after invalidation"
    if interleaving.startswith("erased_"):
        assert len(completed) == 1
        assert not MetricRefreshAttempt.objects.filter(
            project_id=project_id, state__in=MetricRefreshAttempt.OPEN_STATES
        ).exists()
        assert not pin.is_usable()
        with pytest.raises(MetricStateConflict, match="expired"):
            with pinned_read(writer.store, project_id, pin=pin):
                pytest.fail("erased evidence became readable")
        for table in (FIELD_VALUES, FIELD_CHECKPOINT, UNIT_STATE, SOURCE_DEPENDENCIES):
            assert writer.store.execute(
                f"SELECT count() FROM {table.name} WHERE project_id = %(project)s",
                {"project": str(project_id)},
            ) == [(0,)]
    else:
        assert MetricProjectRefresh.for_project(project_id).head_manifest_id
