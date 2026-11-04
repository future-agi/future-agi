# ruff: noqa: F811 -- shared isolated database fixtures
"""Real CH rows, publication receipts, pins and page-keyed state hydration."""

from datetime import UTC, datetime, timedelta
from statistics import quantiles
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4

import pytest

from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricVersion,
    MetricGenerationPin,
    MetricStateConflict,
)
from tracer.services.derived_metrics.deletion_protocol import (
    capture_pin,
    revoke_generations,
)
from tracer.services.derived_metrics.list_values import (
    MetricPageUnit,
    hydrate_metric_list_values,
)
from tracer.services.derived_metrics.publication import (
    MetricVisibilityError,
    committed_rows_select,
)
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.storage import FIELD_VALUES, UNIT_STATE
from tracer.tests.test_derived_metric_clickhouse import metric_ch  # noqa: F401
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    publication_store,
)
from tracer.tests.test_derived_metric_refresh_clickhouse import (
    change_for,
    seed_span_revision,
)


@pytest.fixture
def cells(live_writer, observe_project, workspace):
    metric = DerivedMetric.objects.create(
        project=observe_project,
        workspace=workspace,
        organization_id=workspace.organization_id,
        name="List cells",
    )
    version = new_version(metric)
    DerivedMetric.activate_version(metric.id, version.id, expected_active_id=None)
    now = datetime.now(UTC).replace(microsecond=0)
    return (
        live_writer,
        metric,
        version,
        Scope(workspace.organization_id, workspace.id, observe_project.id),
        now,
    )


def new_version(metric, version=1, **kwargs):
    return DerivedMetricVersion.objects.create(
        metric=metric,
        version=version,
        selection_unit="session",
        weighting="none",
        definition_hash=str(version) * 64,
        backfill_status="complete",
        **{
            "formula": "mean(x)",
            "inputs": [{"alias": "x", "source": "span"}],
            "kind": "share",
            "level": "conversation",
            "value_type": "boolean",
            "value_labels": {"match": "Resolved", "other": "Not resolved"},
            **kwargs,
        },
    )


def raw_select(table, rows):
    branches, params = [], {}
    for index, row in enumerate(rows):
        columns = []
        for name, kind in table.schema:
            quoted = kind.replace("'", "\\'")
            if name in row:
                key = f"list_{index}_{name}"
                params[key] = row[name]
                expression = f"CAST(%({key})s, '{quoted}')"
            else:
                expression = f"defaultValueOfTypeName('{quoted}')"
            columns.append(f"{expression} AS {name}")
        branches.append("SELECT " + ", ".join(columns))
    return " UNION ALL ".join(branches), params


def field_row(metric, now, **kwargs):
    return {
        "unit_id": str(uuid4()),
        "session_id": str(uuid4()),
        "field_id": metric.id,
        "field_version": 1,
        "level": "conversation",
        "unit_start": now - timedelta(days=2),
        "bucket_start": now - timedelta(days=2),
        "last_arrival_ns": int((now - timedelta(hours=2)).timestamp()) * 10**9,
        "settled_at": now - timedelta(hours=1),
        "status": "ok",
        "value_bool": 1,
        "sample_probability": 1.0,
        **kwargs,
    }


def publish(cells, rows=(), states=(), *, commit=True):
    writer = cells[0]
    attempt = writer.begin()
    payloads = []
    for table, records in ((FIELD_VALUES, rows), (UNIT_STATE, states)):
        if records:
            sql, params = raw_select(table, records)
            payloads += [
                (table, value) for value in writer.freeze(attempt, table, sql, params)
            ]
    writer.seal(attempt)
    if not commit:
        # Physical rows exist, but their attempt is NOT in a committed manifest.
        for table, payload in payloads:
            writer.store.insert(table, payload)
        return attempt
    return writer.stage_and_publish(attempt)


def hydrate(cells, ids, **kwargs):
    writer, metric, _, scope, now = cells
    return hydrate_metric_list_values(
        writer.store,
        scope,
        metric.id,
        [MetricPageUnit(str(unit)) for unit in ids],
        **{"now": now, "retention_days": lambda: 0, **kwargs},
    )


def test_live_every_cell_state_and_reopening_without_span_end_time(cells):
    _, metric, _, _, now = cells
    rows = [
        field_row(metric, now),
        field_row(metric, now, value_bool=0),
        field_row(
            metric,
            now,
            status="open",
            settled_at=None,
            last_arrival_ns=int(now.timestamp()) * 10**9,
        ),
        field_row(metric, now, status="open", settled_at=None),
        field_row(
            metric,
            now,
            status="no_value",
            no_value_reason="not_evaluated",
            value_bool=None,
        ),
        field_row(
            metric,
            now,
            status="no_value",
            no_value_reason="attribute_absent",
            value_bool=None,
        ),
        field_row(
            metric,
            now,
            status="no_value",
            no_value_reason="zero_denominator",
            value_bool=None,
        ),
        field_row(
            metric,
            now,
            status="no_value",
            no_value_reason="invalid_probability",
            value_bool=None,
        ),
        field_row(
            metric,
            now,
            status="no_value",
            no_value_reason="non_finite",
            value_bool=None,
        ),
        field_row(metric, now, is_deleted=1),
        field_row(metric, now),
        field_row(metric, now),
    ]
    settled_without_field, open_without_field, unknown = (
        str(uuid4()),
        str(uuid4()),
        str(uuid4()),
    )
    states = [
        {
            "unit_id": rows[10]["unit_id"],
            "level": "conversation",
            "first_seen": rows[10]["unit_start"],
            "last_arrival_ns": int(now.timestamp()) * 10**9,
            "reopened": 1,
        },
        {
            "unit_id": rows[11]["unit_id"],
            "level": "conversation",
            "first_seen": rows[11]["unit_start"],
            "last_arrival_ns": int((now - timedelta(hours=1)).timestamp()) * 10**9,
            "is_deleted": 1,
        },
        {
            "unit_id": settled_without_field,
            "level": "conversation",
            "first_seen": now - timedelta(days=1),
            "last_arrival_ns": int((now - timedelta(hours=1)).timestamp()) * 10**9,
            "is_deleted": 1,
        },
        {
            "unit_id": open_without_field,
            "level": "conversation",
            "first_seen": now,
            "last_arrival_ns": int(now.timestamp()) * 10**9,
        },
    ]
    manifest = publish(cells, rows, states)
    ids = [r["unit_id"] for r in rows] + [
        settled_without_field,
        open_without_field,
        unknown,
    ]
    result = hydrate(cells, ids)
    assert list(result) == ids
    assert [r["state"] for r in result.values()] == [
        "value",
        "value",
        "in_progress",
        "not_calculated",
        *(["no_value"] * 6),
        "in_progress",
        "not_calculated",
        "no_value",
        "no_value",
        "no_value",
    ]
    assert result[ids[0]]["value"] is True and result[ids[0]]["label"] == "Resolved"
    assert (
        result[ids[1]]["value"] is False and result[ids[1]]["label"] == "Not resolved"
    )
    for unit in (settled_without_field, open_without_field, unknown):
        assert result[unit]["reason"] == "unknown_anchor"
        assert result[unit]["counts"] == {"eligible": 0, "evaluated": 0, "missing": 0}
    assert all(c["value"] is None for c in list(result.values())[2:])
    assert {c["manifest"] for c in result.values()} == {str(manifest.id)}
    assert not MetricGenerationPin.objects.filter(project_id=cells[3].project).exists()


def test_authoritative_page_anchor_uses_queue_only_for_arrival_floor(cells):
    writer, metric, _, scope, now = cells
    pages = [MetricPageUnit(str(uuid4()), now - timedelta(days=1)) for _ in range(2)]
    states = [
        {
            "unit_id": page.unit_id,
            "level": "conversation",
            "first_seen": now - timedelta(days=20),
            "last_arrival_ns": int((now - quiet).timestamp()) * 10**9,
            "is_deleted": int(index == 0),
        }
        for index, (page, quiet) in enumerate(
            zip(pages, (timedelta(hours=1), timedelta()), strict=True)
        )
    ]
    publish(cells, states=states)
    result = hydrate_metric_list_values(
        writer.store, scope, metric.id, pages, now=now, retention_days=lambda: 7
    )
    assert [c["state"] for c in result.values()] == ["not_calculated", "in_progress"]
    assert all(c["reason"] == "" and c["value"] is None for c in result.values())
    # Without the page's authoritative anchor these identical queue rows prove
    # neither retention nor a known conversation start, even if already settled.
    unknown = hydrate(cells, [p.unit_id for p in pages], retention_days=lambda: 7)
    assert all(c["reason"] == "unknown_anchor" for c in unknown.values())


@pytest.mark.parametrize("retention_days", [0, 7])
def test_epoch_field_and_queue_timestamp_cannot_manufacture_anchor_authority(
    cells, retention_days
):
    _, metric, _, _, now = cells
    row = field_row(metric, now, unit_start=datetime(1970, 1, 1, tzinfo=UTC))
    publish(
        cells,
        [row],
        [
            {
                "unit_id": row["unit_id"],
                "level": "conversation",
                "first_seen": now - timedelta(days=1),
                "last_arrival_ns": row["last_arrival_ns"],
                "is_deleted": 1,
            }
        ],
    )
    result = hydrate(cells, [row["unit_id"]], retention_days=lambda: retention_days)[
        row["unit_id"]
    ]
    assert result["state"] == "no_value" and result["reason"] == "unknown_anchor"
    assert result["value"] is None
    assert result["counts"] == {"eligible": 0, "evaluated": 0, "missing": 0}


@pytest.mark.django_db(transaction=True)
def test_semantic_earliest_member_deletion_then_retention_shrink_uses_live_anchor(
    cells,
):
    writer, metric, old, scope, now = cells
    version = new_version(
        metric,
        version=2,
        kind="average",
        value_type="number",
        inputs=[
            {
                "alias": "x",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "reducer": "latest",
            }
        ],
    )
    DerivedMetric.activate_version(metric.id, version.id, expected_active_id=old.id)
    session, trace = uuid4(), uuid4()
    original_start = now - timedelta(days=11)
    first_refresh = now - timedelta(days=10)
    live_start = now - timedelta(days=1)
    seed_span_revision(
        writer, scope, trace, "earliest", session, original_start, 4, revision=1
    )
    writer.refresh(
        change_for(session),
        [version],
        scope,
        start=original_start - timedelta(days=1),
        end=now + timedelta(days=1),
        as_of=first_refresh,
    )
    # These are source revisions with a semantic tombstone, NOT physical source
    # pruning or post-expiry re-admission. Both refreshes use unlimited retention.
    seed_span_revision(
        writer, scope, trace, "retained", session, live_start, 8, revision=2
    )
    seed_span_revision(
        writer,
        scope,
        trace,
        "earliest",
        session,
        original_start,
        4,
        revision=3,
        deleted=1,
    )
    manifest = writer.refresh(
        change_for(session, cursor=2),
        [version],
        scope,
        start=original_start - timedelta(days=1),
        end=now + timedelta(days=1),
        as_of=now,
    )
    sql, params = committed_rows_select(
        FIELD_VALUES, manifest, org_id=scope.organization, project_id=scope.project
    )
    assert writer.store.execute(
        f"SELECT unit_start, status, value_number, is_deleted FROM ({sql})", params
    ) == [(live_start, "ok", 8.0, 0)]
    sql, params = committed_rows_select(
        UNIT_STATE, manifest, org_id=scope.organization, project_id=scope.project
    )
    assert writer.store.execute(f"SELECT first_seen FROM ({sql})", params) == [
        (first_refresh,)
    ]
    # Shortening the live read policy applies immediately, before any purge. The
    # old queue timestamp cannot mask the newly committed, retained live value.
    result = hydrate(cells, [session], retention_days=lambda: 7)[str(session)]
    assert result["state"] == "value" and result["value"] == 8.0
    assert result["reason"] == "" and result["version"] == version.version
    assert result["manifest"] == str(manifest.id)


def test_latest_committed_revision_collapses_before_status_retention_and_deletion(
    cells,
):
    _, metric, _, _, now = cells
    rows = [field_row(metric, now) for _ in range(3)]
    publish(cells, rows)
    updates = [
        {
            **rows[0],
            "status": "no_value",
            "no_value_reason": "not_evaluated",
            "value_bool": None,
        },
        {**rows[1], "is_deleted": 1},
        {**rows[2], "unit_start": now - timedelta(days=90)},
    ]
    publish(cells, updates)
    draft = publish(
        cells,
        [
            {**r, "value_bool": 1, "is_deleted": 0, "status": "ok", "unit_start": now}
            for r in updates
        ],
        commit=False,
    )
    result = hydrate(cells, [r["unit_id"] for r in rows], retention_days=lambda: 7)
    assert [c["reason"] for c in result.values()] == [
        "not_evaluated",
        "deleted",
        "retention_expired",
    ]
    assert all(c["value"] is None for c in result.values())
    assert draft.state == "prepared"


@pytest.mark.django_db(transaction=True)
def test_checkpoint_and_old_pin_keep_matching_definition_labels_and_manifest(cells):
    writer, metric, version, scope, now = cells
    row = field_row(metric, now)
    old_manifest = publish(cells, [row])
    pin = capture_pin(
        scope.project,
        metric_id=metric.id,
        consumer_kind="request",
        ttl=timedelta(minutes=5),
        now=now,
    )
    draft = new_version(
        metric, version=2, value_labels={"match": "New match", "other": "New other"}
    )
    DerivedMetric.activate_version(metric.id, draft.id, expected_active_id=version.id)
    publish(cells, [{**row, "field_version": 2, "value_bool": 0}])
    checkpoint = writer.compact()
    old = hydrate(cells, [row["unit_id"]], pin=pin)[row["unit_id"]]
    new = hydrate(cells, [row["unit_id"]])[row["unit_id"]]
    assert (old["value"], old["label"], old["version"], old["manifest"]) == (
        True,
        "Resolved",
        1,
        str(old_manifest.id),
    )
    assert (new["value"], new["label"], new["version"], new["manifest"]) == (
        False,
        "New other",
        2,
        str(checkpoint.id),
    )


def test_retention_shortening_masks_old_pin_and_already_purged_no_row(cells):
    _, metric, _, scope, now = cells
    row = field_row(metric, now, unit_start=now - timedelta(days=20))
    publish(cells, [row])
    pin = capture_pin(
        scope.project,
        metric_id=metric.id,
        consumer_kind="request",
        ttl=timedelta(minutes=5),
        now=now,
    )
    policy = iter([0, 7])
    result = hydrate(
        cells, [row["unit_id"]], pin=pin, retention_days=lambda: next(policy)
    )
    assert result[row["unit_id"]]["reason"] == "retention_expired"
    assert result[row["unit_id"]]["value"] is None
    missing = MetricPageUnit(
        str(uuid4()), now - timedelta(days=20), row["last_arrival_ns"]
    )
    result = hydrate_metric_list_values(
        cells[0].store, scope, metric.id, [missing], now=now, retention_days=lambda: 7
    )
    assert result[missing.unit_id]["reason"] == "retention_expired"
    revoke_generations(scope.project, reason="retention_expired")
    with pytest.raises(MetricStateConflict, match="expired"):
        hydrate(cells, [row["unit_id"]], pin=pin)


@pytest.mark.parametrize("value_type,value", [("number", 0.0), ("text", "")])
def test_live_numeric_zero_and_empty_text_keep_typed_values(cells, value_type, value):
    _, metric, version, _, now = cells
    typed = new_version(
        metric,
        version=2,
        kind="average" if value_type == "number" else "unique_count",
        value_type=value_type,
        value_format="decimal" if value_type == "number" else "",
    )
    DerivedMetric.activate_version(metric.id, typed.id, expected_active_id=version.id)
    row = field_row(metric, now, field_version=2, **{f"value_{value_type}": value})
    publish(cells, [row])
    result = hydrate(cells, [row["unit_id"]])[row["unit_id"]]
    assert result["state"] == "value" and result["value"] == value
    assert result["label"] is None and result["value_type"] == value_type


def test_pin_for_another_metric_and_unproven_replica_fail_closed(cells):
    writer, metric, _, scope, now = cells
    row = field_row(metric, now)
    publish(cells, [row])
    pin = capture_pin(
        scope.project,
        metric_id=metric.id,
        consumer_kind="request",
        ttl=timedelta(minutes=5),
        now=now,
    )
    with pytest.raises(MetricStateConflict, match="definition mismatch"):
        hydrate_metric_list_values(
            writer.store,
            scope,
            uuid4(),
            [MetricPageUnit(row["unit_id"])],
            now=now,
            pin=pin,
            retention_days=lambda: 0,
        )
    with patch.object(
        writer.store, "identity", return_value=["wrong-server", "wrong-database"]
    ):
        with pytest.raises(MetricVisibilityError):
            hydrate(cells, [row["unit_id"]])


@pytest.mark.parametrize("level", ["user", "span", "trace"])
def test_wrong_grain_never_falls_through_to_stored_conversation_cells(cells, level):
    _, metric, old, _, now = cells
    version = new_version(
        metric, version=2, level=level, user_rule="any" if level == "user" else ""
    )
    if level == "trace":
        # Historical/corrupt pointer, not an attempt to enable trace creation.
        DerivedMetric.no_workspace_objects.filter(id=metric.id).update(
            active_version=version
        )
    else:
        DerivedMetric.activate_version(metric.id, version.id, expected_active_id=old.id)
    publish(cells)
    with pytest.raises(ValueError, match="grain-specific"):
        hydrate(cells, [uuid4()])


def test_foreign_workspace_scope_is_rejected(cells):
    writer, metric, _, scope, now = cells
    publish(cells)
    with pytest.raises(MetricStateConflict, match="scope mismatch"):
        hydrate_metric_list_values(
            writer.store,
            Scope(scope.organization, uuid4(), scope.project),
            metric.id,
            [MetricPageUnit(str(uuid4()))],
            retention_days=lambda: 0,
            now=now,
        )


@pytest.mark.django_db(transaction=True)
def test_50_random_page_ids_local_timings_include_pin_receipts_and_gc_guard(
    cells, record_property
):
    writer, metric, _, _, now = cells
    rows = [field_row(metric, now) for _ in range(50)]
    publish(cells, rows)
    writer.compact()
    ids = [row["unit_id"] for row in rows]
    start = perf_counter()
    result = hydrate(cells, ids)
    first = perf_counter() - start
    timings = []
    for _ in range(5):
        start = perf_counter()
        assert hydrate(cells, ids) == result
        timings.append(perf_counter() - start)
    warm = quantiles(timings, n=20, method="inclusive")[-1]
    assert len(result) == 50 and all(c["value"] is True for c in result.values())
    record_property("local_first_read_seconds", first)
    record_property("local_warm_p95_seconds", warm)
    print(
        f"B11 local synthetic 50-row timings: first={first:.6f}s warm_p95={warm:.6f}s; NOT the 10M M7 gate or a cold-cache measurement"
    )
