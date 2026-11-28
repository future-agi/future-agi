"""Run dashboard charts built from the shared per-call rows."""

import uuid

from django.utils import timezone


def _dashboard_row(index: int, **values):
    row = {
        "id": uuid.UUID(int=index),
        "started_at": None,
        "dashboard_csat": None,
        "dashboard_provider_success": None,
        "result_eval_outcome": "inconclusive",
        "result_latency_ms": None,
        "duration_seconds": None,
        "result_cost_cents": None,
        "llm_cost_cents": None,
        "tts_cost_cents": None,
        "stt_cost_cents": None,
        "storage_cost_cents": None,
        "result_scenario": f"Scenario {index}",
        "simulation_call_type": "voice",
        "result_provider": "vapi",
    }
    row.update(values)
    return row


def test_csat_half_points_bin_up_and_agreement_counts_both_verdicts():
    from simulate.services.run_dashboard_v3_distributions import csat_distribution

    scores = [0.5, 1.5, 4.5, 7.5, 8.0, 9.49, 9.5, 10.0]
    verdicts = [
        ("true", "passed"),
        ("false", "failed"),
        ("true", "failed"),
        ("false", "passed"),
        (None, "passed"),
        ("true", "inconclusive"),
        ("maybe", "failed"),
        (None, None),
    ]
    rows = [
        _dashboard_row(
            index,
            dashboard_csat=score,
            dashboard_provider_success=provider,
            result_eval_outcome=outcome,
        )
        for index, (score, (provider, outcome)) in enumerate(
            zip(scores, verdicts, strict=True)
        )
    ]
    rows.append(_dashboard_row(99))

    chart = csat_distribution(rows, len(rows))

    assert {row["label"]: row["count"] for row in chart["bins"] if row["count"]} == {
        "1": 1,
        "2": 1,
        "5": 1,
        "8": 2,
        "9": 1,
        "10": 2,
    }
    assert (chart["measured"], chart["total"], chart["satisfied"]) == (8, 9, 4)
    assert chart["satisfied_percent"] == 50.0
    assert chart["agreement"] == {"compared": 4, "agreed": 2, "percent": 50.0}


def test_latency_bars_around_the_target_are_flagged_from_the_target_up(settings):
    from simulate.services.run_dashboard_v3_distributions import (
        response_time_distribution,
    )

    settings.SIMULATION_VOICE_RESPONSE_TARGET_MS = 1500
    rows = [
        _dashboard_row(index, result_latency_ms=latency)
        for index, latency in enumerate([1450.0, 1500.0, 1550.0])
    ]

    chart = response_time_distribution(rows, len(rows), voice=True)

    assert [
        (row["lower"], row["upper"], row["count"], row["danger"])
        for row in chart["bins"]
    ] == [(1450, 1500, 1, False), (1500, 1550, 1, True), (1550, 1600, 1, True)]
    assert (chart["measured"], chart["at_or_above_target"]) == (3, 2)


def test_top_eight_rankings_break_ties_by_ascending_call_id():
    from simulate.services.run_dashboard_v3 import _tails

    tied = [
        _dashboard_row(index, duration_seconds=900, result_cost_cents=5.0)
        for index in range(9, 0, -1)
    ]
    rows = [*tied, _dashboard_row(0)]

    for field in ("duration_seconds", "result_cost_cents"):
        ranked = _tails(rows, field)
        assert [row["id"] for row in ranked] == [
            str(uuid.UUID(int=index)) for index in range(1, 9)
        ], field
        assert [row["rank"] for row in ranked] == list(range(1, 9))


def test_per_call_series_orders_by_start_then_id_with_unstarted_calls_last():
    from simulate.services.run_dashboard_v3 import SeriesBounds, _series

    started = timezone.now().replace(microsecond=0)
    rows = [
        _dashboard_row(1, duration_seconds=10),
        _dashboard_row(3, started_at=started, result_latency_ms=-1.0),
        _dashboard_row(2, started_at=started, result_latency_ms=800.0),
    ]

    series = _series(None, rows, len(rows), SeriesBounds(first=started, last=started))

    assert [(row["label"], row["started_at"]) for row in series] == [
        ("1", started),
        ("2", started),
        ("3", None),
    ]
    assert [row["latency_ms"] for row in series] == [800.0, None, None]
    assert series[2]["duration_ms"] == 10000.0
