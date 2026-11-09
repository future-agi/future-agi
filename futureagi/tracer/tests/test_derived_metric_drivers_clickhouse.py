# ruff: noqa: F811 -- shared isolated ClickHouse and PG fixtures
"""Drivers are failing units, with effects from the estimator's complete cases."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tracer.models.derived_metric import DerivedMetric, DerivedMetricVersion
from tracer.services.derived_metrics import reads
from tracer.services.derived_metrics.buckets import live_parts_select
from tracer.services.derived_metrics.changes import ChangeBatch, SourceKey, UnitKey
from tracer.services.derived_metrics.queries import Scope
from tracer.services.derived_metrics.unit_reads import field_recipe
from tracer.tests.test_derived_metric_publication_clickhouse import (  # noqa: F401
    live_writer,
    metric_ch,
    publication_store,
)
from tracer.tests.test_derived_metric_reads_clickhouse import AT, WINDOW

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.usefixtures("enabled_metrics"),
]


def published_fixture(writer, workspace, project, *, kind, formula, values):
    metric = DerivedMetric.objects.create(
        organization=workspace.organization,
        workspace=workspace,
        project=project,
        name="Drivers regression",
    )
    definition = DerivedMetricVersion.objects.create(
        metric=metric,
        version=1,
        kind=kind,
        level="conversation",
        formula=formula,
        inputs=[
            {
                "alias": name,
                "source": "span",
                "field": "attribute_number",
                "attribute_key": name,
                "reducer": "latest",
            }
            for name in ("y", "x")
        ],
        field_spec={
            "conditions": [{"alias": "y", "operator": "gt", "value": 0}]
        }
        if kind in {"share", "count"}
        else {},
        selection_unit="session",
        weighting="none",
        backfill_status="complete",
    )
    ids, sources = [], []
    scope = Scope(workspace.organization_id, workspace.id, project.id)
    for index, (y, x) in enumerate(values):
        unit_id, trace = str(uuid4()), str(uuid4())
        ids.append(unit_id)
        sources.append(SourceKey(trace, str(index)))
        writer.store.execute(
            "INSERT INTO spans (id, trace_id, project_id, org_id, trace_session_id, "
            "start_time, end_time, created_at, updated_at, attrs_number) "
            "VALUES (%(span)s, %(trace)s, %(project_id)s, %(org_id)s, %(unit)s, "
            "%(at)s, %(at)s, %(at)s, %(at)s, %(inputs)s)",
            {
                **scope.parameters(),
                "span": str(index),
                "trace": trace,
                "unit": unit_id,
                "at": AT + timedelta(minutes=5),
                "inputs": {
                    k: v for k, v in {"y": y, "x": x}.items() if v is not None
                },
            },
        )
    head = writer.refresh(
        ChangeBatch(
            tuple(sources),
            tuple(UnitKey("conversation", i) for i in ids),
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
    return metric, definition, head, ids


def effect_rows(writer, metric, definition, head):
    fields, params = reads._fields_select(metric, definition, head, WINDOW)
    total = reads._aggregate(writer.store, metric, definition, head, WINDOW)
    sql = reads._driver_influence_select(
        fields, field_recipe(definition), total, params
    )
    return fields, params, total, reads._rows(writer.store, sql, params)


def recompute_without(writer, definition, fields, params, unit_id):
    params = {**params, "removed_unit": unit_id}
    remaining = f"SELECT * FROM ({fields}) WHERE unit_id != %(removed_unit)s"
    sql = reads._aggregate_select(
        live_parts_select(definition, remaining), definition, params
    )
    return reads._rows(writer.store, sql, params)[0]["value"]


def test_incomplete_boolean_or_has_no_influence_and_removal_does_not_change_kpi(
    live_writer, workspace, observe_project
):
    metric, definition, head, ids = published_fixture(
        live_writer,
        workspace,
        observe_project,
        kind="custom",
        formula="sum((y > 0) or (x > 0))",
        values=[(1, None), (0, 0), (1, 1)],
    )
    fields, params, total, rows = effect_rows(live_writer, metric, definition, head)
    incomplete = next(r for r in rows if r["unit_id"] == ids[0])
    assert incomplete["part_n"] == 1
    assert incomplete["no_value_reason"] == "attribute_absent"
    assert incomplete["influence"] is None
    assert total["value"] == 1.0 and total["missing_count"] == 1
    assert (
        recompute_without(live_writer, definition, fields, params, ids[0])
        == total["value"]
    )
    result = reads.read_units(
        metric, WINDOW, drivers=True, store=live_writer.store, retention_days=lambda: 0
    )
    # Neither incomplete nor successful units are failures.
    assert result["results"] == []


@pytest.mark.parametrize("kind", ["share", "count"])
def test_boolean_successes_and_missing_units_never_enter_any_drivers_page(
    live_writer, workspace, observe_project, kind
):
    metric, definition, head, ids = published_fixture(
        live_writer,
        workspace,
        observe_project,
        kind=kind,
        formula="mean(y)",
        values=[(1, 1), (0, 1), (0, 1), (None, 1)],
    )
    fields, params, total, _ = effect_rows(live_writer, metric, definition, head)
    assert total["value"] == pytest.approx(1 / 3 if kind == "share" else 1)
    seen, after = [], ""
    for _ in range(3):
        page = reads.read_units(
            metric,
            {**WINDOW, "limit": 1, "after": after},
            drivers=True,
            store=live_writer.store,
            retention_days=lambda: 0,
        )
        assert len(page["results"]) == 1
        row = page["results"][0]
        assert row["value_bool"] is False and row["status"] == "value"
        assert row["unit_id"] in ids[1:3] and row["unit_id"] not in seen
        without = recompute_without(
            live_writer, definition, fields, params, row["unit_id"]
        )
        assert row["influence"] == pytest.approx(total["value"] - without)
        seen.append(row["unit_id"])
        after = page["next_cursor"]
        if after is None:
            break
    assert set(seen) == set(ids[1:3]) and after is None


def test_complete_zero_denominator_ratio_still_contributes_to_drivers(
    live_writer, workspace, observe_project
):
    metric, definition, head, ids = published_fixture(
        live_writer,
        workspace,
        observe_project,
        kind="custom",
        formula="ratio(y, x)",
        values=[(-2, 0), (6, 2), (0, 2)],
    )
    fields, params, total, _ = effect_rows(live_writer, metric, definition, head)
    result = reads.read_units(
        metric, WINDOW, drivers=True, store=live_writer.store, retention_days=lambda: 0
    )
    assert {r["unit_id"] for r in result["results"]} == {ids[0], ids[2]}
    for row in result["results"]:
        without = recompute_without(
            live_writer, definition, fields, params, row["unit_id"]
        )
        assert row["influence"] == pytest.approx(total["value"] - without)
    zero_denominator = next(r for r in result["results"] if r["unit_id"] == ids[0])
    assert zero_denominator["no_value_reason"] == "zero_denominator"
    assert zero_denominator["value_number"] is None
    assert zero_denominator["influence"] == -0.5
