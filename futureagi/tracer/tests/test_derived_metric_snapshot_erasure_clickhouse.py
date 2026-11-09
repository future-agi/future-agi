# ruff: noqa: F811
"""Real 028–030 capture, 90-day slicing, pinned refresh and physical erasure."""

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricRun,
    DerivedMetricVersion,
    MetricErasureRequest,
    MetricGenerationPin,
)
from tracer.services.derived_metrics import erasure, snapshots
from tracer.services.derived_metrics.deletion_protocol import _intent
from tracer.services.derived_metrics.storage import FIELD_VALUES
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    field_query,
    live_writer,
    metric_ch,
    publication_store,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def capture_case(live_writer, workspace, observe_project, user):
    metric = DerivedMetric.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        project=observe_project,
        name="Capture",
    )
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
        definition_hash="b" * 64,
        backfill_status="complete",
    )
    metric.active_version = definition
    metric.save(update_fields=["active_version"])
    end = timezone.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    from tracer.services.derived_metrics.refresh import payload_select

    attempt = live_writer.begin(
        ledger={
            "dirty_after": [
                {"bucket_start": (end - timedelta(days=age)).isoformat()}
                for age in [1, 31, 61, 89]
            ]
        }
    )
    payloads = []
    for i, age in enumerate([1, 31, 61, 89]):
        sql, params = field_query(
            f"unit-{i}",
            n=i * 2,
            start=end - timedelta(days=age),
            user="erase-me" if i == 0 else "keep-me",
            metric=metric.id,
        )
        payloads.extend(live_writer.freeze(attempt, FIELD_VALUES, sql, params))
    overlay, params = payload_select(FIELD_VALUES, payloads, prefix="capture_test")
    live_writer._freeze_buckets(
        attempt, None, definition, overlay, params, complete_fields=True
    )
    live_writer.seal(attempt)
    live_writer.stage_and_publish(attempt)
    data = {
        "window_start": end - timedelta(days=90),
        "window_end": end,
        "idempotency_key": str(uuid4()),
    }
    with patch.object(snapshots, "enqueue_capture"):
        saved = snapshots.request_snapshot(metric, data, user)
    return metric, DerivedMetricRun.objects.get(pk=saved["id"]), data


def execute(run, store, policy=lambda _: 0):
    native = store.client.execute
    errors = []

    def query(*args, **kwargs):
        try:
            return native(*args, **kwargs)
        except Exception as exc:
            errors.append(str(exc))
            raise

    with (
        patch.object(store.client, "execute", side_effect=query),
        patch(
            "tracer.services.derived_metrics.service._authorized_metric",
            return_value=True,
        ),
        patch(
            "tracer.services.derived_metrics.service._requester_can_run",
            return_value=True,
        ),
    ):
        snapshots.execute_snapshot(run.id, store=store, policy=policy)
    run.refresh_from_db()
    assert not errors, "\n".join(errors)


def overall(store, run):
    return store.execute(
        "SELECT value, unit_count FROM derived_metric_results WHERE run_id = %(run)s AND result_id = %(result)s AND rollup = 'overall'",
        {"run": str(run.id), "result": str(run.result_id)},
    )


def test_90_day_capture_stays_pinned_across_uncommitted_and_later_refresh(
    capture_case, live_writer
):
    metric, run, data = capture_case
    from tracer.services.derived_metrics.reads import _aggregate

    pinned_kpi = _aggregate(
        live_writer.store, metric, metric.active_version, run.pin.manifest, data
    )
    assert (pinned_kpi["value"], pinned_kpi["unit_count"]) == (3.0, 4)
    draft = live_writer.begin()
    sql, params = field_query(
        "unit-0", n=999, start=data["window_end"] - timedelta(days=1), metric=metric.id
    )
    live_writer.freeze(draft, FIELD_VALUES, sql, params)
    live_writer.seal(draft)
    execute(run, live_writer.store)
    assert run.status == "complete", run.error
    assert overall(live_writer.store, run) == [(3.0, 4)]
    assert live_writer.store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [(4,)]
    live_writer.stage_and_publish(draft)
    assert overall(live_writer.store, run) == [(3.0, 4)]


@pytest.mark.parametrize("reason", ["pin", "retention"])
def test_expired_capture_is_failed_not_empty_success(capture_case, live_writer, reason):
    _, run, _ = capture_case
    if reason == "pin":
        MetricGenerationPin.objects.filter(pk=run.pin_id).update(
            captured_at=timezone.now() - timedelta(hours=2),
            expires_at=timezone.now() - timedelta(seconds=1),
        )
    execute(run, live_writer.store, policy=lambda _: 30 if reason == "retention" else 0)
    assert run.status == "failed" and run.error == "expired"
    assert run.result_id is None


def test_all_passing_typed_condition_keeps_numeric_indicator(
    live_writer, workspace, observe_project, user
):
    metric = DerivedMetric.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        project=observe_project,
        name="Share capture",
    )
    descriptor = {
        "alias": "category",
        "source": "span",
        "field": "attribute_string",
        "attribute_key": "category",
        "reducer": "latest",
        "value_type": "text",
    }
    definition = DerivedMetricVersion.objects.create(
        metric=metric,
        version=1,
        kind="share",
        level="conversation",
        formula="",
        inputs=[descriptor],
        field_spec={
            "conditions": [{"alias": "category", "operator": "eq", "value": "pass"}]
        },
        selection_unit="session",
        weighting="none",
        definition_hash="c" * 64,
        backfill_status="complete",
    )
    metric.active_version = definition
    metric.save(update_fields=["active_version"])
    end = timezone.now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    attempt = live_writer.begin()
    sql, params = field_query(
        "all-passing", n=1, start=end - timedelta(hours=1), metric=metric.id
    )
    sql = sql.replace("map('y',", "map('field_condition',")
    live_writer.freeze(attempt, FIELD_VALUES, sql, params)
    live_writer.seal(attempt)
    live_writer.stage_and_publish(attempt)
    with patch.object(snapshots, "enqueue_capture"):
        saved = snapshots.request_snapshot(
            metric,
            {
                "window_start": end - timedelta(days=1),
                "window_end": end,
                "idempotency_key": str(uuid4()),
            },
            user,
        )
    run = DerivedMetricRun.objects.get(pk=saved["id"])
    execute(run, live_writer.store)
    assert run.status == "complete", run.error
    assert overall(live_writer.store, run) == [(1.0, 1)]
    assert live_writer.store.execute(
        "SELECT input_values FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [({"field_condition": 1.0},)]


def test_delete_snapshot_purges_every_result_version(capture_case, live_writer, user):
    metric, run, _ = capture_case
    execute(run, live_writer.store)
    assert run.status == "complete", run.error
    store = live_writer.store
    store.execute(
        "INSERT INTO derived_metric_results SELECT * REPLACE(generateUUIDv4() AS result_id) FROM derived_metric_results WHERE run_id = %(r)s",
        {"r": str(run.id)},
    )
    snapshots.delete_snapshot(metric, run.id, user)
    job = MetricErasureRequest.objects.get(deletion_key=f"run:{run.id}")
    assert erasure.process_erasure(job.id, store=store)
    assert store.execute(
        "SELECT count() FROM derived_metric_results WHERE run_id = %(r)s",
        {"r": str(run.id)},
    ) == [(0,)]
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(r)s",
        {"r": str(run.snapshot_id)},
    ) == [(0,)]
    run.refresh_from_db()
    assert run.cleanup_status == "done"


def test_redaction_recomputes_and_resumes_after_interrupted_delete(
    capture_case, live_writer
):
    metric, run, _ = capture_case
    execute(run, live_writer.store)
    assert run.status == "complete", run.error
    store = live_writer.store
    store.execute(
        "INSERT INTO derived_metric_results SELECT * REPLACE(generateUUIDv4() AS result_id) FROM derived_metric_results WHERE run_id = %(r)s",
        {"r": str(run.id)},
    )
    child = _intent(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope="run",
        run_key=run.id,
        snapshot_key=run.snapshot_id,
        metric_key=metric.id,
        store_payload={
            "protocol": "metric_snapshot_redaction",
            "subject_kind": "users",
            "subjects": ["erase-me"],
            "removed_units": 1,
        },
    )
    original = erasure.delete_rows
    calls = []

    def interrupt(*args):
        original(*args)
        calls.append(args[1])
        if args[1] == "derived_metric_inputs":
            raise RuntimeError("interrupted")

    with patch.object(erasure, "delete_rows", side_effect=interrupt):
        assert not erasure.process_erasure(child.id, store=store)
    assert "derived_metric_inputs" in calls
    assert erasure.process_erasure(child.id, store=store)
    assert erasure.process_erasure(child.id, store=store)
    run.refresh_from_db()
    assert run.removed_unit_count == 1 and run.changed_at
    assert overall(store, run) == [(4.0, 3)]
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE user_id = 'erase-me'"
    ) == [(0,)]
    assert store.execute(
        "SELECT count() FROM derived_metric_results WHERE run_id = %(r)s AND (result_id != %(result)s OR group_key = 'erase-me')",
        {"r": str(run.id), "result": str(run.result_id)},
    ) == [(0,)]


def test_user_then_organization_erasure_across_snapshots(
    capture_case, live_writer, user
):
    metric, first, data = capture_case
    execute(first, live_writer.store)
    runs = [first]
    for _ in range(2):
        with patch.object(snapshots, "enqueue_capture"):
            saved = snapshots.request_snapshot(
                metric, {**data, "idempotency_key": str(uuid4())}, user
            )
        run = DerivedMetricRun.objects.get(pk=saved["id"])
        execute(run, live_writer.store)
        assert run.status == "complete", run.error
        runs.append(run)
    request = erasure.erase_metric_data(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope_kind="users",
        subject_ids=["erase-me"],
    )
    assert erasure.process_erasure(
        request.id, store=live_writer.store, writer=live_writer
    )
    for run in runs:
        run.refresh_from_db()
        assert run.removed_unit_count == 1
        assert overall(live_writer.store, run) == [(4.0, 3)]
    request = erasure.erase_metric_data(
        organization_id=metric.organization_id, scope_kind="organization"
    )
    assert erasure.process_erasure(request.id, store=live_writer.store)
    assert live_writer.store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE org_id = %(org)s",
        {"org": str(metric.organization_id)},
    ) == [(0,)]
    assert live_writer.store.execute(
        "SELECT count() FROM derived_metric_results WHERE org_id = %(org)s AND unit_count != 0",
        {"org": str(metric.organization_id)},
    ) == [(0,)]
    for run in runs:
        run.refresh_from_db()
        assert run.removed_unit_count == 4
        assert overall(live_writer.store, run) == [(None, 0)]
    request.refresh_from_db()
    assert request.status == "done" and len(request.receipts) == 3


@pytest.mark.parametrize("parent", ["metric", "project", "workspace", "organization"])
def test_hard_delete_retains_and_completes_run_intent(
    capture_case, live_writer, parent
):
    metric, run, _ = capture_case
    execute(run, live_writer.store)
    assert run.status == "complete", run.error
    target = {
        "metric": metric,
        "project": metric.project,
        "workspace": metric.workspace,
        "organization": metric.organization,
    }[parent]
    type(target)._base_manager.filter(pk=target.pk).delete()
    assert not DerivedMetricRun.objects.filter(pk=run.id).exists()
    job = MetricErasureRequest.objects.get(deletion_key=f"run:{run.id}")
    assert job.snapshot_key == run.snapshot_id
    assert erasure.process_erasure(job.id, store=live_writer.store)
    assert live_writer.store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [(0,)]
