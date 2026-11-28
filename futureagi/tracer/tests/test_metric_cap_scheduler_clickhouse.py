# ruff: noqa: F811 -- shared isolated PG/CH fixtures
"""The scheduled writer, not the pause helper, owns post-pass cap enforcement."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import MetricProjectRefresh
from tracer.services.derived_metrics import admission, changes, retention, size_cap
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.refresh import TABLE_KINDS
from tracer.tests.test_derived_metric_backfill import make_version
from tracer.tests.test_derived_metric_backfill_clickhouse import (  # noqa: F401
    fields,
    history_writer,
)
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_refresh_clickhouse import (
    metric_bucket_rows,
    seed_span_revision,
)


def unlimited_policy(organization_id):
    return 0


@pytest.mark.django_db(transaction=True)
def test_scheduler_pauses_growth_keeps_privacy_and_resumes(
    history_writer, workspace, observe_project, settings, monkeypatch
):
    from tracer.services.clickhouse import v2

    writer = history_writer
    now = timezone.now().replace(microsecond=0)
    at = now - timedelta(hours=2)
    scope = Scope(workspace.organization_id, workspace.pk, observe_project.pk)
    definition = make_version(
        observe_project,
        workspace,
        active=True,
        kind="total",
        formula="sum(x)",
        history_mode="forward",
        coverage_start=at - timedelta(hours=1),
        inputs=[
            {
                "alias": "x",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "reducer": "sum",
            }
        ],
    )
    first, second, third, trace = uuid4(), uuid4(), uuid4(), uuid4()
    seed_span_revision(
        writer, scope, trace, "first", first, at, 10, revision=changes.ns(at)
    )
    watermark = changes.ns(now - timedelta(minutes=20))
    writer.refresh(
        changes.ChangeBatch(
            (changes.SourceKey(str(trace), "first"),),
            (changes.UnitKey("conversation", str(first)),),
            (),
            (),
            (),
            dict.fromkeys(MetricProjectRefresh.MONOTONE_FIELDS, watermark),
        ),
        [definition],
        scope,
        start=at - timedelta(hours=1),
        end=now,
        as_of=now,
    )
    settings.METRICS_SERVER_SIDE_MIN_ROWS = 1
    head = writer.compact()
    assert head.published_by.source_ceilings["range_compaction"]
    retention._purge_terminal_payloads(observe_project.pk)
    measured = size_cap.head_rows(head)
    assert measured > 0 and not head.published_by.compaction_ranges.exists()
    settings.METRICS_MAX_CHECKPOINT_ROWS = measured
    settings.METRICS_ENABLED = 1
    settings.METRICS_MAX_CONCURRENT_PROJECTS = 1
    settings.METRICS_RETENTION_DAYS_PROVIDER = f"{__name__}.unlimited_policy"
    native = writer.store.client.connection
    config = {
        "host": native.host,
        "tcp_port": native.port,
        "user": native.user,
        "password": native.password,
        "database": native.database,
    }
    monkeypatch.setattr(v2, "get_v2_config", lambda: config)
    writer.lease.release()
    seed_span_revision(
        writer,
        scope,
        trace,
        "second",
        second,
        at,
        20,
        revision=changes.ns(now - timedelta(minutes=1)),
    )

    def tick(when):
        jobs = []
        assert admission.dispatch(jobs.append, clock=lambda: when) == 1
        assert admission.execute_reservation(jobs[0], clock=lambda: when)
        return jobs[0]

    tick(now)
    state = MetricProjectRefresh.for_project(observe_project.pk)
    grown = state.head_manifest
    assert grown.id != head.id
    assert state.paused_reason == "too_large"
    assert state.paused_rows == size_cap.head_rows(grown)
    # With one numeric definition and two owners this pass can contribute at
    # most two rows each for fields, dependencies, lifecycle and bucket keys.
    assert measured < state.paused_rows <= measured + 8
    allowed = tuple(attempt for _, attempt in grown.allowed_attempts())
    actual = sum(
        writer.store.execute(
            f"SELECT count() FROM {table.name} WHERE project_id = %(project)s AND attempt_id IN %(attempts)s",
            {"project": observe_project.pk, "attempts": allowed},
        )[0][0]
        for table in TABLE_KINDS.values()
    )
    from tracer.services.derived_metrics.storage import (
        BUCKET_CHECKPOINT,
        FIELD_CHECKPOINT,
    )

    for table in (FIELD_CHECKPOINT, BUCKET_CHECKPOINT):
        actual += writer.store.execute(
            f"SELECT count() FROM {table.name} WHERE project_id = %(project)s AND attempt_id IN %(attempts)s",
            {"project": observe_project.pk, "attempts": allowed},
        )[0][0]
    assert state.paused_rows == actual
    cut = state.watermark_ns
    seed_span_revision(
        writer, scope, trace, "third", third, at, 30, revision=changes.ns(now)
    )
    later = (
        now
        + retention.expiry_interval()
        + admission.PAUSED_EXPIRY_SLACK
        + timedelta(seconds=1)
    )
    assert tick(later)["purpose"] == "expiry"
    state.refresh_from_db()
    assert state.head_manifest_id == grown.id and state.watermark_ns == cut
    settings.METRICS_MAX_CHECKPOINT_ROWS = measured + 100
    # A full repair deliberately publishes one phase/page per scheduler pass.
    # Resume must make bounded progress, not falsely consume an unfinished cut.
    for step in range(6):
        assert tick(later + timedelta(minutes=step + 1))["purpose"] != "expiry"
        state.refresh_from_db()
        assert not state.paused_reason and state.head_manifest_id != grown.id
        if state.watermark_ns > cut:
            break
    else:
        pytest.fail("resumed three-member fixture did not consume its repair cut")
    resumed = state.head_manifest
    assert {r[0]: r[2] for r in fields(writer, resumed, definition)} == {
        str(first): 10.0,
        str(second): 20.0,
        str(third): 30.0,
    }
    assert sum(r[3] for r in metric_bucket_rows(writer, resumed, definition)) == 60
