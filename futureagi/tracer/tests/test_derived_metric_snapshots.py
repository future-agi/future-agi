from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import (
    DerivedMetricRun,
    DerivedMetricVersion,
    MetricErasureRequest,
)
from tracer.services.derived_metrics import snapshots
from tracer.services.derived_metrics.errors import MetricBusyError, MetricInputError
from tracer.services.derived_metrics.service import (
    SnapshotNotSupportedError,
    queue_recompute,
)
from tracer.tests.test_derived_metric_lifecycle_safety import _commit, _metric


@pytest.fixture
def snapshot_metric(workspace, observe_project):
    metric = _metric(workspace, observe_project)
    definition = DerivedMetricVersion.objects.create(
        metric=metric,
        version=1,
        kind="custom",
        level="conversation",
        formula="mean(y)",
        inputs=[
            {"alias": "y", "source": "span", "field": "duration_ms", "reducer": "sum"}
        ],
        selection_unit="session",
        weighting="none",
        definition_hash="a" * 64,
        backfill_status="complete",
    )
    metric.active_version = definition
    metric.save(update_fields=["active_version"])
    _commit(observe_project, timezone.now())
    return metric


def request_data(days=7):
    end = timezone.now() - timedelta(hours=1)
    return {
        "window_start": end - timedelta(days=days),
        "window_end": end,
        "idempotency_key": str(uuid4()),
    }


@pytest.mark.django_db
def test_capture_pins_active_manifest_and_idempotent_window(snapshot_metric, user):
    data = request_data(90)
    first = snapshots.request_snapshot(snapshot_metric, data, user)
    again = snapshots.request_snapshot(snapshot_metric, data, user)
    assert first["id"] == again["id"]
    run = DerivedMetricRun.objects.get(pk=first["id"])
    assert run.definition_id == snapshot_metric.active_version_id
    assert run.pin.manifest_id == run.pinned_manifest_id
    assert run.captured_at == run.pin.captured_at
    assert run.window_end - run.window_start == timedelta(days=90)
    assert run.pin.expires_at - run.pin.captured_at == snapshots.PIN_TTL
    assert run.window_start.minute % 15 == 0
    with pytest.raises(MetricInputError):
        snapshots.request_snapshot(
            snapshot_metric,
            {**data, "window_start": data["window_start"] + timedelta(days=1)},
            user,
        )


@pytest.mark.django_db
@pytest.mark.parametrize("level", ["span", "user", "trace"])
def test_unsupported_grains_create_neither_pin_nor_run(snapshot_metric, user, level):
    from django.test import override_settings

    definition = snapshot_metric.active_version
    unsupported = DerivedMetricVersion.objects.create(
        metric=snapshot_metric,
        version=2,
        **{
            **definition.semantics(),
            "level": level,
            "kind": "average" if level == "user" else "custom",
            "user_rule": "average" if level == "user" else "",
            "definition_hash": "c" * 64,
        },
    )
    snapshot_metric.active_version = unsupported
    snapshot_metric.save(update_fields=["active_version"])
    with override_settings(DERIVED_METRICS_TRACE_ENABLED=False):
        with pytest.raises(SnapshotNotSupportedError):
            snapshots.request_snapshot(snapshot_metric, request_data(), user)
    assert DerivedMetricRun.objects.count() == 0


@pytest.mark.django_db
def test_admission_and_pending_erasure(snapshot_metric, user):
    for _ in range(2):
        snapshots.request_snapshot(snapshot_metric, request_data(), user)
    with pytest.raises(MetricBusyError):
        snapshots.request_snapshot(snapshot_metric, request_data(), user)


@pytest.mark.django_db
def test_delete_queues_parent_independent_cleanup_and_blocks_replay(
    snapshot_metric, user
):
    saved = snapshots.request_snapshot(snapshot_metric, request_data(), user)
    run = DerivedMetricRun.objects.get(pk=saved["id"])
    assert snapshots.delete_snapshot(snapshot_metric, run.id, user) == {
        "status": "cleanup_pending"
    }
    intent = MetricErasureRequest.objects.get(deletion_key=f"run:{run.id}")
    assert intent.organization_id is None and intent.project_id is None
    assert intent.snapshot_key == run.snapshot_id
    assert not run.pin.is_usable()
    with pytest.raises(MetricInputError, match="cannot be rerun"):
        queue_recompute(run, user)


@pytest.mark.django_db
def test_soft_delete_retains_cleanup_intent(snapshot_metric):
    snapshot_metric.deleted = True
    snapshot_metric.save(update_fields=["deleted"])
    intent = MetricErasureRequest.objects.get(
        deletion_key=f"metric:{snapshot_metric.id}"
    )
    assert intent.metric_key == snapshot_metric.id
    assert intent.project_id is None


@pytest.mark.django_db
def test_rejected_submission_keeps_committed_no_dispatch_receipt(snapshot_metric, user):
    from unittest.mock import Mock

    from tracer.models.derived_metric import MetricStateConflict

    saved = snapshots.request_snapshot(snapshot_metric, request_data(), user)
    run = DerivedMetricRun.objects.get(pk=saved["id"])
    store = Mock()
    # Queued (not running) is not an authorized execution attempt.
    with pytest.raises(MetricStateConflict):
        snapshots._write(store, run, "SELECT 1", {}, capturing=True)
    store.client.execute.assert_not_called()
    receipt = MetricErasureRequest.objects.get(
        store_payload__protocol=snapshots.SUBMISSION_PROTOCOL
    )
    assert receipt.status == "done"
    assert receipt.receipts == {"not_submitted": {"drained": 1}}


@pytest.mark.django_db
def test_unproven_legacy_writer_cannot_get_erasure_completion(snapshot_metric):
    from unittest.mock import Mock

    from tracer.models.derived_metric import MetricStateConflict
    from tracer.services.derived_metrics.deletion_protocol import _intent
    from tracer.services.derived_metrics.erasure import _purge_snapshots

    request = _intent(
        organization_id=snapshot_metric.organization_id,
        project_id=snapshot_metric.project_id,
        scope="run",
        run_key=uuid4(),
        store_payload={"kind": "run", "status": "running"},
    )
    with pytest.raises(MetricStateConflict, match="drainage"):
        _purge_snapshots(Mock(), request)
    request.refresh_from_db()
    assert request.status == "queued" and request.receipts == {}
