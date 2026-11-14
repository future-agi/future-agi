# ruff: noqa: F811 -- shared isolated ClickHouse and PG fixtures
"""Writer-owned committed span evidence, never read-time raw-span evaluation."""

import json
from datetime import UTC, datetime

import pytest
from clickhouse_driver.errors import ServerException

from tracer.models.derived_metric import MetricProjectRefresh
from tracer.services.derived_metrics import reads, span_evidence
from tracer.services.derived_metrics.publication import committed_rows_select
from tracer.services.derived_metrics.retention import expire_project
from tracer.services.derived_metrics.storage import FIELD_VALUES
from tracer.tests.test_derived_metric_drivers_clickhouse import published_fixture
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_reads_clickhouse import WINDOW

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("enabled_metrics"),
]


def publish(writer, workspace, project, values=((2, 1), (6, 1), (None, 1))):
    return published_fixture(
        writer,
        workspace,
        project,
        kind="average",
        formula="mean(y)",
        values=values,
        level="span",
    )


def unit_values(writer, metric, ids):
    return reads.read_units(
        metric,
        {**WINDOW, "context": "spans", "unit_ids": ids},
        store=writer.store,
        retention_days=lambda: 0,
    )


def test_span_values_and_drivers_survive_raw_deletion_and_compaction(
    live_writer, workspace, observe_project
):
    writer = live_writer
    metric, definition, head, ids = publish(writer, workspace, observe_project)
    expected = unit_values(writer, metric, ids)
    assert [r["value_number"] for r in expected["results"]] == [2, 6, None]
    assert [r["status"] for r in expected["results"]] == ["value", "value", "no_value"]
    assert expected["results"][0]["inputs"]["y"] == 2
    driver = reads.read_units(
        metric, WINDOW, drivers=True, store=writer.store, retention_days=lambda: 0
    )
    assert len(driver["results"]) == 1
    assert driver["results"][0]["unit_id"] == ids[0]
    assert driver["results"][0]["influence"] == -2
    writer.store.execute("TRUNCATE TABLE spans")
    assert unit_values(writer, metric, ids) == expected
    assert (
        reads.read_units(
            metric, WINDOW, drivers=True, store=writer.store, retention_days=lambda: 0
        )
        == driver
    )
    compacted = writer.compact()
    assert compacted.id != head.id
    actual = unit_values(writer, metric, ids)
    assert actual["results"] == expected["results"]
    assert actual["provenance"]["manifest_id"] == compacted.id
    assert reads.read_kpi(metric, WINDOW, store=writer.store)["current"]["value"] == 4


def test_span_totals_use_frozen_fields_even_if_source_changes_before_bucket_build(
    live_writer, workspace, observe_project, monkeypatch
):
    writer = live_writer
    original = writer._freeze_buckets
    builds = []

    def erase_source(*args, **kwargs):
        builds.append(True)
        writer.store.execute("TRUNCATE TABLE spans")
        return original(*args, **kwargs)

    monkeypatch.setattr(writer, "_freeze_buckets", erase_source)
    metric, _, _, ids = publish(writer, workspace, observe_project)
    assert builds
    assert [r["value_number"] for r in unit_values(writer, metric, ids)["results"]] == [
        2,
        6,
        None,
    ]
    assert reads.read_kpi(metric, WINDOW, store=writer.store)["current"]["value"] == 4


def test_span_capture_overflow_never_publishes_partial_evidence_or_totals(
    live_writer, workspace, observe_project, monkeypatch
):
    """One identity over the byte budget on its own: paging cannot shrink it."""
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_BYTES", 1)
    with pytest.raises(ServerException, match="metric_span_capture_budget"):
        publish(live_writer, workspace, observe_project)
    assert (
        MetricProjectRefresh.objects.get(project=observe_project).head_manifest_id
        is None
    )
    assert live_writer.store.execute("SELECT count() FROM metric_field_values") == [
        (0,)
    ]
    assert live_writer.store.execute("SELECT count() FROM metric_bucket_totals") == [
        (0,)
    ]
    assert live_writer.store.execute(
        "SELECT count() FROM system.tables WHERE is_temporary AND startsWith(name, 'metric_span_capture_')"
    ) == [(0,)]


def test_span_capture_above_the_row_budget_is_paged_by_identity_not_refused(
    live_writer, workspace, observe_project, monkeypatch
):
    """Three spans, a row budget of two: two identity pages, the same values,
    drivers and KPI as one capture, and no page above the budget."""
    from datetime import timedelta

    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_ROWS", 2)
    since = datetime.now(UTC) - timedelta(seconds=1)
    writer = live_writer
    metric, _, _, ids = publish(writer, workspace, observe_project)
    results = unit_values(writer, metric, ids)["results"]
    assert [r["value_number"] for r in results] == [2, 6, None]
    assert [r["status"] for r in results] == ["value", "value", "no_value"]
    assert reads.read_kpi(metric, WINDOW, store=writer.store)["current"]["value"] == 4
    writer.store.execute("SYSTEM FLUSH LOGS")
    pages = writer.store.execute(
        "SELECT written_rows FROM system.query_log WHERE type = 'QueryFinish' "
        "AND query LIKE 'INSERT INTO metric_span_capture_%%' "
        "AND event_time >= %(since)s",
        {"since": since.replace(tzinfo=None)},
    )
    assert len(pages) >= 2 and max(rows for (rows,) in pages) <= 2, pages


def test_span_retirement_uses_committed_values_after_raw_source_is_gone(
    live_writer, workspace, observe_project
):
    writer = live_writer
    metric, definition, _, ids = publish(writer, workspace, observe_project)
    before = reads.read_kpi(metric, WINDOW, store=writer.store)["current"]
    writer.store.execute("TRUNCATE TABLE spans")
    writer.retention_days = lambda: 1
    result = expire_project(
        writer, [definition], retention_days=lambda: 1, now=datetime.now(UTC)
    )
    assert result["purged"] is True
    assert reads.read_kpi(metric, WINDOW, store=writer.store)["current"] == before
    for name in ("metric_field_values", "metric_field_values_checkpoint"):
        assert writer.store.execute(
            f"SELECT count() FROM {name} WHERE level = 'span'"
        ) == [(0,)]
    with pytest.raises(reads.MetricUnavailableError, match="expired"):
        reads.read_units(
            metric, WINDOW, drivers=True, store=writer.store, retention_days=lambda: 1
        )


def test_span_frozen_batches_are_bounded_and_ids_include_trace(
    live_writer, workspace, observe_project
):
    writer = live_writer
    metric, definition, head, ids = publish(
        writer, workspace, observe_project, values=[(i, 1) for i in range(501)]
    )
    plans = list(writer.plans(head.published_by, kind="field_values"))
    assert len(plans) == 2
    assert [len(p["rows"]) for p in plans] == [500, 1]
    assert len({tuple(json.loads(i)) for i in ids}) == 501
    sql, params = committed_rows_select(
        FIELD_VALUES, head, org_id=writer.org_id, project_id=observe_project.id
    )
    assert writer.store.execute(
        f"SELECT count(), uniqExact(unit_id) FROM ({sql}) WHERE level = 'span' AND is_deleted = 0",
        params,
    ) == [(501, 501)]
    assert (
        unit_values(writer, metric, [ids[0], ids[-1]])["results"][1]["value_number"]
        == 500
    )


def test_span_partial_stage_keeps_old_evidence_then_replays_exact_bytes(
    live_writer, workspace, observe_project, monkeypatch
):
    from tracer.services.derived_metrics.changes import ChangeBatch, SourceKey, UnitKey
    from tracer.services.derived_metrics.queries import Scope
    from tracer.tests.test_derived_metric_reads_clickhouse import AT

    writer = live_writer
    metric, definition, _, ids = publish(writer, workspace, observe_project)
    old = unit_values(writer, metric, ids)["results"]
    trace, span = json.loads(ids[0])
    writer.store.execute(
        "INSERT INTO spans SELECT * REPLACE(toUInt64(_version + 1) AS _version, map('y', 10., 'x', 1.) AS attrs_number) "
        "FROM spans WHERE trace_id = %(trace)s AND id = %(span)s",
        {"trace": trace, "span": span},
    )
    original = writer.store.insert
    staged = []

    def fail_after_fields(table, payload):
        original(table, payload)
        if table == FIELD_VALUES:
            staged.append(True)
            assert unit_values(writer, metric, ids)["results"] == old
            raise RuntimeError("injected span evidence staging failure")

    monkeypatch.setattr(writer.store, "insert", fail_after_fields)
    with pytest.raises(RuntimeError, match="span evidence staging"):
        writer.refresh(
            ChangeBatch(
                (SourceKey(trace, span),),
                (UnitKey("span", ids[0]),),
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
        )
    assert staged
    assert unit_values(writer, metric, ids)["results"] == old
    monkeypatch.setattr(writer.store, "insert", original)
    attempt = observe_project.metric_refresh_attempts.get(state="staging")
    writer.store.execute("TRUNCATE TABLE spans")
    writer.stage_and_publish(attempt)
    result = unit_values(writer, metric, ids)["results"]
    assert [r["value_number"] for r in result] == [10, 6, None]
    assert reads.read_kpi(metric, WINDOW, store=writer.store)["current"]["value"] == 8


def test_legacy_bucket_only_manifest_does_not_claim_complete_span_drivers(
    live_writer, workspace, observe_project
):
    from tracer.services.derived_metrics.storage import BUCKET_TOTALS

    writer = live_writer
    metric, _, head, _ = publish(writer, workspace, observe_project)
    legacy = writer.begin(purpose="compaction")
    writer.freeze_current(legacy, BUCKET_TOTALS, head)
    writer.seal(legacy)
    writer.stage_and_publish(legacy)
    with pytest.raises(reads.MetricUnavailableError, match="incomplete"):
        reads.read_units(
            metric, WINDOW, drivers=True, store=writer.store, retention_days=lambda: 0
        )
    assert reads.read_kpi(metric, WINDOW, store=writer.store)["current"]["value"] == 4
    from tracer.services.derived_metrics.refresh import MetricAdmissionUnproven

    writer.retention_days = lambda: 1
    with pytest.raises(MetricAdmissionUnproven, match="requires backfill"):
        expire_project(
            writer, [metric.active_version], retention_days=writer.retention_days
        )
    assert (
        MetricProjectRefresh.for_project(
            observe_project.id
        ).head_manifest.published_by_id
        == legacy.id
    )


def test_span_text_sketch_evidence_and_repeated_span_id_are_typed_and_trace_scoped(
    live_writer, workspace, observe_project
):
    from uuid import uuid4

    from tracer.models.derived_metric import DerivedMetric, DerivedMetricVersion
    from tracer.services.derived_metrics.changes import ChangeBatch, SourceKey, UnitKey
    from tracer.services.derived_metrics.queries import Scope
    from tracer.tests.test_derived_metric_reads_clickhouse import AT
    from tracer.tests.test_derived_metric_refresh_clickhouse import seed_span_revision

    writer = live_writer
    scope = Scope(writer.org_id, writer.workspace_id, observe_project.id)
    metric = DerivedMetric.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        project=observe_project,
        name="Span regions",
    )
    definition = DerivedMetricVersion.objects.create(
        metric=metric,
        version=1,
        kind="unique_count",
        level="span",
        selection_unit="span",
        inputs=[
            {
                "alias": "region",
                "source": "span",
                "field": "attribute_text",
                "attribute_key": "region",
                "reducer": "latest",
                "value_type": "text",
            }
        ],
        backfill_status="complete",
        weighting="none",
    )
    sources, ids = [], []
    for index, group in enumerate(["west", "east", "west"]):
        trace = uuid4()
        seed_span_revision(
            writer,
            scope,
            trace,
            "same-span",
            uuid4(),
            AT,
            index,
            revision=1,
            group=group,
        )
        sources.append(SourceKey(str(trace), "same-span"))
        ids.append(json.dumps([str(trace), "same-span"], separators=(",", ":")))
    writer.refresh(
        ChangeBatch(
            tuple(sources),
            tuple(UnitKey("span", i) for i in ids),
            (),
            (),
            (),
            {"watermark_ns": 1},
        ),
        [definition],
        scope,
        start=AT,
        end=WINDOW["window_end"],
        as_of=datetime.now(UTC),
    )
    DerivedMetric.activate_version(metric.id, definition.id, expected_active_id=None)
    metric = DerivedMetric.objects.select_related("project", "active_version").get(
        pk=metric.id
    )
    rows = unit_values(writer, metric, ids)["results"]
    assert [r["value_text"] for r in rows] == ["west", "east", "west"]
    assert [r["inputs_text"]["region"] for r in rows] == ["west", "east", "west"]
    assert reads.read_kpi(metric, WINDOW, store=writer.store)["current"]["value"] == 2
    with pytest.raises(reads.MetricUnavailableError, match="sketches"):
        reads.read_units(
            metric, WINDOW, drivers=True, store=writer.store, retention_days=lambda: 0
        )


def test_repeated_partial_bucket_retirement_never_recaptures_already_retired_raw_span(
    live_writer, workspace, observe_project, settings
):
    from datetime import timedelta
    from uuid import uuid4

    from tracer.services.derived_metrics.changes import Dependency
    from tracer.services.derived_metrics.queries import Scope
    from tracer.tests.test_derived_metric_refresh_clickhouse import (
        change_for,
        metric_bucket_rows,
        seed_span_revision,
        span_definition_for,
    )

    # Two expiries two minutes apart: expire on each (physical expiry is
    # otherwise batched per METRICS_EXPIRY_INTERVAL_SECONDS).
    settings.METRICS_EXPIRY_INTERVAL_SECONDS = 60
    writer = live_writer
    wall = datetime.now(UTC).replace(second=0, microsecond=0)
    now = wall.replace(minute=wall.minute // 15 * 15 + 7)
    if now > wall:
        now -= timedelta(minutes=15)
    writer.lease.clock = lambda: now
    writer.lease.renew()
    definition = span_definition_for(writer, workspace, observe_project)
    scope = Scope(writer.org_id, writer.workspace_id, observe_project.id)
    session, trace = uuid4(), uuid4()
    cutoff = now - timedelta(days=1)
    dependencies = []
    for name, at, value in [
        ("old", cutoff - timedelta(minutes=1), 4),
        ("young", cutoff + timedelta(minutes=1), 6),
    ]:
        seed_span_revision(writer, scope, trace, name, session, at, value, revision=1)
        dependencies.append(
            Dependency(str(trace), name, str(session), str(session), "", "", at)
        )
    writer.refresh(
        change_for(session, dependencies=dependencies),
        [definition],
        scope,
        start=cutoff - timedelta(days=1),
        end=now,
        as_of=now,
    )
    writer.retention_days = lambda: 1
    assert expire_project(
        writer, [definition], retention_days=writer.retention_days, now=now
    )["purged"]
    seed_span_revision(
        writer,
        scope,
        trace,
        "old",
        session,
        cutoff - timedelta(minutes=1),
        100,
        revision=2,
    )
    now += timedelta(minutes=2)
    assert expire_project(
        writer, [definition], retention_days=writer.retention_days, now=now
    )["purged"]
    head = MetricProjectRefresh.for_project(observe_project.id).head_manifest
    assert [r[2:] for r in metric_bucket_rows(writer, head, definition)] == [
        (2, 10.0, 2.0)
    ]
    for name in ("metric_field_values", "metric_field_values_checkpoint"):
        assert writer.store.execute(
            f"SELECT count() FROM {name} WHERE level = 'span'"
        ) == [(0,)]


def test_span_retractions_share_the_capture_budget(
    live_writer, workspace, observe_project, monkeypatch
):
    """Retractions count toward a page's budget: three retractions with a
    budget of two are paged by identity, each identity retracted exactly once,
    never one unbounded tombstone stream and never refused."""
    from tracer.services.derived_metrics.changes import ChangeBatch, SourceKey, UnitKey
    from tracer.services.derived_metrics.queries import Scope
    from tracer.tests.test_derived_metric_reads_clickhouse import AT

    writer = live_writer
    metric, definition, _, ids = publish(writer, workspace, observe_project)
    writer.store.execute("TRUNCATE TABLE spans")
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_ROWS", 2)
    trace, span = json.loads(ids[0])
    head = writer.refresh(
        ChangeBatch(
            (SourceKey(trace, span),),
            (UnitKey("span", ids[0]),),
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
    )
    assert MetricProjectRefresh.objects.get(project=observe_project).watermark_ns == 2
    frozen = writer.store.execute(
        "SELECT unit_id, is_deleted FROM metric_field_values "
        "WHERE attempt_id = %(attempt)s AND level = 'span' ORDER BY unit_id",
        {"attempt": str(head.published_by_id)},
    )
    assert frozen == [(unit, 1) for unit in sorted(ids)], frozen


def test_subject_erasure_drops_unassignable_span_evidence_in_all_revisions(
    live_writer, workspace, observe_project
):
    from types import SimpleNamespace
    from uuid import uuid4

    from tracer.services.derived_metrics.retention import purge_by_scope

    writer = live_writer
    publish(writer, workspace, observe_project)
    writer.compact()
    result = purge_by_scope(
        writer.store,
        SimpleNamespace(
            id=uuid4(),
            organization_key=writer.org_id,
            project_key=observe_project.id,
            scope_kind="users",
            subject_ids=[str(uuid4())],
        ),
        writer=writer,
    )
    assert result["anonymous_history_dropped"] is True
    for name in ("metric_field_values", "metric_field_values_checkpoint"):
        assert writer.store.execute(
            f"SELECT count() FROM {name} WHERE level = 'span'"
        ) == [(0,)]
