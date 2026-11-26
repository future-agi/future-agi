"""DB-free producer controls: presentation reuses the exact selected run/row."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import UUID

from tracer.models.derived_metric import (
    DerivedMetric,
    DerivedMetricRun,
    DerivedMetricVersion,
)
from tracer.serializers.dashboard import DashboardQueryApiResponseSerializer
from tracer.services.derived_metrics import integrations


def saved_observation():
    metric = DerivedMetric(
        id=UUID(int=1), project_id=UUID(int=2), current_version=2, name="Resolution"
    )
    definition = DerivedMetricVersion(
        metric=metric,
        version=1,
        formula="ratio(resolved == 1, resolved >= 0)",
        inputs=[
            {
                "alias": "resolved",
                "source": "span",
                "field": "attribute_number",
                "attribute_key": "resolved",
                "reducer": "latest",
            }
        ],
        selection_unit="session",
        weighting="none",
        filters=[],
        cohort_key="",
        definition_hash="a" * 64,
        created_at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    run = DerivedMetricRun(
        id=UUID(int=3),
        definition=definition,
        result_id=UUID(int=4),
        window_start=datetime(2026, 10, 1, tzinfo=UTC),
        window_end=datetime(2026, 10, 26, tzinfo=UTC),
        filters=[],
        status="complete",
    )
    row = {
        "group_key": "",
        "value": 0.5,
        "numerator": 1.0,
        "denominator": 2.0,
        "status": "ok",
        "unit_count": 3,
        "valid_count": 2,
        "missing_count": 1,
        "invalid_probability_count": 0,
        "uncertainty": {
            "confidence_level": 0.95,
            "status": "not_estimable",
            "reason": "unstable_variance",
            "method": "iid_units",
            "interval": "",
            "std_error": None,
            "ci_low": None,
            "ci_high": None,
            "effective_n": 2.0,
            "degrees_of_freedom": 1,
        },
    }
    config = {
        "project_ids": [str(metric.project_id)],
        "time_range": {
            "custom_start": run.window_start.isoformat(),
            "custom_end": run.window_end.isoformat(),
        },
        "granularity": "day",
        "filters": [],
        "breakdowns": [],
        "metrics": [
            {
                "id": str(run.id),
                "name": "Resolution",
                "display_name": "Resolution",
                "type": "derived_metric",
                "source": "derived_metrics",
                "derived_run_id": str(run.id),
                "aggregation": "avg",
            }
        ],
    }
    return run, row, config


def produce(run, row, config):
    selected = Mock()
    selected.filter.return_value.first.return_value = run
    workspace = SimpleNamespace(organization=object())
    with (
        patch.object(
            integrations.DerivedMetricRun.objects,
            "select_related",
            return_value=selected,
        ) as select,
        patch.object(
            integrations, "project_queryset_for_request", return_value=[]
        ) as projects,
        patch.object(
            integrations, "read_results", return_value={"results": [row] if row else []}
        ) as read,
    ):
        result = integrations.dashboard_result(config, workspace)
        select.assert_called_once_with(
            "definition__metric__project", "definition__metric__workspace"
        )
        assert (
            selected.filter.call_args.kwargs["definition__metric__workspace"]
            is workspace
        )
        assert (
            selected.filter.call_args.kwargs["definition__metric__organization"]
            is workspace.organization
        )
        assert selected.filter.call_args.kwargs["result_id__isnull"] is False
        projects.assert_called_once()
        return result, read


def test_frontend_dashboard_fixture_equals_the_actual_producer():
    import json
    from pathlib import Path

    run, row, config = saved_observation()
    result, _ = produce(run, row, config)
    root = Path(__file__).resolve().parents[3]
    fixture = (
        root
        / "frontend/src/sections/dashboards/__tests__/fixtures/savedMetricResponse.json"
    )
    assert json.loads(fixture.read_text()) == {
        "config": config,
        "response": {"status": True, "result": result},
    }


def test_dashboard_context_is_immutable_and_read_once():
    run, row, config = saved_observation()
    before = deepcopy(row)
    output, read = produce(run, row, config)
    read.assert_called_once_with(run, include_uncertainty=True)
    assert row == before
    result = output["metrics"][0]
    context = result["derived_context"]
    assert context["run"]["metric_version"] == 1
    assert context["run"]["definition"]["formula"] == run.definition.formula
    assert context["run"]["definition"]["weighting"] == "none"
    assert context["result"] == row
    assert context["result"]["value"] == result["series"][0]["data"][0]["value"]
    serialized = DashboardQueryApiResponseSerializer(
        {"status": True, "result": output}
    ).data
    assert serialized["result"]["metrics"][0]["derived_context"] == context
    from tracer.serializers.dashboard import DashboardQueryMetricResultSerializer

    assert DashboardQueryMetricResultSerializer().fields["derived_context"].read_only


def test_observation_end_inclusion_and_no_extra_excluded_read():
    for start, end, included in [
        ("2026-10-23T00:00:00Z", "2026-10-24T00:00:00Z", False),
        ("2026-10-26T00:00:00Z", "2026-10-27T00:00:00Z", True),
        ("2026-10-25T00:00:00Z", "2026-10-26T00:00:00Z", True),
        ("2026-10-26T00:00:00Z", "2026-10-26T00:00:00Z", True),
        ("2026-10-27T00:00:00Z", "2026-10-27T00:00:00Z", False),
    ]:
        run, row, config = saved_observation()
        config["time_range"] = {"custom_start": start, "custom_end": end}
        output, read = produce(run, row, config)
        metric = output["metrics"][0]
        assert bool(metric["series"][0]["data"]) is included
        assert read.call_count == int(included)
        assert metric["derived_context"].get("result") == (row if included else None)


def test_context_keeps_committed_identity_during_recompute_and_transitions():
    run, row, config = saved_observation()
    for status in ("queued", "running", "failed", "complete"):
        run.status = status
        output, read = produce(run, row, config)
        context = output["metrics"][0]["derived_context"]
        assert context["run"]["status"] == status
        assert context["run"]["result_id"] == str(UUID(int=4))
        assert context["result"]["value"] == 0.5
        read.assert_called_once()
    run.result_id = UUID(int=5)
    row.update(value=0, numerator=0)
    output, _ = produce(run, row, config)
    context = output["metrics"][0]["derived_context"]
    assert context["run"]["result_id"] == str(UUID(int=5))
    assert context["result"]["value"] == 0


def test_weighted_null_and_empty_are_not_inferred_counts_or_zero():
    run, row, config = saved_observation()
    run.definition.weighting = "inverse_probability"
    run.definition.formula = "ratio(cost, tokens)"
    row.update(
        value=None,
        numerator=None,
        denominator=0,
        status="invalid_probability",
        invalid_probability_count=1,
    )
    output, _ = produce(run, row, config)
    context = output["metrics"][0]["derived_context"]
    assert context["result"] == row
    assert context["run"]["definition"]["weighting"] == "inverse_probability"
    output, read = produce(run, None, config)
    assert "result" not in output["metrics"][0]["derived_context"]
    assert output["metrics"][0]["series"][0]["data"] == []
    read.assert_called_once()
