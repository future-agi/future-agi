# ruff: noqa: F811 -- shared isolated native fixtures
"""Physical source-retention tests against PostgreSQL and ClickHouse."""

from datetime import timedelta
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import MetricProjectRefresh, MetricRefreshAttempt
from tracer.services.derived_metrics import retention
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.storage import BUCKET_TOTALS, RETIRED_PARTS
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    current_fields,
    default_row_select,
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_retention_clickhouse import (
    numeric_definition,
    seed_fields_and_buckets,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_expiry_deletes_legacy_retired_labels_and_all_aggregate_revisions(live_writer):
    writer = live_writer
    now = timezone.now().replace(hour=12, minute=7, second=0, microsecond=0)
    definition = numeric_definition(uuid4())
    seed_fields_and_buckets(
        writer,
        definition,
        [
            ("expired", now - timedelta(days=3), 4, "private-user"),
            (
                "boundary",
                now - timedelta(days=1) + timedelta(minutes=1),
                6,
                "boundary-user",
            ),
            ("retained", now - timedelta(hours=1), 9, "retained-user"),
        ],
    )
    # Migration fixture: identifier-bearing retired history from an old binary.
    attempt = writer.begin()
    for table in (RETIRED_PARTS, BUCKET_TOTALS):
        writer.freeze(
            attempt,
            table,
            default_row_select(
                table,
                {
                    "metric_id": "toUUID(%(metric)s)",
                    "metric_version": "toUInt32(1)",
                    "bucket_start": "toDateTime(%(old)s, 'UTC')",
                    "group_key": "'private-legacy-label'",
                    "unit_count": "toUInt64(1)",
                    "valid_count": "toUInt64(1)",
                    "weighted_n": "toFloat64(42)",
                    "weighted_d": "toFloat64(1)",
                },
            ),
            {"metric": str(definition.metric_id), "old": now - timedelta(days=10)},
        )
    writer.seal(attempt)
    writer.stage_and_publish(attempt)
    writer.compact()
    assert retention.expire_project(
        writer, [definition], retention_days=lambda: 1, now=now
    )["purged"]
    head = MetricProjectRefresh.objects.get(
        project_id=writer.lease.project_id
    ).head_manifest
    assert {row[0] for row in current_fields(writer, head)} == {"boundary", "retained"}
    for table in (RETIRED_PARTS, BUCKET_TOTALS):
        sql, params = committed_rows_select(
            table, head, org_id=writer.org_id, project_id=writer.lease.project_id
        )
        rows = writer.store.execute(
            f"SELECT group_key, bucket_start FROM ({sql})", params
        )
        assert all(row[0] != "private-legacy-label" for row in rows)
        if table == RETIRED_PARTS:
            assert rows == []
        else:
            assert rows and all(
                row[1].replace(tzinfo=now.tzinfo) >= now - timedelta(days=1)
                for row in rows
            )
    for table in (
        "metric_bucket_retired_parts",
        "metric_bucket_totals",
        "metric_bucket_totals_checkpoint",
    ):
        assert writer.store.execute(
            f"SELECT count() FROM {table} WHERE group_key = 'private-legacy-label'"
        ) == [(0,)]
    assert retention.expire_project(
        writer, [definition], retention_days=lambda: -1, now=now
    )["unlimited"]
    assert writer.store.execute("SELECT count() FROM metric_bucket_retired_parts") == [
        (0,)
    ]


def test_failed_expiry_closes_prepared_attempt_and_retry_keeps_progress(
    live_writer, monkeypatch
):
    writer = live_writer
    definition = numeric_definition(uuid4())
    now = timezone.now()
    seed_fields_and_buckets(
        writer,
        definition,
        [
            ("old", now - timedelta(days=3), 4, "old-user"),
            ("keep", now - timedelta(hours=1), 7, "keep-user"),
        ],
    )
    native = writer.freeze_current

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic interrupted expiry")

    monkeypatch.setattr(writer, "freeze_current", fail)
    with pytest.raises(RuntimeError, match="interrupted expiry"):
        retention.expire_project(
            writer, [definition], retention_days=lambda: 1, now=now
        )
    assert not MetricRefreshAttempt.objects.filter(
        project_id=writer.lease.project_id, state__in=MetricRefreshAttempt.OPEN_STATES
    ).exists()
    monkeypatch.setattr(writer, "freeze_current", native)
    assert retention.expire_project(
        writer, [definition], retention_days=lambda: 1, now=now
    )["purged"]


def test_history_kpi_and_trend_have_honest_native_finality_and_policy_guard(
    live_writer,
    workspace,
    observe_project,
    settings,
    monkeypatch,
):
    from tracer.models.derived_metric import DerivedMetric, DerivedMetricVersion
    from tracer.services.derived_metrics import reads, source_retention
    from tracer.services.derived_metrics.service import MetricUnavailableError

    settings.METRICS_ENABLED = 1
    now = timezone.now().replace(minute=0, second=0, microsecond=0)
    floor = {"at": now - timedelta(days=30)}
    monkeypatch.setattr(source_retention, "current_cutoff", lambda *a, **k: floor["at"])
    metric = DerivedMetric.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        project=observe_project,
        name="Retained history read",
    )
    definition = DerivedMetricVersion.objects.create(
        metric=metric,
        version=1,
        kind="custom",
        level="conversation",
        formula="mean(y)",
        inputs=numeric_definition(metric.pk).inputs,
        selection_unit="session",
        weighting="none",
        cohort_key="region",
        backfill_status="complete",
        history_mode="history",
    )
    writer = live_writer
    seed_fields_and_buckets(
        writer,
        definition,
        [
            ("final-unit", now - timedelta(hours=2), 7, "retained-user"),
        ],
    )
    attempt = writer.begin(versions=[{"id": definition.pk, "metric_id": metric.pk}])
    writer.seal(
        attempt,
        cursors={"watermark_ns": int((now - timedelta(hours=1)).timestamp()) * 10**9},
    )
    writer.stage_and_publish(attempt)
    DerivedMetric.activate_version(metric.pk, definition.pk, expected_active_id=None)
    metric.refresh_from_db()
    window = {"window_start": now - timedelta(hours=2), "window_end": now}
    kpi = reads.read_kpi(metric, window, store=writer.store)
    assert kpi["partial"] and kpi["current"]["value"] == 7
    assert kpi["current"]["eligible_count"] == 1
    trend = reads.read_trend(metric, {**window, "points": 8}, store=writer.store)
    assert trend["partial"] and trend["complete_through"] < now
    assert any(row["point_status"] == "not_calculated" for row in trend["results"])
    assert sum(row["eligible_count"] for row in trend["results"]) == 1
    floor["at"] = window["window_start"] + timedelta(microseconds=1)
    for reader in (reads.read_kpi, reads.read_trend):
        with pytest.raises(MetricUnavailableError, match="outside current retention"):
            reader(metric, window, store=writer.store)


def test_no_head_replan_scrubs_late_writes_without_admitting_a_project(live_writer):
    from tracer.models.derived_metric import (
        MetricAttemptLedgerChunk,
        MetricErasureRequest,
    )
    from tracer.services.derived_metrics.admission import run_expiry_pass
    from tracer.services.derived_metrics.deletion_protocol import _intent
    from tracer.services.derived_metrics.refresh import MetricRetentionChanged
    from tracer.tests.test_derived_metric_publication_clickhouse import prepare_field

    writer, now = live_writer, timezone.now()
    attempt, _ = prepare_field(
        writer, unit_id="unpublished-private", start=now - timedelta(days=3)
    )
    writer.retention_days = lambda: 1
    with pytest.raises(MetricRetentionChanged):
        writer.stage_and_publish(attempt)
    debt = MetricErasureRequest.objects.get(
        deletion_key=f"retention-replan:{attempt.pk}"
    )
    submission = _intent(
        organization_id=writer.org_id,
        project_id=writer.lease.project_id,
        scope="history",
        store_payload={
            "protocol": "metric_submission",
            "generation": attempt.generation,
            "attempt": str(attempt.pk),
            "shard": writer.store.identity(),
        },
    )
    params = {
        "org": str(writer.org_id),
        "project": str(writer.lease.project_id),
        "attempt": str(attempt.pk),
        "generation": attempt.generation,
    }
    insert = "INSERT INTO metric_field_values (org_id, project_id, attempt_id, generation, unit_id) VALUES (%(org)s, %(project)s, %(attempt)s, %(generation)s, 'late-private')"
    writer.store.execute(insert, params)
    run_expiry_pass(writer)
    debt.refresh_from_db()
    assert debt.status != "done"
    assert not MetricAttemptLedgerChunk.objects.filter(attempt=attempt).exists()
    # A done row without its synchronous response is NOT a drainage receipt.
    MetricErasureRequest.objects.filter(pk=submission.pk).update(
        status="done", completed_at=now
    )
    writer.store.execute(insert, params)
    run_expiry_pass(writer)
    debt.refresh_from_db()
    assert debt.status != "done"
    MetricErasureRequest.objects.filter(pk=submission.pk).update(
        receipts={"synchronous_response": {"drained": 1}}
    )
    writer.store.execute(insert, params)
    run_expiry_pass(writer)
    debt.refresh_from_db()
    assert debt.status == "done"
    state = MetricProjectRefresh.for_project(writer.lease.project_id)
    assert state.head_manifest_id is None  # the no-head admission guard still applies
    assert state.pending_repair and not state.pending_expiry
    assert writer.store.execute(
        "SELECT count() FROM metric_field_values WHERE unit_id = 'late-private'"
    ) == [(0,)]
