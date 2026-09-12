import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from tracer.models.project import Project
from tracer.models.trace_investigation import (
    TraceInvestigationJob,
    TraceInvestigationReconciliationCursor,
)
from tracer.models.trace_scan import (
    TraceScanConfig,
    TraceScanEngine,
    TraceScanResult,
    TraceScanStatus,
)
from tracer.services.trace_investigation_reconciliation import (
    reconcile_omega_investigations,
)

pytestmark = pytest.mark.django_db


class FakeRootReader:
    def __init__(self, now, roots_by_project):
        self.now = now
        self.roots_by_project = roots_by_project
        self.page_calls = []
        self.fail_hydration_once = False

    def ch_now(self):
        return self.now

    def root_trace_candidates_page(self, project_id, lower, upper, *, after, limit):
        self.page_calls.append((project_id, lower, upper, after, limit))
        candidates = sorted(
            (
                (trace_id, row["created_at"])
                for trace_id, row in self.roots_by_project.get(project_id, {}).items()
                if lower <= row["created_at"] <= upper
            ),
            key=lambda item: (item[1], item[0]),
        )
        if after is not None:
            candidates = [item for item in candidates if (item[1], item[0]) > after]
        return candidates[:limit]

    def list_root_spans_by_trace_ids(self, trace_ids, *, project_id, columns):
        assert columns == ["trace_id", "id", "end_time"]
        if self.fail_hydration_once:
            self.fail_hydration_once = False
            raise RuntimeError("ClickHouse hydration failed")
        project_roots = self.roots_by_project.get(project_id, {})
        return {
            trace_id: {
                "trace_id": trace_id,
                "id": project_roots[trace_id]["id"],
                "end_time": project_roots[trace_id]["end_time"],
            }
            for trace_id in trace_ids
            if trace_id in project_roots
        }


def _configure(project, *, engine=TraceScanEngine.OMEGA, enabled=True):
    return TraceScanConfig.no_workspace_objects.create(
        project=project,
        enabled=enabled,
        engine=engine,
        scan_version="omega-v1",
    )


def _root(created_at):
    return {
        "id": uuid.uuid4().hex[:16],
        "created_at": created_at,
        "end_time": created_at - timedelta(seconds=1),
    }


def _run(reader, **overrides):
    arguments = {
        "reader": reader,
        "project_limit": 10,
        "page_size": 2,
        "max_pages_per_project": 10,
        "lookback_seconds": 3600,
        "grace_seconds": 1,
    }
    arguments.update(overrides)
    return reconcile_omega_investigations(
        **arguments,
    )


def test_recovers_missing_notifications_without_repeat_generation_churn(
    observe_project,
):
    _configure(observe_project)
    now = timezone.now()
    missing_trace = str(uuid.uuid4())
    completed_trace = uuid.uuid4()
    TraceScanResult.no_workspace_objects.create(
        project=observe_project,
        trace_id=completed_trace,
        status=TraceScanStatus.COMPLETED,
    )
    reader = FakeRootReader(
        now,
        {
            str(observe_project.id): {
                missing_trace: _root(now - timedelta(minutes=2)),
                str(completed_trace): _root(now - timedelta(minutes=1)),
            }
        },
    )

    first = _run(reader)
    second = _run(reader)

    job = TraceInvestigationJob.no_workspace_objects.get(trace_id=missing_trace)
    assert job.generation == 1
    assert job.project_id == observe_project.id
    assert first["jobs_created"] == 1
    assert first["completed_traces"] == 1
    assert second["jobs_created"] == 0
    assert not TraceInvestigationJob.no_workspace_objects.filter(
        trace_id=completed_trace
    ).exists()


def test_pages_resume_and_discovers_late_roots_but_respects_cold_cutoff(
    observe_project,
):
    _configure(observe_project)
    now = timezone.now()
    old_trace = str(uuid.uuid4())
    in_window = [str(uuid.uuid4()) for _ in range(3)]
    project_id = str(observe_project.id)
    roots = {old_trace: _root(now - timedelta(hours=2))}
    roots.update(
        {
            trace_id: _root(now - timedelta(minutes=10 - index))
            for index, trace_id in enumerate(in_window)
        }
    )
    reader = FakeRootReader(now, {project_id: roots})

    first = _run(reader, max_pages_per_project=1)
    cursor = TraceInvestigationReconciliationCursor.no_workspace_objects.get(
        project=observe_project
    )
    assert first["jobs_created"] == 2
    assert cursor.completed_through is None
    assert cursor.after_trace_id is not None

    second = _run(reader, max_pages_per_project=1)
    cursor.refresh_from_db()
    assert second["jobs_created"] == 1
    assert cursor.completed_through == now - timedelta(seconds=1)
    assert not TraceInvestigationJob.no_workspace_objects.filter(
        trace_id=old_trace
    ).exists()

    late_trace = str(uuid.uuid4())
    reader.now = now + timedelta(minutes=5)
    roots[late_trace] = _root(now + timedelta(minutes=2))
    late = _run(reader)
    assert late["jobs_created"] == 1
    assert TraceInvestigationJob.no_workspace_objects.filter(
        project=observe_project,
        trace_id=late_trace,
    ).exists()


def test_reconciliation_is_tenant_and_engine_scoped(
    observe_project,
    organization,
    workspace,
):
    _configure(observe_project)
    legacy_project = Project.no_workspace_objects.create(
        name="Legacy Scanner Project",
        organization=organization,
        workspace=workspace,
        model_type=observe_project.model_type,
        trace_type="observe",
    )
    disabled_project = Project.no_workspace_objects.create(
        name="Disabled Omega Project",
        organization=organization,
        workspace=workspace,
        model_type=observe_project.model_type,
        trace_type="observe",
    )
    _configure(legacy_project, engine=TraceScanEngine.LEGACY)
    _configure(disabled_project, enabled=False)
    now = timezone.now()
    roots = {
        str(project.id): {str(uuid.uuid4()): _root(now - timedelta(minutes=1))}
        for project in (observe_project, legacy_project, disabled_project)
    }
    reader = FakeRootReader(now, roots)

    result = _run(reader)

    assert result["projects_considered"] == 1
    assert {call[0] for call in reader.page_calls} == {str(observe_project.id)}
    assert set(
        TraceInvestigationJob.no_workspace_objects.values_list("project_id", flat=True)
    ) == {observe_project.id}


def test_clickhouse_failure_does_not_advance_durable_page_cursor(observe_project):
    _configure(observe_project)
    now = timezone.now()
    trace_id = str(uuid.uuid4())
    reader = FakeRootReader(
        now,
        {
            str(observe_project.id): {
                trace_id: _root(now - timedelta(minutes=1)),
            }
        },
    )
    reader.fail_hydration_once = True

    with pytest.raises(RuntimeError, match="hydration failed"):
        _run(reader)
    cursor = TraceInvestigationReconciliationCursor.no_workspace_objects.get(
        project=observe_project
    )
    assert cursor.completed_through is None
    assert cursor.after_created_at is None
    assert not TraceInvestigationJob.no_workspace_objects.exists()

    recovered = _run(reader)
    assert recovered["jobs_created"] == 1
    assert TraceInvestigationJob.no_workspace_objects.filter(
        project=observe_project,
        trace_id=trace_id,
    ).exists()
