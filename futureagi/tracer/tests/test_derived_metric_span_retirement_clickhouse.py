# ruff: noqa: F811 -- shared isolated ClickHouse and PG fixtures
"""Legacy evidence migration and bounded idle retirement on real CH/PG."""

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tracer.models.derived_metric import MetricProjectRefresh
from tracer.services.derived_metrics import reads, span_evidence
from tracer.services.derived_metrics.bounded_reads import SOURCE_SCHEMA
from tracer.services.derived_metrics.changes import (
    ChangeBatch,
    Dependency,
    SourceKey,
    UnitKey,
)
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.refresh import MetricAdmissionUnproven
from tracer.services.derived_metrics.retention import expire_project
from tracer.services.derived_metrics.storage import (
    BUCKET_TOTALS,
    FIELD_VALUES,
    SOURCE_DEPENDENCIES,
)
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_reads_clickhouse import AT, WINDOW
from tracer.tests.test_derived_metric_refresh_clickhouse import (
    change_for,
    metric_bucket_rows,
    seed_span_revision,
    span_definition_for,
)
from tracer.tests.test_derived_metric_span_evidence_clickhouse import (
    publish,
    unit_values,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("enabled_metrics"),
]


def legacy_head(writer, head):
    attempt = writer.begin(purpose="compaction")
    for table in (BUCKET_TOTALS, SOURCE_DEPENDENCIES):
        writer.freeze_current(attempt, table, head)
    writer.seal(attempt)
    return writer.stage_and_publish(attempt)


def test_finite_retention_legacy_manifest_upgrades_from_frozen_backfill(
    live_writer, workspace, observe_project
):
    writer = live_writer
    metric, definition, head, ids = publish(writer, workspace, observe_project)
    expected = unit_values(writer, metric, ids)["results"]
    legacy = legacy_head(writer, head)
    writer.retention_days = lambda: (datetime.now(UTC) - AT).days + 5
    # A retained legacy head is not itself an expiry failure.
    assert not expire_project(
        writer, [definition], retention_days=writer.retention_days
    )["purged"]
    sources = tuple(SourceKey(*json.loads(i)) for i in ids)
    upgraded = writer.refresh(
        ChangeBatch(
            sources,
            tuple(UnitKey("span", i) for i in ids),
            (),
            (),
            (),
            {"watermark_ns": 2},
        ),
        [definition],
        Scope(writer.org_id, writer.workspace_id, observe_project.id),
        start=AT,
        end=WINDOW["window_end"],
        as_of=datetime.now(UTC),
        purpose="backfill",
    )
    assert upgraded.id != legacy.id
    assert unit_values(writer, metric, ids)["results"] == expected
    assert reads.read_kpi(metric, WINDOW, store=writer.store)["current"]["value"] == 4
    assert (
        len(
            reads.read_units(
                metric,
                WINDOW,
                drivers=True,
                store=writer.store,
                retention_days=writer.retention_days,
            )["results"]
        )
        == 1
    )
    assert MetricProjectRefresh.for_project(observe_project.id).watermark_ns == 2
    # Normal refresh can resume, using the published fields as identity proof
    # even when this legacy producer had no dependency rows to migrate.
    refreshed = writer.refresh(
        ChangeBatch(
            sources,
            tuple(UnitKey("span", i) for i in ids),
            (),
            (),
            (),
            {"watermark_ns": 3},
        ),
        [definition],
        Scope(writer.org_id, writer.workspace_id, observe_project.id),
        start=AT,
        end=WINDOW["window_end"],
        as_of=datetime.now(UTC),
    )
    assert refreshed.id != upgraded.id
    assert MetricProjectRefresh.for_project(observe_project.id).watermark_ns == 3
    assert unit_values(writer, metric, ids)["results"] == expected
    trace, span = json.loads(ids[0])
    writer.store.execute(
        "INSERT INTO spans SELECT * REPLACE('unproven' AS id) FROM spans "
        "WHERE trace_id = %(trace)s AND id = %(span)s",
        {"trace": trace, "span": span},
    )
    with pytest.raises(MetricAdmissionUnproven, match="metric_admission_unproven"):
        writer.refresh(
            ChangeBatch(
                (SourceKey(trace, "unproven"),), (), (), (), (), {"watermark_ns": 4}
            ),
            [definition],
            Scope(writer.org_id, writer.workspace_id, observe_project.id),
            start=AT,
            end=WINDOW["window_end"],
            as_of=datetime.now(UTC),
        )
    assert (
        MetricProjectRefresh.for_project(observe_project.id).head_manifest_id
        == refreshed.id
    )
    # The upgraded evidence is also sufficient for later physical retirement.
    writer.store.execute("TRUNCATE TABLE spans")
    writer.retention_days = lambda: 1
    assert expire_project(writer, [definition], retention_days=writer.retention_days)[
        "purged"
    ]
    assert reads.read_kpi(metric, WINDOW, store=writer.store)["current"]["value"] == 4


def two_publications(writer, workspace, project):
    definition = span_definition_for(writer, workspace, project)
    scope = Scope(writer.org_id, writer.workspace_id, project.id)
    trace, session = uuid4(), uuid4()
    batches = []
    for batch in range(2):
        at = AT + timedelta(minutes=5 + batch * 15)
        deps = []
        for index in range(3):
            name = f"{batch}-{index}"
            seed_span_revision(
                writer,
                scope,
                trace,
                name,
                session,
                at,
                batch * 3 + index + 1,
                revision=1,
            )
            deps.append(
                Dependency(str(trace), name, str(session), str(session), "", "", at)
            )
        head = writer.refresh(
            change_for(session, cursor=batch + 1, dependencies=deps),
            [definition],
            scope,
            start=AT,
            end=WINDOW["window_end"],
            as_of=datetime.now(UTC),
        )
        batches.append(deps)
    return definition, scope, trace, session, head, batches


def test_legacy_backfill_makes_bounded_progress_without_replacing_uncovered_buckets(
    live_writer, workspace, observe_project, monkeypatch
):
    writer = live_writer
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_ROWS", 3)
    definition, scope, trace, session, head, batches = two_publications(
        writer, workspace, observe_project
    )
    expected = metric_bucket_rows(writer, head, definition)
    legacy_head(writer, head)
    writer.retention_days = lambda: (datetime.now(UTC) - AT).days + 5
    for index, deps in enumerate(batches):
        head = writer.refresh(
            change_for(session, cursor=index + 3, dependencies=deps),
            [definition],
            scope,
            start=AT,
            end=WINDOW["window_end"],
            as_of=datetime.now(UTC),
            purpose="backfill",
        )
        fields, params = committed_rows_select(
            FIELD_VALUES, head, org_id=writer.org_id, project_id=observe_project.id
        )
        assert writer.store.execute(
            f"SELECT count() FROM ({fields}) WHERE is_deleted = 0", params
        ) == [((index + 1) * 3,)]
        assert metric_bucket_rows(writer, head, definition) == expected


def test_new_bucket_cannot_mask_uncovered_legacy_expiry(
    live_writer, workspace, observe_project
):
    writer = live_writer
    definition, scope, trace, session, head, batches = two_publications(
        writer, workspace, observe_project
    )
    legacy = legacy_head(writer, head)
    writer.retention_days = lambda: 1
    at = datetime.now(UTC) - timedelta(hours=1)
    deps = []
    for index in range(7):
        name = f"new-{index}"
        seed_span_revision(writer, scope, trace, name, session, at, 100, revision=1)
        deps.append(
            Dependency(str(trace), name, str(session), str(session), "", "", at)
        )
    with pytest.raises(MetricAdmissionUnproven, match="requires backfill"):
        writer.refresh(
            change_for(session, cursor=3, dependencies=deps),
            [definition],
            scope,
            start=at - timedelta(hours=1),
            end=at + timedelta(hours=1),
            as_of=datetime.now(UTC),
            purpose="backfill",
        )
    assert (
        MetricProjectRefresh.for_project(observe_project.id).head_manifest_id
        == legacy.id
    )
    assert metric_bucket_rows(writer, legacy, definition) == metric_bucket_rows(
        writer, head, definition
    )


@pytest.mark.parametrize("budget", ["rows", "bytes"])
def test_idle_retirement_streams_multiple_admitted_batches_and_terminal_moves(
    live_writer, workspace, observe_project, monkeypatch, budget
):
    writer = live_writer
    # Six identities exceed the per-capture bound but each three-row publication
    # is admitted under it. This is the 30k + 30k / 50k failure with smaller data.
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_ROWS", 3)
    definition, scope, trace, session, head, batches = two_publications(
        writer, workspace, observe_project
    )
    if budget == "bytes":
        sql, params = committed_rows_select(
            FIELD_VALUES, head, org_id=writer.org_id, project_id=observe_project.id
        )
        columns = ", ".join(c for c, _ in SOURCE_SCHEMA if c != "source_rows")
        sizes = writer.store.execute(
            f"SELECT sum(length(formatRow('RowBinary', {columns}, toUInt64(1)))) FROM ({sql}) GROUP BY bucket_start",
            params,
        )
        bound = max(row[0] for row in sizes)
        assert sum(row[0] for row in sizes) > bound
        monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_ROWS", 50_000)
        monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_BYTES", bound)
    # A terminal correction crosses chunk boundaries. It contributes once at its
    # new anchor; the second page must not recapture that identity.
    seed_span_revision(
        writer, scope, trace, "0-0", session, AT + timedelta(minutes=20), 10, revision=2
    )
    captures = []
    original = span_evidence.captured_span_fields

    @contextmanager
    def recorded(*args, **kwargs):
        with original(*args, **kwargs) as captured:
            captures.append((captured[3], captured[4]))
            yield captured

    monkeypatch.setattr(span_evidence, "captured_span_fields", recorded)
    writer.retention_days = lambda: 1
    assert expire_project(writer, [definition], retention_days=writer.retention_days)[
        "purged"
    ]
    assert len(captures) >= 2
    assert sum(count for count, _ in captures) == 6
    assert all(
        count <= span_evidence.SPAN_CAPTURE_ROWS
        and size <= span_evidence.SPAN_CAPTURE_BYTES
        for count, size in captures
    )
    new_head = MetricProjectRefresh.for_project(observe_project.id).head_manifest
    assert [row[2:] for row in metric_bucket_rows(writer, new_head, definition)] == [
        (2, 5.0, 2.0),
        (4, 25.0, 4.0),
    ]
    for name in ("metric_field_values", "metric_field_values_checkpoint"):
        assert writer.store.execute(f"SELECT count() FROM {name}") == [(0,)]
    assert writer.store.execute(
        "SELECT count() FROM system.tables WHERE is_temporary AND (startsWith(name, 'metric_span_capture_') OR startsWith(name, 'metric_frozen_'))"
    ) == [(0,)]
    assert not expire_project(
        writer, [definition], retention_days=writer.retention_days
    )["purged"]


def test_retirement_capture_failure_does_not_publish_partial_anonymous_history(
    live_writer, workspace, observe_project, monkeypatch
):
    writer = live_writer
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_ROWS", 3)
    definition, scope, trace, session, head, batches = two_publications(
        writer, workspace, observe_project
    )
    expected = metric_bucket_rows(writer, head, definition)
    original, captures = span_evidence.captured_span_fields, []

    @contextmanager
    def fail_second(*args, **kwargs):
        captures.append(True)
        if len(captures) == 2:
            raise RuntimeError("injected second retirement capture failure")
        with original(*args, **kwargs) as captured:
            yield captured

    monkeypatch.setattr(span_evidence, "captured_span_fields", fail_second)
    writer.retention_days = lambda: 1
    with pytest.raises(RuntimeError, match="second retirement capture"):
        expire_project(writer, [definition], retention_days=writer.retention_days)
    assert (
        MetricProjectRefresh.for_project(observe_project.id).head_manifest_id == head.id
    )
    assert metric_bucket_rows(writer, head, definition) == expected
    assert writer.store.execute("SELECT count() FROM metric_bucket_retired_parts") == [
        (0,)
    ]
    assert writer.store.execute(
        "SELECT count() FROM system.tables WHERE is_temporary AND (startsWith(name, 'metric_span_capture_') OR startsWith(name, 'metric_frozen_'))"
    ) == [(0,)]
    monkeypatch.setattr(span_evidence, "captured_span_fields", original)
    assert expire_project(writer, [definition], retention_days=writer.retention_days)[
        "purged"
    ]
    head = MetricProjectRefresh.for_project(observe_project.id).head_manifest
    assert metric_bucket_rows(writer, head, definition) == expected
