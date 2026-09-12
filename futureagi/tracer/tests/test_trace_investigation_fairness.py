import uuid
from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from tracer.models.project import Project
from tracer.models.trace_investigation import TraceInvestigationJob
from tracer.models.trace_scan import TraceScanConfig, TraceScanEngine
from tracer.services.trace_investigation import claim_due_investigations


@pytest.mark.django_db
@override_settings(
    ERROR_FEED_OMEGA_ENABLED=True, ERROR_FEED_OMEGA_PROJECT_CONCURRENCY=2
)
def test_large_older_project_backlog_cannot_starve_another_project(observe_project):
    now = timezone.now()
    other = Project.no_workspace_objects.create(
        name="Other Omega project",
        organization=observe_project.organization,
        workspace=observe_project.workspace,
        model_type=observe_project.model_type,
        trace_type="observe",
    )
    for index, project in enumerate([observe_project, other]):
        TraceScanConfig.no_workspace_objects.create(
            project=project,
            enabled=True,
            engine=TraceScanEngine.OMEGA,
            scan_version="omega-v1",
            omega_last_claimed_at=now - timedelta(days=2 - index),
        )
        TraceInvestigationJob.no_workspace_objects.bulk_create(
            [
                TraceInvestigationJob(
                    organization=project.organization,
                    workspace=project.workspace,
                    project=project,
                    trace_id=uuid.uuid4(),
                    root_span_id="0123456789abcdef",
                    root_end_time=now - timedelta(hours=1),
                    not_before=now - timedelta(days=2 - index),
                )
                for _ in range(100 if index == 0 else 1)
            ]
        )

    def claim():
        return claim_due_investigations(
            worker_id="node-test", engine_version="omega-v1", limit=1
        )["claims"]

    first = claim()
    second = claim()
    assert first[0]["project_id"] == observe_project.id
    assert second[0]["project_id"] == other.id
    # Remaining capacity is usable, but the per-project ceiling stays enforced.
    assert claim()[0]["project_id"] == observe_project.id
    assert claim() == []
