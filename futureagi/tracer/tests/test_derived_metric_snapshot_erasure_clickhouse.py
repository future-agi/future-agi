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
def capture_case(live_writer, workspace, observe_project, user, request):
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
    owners = getattr(request, "param", {})
    for i, age in enumerate([1, 31, 61, 89]):
        sql, params = field_query(
            owners.get("session", "unit-0") if i == 0 else f"unit-{i}",
            n=i * 2,
            start=end - timedelta(days=age),
            user=owners.get("user", "erase-me") if i == 0 else "keep-me",
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


@pytest.mark.parametrize("failure", ["deleted", "capture_failed", "orphan"])
def test_parent_erasure_waits_for_all_partial_namespace_cleanup(
    capture_case, live_writer, user, failure
):
    metric, run, _ = capture_case
    store = live_writer.store
    execute(run, store)
    assert run.status == "complete"
    if failure == "deleted":
        snapshots.delete_snapshot(metric, run.id, user)
    elif failure == "orphan":
        DerivedMetricRun.objects.filter(pk=run.id).delete()
    else:
        DerivedMetricRun.objects.filter(pk=run.id).update(
            status="failed", error="capture_failed"
        )
        _intent(
            organization_id=metric.organization_id,
            project_id=metric.project_id,
            scope="run",
            run_key=run.id,
            snapshot_key=run.snapshot_id,
            metric_key=metric.id,
            deletion_key=f"failed-capture:{run.id}",
        )
    parent = erasure.erase_metric_data(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope_kind="users",
        subject_ids=["erase-me"],
    )
    original = snapshots.delete_rows

    def interrupt(store, table, predicate, params):
        if table == "derived_metric_inputs":
            raise RuntimeError("interrupted physical purge")
        return original(store, table, predicate, params)

    with patch.object(snapshots, "delete_rows", side_effect=interrupt):
        assert not erasure.process_erasure(parent.id, store=store, writer=live_writer)
    parent.refresh_from_db()
    assert parent.status == "failed" and parent.receipts == {}
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [(4,)]
    assert erasure.process_erasure(parent.id, store=store, writer=live_writer)
    parent.refresh_from_db()
    assert parent.status == "done" and len(parent.receipts) == 3
    for table, key, value in (
        ("derived_metric_inputs", "snapshot_id", run.snapshot_id),
        ("derived_metric_results", "run_id", run.id),
        ("derived_metric_commits", "run_id", run.id),
    ):
        assert store.execute(
            f"SELECT count() FROM {table} WHERE {key} = %(key)s", {"key": str(value)}
        ) == [(0,)]


OLD_OWNER = "20000000-0000-0000-0000-000000000001"
NEW_OWNER = "10000000-0000-0000-0000-000000000001"
SHARED_OWNER = "30000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize(
    "capture_case,kind,table",
    [
        ({"user": OLD_OWNER}, "users", "end_user_id_remap"),
        ({"session": OLD_OWNER}, "sessions", "trace_session_id_remap"),
    ],
    indirect=["capture_case"],
)
def test_erasure_finds_owners_captured_before_consolidation(
    capture_case, live_writer, kind, table
):
    metric, run, _ = capture_case
    store = live_writer.store
    execute(run, store)
    assert overall(store, run) == [(3.0, 4)]
    store.execute(
        f"INSERT INTO {table} (old_id, new_id) VALUES "
        "(toUUID(%(old)s), toUUID(%(shared)s)), (toUUID(%(new)s), toUUID(%(shared)s))",
        {"old": OLD_OWNER, "new": NEW_OWNER, "shared": SHARED_OWNER},
    )
    request = erasure.erase_metric_data(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope_kind=kind,
        subject_ids=[NEW_OWNER],
    )
    assert erasure.process_erasure(request.id, store=store, writer=live_writer)
    run.refresh_from_db()
    assert run.removed_unit_count == 1
    assert overall(store, run) == [(4.0, 3)]
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s "
        "AND (user_id = %(old)s OR session_id = %(old)s)",
        {"s": str(run.snapshot_id), "old": OLD_OWNER},
    ) == [(0,)]


def test_missing_identity_lineage_blocks_zero_match_completion(
    capture_case, live_writer
):
    metric, run, _ = capture_case
    store = live_writer.store
    execute(run, store)
    request = erasure.erase_metric_data(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope_kind="users",
        subject_ids=[NEW_OWNER],
    )
    store.execute("DROP TABLE end_user_id_remap")
    assert not erasure.process_erasure(request.id, store=store, writer=live_writer)
    request.refresh_from_db()
    assert request.status == "failed" and request.receipts == {}
    assert overall(store, run) == [(3.0, 4)]


def test_worker_rechecks_pinned_backfill_coverage(capture_case, live_writer):
    _, run, _ = capture_case
    DerivedMetricVersion.objects.filter(pk=run.definition_id).update(
        backfill_cursor={
            "start": (run.window_start + timedelta(minutes=15)).isoformat()
        }
    )
    execute(run, live_writer.store)
    assert run.status == "failed" and run.error == "window_not_calculated"
    assert run.result_id is None
    assert live_writer.store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [(0,)]


def test_lost_native_ack_drains_from_terminal_log_before_purge(
    capture_case, live_writer
):
    from tracer.models.derived_metric import MetricStateConflict

    metric, run, _ = capture_case
    store = live_writer.store
    DerivedMetricRun.objects.filter(pk=run.id).update(
        status="running", attempt_id=uuid4()
    )
    run.refresh_from_db()
    sql, params = snapshots.capture_select(
        run, run.pin.manifest, run.window_start, run.window_end
    )
    native = store.client.execute

    def lose_ack(*args, **kwargs):
        result = native(*args, **kwargs)
        if kwargs.get("query_id", "").startswith("metric-snapshot-"):
            assert store.client.connection.disable_reconnect is True
            raise ConnectionError("lost native acknowledgement")
        return result

    with patch.object(store.client, "execute", side_effect=lose_ack):
        with pytest.raises(ConnectionError):
            snapshots._write(store, run, sql, params)
    job = MetricErasureRequest.objects.get(
        store_payload__protocol=snapshots.SUBMISSION_PROTOCOL
    )
    assert job.status != "done" and job.receipts == {}
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [(4,)]
    store.execute("SYSTEM FLUSH LOGS")
    snapshots.drain_submissions(
        store, organization_id=metric.organization_id, run_id=run.id
    )
    job.refresh_from_db()
    assert job.receipts == {"terminal_query": {"drained": 1}}
    snapshots.purge_run(
        store,
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        run_id=run.id,
        snapshot_id=run.snapshot_id,
    )
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(s)s",
        {"s": str(run.snapshot_id)},
    ) == [(0,)]
    # A legacy no-terminal query on the same empty process list stays pending.
    _intent(
        organization_id=metric.organization_id,
        project_id=metric.project_id,
        scope="history",
        store_payload={
            "protocol": snapshots.SUBMISSION_PROTOCOL,
            "query_id": "never-dispatched",
        },
    )
    with pytest.raises(MetricStateConflict, match="drainage"):
        snapshots.drain_submissions(store, organization_id=metric.organization_id)


def test_rollup_pages_keep_duplicate_groups_whole(
    capture_case, live_writer, monkeypatch
):
    _, run, _ = capture_case
    monkeypatch.setattr(snapshots, "ROLLUP_PAGE_ROWS", 1)
    execute(run, live_writer.store)
    assert run.status == "complete", run.error
    assert live_writer.store.execute(
        "SELECT group_key, value, unit_count FROM derived_metric_results "
        "WHERE run_id = %(r)s AND result_id = %(result)s AND rollup = 'user' ORDER BY group_key",
        {"r": str(run.id), "result": str(run.result_id)},
    ) == [("erase-me", 0.0, 1), ("keep-me", 4.0, 3)]


def test_rollup_pages_preserve_window_wide_scope_rejection(
    capture_case, live_writer, monkeypatch
):
    _, run, _ = capture_case
    store = live_writer.store
    execute(run, store)
    store.execute(
        "ALTER TABLE derived_metric_inputs UPDATE sampling_scope = user_id "
        "WHERE snapshot_id = %(s)s SETTINGS mutations_sync=2",
        {"s": str(run.snapshot_id)},
    )
    DerivedMetricRun.objects.filter(pk=run.id).update(
        status="running", attempt_id=uuid4()
    )
    run.refresh_from_db()
    monkeypatch.setattr(snapshots, "ROLLUP_PAGE_ROWS", 1)
    snapshots.commit_results(store, run)
    run.refresh_from_db()
    assert store.execute(
        "SELECT group_key, status, value FROM derived_metric_results "
        "WHERE run_id = %(r)s AND result_id = %(result)s AND rollup = 'user' ORDER BY group_key",
        {"r": str(run.id), "result": str(run.result_id)},
    ) == [
        ("erase-me", "invalid_probability", None),
        ("keep-me", "invalid_probability", None),
    ]


def test_rollups_and_erasure_exceed_one_million_sessions(capture_case, live_writer):
    """Reduced-width >1M cardinality gate; the 10M VM qualification is separate."""
    from time import monotonic

    metric, run, _ = capture_case
    store = live_writer.store
    execute(run, store)
    assert run.status == "complete"
    store.execute(
        "ALTER TABLE derived_metric_inputs DELETE WHERE snapshot_id = %(s)s SETTINGS mutations_sync=2",
        {"s": str(run.snapshot_id)},
    )
    count = 1000001
    store.execute(
        "INSERT INTO derived_metric_inputs "
        "(org_id, workspace_id, project_id, snapshot_id, unit_id, session_id, user_id, "
        "cohort, day, input_values, probability, invalid_probability, source_rows, sampling_design) "
        "SELECT toUUID(%(org)s), toUUID(%(workspace)s), toUUID(%(project)s), toUUID(%(snapshot)s), "
        "toString(number), toString(number), if(number=0, 'erase-me', 'keep-me'), '', "
        "toDate('2026-01-01'), map('y', toNullable(toFloat64(number %% 2))), 1., 0, 1, 'live' "
        "FROM numbers(%(count)s)",
        {
            "org": str(metric.organization_id),
            "workspace": str(metric.workspace_id),
            "project": str(metric.project_id),
            "snapshot": str(run.snapshot_id),
            "count": count,
        },
    )
    DerivedMetricRun.objects.filter(pk=run.id).update(
        status="running", attempt_id=uuid4()
    )
    run.refresh_from_db()
    started = monotonic()
    snapshots.commit_results(store, run)
    run.refresh_from_db()
    assert overall(store, run) == [(500000 / count, count)]
    assert store.execute(
        "SELECT count() FROM derived_metric_results WHERE run_id = %(run)s "
        "AND result_id = %(result)s AND rollup = 'session'",
        {"run": str(run.id), "result": str(run.result_id)},
    ) == [(count,)]
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
    assert erasure.process_erasure(child.id, store=store)
    run.refresh_from_db()
    assert overall(store, run) == [(0.5, 1000000)]
    assert store.execute(
        "SELECT count() FROM derived_metric_results WHERE run_id = %(run)s "
        "AND result_id = %(result)s AND rollup = 'session'",
        {"run": str(run.id), "result": str(run.result_id)},
    ) == [(1000000,)]
    print(
        f"B18 cardinality: {count} session rollups materialized; 1000000 after erasure; {monotonic() - started:.2f}s"
    )
