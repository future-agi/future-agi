# ruff: noqa: F811 -- pytest fixtures are imported from the derived-metric suites
"""Contributor drill-down over a run's frozen snapshot, on real ClickHouse."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.utils import timezone

from tracer.models.derived_metric import DerivedMetricRun
from tracer.services.derived_metrics.contributors import read_contributors
from tracer.services.derived_metrics.errors import MetricInputError
from tracer.services.derived_metrics.queries import QUERY_SETTINGS, rollup_insert
from tracer.services.derived_metrics.service import execute_run
from tracer.tests.test_derived_metric_api import (  # noqa: F401 -- fixtures
    ROOT,
    RUNS,
    run_body,
    saved,
)
from tracer.tests.test_derived_metric_clickhouse import (  # noqa: F401 -- fixture
    metric_ch,
    seed_span,
)
from tracer.tests.test_derived_metric_uncertainty import _scope, insert_units

# cohort, unit, y, x, p, invalid. Cohort "clean" holds four valid units (one
# outside the denominator); cohort "dirty" holds one valid, one missing and one
# invalid-probability unit.
UNITS = [
    ("clean", "a", 1.0, 1.0, 0.5, 0),
    ("clean", "b", 0.0, 1.0, 0.25, 0),
    ("clean", "c", 1.0, 1.0, 1.0, 0),
    ("clean", "f", 0.0, 0.0, 0.5, 0),
    ("dirty", "g", 1.0, 1.0, 0.5, 0),
    ("dirty", "m", None, 1.0, 0.5, 0),
    ("dirty", "v", 1.0, 1.0, 0.5, 1),
]


def persisted_run(client, formula, rollups=("overall", "cohort")):
    scope, snapshot = _scope(), uuid4()
    insert_units(
        client,
        scope,
        snapshot,
        [
            (unit, cohort, {"y": y, "x": x}, p, "live", "live", None, invalid)
            for cohort, unit, y, x, p, invalid in UNITS
        ],
    )
    definition = SimpleNamespace(
        formula=formula,
        inputs=[{"alias": "y"}, {"alias": "x"}],
        selection_unit="session",
        metric=SimpleNamespace(
            id=uuid4(),
            organization_id=scope.organization,
            workspace_id=scope.workspace,
            project_id=scope.project,
        ),
        version=1,
        definition_hash="c" * 64,
    )
    now = datetime.now(UTC)
    run = SimpleNamespace(
        id=uuid4(),
        snapshot_id=snapshot,
        result_id=uuid4(),
        definition=definition,
        window_start=now - timedelta(days=1),
        window_end=now,
        as_of=now,
        source_run_id="",
        source_manifest={"sampling_scope": ""},
    )
    for rollup in rollups:
        query, params = rollup_insert(definition, run, run.result_id, rollup)
        client.execute(query, params, settings=QUERY_SETTINGS)
    return run


@pytest.fixture
def explain(metric_ch):
    with (
        patch(
            "tracer.services.derived_metrics.contributors.get_clickhouse_client",
            return_value=metric_ch,
        ),
        patch(
            "tracer.services.derived_metrics.service._authorized_metric",
            return_value=True,
        ),
    ):
        yield metric_ch


def test_ratio_units_are_ranked_by_exact_leave_one_out_influence(explain):
    # Cohort "clean": N = 2*1 + 4*0 + 1*1 + 2*0 = 3, D = 2 + 4 + 1 + 0 = 7, R = 3/7.
    # Without b: 3/(7-4) = 1, influence 3/7 - 1 = -4/7.
    # Without a: (3-2)/(7-2) = 1/5, influence 3/7 - 1/5 = 8/35.
    # Without c: (3-1)/(7-1) = 1/3, influence 3/7 - 1/3 = 2/21.
    # Without f (outside the denominator): 3/7, influence 0.
    run = persisted_run(explain, "ratio(y, x)")
    page = read_contributors(run, rollup="cohort", group_key="clean", limit=100)
    assert page["status"] == "ok" and page["value"] == pytest.approx(3 / 7)
    assert [row["unit_id"] for row in page["results"]] == ["b", "a", "c", "f"]
    influence = [row["influence"] for row in page["results"]]
    assert influence == pytest.approx([-4 / 7, 8 / 35, 2 / 21, 0], abs=1e-12)
    first = page["results"][0]
    assert (first["weight"], first["numerator"], first["denominator"]) == (4, 0, 1)
    assert (first["weighted_numerator"], first["weighted_denominator"]) == (0, 4)
    # Every valid unit is listed, so the weighted parts add up to the persisted totals.
    assert sum(row["weighted_numerator"] for row in page["results"]) == pytest.approx(
        page["numerator"]
    )
    assert sum(row["weighted_denominator"] for row in page["results"]) == pytest.approx(
        page["denominator"]
    )
    assert page["truncated"] is False
    diagnostics = page["diagnostics"]
    assert diagnostics["unit_count"] == 4 and diagnostics["valid_count"] == 4
    assert diagnostics["outside_denominator_count"] == 1
    assert diagnostics["missing_count"] == diagnostics["invalid_probability_count"] == 0


def test_the_list_is_bounded_and_says_when_it_is_truncated(explain):
    run = persisted_run(explain, "ratio(y, x)")
    page = read_contributors(run, rollup="cohort", group_key="clean", limit=2)
    assert [row["unit_id"] for row in page["results"]] == ["b", "a"]
    assert page["truncated"] is True
    with pytest.raises(MetricInputError):
        read_contributors(run, rollup="cohort", group_key="clean", limit=101)


def test_missing_and_invalid_units_are_counted_with_reasons(explain):
    run = persisted_run(explain, "ratio(y, x)")
    page = read_contributors(run, rollup="cohort", group_key="dirty")
    # One invalid probability withholds the value, so no influence is invented;
    # valid units are still listed by their weighted contribution.
    assert page["status"] == "invalid_probability" and page["value"] is None
    assert [(row["unit_id"], row["influence"]) for row in page["results"]] == [
        ("g", None)
    ]
    diagnostics = page["diagnostics"]
    assert diagnostics["unit_count"] == 3 and diagnostics["valid_count"] == 1
    assert diagnostics["missing_count"] == 1 and diagnostics["missing_examples"] == [
        "m"
    ]
    assert diagnostics["invalid_probability_count"] == 1
    assert diagnostics["invalid_probability_examples"] == ["v"]
    assert diagnostics["missing_by_input"] == {"x": 0, "y": 1}
    assert diagnostics["undefined_arithmetic_count"] == 0


def test_overall_row_covers_every_group(explain):
    run = persisted_run(explain, "ratio(y, x)")
    page = read_contributors(run, limit=100)
    assert page["group_key"] == "" and page["status"] == "invalid_probability"
    assert sorted(row["unit_id"] for row in page["results"]) == [
        "a",
        "b",
        "c",
        "f",
        "g",
    ]
    assert page["diagnostics"]["unit_count"] == 7


def test_total_influence_is_the_weighted_contribution(explain):
    # sum(y) over "clean": a contributes 2*1, c 1*1, b and f nothing.
    run = persisted_run(explain, "sum(y)")
    page = read_contributors(run, rollup="cohort", group_key="clean")
    assert page["value"] == pytest.approx(3) and page["denominator"] is None
    assert [(row["unit_id"], row["influence"]) for row in page["results"]] == [
        ("a", 2),
        ("c", 1),
        ("b", 0),
        ("f", 0),
    ]
    assert all(row["weighted_denominator"] is None for row in page["results"])
    assert page["diagnostics"]["outside_denominator_count"] == 0


def test_overflowing_contributions_fail_with_an_explicit_reason(explain, monkeypatch):
    import sys

    monkeypatch.setattr(
        sys.modules[__name__], "UNITS", [("clean", "tiny", 1.0, 1.0, 1e-320, 0)]
    )
    run = persisted_run(explain, "ratio(y, x)")
    with pytest.raises(MetricInputError, match="finite numbers"):
        read_contributors(run, rollup="cohort", group_key="clean")


def test_an_unpersisted_group_is_rejected_not_guessed(explain):
    run = persisted_run(explain, "ratio(y, x)", rollups=("overall",))
    with pytest.raises(MetricInputError):
        read_contributors(run, rollup="cohort", group_key="clean")
    with pytest.raises(MetricInputError):
        read_contributors(run, rollup="overall", group_key="nope")


@pytest.mark.django_db
def test_contributors_api_is_scoped_bounded_and_links_units(
    auth_client, saved, user, workspace, observe_project, metric_ch
):
    from accounts.models.workspace import Workspace
    from tracer.services.derived_metrics.queries import Scope

    now = timezone.now().replace(microsecond=0)
    scope = Scope(workspace.organization_id, workspace.id, observe_project.id)
    sessions = [uuid4(), uuid4()]
    for index, (session, escalated) in enumerate(zip(sessions, (0, 1), strict=True)):
        seed_span(
            metric_ch,
            scope,
            f"span-{index}",
            session,
            uuid4(),
            now - timedelta(hours=1),
            escalated=escalated,
        )
    with (
        patch("tracer.views.derived_metric._enqueue"),
        patch(
            "tracer.services.derived_metrics.service.get_clickhouse_client",
            return_value=metric_ch,
        ),
        patch(
            "tracer.services.derived_metrics.contributors.get_clickhouse_client",
            return_value=metric_ch,
        ),
    ):
        run_id = auth_client.post(
            f"{ROOT}{saved['id']}/runs/", run_body(), format="json"
        ).data["id"]
        execute_run(run_id)
        assert DerivedMetricRun.objects.get(pk=run_id).status == "complete"
        response = auth_client.get(f"{RUNS}{run_id}/contributors/", {"limit": 5})
        assert response.status_code == 200, response.data
        page = response.data
        # Unweighted session ratio 1/2: removing the resolved session leaves 0
        # (influence +1/2), removing the escalated one leaves 1 (influence -1/2).
        assert page["value"] == 0.5 and page["selection_unit"] == "session"
        assert {row["session_id"]: row["influence"] for row in page["results"]} == {
            str(sessions[0]): 0.5,
            str(sessions[1]): -0.5,
        }
        assert all(row["trace_id"] is None for row in page["results"])
        assert all(row["weight"] == 1 for row in page["results"])
        assert (
            auth_client.get(f"{RUNS}{run_id}/contributors/", {"limit": 101}).status_code
            == 400
        )
        assert (
            auth_client.get(
                f"{RUNS}{run_id}/contributors/", {"rollup": "cohort", "group_key": "x"}
            ).status_code
            == 400
        )
        other = Workspace.objects.create(
            name="Other workspace", organization=user.organization, created_by=user
        )
        observe_project.workspace = other
        observe_project.save()
        assert auth_client.get(f"{RUNS}{run_id}/contributors/").status_code == 404
