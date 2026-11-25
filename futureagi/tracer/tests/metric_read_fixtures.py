"""Finite source-policy fixtures for readers that exercise real PostgreSQL gates."""

import pytest


@pytest.fixture
def ninety_day_source_policy(workspace, settings):
    from ee.usage.models.usage import PlanEntitlement

    settings.METRICS_RETENTION_DAYS_PROVIDER = (
        "tfc.capabilities.retention.current_trace_retention_days"
    )
    policy, _ = PlanEntitlement.objects.update_or_create(
        organization=workspace.organization,
        plan="free",
        feature="retention_traces_days",
        defaults={"value_int": 90},
    )
    return policy


@pytest.fixture
def retained_sql_case(
    ninety_day_source_policy, observe_project, workspace, monkeypatch
):
    """SQL-focused parts plus real scope/policy/consumed-cut reader admission."""
    from datetime import timedelta
    from types import SimpleNamespace
    from uuid import uuid4

    from django.utils import timezone

    from tracer.models.derived_metric import MetricProjectRefresh
    from tracer.services.derived_metrics.queries import Scope
    from tracer.tests.test_derived_metric_reads_clickhouse import AT, case

    now = AT + timedelta(days=1)
    monkeypatch.setattr(timezone, "now", lambda: now)
    watermark = int((AT + timedelta(hours=2)).timestamp()) * 10**9
    state = MetricProjectRefresh.for_project(observe_project.pk)
    state.watermark_ns = watermark
    state.save(update_fields=["watermark_ns"])

    def make(text="mean(y)"):
        _, metric, definition, attempt, manifest = case(text)
        scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
        metric.organization_id = scope.organization
        metric.workspace_id = scope.workspace
        metric.project_id = scope.project
        manifest.id = uuid4()
        manifest.project_id = scope.project
        manifest.published_by = SimpleNamespace(
            source_ceilings={
                "retention_floor": (now - timedelta(days=90)).isoformat(),
                "read_watermark_ns": watermark,
            }
        )
        return scope, metric, definition, attempt, manifest

    return make
