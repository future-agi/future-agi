from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management.base import CommandError

from tracer.management.commands import benchmark_dashboard_complex_query as benchmark


class _Builder:
    def __init__(self, config):
        self.config = config

    def build_all_queries(self):
        return [
            (
                "SELECT 1 FROM dashboard_root_spans AS spans FINAL",
                {"project_ids": self.config["project_ids"]},
                {},
            )
        ]


class _Analytics:
    calls = []

    def execute_ch_query(self, query, params, timeout_ms, settings):
        self.calls.append((query, params, timeout_ms, settings))
        return []


@pytest.mark.unit
def test_nearest_rank_percentile_uses_observed_p95():
    values = [
        100,
        500,
        200,
        400,
        300,
        600,
        700,
        800,
        900,
        1_000,
        1_100,
        1_200,
        1_300,
        1_400,
        1_500,
        1_600,
        1_700,
        1_800,
        1_900,
        2_000,
    ]

    assert benchmark._nearest_rank_percentile(values, 0.95) == 1_900


@pytest.mark.unit
def test_benchmark_is_select_only_and_uses_thirty_second_wall(monkeypatch):
    _Analytics.calls = []
    resets = []
    monkeypatch.setattr(benchmark, "DashboardQueryBuilderV2", _Builder)
    monkeypatch.setattr(benchmark, "V2AnalyticsQueryService", _Analytics)
    monkeypatch.setattr(benchmark, "reset_v2_query_client", lambda: resets.append(True))
    ticks = iter((0.0, 0.2, 1.0, 1.3, 2.0, 2.4, 3.0, 3.5, 4.0, 4.6))
    monkeypatch.setattr(benchmark.time, "perf_counter", lambda: next(ticks))
    output = StringIO()

    benchmark.Command(stdout=output).handle(
        project_id="00000000-0000-0000-0000-000000000001",
        breakdown_key="llm.model_name",
        string_filter=["user.country=Mexico"],
        number_filter=[],
        boolean_filter=["llm_present=true"],
        samples=5,
        warmups=0,
        target_ms=10_000,
    )

    report = json.loads(output.getvalue())
    assert report["passed"] is True
    assert report["p95_ms"] == 600.0
    assert report["hard_wall_ms"] == 30_000
    assert len(_Analytics.calls) == 5
    assert all(call[0].startswith("SELECT") for call in _Analytics.calls)
    assert all(call[2] == 30_000 for call in _Analytics.calls)
    assert resets == [True]


@pytest.mark.unit
def test_benchmark_rejects_non_uuid_project_before_building(monkeypatch):
    monkeypatch.setattr(
        benchmark,
        "DashboardQueryBuilderV2",
        lambda _config: pytest.fail("invalid scope must not build a query"),
    )

    with pytest.raises(CommandError, match="project-id must be a UUID"):
        benchmark.Command(stdout=StringIO()).handle(
            project_id="not-a-project",
            breakdown_key="llm.model_name",
            string_filter=["user.country=Mexico"],
            number_filter=[],
            boolean_filter=[],
            samples=5,
            warmups=0,
            target_ms=10_000,
        )


@pytest.mark.unit
def test_benchmark_cannot_relax_configured_p95_ceiling(monkeypatch):
    monkeypatch.setattr(
        benchmark,
        "DashboardQueryBuilderV2",
        lambda _config: pytest.fail("an invalid target must not build a query"),
    )

    with pytest.raises(CommandError, match="configured p95 ceiling"):
        benchmark.Command(stdout=StringIO()).handle(
            project_id="00000000-0000-0000-0000-000000000001",
            breakdown_key="llm.model_name",
            string_filter=["user.country=Mexico"],
            number_filter=[],
            boolean_filter=[],
            samples=5,
            warmups=0,
            target_ms=10_001,
        )
