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
from tracer.services.derived_metrics.service import MetricUnavailableError
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
    READ_CUT,
    publish,
    source_policy_matches_writer,  # noqa: F401
    unit_values,
)

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("enabled_metrics"),
]


def assert_expired_span_copies_removed(writer, definition):
    """All seeded January units are before the one-day cutoff; truth is empty."""
    head = MetricProjectRefresh.for_project(writer.lease.project_id).head_manifest
    assert metric_bucket_rows(writer, head, definition) == []
    for name in (
        "metric_field_values",
        "metric_field_values_checkpoint",
        "metric_bucket_totals",
        "metric_bucket_totals_checkpoint",
        "metric_bucket_retired_parts",
        "metric_unit_state",
        "metric_source_dependencies",
    ):
        assert writer.store.execute(f"SELECT count() FROM {name}") == [(0,)], name
    assert writer.store.execute(
        "SELECT count() FROM system.tables WHERE is_temporary AND (startsWith(name, 'metric_span_capture_') OR startsWith(name, 'metric_frozen_'))"
    ) == [(0,)]


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
            {"watermark_ns": READ_CUT + 1},
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
    assert (
        MetricProjectRefresh.for_project(observe_project.id).watermark_ns
        == READ_CUT + 1
    )
    # Normal refresh can resume, using the published fields as identity proof
    # even when this legacy producer had no dependency rows to migrate.
    refreshed = writer.refresh(
        ChangeBatch(
            sources,
            tuple(UnitKey("span", i) for i in ids),
            (),
            (),
            (),
            {"watermark_ns": READ_CUT + 2},
        ),
        [definition],
        Scope(writer.org_id, writer.workspace_id, observe_project.id),
        start=AT,
        end=WINDOW["window_end"],
        as_of=datetime.now(UTC),
    )
    assert refreshed.id != upgraded.id
    assert (
        MetricProjectRefresh.for_project(observe_project.id).watermark_ns
        == READ_CUT + 2
    )
    assert unit_values(writer, metric, ids)["results"] == expected
    trace, span = json.loads(ids[0])
    writer.store.execute(
        "INSERT INTO spans SELECT * REPLACE('unproven' AS id) FROM spans "
        "WHERE trace_id = %(trace)s AND id = %(span)s",
        {"trace": trace, "span": span},
    )
    # This expiry retired no live unit (retention covered all the data), so
    # no retired evidence exists that an unknown span could duplicate: it is
    # admitted (10M B5). Admission proof after a live retirement is covered by
    # test_units_arriving_around_a_live_purge_are_admitted_without_proof.
    assert not MetricProjectRefresh.for_project(
        observe_project.id
    ).head_manifest.published_by.source_ceilings.get("retention_retired_live")
    admitted = writer.refresh(
        ChangeBatch(
            (SourceKey(trace, "unproven"),),
            (),
            (),
            (),
            (),
            {"watermark_ns": READ_CUT + 3},
        ),
        [definition],
        Scope(writer.org_id, writer.workspace_id, observe_project.id),
        start=AT,
        end=WINDOW["window_end"],
        as_of=datetime.now(UTC),
    )
    assert admitted.id != refreshed.id
    # 'unproven' is a distinct live span id (not a revision of a counted one),
    # inside retention; the KPI equals the truth computed from the live spans.
    live = writer.store.execute(
        "SELECT id, attrs_number['y'] FROM spans WHERE project_id = %(project)s "
        "AND start_time >= %(start)s AND start_time < %(end)s "
        "AND mapContains(attrs_number, 'y') ORDER BY id",
        {
            "project": str(observe_project.id),
            "start": WINDOW["window_start"],
            "end": WINDOW["window_end"],
        },
    )
    ids_live = [row[0] for row in live]
    assert len(ids_live) == len(set(ids_live)) and "unproven" in ids_live
    truth = sum(row[1] for row in live) / len(live)
    assert truth == pytest.approx(10 / 3)  # mean(2, 6, 2)
    assert reads.read_kpi(metric, WINDOW, store=writer.store)["current"][
        "value"
    ] == pytest.approx(truth)
    # The upgraded evidence is also sufficient for later physical retirement.
    writer.store.execute("TRUNCATE TABLE spans")
    writer.retention_days = lambda: 1
    assert expire_project(writer, [definition], retention_days=writer.retention_days)[
        "purged"
    ]
    # Source policy, not the pre-expiry KPI, is the oracle: the whole
    # January window is expired, including its aggregate contributions.
    with pytest.raises(MetricUnavailableError) as expired:
        reads.read_kpi(metric, WINDOW, store=writer.store)
    assert expired.value.code == "retention_expired"
    assert_expired_span_copies_removed(writer, definition)


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


@pytest.mark.parametrize("purpose", ["incremental", "backfill"])
def test_same_bucket_arrivals_cannot_mask_uncovered_legacy_partial_expiry(
    live_writer, workspace, observe_project, monkeypatch, purpose
):
    writer = live_writer
    wall = datetime.now(UTC).replace(second=0, microsecond=0)
    now = wall.replace(minute=wall.minute // 15 * 15 + 7)
    if now > wall:
        now -= timedelta(minutes=15)
    writer.lease.clock = lambda: now
    writer.lease.renew()
    cutoff = now - timedelta(days=1)
    bucket = cutoff.replace(minute=cutoff.minute // 15 * 15)
    definition = span_definition_for(writer, workspace, observe_project)
    scope = Scope(writer.org_id, writer.workspace_id, observe_project.id)
    trace, session = uuid4(), uuid4()
    old = []
    for index in range(2):
        at = cutoff - timedelta(minutes=index + 1)
        name = f"old-{index}"
        seed_span_revision(writer, scope, trace, name, session, at, 10, revision=1)
        old.append(Dependency(str(trace), name, str(session), str(session), "", "", at))
    head = writer.refresh(
        change_for(session, dependencies=old),
        [definition],
        scope,
        start=bucket,
        end=now,
        as_of=now,
    )
    expected = metric_bucket_rows(writer, head, definition)
    assert [row[2:] for row in expected] == [(2, 20.0, 2.0)]
    legacy = legacy_head(writer, head)
    fields, params = committed_rows_select(
        FIELD_VALUES, legacy, org_id=writer.org_id, project_id=observe_project.id
    )
    assert writer.store.execute(
        f"SELECT count() FROM ({fields}) WHERE is_deleted = 0", params
    ) == [(0,)]
    new = []
    for index in range(2):
        at = cutoff + timedelta(minutes=index + 1)
        name = f"new-{index}"
        seed_span_revision(writer, scope, trace, name, session, at, 100, revision=1)
        new.append(Dependency(str(trace), name, str(session), str(session), "", "", at))
    captures = []
    original = span_evidence.captured_span_fields

    @contextmanager
    def recorded(*args, **kwargs):
        with original(*args, **kwargs) as captured:
            captures.append(
                writer.store.execute(
                    f"SELECT unit_id, bucket_start FROM ({captured[0]}) ORDER BY unit_id"
                )
            )
            yield captured

    monkeypatch.setattr(span_evidence, "captured_span_fields", recorded)
    writer.retention_days = lambda: 1
    with pytest.raises(MetricAdmissionUnproven, match="requires backfill"):
        writer.refresh(
            change_for(session, cursor=2, dependencies=new),
            [definition],
            scope,
            start=bucket,
            end=now,
            as_of=now,
            purpose=purpose,
        )
    # The proposed count equals the legacy count in exactly the same bucket,
    # but it contains only retained arrivals, not either retiring identity.
    assert captures == [
        [
            (json.dumps([str(trace), f"new-{index}"], separators=(",", ":")), bucket)
            for index in range(2)
        ]
    ]
    state = MetricProjectRefresh.for_project(observe_project.id)
    assert state.head_manifest_id == legacy.id
    assert state.watermark_ns == 1
    assert metric_bucket_rows(writer, legacy, definition) == expected
    assert writer.store.execute("SELECT count() FROM metric_bucket_retired_parts") == [
        (0,)
    ]


@pytest.mark.parametrize("budget", ["rows", "bytes"])
def test_idle_expiry_discards_all_batches_without_recapturing_terminal_moves(
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
    # Every source anchor (including the terminal move) predates the cutoff.
    # No source correction can produce a retained contribution. In particular,
    # expiry must not recapture expired values just to keep anonymous history.
    assert captures == []
    assert_expired_span_copies_removed(writer, definition)
    assert not expire_project(
        writer, [definition], retention_days=writer.retention_days
    )["purged"]


def test_expiry_ignores_source_corrections_that_exceed_capture_byte_budget(
    live_writer, workspace, observe_project, monkeypatch
):
    writer = live_writer
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_ROWS", 3)
    definition, scope, trace, session, head, batches = two_publications(
        writer, workspace, observe_project
    )
    fields, params = committed_rows_select(
        FIELD_VALUES, head, org_id=writer.org_id, project_id=observe_project.id
    )
    columns = ", ".join(c for c, _ in SOURCE_SCHEMA if c != "source_rows")
    sizes = writer.store.execute(
        f"SELECT length(formatRow('RowBinary', {columns}, toUInt64(1))) "
        f"FROM ({fields}) ORDER BY unit_id",
        params,
    )
    assert len(sizes) == 6 and len({size for (size,) in sizes}) == 1
    row_bytes = sizes[0][0]
    bound = 3 * row_bytes
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_BYTES", bound)
    # Committed preimages fit three per page. Only the second page grows: each
    # corrected field still fits alone, while the whole page exceeds the cap.
    group = "w" * (len("west") + row_bytes)
    for index in range(3):
        seed_span_revision(
            writer,
            scope,
            trace,
            f"1-{index}",
            session,
            AT + timedelta(minutes=20),
            10 + index,
            revision=2,
            group=group,
        )

    # The old anonymous-retirement implementation re-evaluated these expired
    # sources, overflowed on page two, and restarted. No page is retainable
    # under the current contract, so source capture must not run at all.
    @contextmanager
    def forbidden_capture(*args, **kwargs):
        raise AssertionError("expired source was recaptured")
        yield  # pragma: no cover -- context-manager protocol

    monkeypatch.setattr(span_evidence, "captured_span_fields", forbidden_capture)
    writer.retention_days = lambda: 1
    assert expire_project(writer, [definition], retention_days=writer.retention_days)[
        "purged"
    ]
    assert_expired_span_copies_removed(writer, definition)
    assert not expire_project(
        writer, [definition], retention_days=writer.retention_days
    )["purged"]


def test_expiry_freeze_failure_does_not_publish_partial_replacement(
    live_writer, workspace, observe_project, monkeypatch
):
    writer = live_writer
    monkeypatch.setattr(span_evidence, "SPAN_CAPTURE_ROWS", 3)
    definition, scope, trace, session, head, batches = two_publications(
        writer, workspace, observe_project
    )
    expected = metric_bucket_rows(writer, head, definition)
    original, freezes = writer.freeze, []

    def fail_second(*args, **kwargs):
        freezes.append(True)
        if len(freezes) == 2:
            raise RuntimeError("injected second expiry freeze failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(writer, "freeze", fail_second)
    writer.retention_days = lambda: 1
    with pytest.raises(RuntimeError, match="second expiry freeze"):
        expire_project(writer, [definition], retention_days=writer.retention_days)
    assert len(freezes) == 2
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
    monkeypatch.setattr(writer, "freeze", original)
    assert expire_project(writer, [definition], retention_days=writer.retention_days)[
        "purged"
    ]
    assert_expired_span_copies_removed(writer, definition)
