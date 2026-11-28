# ruff: noqa: F811 -- shared PostgreSQL policy fixture
"""Wire/identity controls with real PG admission; CH result rows are substituted."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import Mock, patch
from uuid import UUID

import pytest

from tracer.serializers.dashboard import DashboardTimeRangeSerializer
from tracer.serializers.derived_metric import (
    DerivedMetricCommittedResultPageSerializer,
    DerivedMetricContributorPageSerializer,
    DerivedMetricResultPageSerializer,
    DerivedMetricRunSerializer,
)
from tracer.services.derived_metrics import contributors, service
from tracer.services.derived_metrics.cursor import decode_result_cursor
from tracer.services.derived_metrics.errors import MetricInputError
from tracer.services.derived_metrics.queries import QUERY_SETTINGS
from tracer.tests.metric_read_fixtures import ninety_day_source_policy  # noqa: F401
from tracer.tests.test_derived_metric_dashboard_context import (
    produce,
    saved_observation,
)


@pytest.fixture
def persisted_identity_runs(
    ninety_day_source_policy, workspace, observe_project, monkeypatch
):
    from django.utils import timezone as clock

    from tracer.models.derived_metric import DerivedMetricVersion

    original = saved_observation
    monkeypatch.setattr(clock, "now", lambda: datetime(2026, 10, 26, 0, 2, tzinfo=UTC))

    def saved():
        run, row, config = original()
        metric, definition = run.definition.metric, run.definition
        metric.organization = workspace.organization
        metric.workspace = workspace
        metric.project = observe_project
        metric.save()
        created = definition.created_at
        definition.save()
        DerivedMetricVersion.objects.filter(pk=definition.pk).update(created_at=created)
        definition.created_at = created
        run.as_of = run.window_end + timedelta(minutes=1)
        run.snapshot_id = UUID(int=6)
        run.completed_at = run.as_of + timedelta(minutes=1)
        run.save()
        return run, row, config

    monkeypatch.setattr(__name__ + ".saved_observation", saved)


def persist_read_identity(run):
    # Each scenario represents a committed namespace, including the last good
    # result during recomputation. Keep the real snapshot_read checks enabled.
    run.save(update_fields=["status", "snapshot_id", "result_id", "completed_at"])


def read_fixture(run, row, *, rollup="overall", after="", limit=50, extra=False):
    persist_read_identity(run)
    raw = {
        **row,
        "ci_status": "not_estimable",
        "ci_reason": "unstable_variance",
        "ci_method": "iid_units",
        "ci_interval": "",
        "std_error": None,
        "ci_low": None,
        "ci_high": None,
        "effective_n": 2.0,
        "ci_df": 1,
    }
    columns = service.RESULT_COLUMNS + service.UNCERTAINTY_COLUMNS
    rows = [tuple(raw[k] for k in columns)] * (limit + 1 if extra else 1)
    client = Mock()
    client.execute.return_value = (rows, [(k, "test") for k in columns])
    with (
        patch.object(service, "get_clickhouse_client", return_value=client),
        patch.object(service, "_authorized_metric", return_value=True),
    ):
        page = service.read_results(run, rollup, after, limit, include_uncertainty=True)
    client.execute.assert_called_once()
    assert client.execute.call_args.kwargs["settings"] == QUERY_SETTINGS
    assert page["result_id"] == client.execute.call_args.args[1]["result_id"]
    assert page["result_id"] == str(run.result_id)
    serializer = DerivedMetricCommittedResultPageSerializer(data=page)
    assert serializer.is_valid(), serializer.errors
    assert DerivedMetricCommittedResultPageSerializer(page).data == page
    return page, client


def wire_fixture():
    run, row, _ = saved_observation()
    run.as_of = datetime(2026, 10, 26, 0, 1, tzinfo=UTC)
    run.created_at = run.as_of
    run.completed_at = run.as_of + timedelta(minutes=1)
    run.snapshot_id = UUID(int=6)
    wire_run = DerivedMetricRunSerializer(run).data
    old_page, _ = read_fixture(run, row)
    session_page, _ = read_fixture(
        run, {**row, "group_key": "session-a"}, rollup="session", limit=1, extra=True
    )
    session_next, _ = read_fixture(
        run,
        {**row, "group_key": "session-b"},
        rollup="session",
        after=session_page["next_cursor"],
    )
    contributor_page, _ = contributor_fixture(run)
    run.result_id = UUID(int=5)
    new_page, _ = read_fixture(run, {**row, "value": 0.0, "numerator": 0.0})
    return {
        "run": wire_run,
        "old_page": old_page,
        "new_page": new_page,
        "session_page": session_page,
        "session_next": session_next,
        "contributors": contributor_page,
    }


def legacy_contract(actual, expected):
    """Keep the existing frontend goldens pinned while allowing additive v2 fields.

    The owner of frontend fixtures regenerates them at C01. All old values and
    identities remain compared; the new definition fields have their own checks.
    """
    if isinstance(expected, dict):
        return {
            key: legacy_contract(actual[key], value) for key, value in expected.items()
        }
    if isinstance(expected, list):
        assert len(actual) == len(expected)
        return [legacy_contract(a, e) for a, e in zip(actual, expected, strict=True)]
    return actual


@pytest.mark.django_db
@pytest.mark.usefixtures("persisted_identity_runs")
def test_committed_identity_wire_fixture_matches_actual_producers():
    import json
    from pathlib import Path

    fixture = Path(__file__).resolve().parents[3] / (
        "frontend/src/sections/derived-metrics/__tests__/fixtures/committedResults.json"
    )
    expected, actual = json.loads(fixture.read_text()), wire_fixture()
    assert expected == legacy_contract(actual, expected)
    assert actual["run"]["definition"]["needs_rebuild"] is False
    assert actual["run"]["definition"]["level"] == "conversation"
    assert actual["run"]["kind"] == "run"


@pytest.mark.django_db
@pytest.mark.usefixtures("persisted_identity_runs")
def test_identity_applies_to_all_rollups_and_cursors_without_extra_reads():
    run, row, _ = saved_observation()
    original_result_id = str(run.result_id)
    for status in ("queued", "running", "failed", "complete"):
        run.status = status
        for rollup in ("overall", "session", "user", "cohort", "day"):
            page, _ = read_fixture(run, row, rollup=rollup, limit=1, extra=True)
            assert decode_result_cursor(
                page["next_cursor"], original_result_id, rollup
            ) == (True, "")
            following, _ = read_fixture(
                run, row, rollup=rollup, after=page["next_cursor"]
            )
            assert following["result_id"] == original_result_id
            if rollup == "day":
                day_cursor = page["next_cursor"]
    # A cursor for R1 is never repurposed for R2, and rejection precedes CH.
    replacement_id = UUID(int=5)
    run.result_id = replacement_id
    persist_read_identity(run)
    client = Mock()
    with (
        patch.object(service, "get_clickhouse_client", return_value=client),
        patch.object(service, "_authorized_metric", return_value=True),
    ):
        with pytest.raises(
            MetricInputError,
            match="Result cursor is invalid or belongs to another result/rollup",
        ):
            service.read_results(run, "day", day_cursor)
        client.execute.assert_not_called()

        # Same persisted namespace and admission, but no R1 cursor: R2 is readable.
        columns = service.RESULT_COLUMNS
        replacement = {**row, "group_key": "2026-10-25", "value": 1.0, "numerator": 2.0}
        client.execute.return_value = (
            [tuple(replacement[key] for key in columns)],
            [(key, "test") for key in columns],
        )
        fresh = service.read_results(run, "day")
    client.execute.assert_called_once()
    assert client.execute.call_args.args[1]["result_id"] == str(replacement_id)
    assert fresh["result_id"] == str(replacement_id)
    assert fresh["results"][0]["value"] == 1.0


@pytest.mark.django_db
@pytest.mark.usefixtures("persisted_identity_runs")
def test_identity_is_required_for_persisted_pages_not_preview():
    page = wire_fixture()["old_page"]
    for identity in (None, "", "not-a-uuid"):
        invalid = DerivedMetricCommittedResultPageSerializer(
            data={**page, "result_id": identity}
        )
        assert not invalid.is_valid()
    legacy = {k: v for k, v in page.items() if k != "result_id"}
    assert not DerivedMetricCommittedResultPageSerializer(data=legacy).is_valid()
    assert DerivedMetricResultPageSerializer(data=legacy).is_valid()


def contributor_fixture(run):
    persist_read_identity(run)
    diagnostics = {
        "unit_count": 3,
        "valid_count": 2,
        "missing_count": 1,
        "invalid_probability_count": 0,
        "outside_denominator_count": 0,
        "undefined_arithmetic_count": 0,
        "missing_resolved": 1,
        "missing_examples": ["session-missing"],
        "invalid_probability_examples": [],
    }
    units = [
        {
            "unit_id": f"session-{n}",
            "session_id": f"session-{n}",
            "user_id": "",
            "cohort": "",
            "day": "2026-10-26",
            "probability": 1.0,
            "weight": 1.0,
            "numerator": float(n),
            "denominator": 1.0,
            "weighted_numerator": float(n),
            "weighted_denominator": 1.0,
            "influence": float(n) - 0.5,
        }
        for n in (1, 0)
    ]
    client = Mock()
    client.execute.side_effect = [
        [(0.5, 1.0, 2.0, "ok")],
        ([tuple(unit.values()) for unit in units], [(k, "test") for k in units[0]]),
        ([tuple(diagnostics.values())], [(k, "test") for k in diagnostics]),
    ]
    with (
        patch.object(contributors, "get_clickhouse_client", return_value=client),
        patch.object(service, "_authorized_metric", return_value=True),
    ):
        page = contributors.read_contributors(run)
    return page, client


@pytest.mark.django_db
@pytest.mark.usefixtures("persisted_identity_runs")
def test_contributor_identity_is_the_persisted_value_identity():
    run, _, _ = saved_observation()
    run.snapshot_id = UUID(int=6)
    page, client = contributor_fixture(run)
    assert (
        client.execute.call_count == 3
    )  # unchanged persisted + units + diagnostics budget
    assert all(
        c.args[1]["result_id"] == page["result_id"] == str(run.result_id)
        for c in client.execute.call_args_list
    )
    assert all(
        c.kwargs["settings"] == QUERY_SETTINGS for c in client.execute.call_args_list
    )
    serializer = DerivedMetricContributorPageSerializer(data=page)
    assert serializer.is_valid(), serializer.errors
    assert DerivedMetricContributorPageSerializer(page).data == page


def timestamp_oracle():
    """Independent Python/actual serializer+integration oracle, not JS arithmetic."""
    cases = []
    for point in (
        "2026-10-26T00:00:00.000001Z",
        "2026-10-26T00:00:00.999999Z",
        "2024-02-29T23:59:59.123456-07:00",
        "2026-01-01T00:00:00.000001+05:45",
        "1969-12-31T23:59:59.999999Z",
        "2026-11-01T01:30:00.000001-08:00",
        "2026-03-08T03:00:00.000001-07:00",
        "2026-10-26T00:00:00.0000019+00:00",
    ):
        for boundary in ("start", "end"):
            for delta in (-1, 0, 1):
                run, row, config = saved_observation()
                parsed_point = DashboardTimeRangeSerializer(
                    data={"custom_start": point, "custom_end": point}
                )
                assert parsed_point.is_valid(), parsed_point.errors
                observed = parsed_point.validated_data["custom_end"]
                run.window_start = observed - timedelta(days=1)
                run.window_end = observed
                # Offsets cross calendar days, and input offsets differ from the
                # canonical serializer's UTC output. Both must compare equally.
                edge = (
                    (observed + timedelta(microseconds=delta))
                    .astimezone(timezone(timedelta(hours=5, minutes=30)))
                    .isoformat()
                )
                start = (
                    edge
                    if boundary == "start"
                    else (observed - timedelta(days=1)).isoformat()
                )
                end = (
                    edge
                    if boundary == "end"
                    else (observed + timedelta(days=1)).isoformat()
                )
                serializer = DashboardTimeRangeSerializer(
                    data={"custom_start": start, "custom_end": end}
                )
                assert serializer.is_valid(), serializer.errors
                config = deepcopy(config)
                config["time_range"] = serializer.validated_data
                output, reader = produce(run, row, config)
                included = (
                    serializer.validated_data["custom_start"]
                    <= observed
                    <= serializer.validated_data["custom_end"]
                )
                assert bool(output["metrics"][0]["series"][0]["data"]) == included
                assert reader.call_count == int(included)
                cases.append(
                    {
                        "input_point": point,
                        "boundary": boundary,
                        "delta_us": delta,
                        "config": {"metrics": config["metrics"]},
                        "saved_run": output["metrics"][0]["derived_context"]["run"],
                        "range": {"start": start, "end": end},
                        "serialized_range": {
                            "start": serializer.data["custom_start"],
                            "end": serializer.data["custom_end"],
                        },
                        "included": included,
                    }
                )
    return cases


def test_timestamp_fixture_equals_actual_serializer_and_producer():
    import json
    from pathlib import Path

    fixture = Path(__file__).resolve().parents[3] / (
        "frontend/src/sections/dashboards/__tests__/fixtures/preciseBoundaryCases.json"
    )
    assert json.loads(fixture.read_text()) == timestamp_oracle()
