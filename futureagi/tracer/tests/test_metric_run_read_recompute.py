"""Keep committed raw-run results readable without opening redacting snapshots."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import DerivedMetricRun
from tracer.services.derived_metrics.errors import MetricInputError
from tracer.services.derived_metrics.snapshots import snapshot_read
from tracer.tests.test_derived_metric_lifecycle_safety import _metric, _version

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("retained_metrics_source_policy"),
]


@pytest.fixture
def committed_run(workspace, observe_project):
    now = timezone.now()
    return DerivedMetricRun.objects.create(
        definition=_version(_metric(workspace, observe_project), now),
        window_start=now - timedelta(days=3),
        window_end=now - timedelta(days=2),
        as_of=now,
        fingerprint=uuid4().hex,
        status="complete",
        snapshot_id=uuid4(),
        result_id=uuid4(),
        attempt_id=uuid4(),
        completed_at=now,
    )


@pytest.mark.parametrize("status", ["queued", "running", "failed"])
def test_raw_recompute_reads_last_commit_not_the_active_attempt(committed_run, status):
    run = committed_run
    result_id, snapshot_id = run.result_id, run.snapshot_id
    DerivedMetricRun.objects.filter(pk=run.pk).update(status=status, attempt_id=None)
    with snapshot_read(run) as pinned:
        assert pinned.result_id == result_id and pinned.snapshot_id == snapshot_id
        # A dequeue/failed retry changes the active attempt, not the old result.
        DerivedMetricRun.objects.filter(pk=run.pk).update(
            status="running", attempt_id=uuid4()
        )


@pytest.mark.parametrize("status", ["queued", "running", "failed"])
def test_noncomplete_saved_snapshot_never_reads_old_result(committed_run, status):
    DerivedMetricRun.objects.filter(pk=committed_run.pk).update(
        kind="snapshot", supported_grain="conversation", status=status
    )
    with pytest.raises(MetricInputError, match="no longer available"):
        with snapshot_read(committed_run):
            pytest.fail("capture or redaction exposed its prior result")


@pytest.mark.parametrize("missing", ["snapshot_id", "result_id", "completed_at"])
def test_unpublished_raw_run_does_not_qualify_for_recompute_reads(
    committed_run, missing
):
    DerivedMetricRun.objects.filter(pk=committed_run.pk).update(
        status="queued", **{missing: None}
    )
    with pytest.raises(MetricInputError, match="no longer available"):
        with snapshot_read(committed_run):
            pytest.fail("an unpublished namespace became readable")


@pytest.mark.parametrize("kind", ["run", "snapshot"])
@pytest.mark.parametrize(
    "change", ["snapshot_id", "result_id", "changed_at", "deleted_at"]
)
def test_recompute_read_still_fences_namespace_and_privacy_changes(
    committed_run, kind, change
):
    run = committed_run
    DerivedMetricRun.objects.filter(pk=run.pk).update(
        kind=kind, supported_grain="conversation" if kind == "snapshot" else ""
    )
    with pytest.raises(MetricInputError, match="changed while"):
        with snapshot_read(run):
            value = uuid4() if change.endswith("_id") else timezone.now()
            updates = {change: value}
            if change == "changed_at":
                updates["change_reason"] = "erasure"
            elif change == "deleted_at":
                updates["cleanup_status"] = "pending"
            DerivedMetricRun.objects.filter(pk=run.pk).update(**updates)


def test_saved_snapshot_keeps_its_attempt_fence(committed_run):
    DerivedMetricRun.objects.filter(pk=committed_run.pk).update(
        kind="snapshot", supported_grain="conversation"
    )
    with pytest.raises(MetricInputError, match="changed while"):
        with snapshot_read(committed_run):
            DerivedMetricRun.objects.filter(pk=committed_run.pk).update(
                attempt_id=uuid4()
            )


def test_recompute_read_rechecks_source_policy(
    committed_run, retained_metrics_source_policy
):
    DerivedMetricRun.objects.filter(pk=committed_run.pk).update(status="queued")
    with pytest.raises(MetricInputError, match="source retention"):
        with snapshot_read(committed_run):
            retained_metrics_source_policy.value_int = 1
            retained_metrics_source_policy.save(update_fields=["value_int"])
    committed_run.refresh_from_db()
    assert committed_run.deleted_at is not None
