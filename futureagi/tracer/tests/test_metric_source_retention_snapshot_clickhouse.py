# ruff: noqa: F811 -- shared isolated native fixtures
"""Frozen result expiry physically deletes the authorized namespace."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import DerivedMetricRun, MetricErasureRequest
from tracer.services.derived_metrics import erasure, source_retention
from tracer.tests.test_derived_metric_lifecycle_safety import _metric, _version
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    metric_ch,
    publication_store,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("kind", ["run", "snapshot"])
def test_frozen_expiry_revokes_and_purges_even_when_rollout_disabled(
    publication_store, observe_project, workspace, settings, monkeypatch, kind,
):
    settings.METRICS_ENABLED = 0
    now = timezone.now()
    monkeypatch.setattr(source_retention, "current_cutoff", lambda *a, **k: now - timedelta(days=1))
    run = DerivedMetricRun.objects.create(
        definition=_version(_metric(workspace, observe_project), now),
        window_start=now - timedelta(days=3), window_end=now - timedelta(days=2), as_of=now,
        fingerprint=uuid4().hex, status="complete", kind=kind,
        supported_grain="conversation" if kind == "snapshot" else "",
        snapshot_id=uuid4(), result_id=uuid4(), attempt_id=uuid4(),
    )
    params = {"org": str(workspace.organization_id), "workspace": str(workspace.pk),
              "project": str(observe_project.pk), "snapshot": str(run.snapshot_id),
              "run": str(run.pk), "result": str(run.result_id)}
    publication_store.execute(
        "INSERT INTO derived_metric_inputs (org_id, workspace_id, project_id, snapshot_id, unit_id, input_values) "
        "VALUES (%(org)s, %(workspace)s, %(project)s, %(snapshot)s, 'private-frozen-unit', map('y', 42.))", params,
    )
    publication_store.execute(
        "INSERT INTO derived_metric_results (org_id, workspace_id, project_id, run_id, result_id, group_key) "
        "VALUES (%(org)s, %(workspace)s, %(project)s, %(run)s, %(result)s, 'private-frozen-label')", params,
    )
    assert source_retention.expire_runs() == 1
    intent = MetricErasureRequest.objects.get(deletion_key=f"run:{run.pk}")
    assert erasure.process_erasure(intent.pk, store=publication_store)
    run.refresh_from_db()
    assert run.deleted_at and run.cleanup_status == "done"
    assert publication_store.execute("SELECT count() FROM derived_metric_inputs WHERE snapshot_id = %(snapshot)s", params) == [(0,)]
    assert publication_store.execute("SELECT count() FROM derived_metric_results WHERE run_id = %(run)s", params) == [(0,)]
