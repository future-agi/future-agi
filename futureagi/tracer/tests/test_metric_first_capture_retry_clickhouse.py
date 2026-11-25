# ruff: noqa: F811 -- shared isolated PG/CH fixtures
"""A positively unsent first input capture may retry, never ambiguous writes."""

from contextlib import nullcontext
from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import DerivedMetricVersion, MetricErasureRequest
from tracer.services.derived_metrics import service, snapshots, source_retention
from tracer.tests.test_derived_metric_lifecycle_safety import _metric
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_retention_clickhouse import numeric_definition

pytestmark = pytest.mark.django_db(transaction=True)


def test_preprotocol_fixture_is_refused_before_any_native_work(
    workspace, observe_project, user, monkeypatch
):
    from tracer.tests.test_metric_retention_review_clickhouse import frozen_run

    run = frozen_run(workspace, observe_project, timezone.now(), status="queued")
    monkeypatch.setattr(service, "_authorized_metric", lambda m: True)
    monkeypatch.setattr(service, "_requester_can_run", lambda r: True)
    for invoke in (
        lambda: service.execute_run(run.pk),
        lambda: service.queue_recompute(run, user),
        lambda: service._compute_tracked(run, None),
    ):
        with pytest.raises(
            service.MetricInputError, match="writer precondition failed"
        ):
            invoke()
    run.refresh_from_db()
    assert run.status == "queued" and not run.source_manifest


@pytest.mark.parametrize(
    "receipt", ["not_submitted", "acknowledged", "missing", "dispatching"]
)
def test_first_capture_not_submitted_retries_real_service(
    publication_store, workspace, observe_project, user, monkeypatch, receipt
):
    now = timezone.now()
    metric = _metric(workspace, observe_project)
    DerivedMetricVersion.objects.create(
        metric=metric,
        version=1,
        kind="custom",
        level="conversation",
        formula="mean(y)",
        inputs=numeric_definition(metric.pk).inputs,
        selection_unit="session",
        weighting="none",
        cohort_key="region",
        backfill_status="complete",
        history_mode="history",
    )
    at = now - timedelta(hours=1)
    publication_store.execute(
        "INSERT INTO spans (id, trace_id, project_id, org_id, trace_session_id, "
        "start_time, end_time, created_at, updated_at, _version, attrs_number) VALUES "
        "('retry-member', %(trace)s, %(project)s, %(org)s, %(session)s, "
        "%(at)s, %(at)s, %(at)s, %(at)s, 1, map('y', 7.))",
        {
            "trace": str(uuid4()),
            "project": str(observe_project.pk),
            "org": str(workspace.organization_id),
            "session": str(uuid4()),
            "at": at,
        },
    )
    monkeypatch.setattr(
        snapshots, "snapshot_store", lambda: nullcontext(publication_store)
    )
    monkeypatch.setattr(source_retention, "current_cutoff", lambda *a, **k: None)
    monkeypatch.setattr(service, "_authorized_metric", lambda m: True)
    monkeypatch.setattr(service, "_requester_can_run", lambda r: True)
    run, created = service.create_run(
        metric,
        {
            "window_start": now - timedelta(hours=2),
            "window_end": now,
            "filters": [],
            "idempotency_key": str(uuid4()),
        },
        user,
    )
    assert created

    def unavailable_clock(store):
        raise RuntimeError("fixture clock unavailable before dispatch")

    with monkeypatch.context() as fault:
        fault.setattr(snapshots, "_server_clock", unavailable_clock)
        with pytest.raises(RuntimeError, match="fixture clock unavailable"):
            service.execute_run(run.pk)
    run.refresh_from_db()
    assert run.status == "failed" and run.snapshot_id is not None
    first_namespace = run.snapshot_id
    first = MetricErasureRequest.objects.get(
        run_key=run.pk, store_payload__protocol=snapshots.SUBMISSION_PROTOCOL
    )
    assert first.status == "done"
    assert first.receipts == {"not_submitted": {"drained": 1}}
    assert publication_store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(snapshot)s",
        {"snapshot": str(first_namespace)},
    ) == [(0,)]

    if receipt == "acknowledged":
        first.receipts = {"synchronous_response": {"drained": 1}}
        first.save(update_fields=["receipts"])
    elif receipt == "missing":
        first.delete()
    elif receipt == "dispatching":
        MetricErasureRequest.objects.filter(pk=first.pk).update(
            status="queued",
            receipts={},
            store_payload={**first.store_payload, "phase": "dispatching"},
        )
    service.queue_recompute(run, user)
    if receipt != "not_submitted":
        with pytest.raises(service.MetricInputError, match="Frozen snapshot format"):
            service.execute_run(run.pk)
        run.refresh_from_db()
        assert run.snapshot_id == first_namespace and run.status == "failed"
        return
    service.execute_run(run.pk)
    run.refresh_from_db()
    assert run.status == "complete"
    assert run.snapshot_id != first_namespace
    assert publication_store.execute(
        "SELECT value FROM derived_metric_results WHERE run_id = %(run)s AND rollup = 'overall'",
        {"run": str(run.pk)},
    ) == [(7.0,)]
    assert (
        MetricErasureRequest.objects.filter(
            run_key=run.pk,
            store_payload__protocol=snapshots.SUBMISSION_PROTOCOL,
            status="done",
            receipts__synchronous_response__drained=1,
        ).count()
        == 7
    )
