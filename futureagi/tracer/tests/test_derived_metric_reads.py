# ruff: noqa: F811 -- shared pytest fixture imported from the API suite
"""PG-only boundaries for committed reads, snapping and on-demand denial."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from tracer.models.derived_metric import DerivedMetric, DerivedMetricVersion
from tracer.services.derived_metrics import reads
from tracer.services.derived_metrics.errors import MetricInputError
from tracer.services.derived_metrics.service import MetricUnavailableError
from tracer.tests.test_derived_metric_api import (  # noqa: F401
    ROOT,
    body,
    run_body,
    saved,
)

pytestmark = pytest.mark.usefixtures("enabled_metrics")


@pytest.mark.parametrize("offset", ["+00:00", "+05:30", "+05:45", "-03:30"])
def test_snapping_uses_utc_instants_and_previous_equal_duration(offset):
    start = datetime.fromisoformat("2026-01-10T12:07:43" + offset)
    end = start + timedelta(hours=3, minutes=5)
    result = reads.effective_window(start, end)
    assert result["window_start"].tzinfo is UTC
    assert result["window_end"].tzinfo is UTC
    assert result["window_start"].timestamp() % 900 == 0
    assert result["window_end"].timestamp() % 900 == 0
    assert timedelta(0) <= start - result["window_start"] < timedelta(minutes=15)
    assert timedelta(0) <= end - result["window_end"] < timedelta(minutes=15)
    assert result["rounding"] == "Rounded to 15 minutes"


@pytest.mark.parametrize(
    "start,end",
    [
        (datetime(2026, 1, 1), datetime(2026, 1, 2)),
        (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, 0, 14, tzinfo=UTC)),
        (datetime(2026, 1, 2, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
        (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 6, 1, tzinfo=UTC)),
    ],
)
def test_invalid_or_sub_bucket_windows_fail(start, end):
    with pytest.raises(MetricInputError):
        reads.effective_window(start, end)


def test_scalar_bucket_projection_never_reads_sketches_and_binds_scope():
    metric = SimpleNamespace(
        id=uuid4(), organization_id=uuid4(), workspace_id=uuid4(), project_id=uuid4()
    )
    checkpoint, delta = uuid4(), uuid4()
    manifest = SimpleNamespace(
        project_id=metric.project_id,
        checkpoint_attempt_id=checkpoint,
        allowed_attempts=lambda: [(1, checkpoint), (2, delta)],
    )
    definition = SimpleNamespace(version=3, kind="total")
    window = reads.effective_window(
        datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
    )
    sql, params = reads.bucket_select(metric, definition, manifest, window)
    assert "uniq_state" not in sql and "quantile_state" not in sql
    assert "metric_bucket_totals_checkpoint" in sql and "metric_bucket_totals" in sql
    assert str(metric.id) not in sql
    assert params["read_bucket_kin_0"] == (metric.id,)
    assert params["read_workspace"] == str(metric.workspace_id)
    assert sql.index("LIMIT 1 BY") < sql.index("WHERE is_deleted = 0")


@pytest.mark.parametrize("packed", [False, True])
def test_rows_preserve_every_server_computed_uncertainty_field(packed):
    ci = (10.5, 36.0, 84.0, 24.0, 11, "stratified_wor_fpc", "ok", "", "wald_t")
    expected = {
        "group_key": "",
        "value": 60.0,
        **dict(zip(reads.UNCERTAINTY_COLUMNS, ci, strict=True)),
    }
    columns = [("group_key", "String"), ("value", "Float64")]
    if packed:
        columns.append(("ci", "Tuple"))
        values = ("", 60.0, ci)
    else:
        columns.extend((name, "unused") for name in reads.UNCERTAINTY_COLUMNS)
        values = ("", 60.0, *ci)
    client = Mock()
    client.execute.return_value = ([values], columns)
    assert reads._rows(reads.MetricReadStore(client), "query", {}) == [expected]
    assert client.execute.call_args.kwargs["settings"]["use_query_cache"] == 0
    assert client.execute.call_args.kwargs["settings"]["compile_expressions"] == 0


@pytest.mark.django_db
def test_history_lists_versions_without_clickhouse_and_pages(auth_client, saved):
    metric = DerivedMetric.objects.get(pk=saved["id"])
    initial = metric.active_version
    second = DerivedMetricVersion.objects.create(
        metric=metric, version=2, **initial.semantics()
    )
    with patch("tracer.services.derived_metrics.reads.read_store") as store:
        response = auth_client.get(f"{ROOT}{metric.id}/history/", {"limit": 1})
        assert response.status_code == 200, response.data
        assert response.data["results"][0]["definition"]["version"] == 2
        assert response.data["next_cursor"] == str(second.id)
        page = auth_client.get(
            f"{ROOT}{metric.id}/history/", {"limit": 1, "before": str(second.id)}
        )
        assert page.status_code == 200
        assert page.data["results"][0]["definition"]["version"] == 1
        assert page.data["next_cursor"] is None
        invalid = auth_client.get(
            f"{ROOT}{metric.id}/history/", {"before": str(uuid4())}
        )
        assert invalid.status_code == 400
        store.assert_not_called()


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["kpi", "trend", "drivers", "unit-values", "fields"])
def test_user_reads_are_on_demand_unavailable_without_values(
    auth_client, saved, action
):
    metric = DerivedMetric.objects.get(pk=saved["id"])
    definition = DerivedMetricVersion.objects.create(
        metric=metric,
        version=2,
        **{
            **metric.active_version.semantics(),
            "kind": "average",
            "formula": "mean(resolved)",
            "level": "user",
            "user_rule": "average",
        },
        backfill_status="complete",
    )
    DerivedMetric.activate_version(
        metric.id, definition.id, expected_active_id=metric.active_version_id
    )
    window = {k: v for k, v in run_body().items() if k.startswith("window_")}
    data = {"context": "users"} if action == "fields" else window
    if action == "unit-values":
        data = {**window, "context": "users", "unit_ids": [str(uuid4())]}
    with patch("tracer.services.derived_metrics.reads.read_store") as store:
        response = auth_client.get(f"{ROOT}{metric.id}/{action}/", data)
        assert response.status_code == 503, response.data
        assert response.data["value_mode"] == "on_demand"
        assert response.data["code"] == "user_reads_unavailable"
        assert "value" not in response.data and "results" not in response.data
        store.assert_not_called()
    listing = auth_client.get(ROOT)
    assert listing.status_code == 200
    assert listing.data["results"][0]["value_mode"] == "on_demand"
    assert "value" not in listing.data["results"][0]


@pytest.mark.django_db
@pytest.mark.parametrize(
    "action", ["kpi", "trend", "drivers", "history", "fields", "unit-values"]
)
def test_disabled_read_actions_do_not_open_storage(
    auth_client, saved, settings, action
):
    settings.METRICS_ENABLED = False
    with patch("tracer.services.derived_metrics.reads.read_store") as store:
        response = auth_client.get(f"{ROOT}{saved['id']}/{action}/")
        assert response.status_code == 404
        store.assert_not_called()


@pytest.mark.django_db
def test_draft_only_is_recalculating_not_zero(auth_client, observe_project):
    response = auth_client.post(ROOT, body(observe_project), format="json")
    assert response.status_code == 201
    query = {k: v for k, v in run_body().items() if k.startswith("window_")}
    with patch("tracer.services.derived_metrics.reads.read_store") as store:
        result = auth_client.get(f"{ROOT}{response.data['id']}/kpi/", query)
        assert result.status_code == 503
        assert result.data["code"] == "metric_recalculating"
        assert "value" not in result.data
        store.assert_not_called()


def test_trace_remains_default_off(settings):
    settings.DERIVED_METRICS_TRACE_ENABLED = False
    with pytest.raises(MetricInputError):
        reads.require_materialized(
            SimpleNamespace(level="trace", selection_unit="session")
        )


@pytest.mark.django_db
def test_previous_period_uses_the_same_pin_and_equal_snapped_duration(saved):
    metric = DerivedMetric.objects.select_related("active_version").get(pk=saved["id"])
    definition = metric.active_version
    committed = datetime(2026, 1, 11, tzinfo=UTC)
    from tracer.models.derived_metric import MetricProjectRefresh

    # The pin mock still supplies the current finality contract: a consumed
    # project cut and the same frozen cut for this historical manifest.
    cut = int(committed.timestamp()) * 10**9
    state = MetricProjectRefresh.for_project(metric.project_id)
    state.watermark_ns = cut
    state.save(update_fields=["watermark_ns"])
    manifest = SimpleNamespace(
        id=uuid4(),
        project_id=metric.project_id,
        generation=7,
        published_by=SimpleNamespace(
            committed_at=committed,
            source_ceilings={"cursor_updates": {"watermark_ns": cut}},
        ),
    )
    pinned, ranges = [], []

    @contextmanager
    def pin(_metric, *, store):
        pinned.append(definition.id)
        yield store, manifest, definition

    def aggregate(_store, _metric, version, head, window, **_kwargs):
        assert version is definition and head is manifest
        ranges.append(window)
        return {
            **reads.empty_row(),
            "value": 7.0 if len(ranges) == 1 else 5.0,
            "status": "ok",
            "unit_count": 3,
            "valid_count": 3,
        }

    def aggregate_periods(store, metric, version, head, current, previous, **_kwargs):
        return (
            aggregate(store, metric, version, head, current),
            aggregate(store, metric, version, head, previous),
        )

    data = {
        "window_start": datetime.fromisoformat("2026-01-10T12:07:43+05:45"),
        "window_end": datetime.fromisoformat("2026-01-10T15:12:43+05:45"),
    }
    with (
        patch.object(reads, "metric_read", pin),
        patch.object(reads, "_aggregate", aggregate),
        patch.object(reads, "_aggregate_periods", aggregate_periods),
    ):
        result = reads.read_kpi(metric, data)
        assert len(pinned) == 1 and len(ranges) == 2
        width = ranges[0]["window_end"] - ranges[0]["window_start"]
        assert ranges[1]["window_end"] == ranges[0]["window_start"]
        assert ranges[1]["window_start"] == ranges[0]["window_start"] - width
        assert result["delta"] == 2.0
        assert result["provenance"]["updated_at"] == committed
        # A missing historical backfill is not an empty observed population.
        definition.backfill_cursor = {"start": ranges[0]["window_start"].isoformat()}
        ranges.clear()
        result = reads.read_kpi(metric, data)
        assert len(ranges) == 1
        assert result["previous"] is None and result["delta"] is None


def test_unbackfilled_windows_are_unavailable_not_empty_counts():
    definition = SimpleNamespace(backfill_cursor={"start": "2026-01-10T12:07:00+00:00"})
    early = {"window_start": datetime(2026, 1, 10, 12, tzinfo=UTC)}
    covered = {"window_start": datetime(2026, 1, 10, 12, 15, tzinfo=UTC)}
    assert reads.window_is_calculated(definition, covered)
    assert not reads.window_is_calculated(definition, early)
    with pytest.raises(MetricUnavailableError) as exc:
        reads.require_calculated_window(definition, early)
    assert exc.value.code == "window_not_calculated"


def test_typed_evidence_keeps_booleans_nulls_and_text_without_numeric_coercion():
    row = {
        "unit_id": "u",
        "status": "ok",
        "no_value_reason": "",
        "value_bool": 0,
        "value_number": None,
        "value_text": None,
        "inputs": {"cost": None, "bad": float("nan")},
        "inputs_bool": {"match": 0, "missing": None, "yes": 1},
        "inputs_text": {"locale": "fr"},
    }
    result = reads._unit_value(row)
    assert result["value_bool"] is False and result["value_number"] is None
    assert result["inputs_bool"] == {"match": False, "missing": None, "yes": True}
    assert result["inputs_bool"]["yes"] is True
    assert result["inputs_text"] == {"locale": "fr"}
    assert result["inputs"] == {"cost": None, "bad": None}
    missing = reads._unit_value(
        {**row, "status": "no_value", "no_value_reason": "not_evaluated"}
    )
    assert missing["value_bool"] is None and missing["missing_count"] == 1
    open_value = reads._unit_value({**row, "status": "open"})
    assert open_value["status"] == "in_progress" and open_value["eligible_count"] == 0
    assert (
        open_value["inputs"]
        == open_value["inputs_bool"]
        == open_value["inputs_text"]
        == {}
    )


def test_user_unavailable_exception_has_no_numeric_payload():
    with pytest.raises(MetricUnavailableError) as exc:
        reads.require_materialized(
            SimpleNamespace(level="user", selection_unit="session", user_rule="average")
        )
    assert exc.value.value_mode == "on_demand"
    assert not hasattr(exc.value, "value")
