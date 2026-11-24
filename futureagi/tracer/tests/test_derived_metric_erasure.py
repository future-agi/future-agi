# ruff: noqa: F811
"""One hundred real frozen captures, bounded resume and all-version erasure."""

from uuid import uuid4

import pytest

from tracer.models.derived_metric import DerivedMetricRun, MetricErasureRequest
from tracer.services.derived_metrics import erasure
from tracer.tests.test_derived_metric_snapshot_erasure_clickhouse import (  # noqa: F401
    capture_case,
    execute,
    field_query,
    live_writer,
    metric_ch,
    publication_store,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_user_and_organization_erasure_resume_across_100_snapshots(
    capture_case, live_writer
):
    metric, first, _ = capture_case
    store = live_writer.store
    execute(first, store)
    assert first.status == "complete", first.error
    # Copies seed historical captures only. The production capture path is exercised
    # separately; every deletion and recomputation below uses the real worker.
    for _ in range(99):
        run = DerivedMetricRun.objects.create(
            definition=first.definition,
            kind="snapshot",
            supported_grain="conversation",
            window_start=first.window_start,
            window_end=first.window_end,
            as_of=first.as_of,
            captured_at=first.captured_at,
            status="complete",
            snapshot_id=uuid4(),
            result_id=uuid4(),
            fingerprint=uuid4().hex + uuid4().hex,
            source_manifest=first.source_manifest,
        )
        params = {
            "source_snapshot": str(first.snapshot_id),
            "snapshot": str(run.snapshot_id),
            "source_run": str(first.id),
            "source_result": str(first.result_id),
            "run": str(run.id),
            "result": str(run.result_id),
        }
        store.execute(
            "INSERT INTO derived_metric_inputs SELECT * REPLACE(toUUID(%(snapshot)s) AS snapshot_id) FROM derived_metric_inputs WHERE snapshot_id = %(source_snapshot)s",
            params,
        )
        store.execute(
            "INSERT INTO derived_metric_results SELECT * REPLACE(toUUID(%(run)s) AS run_id, toUUID(%(result)s) AS result_id) FROM derived_metric_results WHERE run_id = %(source_run)s AND result_id = %(source_result)s",
            params,
        )
        store.execute(
            "INSERT INTO derived_metric_commits SELECT * REPLACE(toUUID(%(run)s) AS run_id, toUUID(%(result)s) AS result_id) FROM derived_metric_commits WHERE run_id = %(source_run)s",
            params,
        )
    runs = DerivedMetricRun.objects.filter(definition=first.definition)
    assert runs.count() == 100
    for scope, subjects, removed, expected_value, expected_units in [
        ("users", ["erase-me"], 1, 4.0, 3),
        ("organization", [], 4, None, 0),
    ]:
        request = erasure.erase_metric_data(
            organization_id=metric.organization_id,
            project_id=metric.project_id if scope == "users" else None,
            scope_kind=scope,
            subject_ids=subjects,
        )
        passes = 0
        for _ in range(10):
            live_writer.lease.keepalive()
            done = erasure.process_erasure(request.id, store=store, writer=live_writer)
            passes += 1
            if done:
                break
        assert done and passes == 10  # ten children per pass, never an unbounded job
        assert (
            runs.filter(
                removed_unit_count=removed, changed_at__isnull=False, status="complete"
            ).count()
            == 100
        )
        results = store.execute(
            "SELECT value, unit_count FROM derived_metric_results WHERE metric_id = %(metric)s AND rollup = 'overall'",
            {"metric": str(metric.id)},
        )
        assert results == [(expected_value, expected_units)] * 100
        assert store.execute(
            "SELECT count() FROM derived_metric_inputs WHERE user_id = 'erase-me'"
        ) == [(0,)]
        assert store.execute(
            "SELECT count() FROM derived_metric_results WHERE metric_id = %(metric)s AND group_key = 'erase-me'",
            {"metric": str(metric.id)},
        ) == [(0,)]
        request.refresh_from_db()
        assert request.status == "done" and set(request.receipts) == set(
            MetricErasureRequest.Store.values
        )
        # A repeated delivery must neither subtract again nor add result versions.
        assert erasure.process_erasure(request.id, store=store, writer=live_writer)
        assert runs.filter(removed_unit_count=removed).count() == 100
    assert store.execute(
        "SELECT count() FROM derived_metric_inputs WHERE org_id = %(org)s",
        {"org": str(metric.organization_id)},
    ) == [(0,)]
