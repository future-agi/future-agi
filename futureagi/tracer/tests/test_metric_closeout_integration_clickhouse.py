# ruff: noqa: F811 -- shared isolated PG/CH fixtures
"""Admission composition across the progress and source-retention lanes."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import (
    DerivedMetricVersion,
    MetricErasureRequest,
    MetricProjectRefresh,
    MetricReadManifest,
    MetricRefreshAttempt,
)
from tracer.services.derived_metrics import admission, size_cap
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.refresh import MetricRetentionChanged
from tracer.tests.test_derived_metric_changes_clickhouse import Analytics
from tracer.tests.test_derived_metric_clickhouse import seed_span
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    metric_ch,
    prepare_field,
    publication_store,
)
from tracer.tests.test_derived_metric_refresh_clickhouse import definition_for

pytestmark = pytest.mark.django_db(transaction=True)


def one_day_policy(organization_id):
    return 1


@pytest.mark.parametrize("rollout", [0, 1])
@pytest.mark.parametrize("already_paused", [False, True])
def test_no_head_privacy_priority_then_source_cap_remains_enforced(
    live_writer, workspace, observe_project, settings, monkeypatch,
    rollout, already_paused,
):
    """Real dispatch/execution drains first-publication debt, never admits a head."""
    from tracer.services.clickhouse import v2

    writer = live_writer
    now = timezone.now().replace(microsecond=0)
    definition = definition_for(writer, workspace, observe_project)
    DerivedMetricVersion.objects.filter(pk=definition.pk).update(
        history_mode="forward", coverage_start=now - timedelta(days=1)
    )
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    for index in range(3):
        seed_span(
            writer.store.client, scope, f"source-{index}", uuid4(), uuid4(),
            now - timedelta(hours=1), resolved=1,
        )
    settings.METRICS_MAX_CHECKPOINT_ROWS = 2
    settings.METRICS_MAX_CONCURRENT_PROJECTS = 1
    settings.METRICS_RETENTION_DAYS_PROVIDER = f"{__name__}.one_day_policy"
    if already_paused:
        assert size_cap.pause_at_enrollment(
            writer.lease, Analytics(writer.store.client), retention_days=1
        )

    # This staged copy was prepared under the old policy but cannot publish
    # after shortening. Its actual native submission receipts must drain.
    attempt, _ = prepare_field(
        writer, metric=definition.metric_id, unit_id="unpublished-private",
        start=now - timedelta(days=3),
    )
    writer.retention_days = lambda: 1
    with pytest.raises(MetricRetentionChanged):
        writer.stage_and_publish(attempt)
    debt = MetricErasureRequest.objects.get(
        deletion_key=f"retention-replan:{attempt.pk}"
    )
    attempts_before = MetricRefreshAttempt.objects.filter(
        project=observe_project
    ).count()
    writer.lease.release()

    # Only route the production runner to this owned native fixture database.
    connection = writer.store.client.connection
    config = {
        "host": connection.host, "tcp_port": connection.port,
        "user": connection.user, "password": connection.password,
        "database": connection.database,
    }
    monkeypatch.setattr(v2, "get_v2_config", lambda: config)
    settings.METRICS_ENABLED = rollout
    payloads = []
    assert admission.dispatch(payloads.append) == 1
    assert payloads[0]["purpose"] == "expiry"
    assert admission.execute_reservation(payloads[0])
    debt.refresh_from_db()
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert debt.status == "done"
    assert not state.pending_expiry and state.pending_repair
    assert state.head_manifest_id is None
    assert not MetricReadManifest.objects.filter(project=observe_project).exists()
    assert MetricRefreshAttempt.objects.filter(project=observe_project).count() == attempts_before
    assert writer.store.execute(
        "SELECT count() FROM metric_field_values WHERE project_id = %(project)s",
        {"project": observe_project.id},
    ) == [(0,)]
    assert bool(state.paused_reason) == already_paused

    # Once calculation is enabled again, the retained source count must still
    # stop the no-head project. Privacy work must not mint an admission receipt.
    settings.METRICS_ENABLED = 1
    if not already_paused:
        payloads = []
        assert admission.dispatch(payloads.append) == 1
        assert payloads[0]["purpose"] in {"repair", "incremental", "backfill"}
        assert admission.execute_reservation(payloads[0])
    state.refresh_from_db()
    assert (state.paused_reason, state.paused_rows) == ("too_large", 3)
    assert state.head_manifest_id is None
    assert not MetricReadManifest.objects.filter(project=observe_project).exists()
    assert MetricRefreshAttempt.objects.filter(project=observe_project).count() == attempts_before
