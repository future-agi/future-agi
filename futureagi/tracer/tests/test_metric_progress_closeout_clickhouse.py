# ruff: noqa: F811 -- shared isolated CH/PG fixtures
"""Closeout progress regressions on real Postgres and ClickHouse."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tracer.models.derived_metric import DerivedMetricVersion, MetricProjectRefresh
from tracer.services.derived_metrics import size_cap
from tracer.services.derived_metrics.queries import Scope
from tracer.tests.test_derived_metric_changes_clickhouse import Analytics
from tracer.tests.test_derived_metric_clickhouse import seed_span
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_refresh_clickhouse import definition_for


@pytest.mark.django_db(transaction=True)
def test_decided_no_head_project_still_passes_bounded_admission(
    live_writer, workspace, observe_project, settings
):
    writer = live_writer
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    definition = definition_for(writer, workspace, observe_project)
    now = datetime.now(UTC).replace(microsecond=0)
    DerivedMetricVersion.objects.filter(pk=definition.pk).update(
        history_mode="forward", coverage_start=now - timedelta(hours=3)
    )
    for index in range(3):
        seed_span(
            writer.store.client, scope, f"span-{index}", uuid4(), uuid4(),
            now - timedelta(hours=1), resolved=1,
        )
    analytics = Analytics(writer.store.client)
    settings.METRICS_MAX_CHECKPOINT_ROWS = 2
    assert size_cap.pause_at_enrollment(writer.lease, analytics, retention_days=0)
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert state.head_manifest_id is None
    assert (state.paused_reason, state.paused_rows) == ("too_large", 3)
    settings.METRICS_MAX_CHECKPOINT_ROWS = 3
    assert size_cap.resume_within_limit(now=now) == 1
    assert not size_cap.pause_at_enrollment(writer.lease, analytics, retention_days=0)
