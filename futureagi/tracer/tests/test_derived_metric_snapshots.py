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
def snapshot_metric(
    workspace, observe_project, monkeypatch, retained_metrics_source_policy
):
    monkeypatch.setattr(snapshots, "enqueue_capture", lambda _: None)
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
    # This protocol-only fixture publishes an empty, fully consumed source cut.
    # A manifest alone does not prove that any window is calculated.
    now = timezone.now()
    _commit(
        observe_project,
        now,
        cursors={"watermark_ns": int(now.timestamp()) * 10**9},
    )
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
@pytest.mark.parametrize("kind", ["unique_count", "percentile"])
def test_sketch_admission_and_ui_capability(snapshot_metric, user, kind):
    from tracer.serializers.derived_metric import DerivedMetricSerializer

    original = snapshot_metric.active_version
    definition = DerivedMetricVersion.objects.create(
        metric=snapshot_metric,
        version=2,
        **{**original.semantics(), "kind": kind, "definition_hash": "f" * 64},
        backfill_status="complete",
    )
    snapshot_metric.active_version = definition
    snapshot_metric.save(update_fields=["active_version"])
    data = DerivedMetricSerializer(snapshot_metric).data
    assert data["snapshot_supported"] is True
    assert data["snapshot_unsupported_reason"] == ""
    saved = snapshots.request_snapshot(snapshot_metric, request_data(), user)
    assert DerivedMetricRun.objects.get(pk=saved["id"]).definition_id == definition.id


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["unique_count", "percentile"])
def test_sketch_contributors_do_not_claim_additive_influence(snapshot_metric, kind):
    from types import SimpleNamespace
    from unittest.mock import patch

    from tracer.services.derived_metrics.contributors import _read_contributors

    run = SimpleNamespace(
        kind="snapshot",
        result_id=uuid4(),
        snapshot_id=uuid4(),
        definition=SimpleNamespace(metric=snapshot_metric, kind=kind),
    )
    with patch(
        "tracer.services.derived_metrics.service._authorized_metric", return_value=True
    ):
        with pytest.raises(MetricInputError, match="influence"):
            _read_contributors(run)


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


@pytest.mark.django_db(transaction=True)
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

    from tracer.services.derived_metrics.deletion_protocol import _intent
    from tracer.services.derived_metrics.erasure import (
        ErasureRecoveryRequired,
        _purge_snapshots,
    )

    request = _intent(
        organization_id=snapshot_metric.organization_id,
        project_id=snapshot_metric.project_id,
        scope="run",
        run_key=uuid4(),
        store_payload={"kind": "run", "status": "running"},
    )
    # An unenrolled writer cannot have a submission-drainage receipt. It must
    # fail at the stricter enrollment precondition, without claiming any purge.
    store = Mock()
    with pytest.raises(
        ErasureRecoveryRequired, match="^legacy_writer_precondition_failed$"
    ):
        _purge_snapshots(store, request)
    store.execute.assert_not_called()
    store.client.execute.assert_not_called()
    request.refresh_from_db()
    assert request.status == "queued" and request.receipts == {}


@pytest.mark.django_db
def test_capture_rejects_normalized_window_crossing_backfill_start(
    snapshot_metric, user
):
    from tracer.models.derived_metric import MetricGenerationPin
    from tracer.services.derived_metrics.reads import effective_window
    from tracer.services.derived_metrics.service import MetricUnavailableError

    data = request_data()
    window = effective_window(data["window_start"], data["window_end"])
    DerivedMetricVersion.objects.filter(pk=snapshot_metric.active_version_id).update(
        backfill_cursor={
            "start": (window["window_start"] + timedelta(minutes=15)).isoformat()
        }
    )
    with pytest.raises(MetricUnavailableError) as caught:
        snapshots.request_snapshot(snapshot_metric, data, user)
    assert caught.value.code == "window_not_calculated"
    assert not DerivedMetricRun.objects.exists()
    assert not MetricGenerationPin.objects.exists()


def running_capture(metric, user):
    saved = snapshots.request_snapshot(metric, request_data(), user)
    run = DerivedMetricRun.objects.get(pk=saved["id"])
    DerivedMetricRun.objects.filter(pk=run.id).update(
        status="running", attempt_id=uuid4()
    )
    run.refresh_from_db()
    return run


@pytest.mark.django_db(transaction=True)
def test_intent_interruption_is_recoverable_and_late_writer_is_fenced(
    snapshot_metric, user
):
    from unittest.mock import Mock, patch

    from tracer.models.derived_metric import MetricStateConflict

    run = running_capture(snapshot_metric, user)
    store = Mock()

    def recover_before_dispatch(_store):
        # Cleanup can retire the durable prepared intent while the native probe
        # is in flight. Its retirement must win the later dispatch transaction.
        snapshots.drain_submissions(
            store, organization_id=snapshot_metric.organization_id
        )
        return {"server": "server", "time": 1000, "uptime": 500}

    with patch.object(snapshots, "_server_clock", recover_before_dispatch):
        with pytest.raises(MetricStateConflict, match="fenced"):
            snapshots._write(store, run, "INSERT INTO unused VALUES (1)", {})
    store.execute.assert_not_called()
    store.client.execute.assert_not_called()
    job = MetricErasureRequest.objects.get(
        store_payload__protocol=snapshots.SUBMISSION_PROTOCOL
    )
    assert job.status == "done"
    assert job.receipts == {"not_submitted": {"drained": 1}}


@pytest.mark.django_db(transaction=True)
def test_connection_failure_before_dispatch_has_no_dispatch_proof(
    snapshot_metric, user
):
    from unittest.mock import Mock

    run = running_capture(snapshot_metric, user)
    store = Mock()
    store.execute.side_effect = ConnectionError("probe failed")
    with pytest.raises(ConnectionError):
        snapshots._write(store, run, "INSERT INTO unused VALUES (1)", {})
    store.client.execute.assert_not_called()
    job = MetricErasureRequest.objects.get(
        store_payload__protocol=snapshots.SUBMISSION_PROTOCOL
    )
    assert job.status == "done"
    assert job.receipts == {"not_submitted": {"drained": 1}}


@pytest.mark.django_db(transaction=True)
def test_native_failure_is_ambiguous_until_observed_server_restart(
    snapshot_metric, user
):
    from unittest.mock import Mock

    from tracer.models.derived_metric import MetricStateConflict

    run = running_capture(snapshot_metric, user)
    store = Mock()
    store.execute.return_value = [("server", 1000, 500)]
    store.client.execute.side_effect = ConnectionError("native send failed")
    with pytest.raises(ConnectionError):
        snapshots._write(store, run, "INSERT INTO unused VALUES (1)", {})
    job = MetricErasureRequest.objects.get(
        store_payload__protocol=snapshots.SUBMISSION_PROTOCOL
    )
    assert job.store_payload["phase"] == "dispatching"
    # No process and no terminal record do NOT prove drainage.
    store.execute.side_effect = [[(0,)], [(0,)], [("server", 1100, 600)]]
    with pytest.raises(MetricStateConflict, match="drainage"):
        snapshots.drain_submissions(
            store, organization_id=snapshot_metric.organization_id
        )
    job.refresh_from_db()
    assert job.status != "done" and job.receipts == {}
    # A different host cannot supply proof for this query's native server.
    store.execute.side_effect = [[(0,)], [(0,)], [("other-server", 1200, 10)]]
    with pytest.raises(MetricStateConflict):
        snapshots.drain_submissions(
            store, organization_id=snapshot_metric.organization_id
        )
    # The operational re-arm path records only a new observation, not a receipt.
    store.execute.side_effect = None
    store.execute.return_value = [("server", 1150, 650)]
    snapshots.arm_submission_recovery(job.id, store=store)
    job.refresh_from_db()
    assert job.status != "done" and job.receipts == {}
    store.execute.side_effect = [[(0,)], [(0,)], [("server", 1200, 10)]]
    snapshots.drain_submissions(store, organization_id=snapshot_metric.organization_id)
    job.refresh_from_db()
    assert job.status == "done" and job.receipts == {"server_restart": {"drained": 1}}


@pytest.mark.django_db(transaction=True)
def test_legacy_unsealed_submission_cannot_infer_restart_proof(snapshot_metric):
    from unittest.mock import Mock

    from tracer.models.derived_metric import MetricStateConflict
    from tracer.services.derived_metrics.deletion_protocol import _intent

    job = _intent(
        organization_id=snapshot_metric.organization_id,
        project_id=snapshot_metric.project_id,
        scope="history",
        store_payload={
            "protocol": snapshots.SUBMISSION_PROTOCOL,
            "query_id": "legacy-query",
        },
    )
    store = Mock()
    store.execute.return_value = [(0,)]
    with pytest.raises(MetricStateConflict, match="legacy"):
        snapshots.arm_submission_recovery(job.id, store=store)
    with pytest.raises(MetricStateConflict, match="drainage"):
        snapshots.drain_submissions(
            store, organization_id=snapshot_metric.organization_id
        )
    job.refresh_from_db()
    assert job.status != "done" and job.receipts == {}
