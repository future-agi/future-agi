"""Percentage-change monitors judge against the previous period, on the graph
and in the alert worker alike.

Each graph bucket is compared with its own previous period; the bucket holding
now takes the worker's own baseline (get_previous_period_baseline), so its bar
matches what fires. Count metrics average per period over the history window,
with empty periods counted as 0, never over partial clock-hour buckets.

A monitor saved before the threshold update and not edited since keeps the
earlier rule, a band around the history's mean, on the graph as in the worker.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tracer.models.monitor import ComparisonOperatorChoices, MonitorMetricTypeChoices
from tracer.utils import monitor as monitor_mod
from tracer.utils import monitor_graphs
from tracer.utils.monitor_graphs import _bucket_status

# Stored monitors carry the plain values.
GT = ComparisonOperatorChoices.GREATER_THAN.value
LT = ComparisonOperatorChoices.LESS_THAN.value


def _thresholds(op: str, critical: float | None, warning: float | None):
    return SimpleNamespace(
        threshold_operator=op,
        critical_threshold_value=critical,
        warning_threshold_value=warning,
    )


@pytest.mark.parametrize(
    ("op", "critical", "warning", "baseline", "current", "expected"),
    [
        # Stable baseline 100, 50% critical: 101 is a 1% increase, so healthy.
        (GT, 50, None, 100.0, 101.0, "healthy"),
        (GT, 50, None, 100.0, 151.0, "critical"),
        (GT, 50, None, 100.0, 150.0, "healthy"),
        (GT, 50, 20, 100.0, 121.0, "warning"),
        (LT, 20, 50, 100.0, 79.0, "critical"),
        (LT, 50, 20, 100.0, 79.0, "warning"),
        (LT, 50, None, 100.0, 60.0, "healthy"),
        # Zero baseline: unchanged zero never alerts; any increase does for greater-than.
        (GT, 50, None, 0.0, 0.0, "healthy"),
        (GT, 50, None, 0.0, 1.0, "critical"),
        # No baseline at all is not enough history.
        (GT, 50, None, None, 1.0, "insufficient_data"),
    ],
)
def test_bucket_status_uses_baseline_percentage(
    op, critical, warning, baseline, current, expected
) -> None:
    assert (
        _bucket_status(_thresholds(op, critical, warning), current, baseline)
        == expected
    )


# --- A ClickHouse stand-in that answers from individual spans -----------------

TODAY_START = datetime(2026, 11, 10, tzinfo=UTC)
# The "Today" view asks for the end of the selected day, which is in the future.
TODAY_END = datetime(2026, 11, 10, 23, 59, 59, tzinfo=UTC)


def _at(hour: float) -> datetime:
    return TODAY_START + timedelta(hours=hour)


def _spread(hour: datetime, count: int, minute: int, status: str) -> list:
    step = timedelta(minutes=60) / max(count, 1)
    return [(hour + timedelta(minutes=minute) + step * i, status) for i in range(count)]


def _hourly(first_hour: int, errors: list[int | None], ok: int = 3) -> list:
    """Spans for consecutive hours: ``errors[i]`` error spans and ``ok`` good
    spans in hour ``first_hour + i``, each spread evenly across the hour (four
    errors land at :01, :16, :31 and :46). ``None`` is an hour with no spans
    at all: no data, not zero."""
    spans = []
    for offset, error_count in enumerate(errors):
        if error_count is None:
            continue
        hour = _at(first_hour + offset)
        spans += _spread(hour, error_count, 1, "ERROR") + _spread(hour, ok, 2, "OK")
    return spans


def _fake_clickhouse(spans: list) -> tuple[Mock, Mock]:
    """Answer the monitor queries for count of errors from ``spans``, with the
    semantics of the real SQL: value = errors in [start, end); baseline =
    errors / periods, NULL with no spans; series = errors per epoch-aligned
    bucket, one row per bucket that has spans; the earlier rule's band = mean
    and sample stddev of the errors in each calendar bucket that has spans,
    (0, 0) with none."""

    def value_query(metric_type, start, end):
        return "value", {"start_time": start, "end_time": end}

    def stats_query(metric_type, start, end, periods=1.0):
        return "stats", {"start_time": start, "end_time": end, "periods": periods}

    def series_query(metric_type, start, end, frequency_seconds):
        return "series", {
            "start_time": start,
            "end_time": end,
            "freq": frequency_seconds,
        }

    def band_query(metric_type, start, end, interval_kind=None):
        return "band", {"start_time": start, "end_time": end, "kind": interval_kind}

    def execute_ch_query(query, params, **kwargs):
        inside = [
            (ts, status)
            for ts, status in spans
            if params["start_time"] <= ts < params["end_time"]
        ]
        errors = sum(status == "ERROR" for _, status in inside)
        if query == "value":
            return SimpleNamespace(data=[{"value": errors}])
        if query == "stats":
            mean = errors / params["periods"] if inside else None
            return SimpleNamespace(data=[{"mean": mean, "stddev": None}])
        if query == "band":
            size = {"minute": 60, "hour": 3600}[params["kind"]]
            counts: dict[int, int] = {}
            for ts, status in inside:
                key = int(ts.timestamp()) // size
                counts[key] = counts.get(key, 0) + (status == "ERROR")
            values = list(counts.values())
            mean = statistics.mean(values) if values else 0.0
            stddev = statistics.stdev(values) if len(values) > 1 else 0.0
            return SimpleNamespace(data=[{"mean": mean, "stddev": stddev}])
        buckets: dict[datetime, int] = {}
        for ts, status in inside:
            epoch = int(ts.timestamp()) // params["freq"] * params["freq"]
            key = datetime.fromtimestamp(epoch, tz=UTC)
            buckets[key] = buckets.get(key, 0) + (status == "ERROR")
        return SimpleNamespace(
            data=[{"timestamp": ts, "value": buckets[ts]} for ts in sorted(buckets)]
        )

    builder = Mock()
    builder.build_metric_value_query.side_effect = value_query
    builder.build_historical_stats_query.side_effect = stats_query
    builder.build_time_series_query.side_effect = series_query
    builder.build_earlier_band_query.side_effect = band_query
    analytics = Mock()
    analytics.execute_ch_query.side_effect = execute_ch_query
    return builder, analytics


def _monitor(history_minutes: int = 60, **overrides) -> SimpleNamespace:
    # The walk's setup: count of errors, checked hourly against the hour
    # before ("Same time one hour ago"), warning at 10% higher, critical at 20%.
    fields = {
        "id": "monitor-1",
        "name": "Errors",
        "project": SimpleNamespace(name="Project"),
        "metric_type": MonitorMetricTypeChoices.COUNT_OF_ERRORS.value,
        "threshold_type": "percentage_change",
        "threshold_operator": GT,
        "critical_threshold_value": 20,
        "warning_threshold_value": 10,
        "alert_frequency": 60,
        "auto_threshold_time_window": history_minutes,
        "threshold_rule": "current",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _run_graph(spans, start, end, now, monitor=None) -> tuple[list[str], Mock]:
    builder, analytics = _fake_clickhouse(spans)
    deadline = Mock()
    deadline.remaining_ms.return_value = 1_000
    with (
        patch.object(monitor_graphs.timezone, "now", return_value=now),
        patch.object(monitor_graphs, "AnalyticsQueryService", return_value=analytics),
        patch.object(
            monitor_graphs, "_build_monitor_graph_ch_builder", return_value=builder
        ),
    ):
        graph = monitor_graphs.get_graph_data(
            monitor or _monitor(), start, end, deadline=deadline
        )
    return [bar["status"] for bar in graph["alert_bar_data"]], builder


def _bar_statuses(spans, start, end, now, monitor=None) -> list[str]:
    return _run_graph(spans, start, end, now, monitor)[0]


def _run_worker(spans, now, monitor=None) -> tuple[list[tuple[str, str]], Mock]:
    """Run the alert worker at ``now``: the (type, message) it fires."""
    builder, analytics = _fake_clickhouse(spans)
    with (
        patch.object(monitor_mod, "AnalyticsQueryService", return_value=analytics),
        patch.object(monitor_mod, "build_monitor_ch_builder", return_value=builder),
        patch.object(monitor_mod, "_handle_alert_trigger") as trigger,
    ):
        monitor_mod._process_monitor(monitor or _monitor(), now)
    alerts = [(str(call.args[2]), call.args[1]) for call in trigger.call_args_list]
    return alerts, builder


def _worker_alerts(spans, now, monitor=None) -> list[tuple[str, str]]:
    return _run_worker(spans, now, monitor)[0]


def _worker_status(spans, now, monitor=None) -> str | None:
    alerts = _worker_alerts(spans, now, monitor)
    assert len(alerts) <= 1
    return alerts[0][0] if alerts else None


# --- Cause 1: the baseline is the previous period, not partial clock hours ----


def test_baseline_straddling_an_hour_boundary_is_that_hours_count() -> None:
    # Four errors an hour. The worker at 15:30 compares with 13:30-14:30,
    # which straddles 14:00: two errors on each side. The baseline is the
    # four errors of that hour, not the mean of two partial clock hours (2).
    spans = _hourly(10, [4, 4, 4, 4, 4, 4])
    builder, analytics = _fake_clickhouse(spans)

    baseline = monitor_mod.get_previous_period_baseline(
        _monitor(), _at(13.5), _at(14.5), builder, analytics=analytics
    )

    assert baseline == 4.0
    assert builder.build_historical_stats_query.call_args.kwargs["periods"] == 1.0


def test_flat_errors_across_an_hour_boundary_do_not_alert() -> None:
    spans = _hourly(10, [4, 4, 4, 4, 4, 4])

    assert _worker_alerts(spans, _at(15.5)) == []
    assert "critical" not in _bar_statuses(spans, _at(11), TODAY_END, _at(15.5))


def test_multi_period_history_averages_per_period_counting_empty_ones() -> None:
    # Three hours of history before 15:00: three errors in the first hour,
    # then two hours with no spans at all. Per period that is 3 / 3 = 1, so
    # two errors now are critical; averaging only the hours with data gave 3.
    monitor = _monitor(history_minutes=180)
    spans = _hourly(12, [3, None, None, 2])
    builder, analytics = _fake_clickhouse(spans)

    baseline = monitor_mod.get_previous_period_baseline(
        monitor, _at(12), _at(15), builder, analytics=analytics
    )

    assert baseline == 1.0
    assert builder.build_historical_stats_query.call_args.kwargs["periods"] == 3.0
    assert _worker_status(spans, _at(16), monitor) == "critical"


def test_graph_earlier_buckets_average_per_period_counting_empty_ones() -> None:
    # 13:00 holds 2 errors; its three hours before hold 3, 0 (empty), 0
    # (empty): baseline 1, so the bar is critical. Now (16:00) has no data.
    monitor = _monitor(history_minutes=180)
    spans = _hourly(10, [3, None, None, 2])

    statuses = _bar_statuses(spans, _at(13), TODAY_END, _at(16), monitor)

    assert statuses == ["critical"]


# --- Cause 2: every bucket is judged against its own previous period ----------


def test_steady_then_jump_marks_only_the_jump_on_graph_and_worker() -> None:
    # 4, 4, 4, 4 then 12 errors an hour: only the jump is more than 20% higher.
    spans = _hourly(10, [4, 4, 4, 4, 4, 12])

    statuses = _bar_statuses(spans, _at(11), TODAY_END, _at(16))
    worker = [_worker_status(spans, _at(hour)) for hour in (12, 13, 14, 15, 16)]

    assert statuses == ["healthy", "healthy", "healthy", "healthy", "critical"]
    assert worker == [None, None, None, None, "critical"]


def test_each_bucket_uses_its_own_previous_period_not_the_latest() -> None:
    # The latest baseline is 12. Judging every bar against it hid the jump
    # from 4 to 12 at 12:00; each bar now compares with the hour before it.
    spans = _hourly(10, [4, 4, 12, 12, 12, 12])

    statuses = _bar_statuses(spans, _at(11), TODAY_END, _at(16))

    assert statuses == ["healthy", "critical", "healthy", "healthy", "healthy"]


def test_today_view_judges_now_against_the_hour_before_now() -> None:
    # Now is 15:30 and the page ends tonight: the bar holding now takes the
    # worker's baseline (13:30-14:30, four errors), not an empty future window.
    spans = _hourly(10, [4, 4, 4, 4, 4, 12])

    statuses = _bar_statuses(spans, _at(11), TODAY_END, _at(15.5))

    assert statuses == ["healthy", "healthy", "healthy", "healthy", "critical"]
    assert _worker_status(spans, _at(15.5)) == "critical"


def test_first_error_after_a_clean_hour_is_critical_like_the_worker() -> None:
    # The hours before had spans but no errors: a real zero baseline. The
    # worker fires on the first error against it (any increase over zero), so
    # the graph marks that bucket critical too and keeps the clean hours healthy.
    spans = _hourly(10, [0, 0, 0, 0, 0, 1])

    statuses = _bar_statuses(spans, _at(11), TODAY_END, _at(15.5))

    assert statuses == ["healthy", "healthy", "healthy", "healthy", "critical"]
    assert _worker_status(spans, _at(15.5)) == "critical"


@pytest.mark.parametrize(
    ("errors", "expected_bar", "expected_alert"),
    [
        pytest.param([4, 4, 4, 4, 4, 4], "healthy", None, id="steady"),
        pytest.param([4, 4, 4, 4, 4, 12], "critical", "critical", id="jump"),
        pytest.param([10, 10, 10, 10, 10, 12], "warning", "warning", id="warning"),
        pytest.param([0, 0, 0, 0, 0, 1], "critical", "critical", id="real-zero"),
        pytest.param(
            [None, None, None, None, None, 5],
            "insufficient_data",
            None,
            id="no-history",
        ),
        pytest.param([4, 4, 4, 4, 8, 4], "healthy", None, id="drop"),
    ],
)
def test_worker_and_graph_agree_on_the_latest_bucket(
    errors, expected_bar, expected_alert
) -> None:
    # At 16:00 the worker judges 15:00-16:00, exactly the latest bar.
    spans = _hourly(10, errors)

    statuses = _bar_statuses(spans, _at(10), TODAY_END, _at(16))

    assert statuses[-1] == expected_bar
    assert _worker_status(spans, _at(16)) == expected_alert


@pytest.mark.parametrize("now_hour", [16, 15.5, 15.25])
def test_the_bar_holding_now_takes_the_workers_own_baseline(now_hour) -> None:
    # One baseline function: the graph asks for exactly the baseline the
    # worker asks for at the same moment (same window, same periods), also
    # when now is part-way through an hour.
    spans = _hourly(10, [4, 4, 4, 4, 4, 12])

    _, graph_builder = _run_graph(spans, _at(10), TODAY_END, _at(now_hour))
    _, worker_builder = _run_worker(spans, _at(now_hour))

    graph_calls = graph_builder.build_historical_stats_query.call_args_list
    worker_calls = worker_builder.build_historical_stats_query.call_args_list
    assert len(worker_calls) == 1
    assert graph_calls == worker_calls


# --- (a) no data in the previous period vs (b) a real zero --------------------


def test_no_data_in_the_previous_period_does_not_alert() -> None:
    # (a) No spans at all in 13:30-14:30: not enough history, no alert.
    spans = _hourly(14, [None, 5])

    assert _worker_alerts(spans, _at(15.5)) == []


@pytest.mark.parametrize(
    "now_hour",
    [
        pytest.param(14.5, id="bar-holding-now"),
        pytest.param(16, id="earlier-bar"),
    ],
)
def test_no_data_in_the_previous_period_reads_insufficient_on_the_graph(
    now_hour,
) -> None:
    # (a) 14:00 has errors, but 13:00 had no spans at all: whether 14:00 is
    # the bar holding now (worker baseline) or an earlier bar (series), it is
    # not judged. 11:00 has nothing before it either.
    spans = _hourly(11, [3, None, None, 5])

    statuses = _bar_statuses(spans, _at(11), TODAY_END, _at(now_hour))

    assert statuses == ["insufficient_data", "insufficient_data"]


def test_real_zero_in_the_previous_period_fires_and_says_up_from_zero() -> None:
    # (b) Spans but no errors in 13:30-14:30, then an error: it fires.
    spans = _hourly(13, [0, 0, 1], ok=4)

    alerts = _worker_alerts(spans, _at(15.5))

    assert [alert_type for alert_type, _ in alerts] == ["critical"]
    message = alerts[0][1]
    assert "(greater_than 0.00)" in message
    assert "up from 0 in the previous period" in message
    assert "mean" not in message


def test_real_zero_in_the_previous_period_reads_critical_on_the_graph() -> None:
    # (b) The same on the graph: the bar after a clean hour is critical.
    spans = _hourly(13, [0, 0, 1], ok=4)

    statuses = _bar_statuses(spans, _at(14), TODAY_END, _at(15.5))

    assert statuses == ["healthy", "critical"]


# --- (c) the alert says the previous period's value in plain words ------------


def test_alert_names_the_previous_hours_count() -> None:
    spans = _hourly(10, [4, 4, 4, 4, 4, 12])

    [(alert_type, message)] = _worker_alerts(spans, _at(16))

    assert alert_type == "critical"
    assert "(12.00) breached the critical threshold (greater_than 4.80)" in message
    assert message.endswith(": up from 4 in the previous period.")
    assert "historical data" not in message


@pytest.mark.parametrize(
    ("overrides", "baseline", "expected"),
    [
        ({}, 0.0, "up from 0 in the previous period"),
        ({}, 2.5, "up from 2.5 in the previous period"),
        ({"threshold_operator": LT}, 4.0, "down from 4 in the previous period"),
        (
            {"auto_threshold_time_window": 180},
            1.0,
            "up from an average of 1 per period over the previous 3 hours",
        ),
        (
            {"auto_threshold_time_window": 60 * 24 * 7, "alert_frequency": 15},
            0.25,
            "up from an average of 0.25 per period over the previous 7 days",
        ),
        (
            {
                "metric_type": MonitorMetricTypeChoices.LLM_API_FAILURE_RATES,
                "auto_threshold_time_window": 1440,
            },
            0.004,
            "up from 0.004 over the previous day",
        ),
        # A whole day of daily tokens is still one period's value.
        (
            {
                "metric_type": MonitorMetricTypeChoices.DAILY_TOKENS_SPENT,
                "auto_threshold_time_window": 1440,
            },
            1200.0,
            "up from 1200 in the previous period",
        ),
        # A history shorter than one period: the count was scaled up to the
        # period, so it reads as a rate over the minutes it came from.
        (
            {"auto_threshold_time_window": 15},
            4.0,
            "up from a rate of 4 per period over the previous 15 minutes",
        ),
        (
            {"auto_threshold_time_window": 30, "threshold_operator": LT},
            2.5,
            "down from a rate of 2.5 per period over the previous 30 minutes",
        ),
        (
            {
                "metric_type": MonitorMetricTypeChoices.DAILY_TOKENS_SPENT,
                "auto_threshold_time_window": 60,
            },
            1200.0,
            "up from a rate of 1200 per period over the previous hour",
        ),
        # A rate is not scaled: it is the short history's own value.
        (
            {
                "metric_type": MonitorMetricTypeChoices.LLM_API_FAILURE_RATES,
                "auto_threshold_time_window": 15,
            },
            0.004,
            "up from 0.004 over the previous 15 minutes",
        ),
    ],
)
def test_previous_period_phrase(overrides, baseline, expected) -> None:
    assert monitor_mod._previous_period_phrase(_monitor(**overrides), baseline) == (
        expected
    )


# --- Series edges --------------------------------------------------------------


def _series(first_hour: int, values: list[float]) -> list[dict]:
    return [
        {"timestamp": _at(first_hour + offset), "value": value}
        for offset, value in enumerate(values)
    ]


def test_buckets_without_their_whole_history_read_insufficient() -> None:
    # No page start: the graph reads 30 days back, so the first buckets have
    # history the graph never read. Judging them on part of it would read
    # critical; they are not judged.
    monitor = _monitor(history_minutes=180)

    out = monitor_graphs._process_percentage_change_buckets(
        _series(10, [5, 5, 5, 5, 5]),
        monitor,
        None,
        timedelta(hours=1),
        timedelta(hours=3),
        series_start=_at(10),
    )

    assert [bar["status"] for bar in out["alert_bar_data"]] == [
        "insufficient_data",
        "insufficient_data",
        "insufficient_data",
        "healthy",
        "healthy",
    ]


def test_token_buckets_without_tokens_are_no_history() -> None:
    # Spans with no tokens are no token data (as in the value query), so a
    # token monitor has no baseline there; an error count of 0 is a real zero.
    tokens = _monitor(metric_type=MonitorMetricTypeChoices.TOKEN_USAGE)
    errors = _monitor()

    def statuses(monitor):
        out = monitor_graphs._process_percentage_change_buckets(
            _series(10, [0, 50]),
            monitor,
            None,
            timedelta(hours=1),
            timedelta(hours=1),
            series_start=_at(9),
        )
        return [bar["status"] for bar in out["alert_bar_data"]]

    assert statuses(tokens) == ["insufficient_data", "insufficient_data"]
    assert statuses(errors) == ["insufficient_data", "critical"]


# --- A history that is not a whole number of buckets ---------------------------


def _buckets(minutes: int, errors: list[int], ok: int = 3) -> list:
    """``_hourly`` from 10:00 for buckets of ``minutes``: ``errors[i]`` error
    spans and ``ok`` good spans in bucket ``i``, each spread evenly across it
    (four errors in ten minutes land at :01, :03:30, :06 and :08:30), so any
    stretch of a bucket holds its share."""
    length = timedelta(minutes=minutes)
    spans = []
    for offset, error_count in enumerate(errors):
        start = _at(10) + length * offset
        for count, minute, status in ((error_count, 1, "ERROR"), (ok, 2, "OK")):
            step = length / max(count, 1)
            spans += [
                (start + timedelta(minutes=minute) + step * i, status)
                for i in range(count)
            ]
    return spans


STEADY = [4] * 12
# One bucket of six errors (50% up) among fours.
BUMP = [4] * 8 + [6] + [4] * 3
BUMP_BARS = ["healthy", "healthy", "critical", "healthy", "healthy", "healthy"]


@pytest.mark.parametrize(
    ("interval", "history", "errors", "expected"),
    [
        pytest.param(10, 15, STEADY, ["healthy"] * 6, id="10m-every-15m-back-steady"),
        pytest.param(10, 15, BUMP, BUMP_BARS, id="10m-every-15m-back-bump"),
        pytest.param(60, 60, STEADY, ["healthy"] * 6, id="60m-every-60m-back-steady"),
        pytest.param(60, 60, BUMP, BUMP_BARS, id="60m-every-60m-back-bump"),
    ],
)
def test_earlier_bars_judge_their_history_by_the_whole_buckets_in_it(
    interval, history, errors, expected
) -> None:
    # A 10-minute interval with "Same time 15 minutes ago": the 15 minutes
    # before an earlier bar hold one whole bucket, so that bucket is its
    # baseline, not two thirds of it (which read steady traffic as 50% up).
    # The worker reads those 15 minutes of spans over 1.5 periods (for steady
    # traffic, the same baseline), and each bar matches what the worker fires
    # when that bar ends. A whole number of buckets (60-minute interval,
    # 60-minute history) is judged as before.
    monitor = _monitor(history_minutes=history, alert_frequency=interval)
    spans = _buckets(interval, errors)
    bucket = timedelta(minutes=interval)
    page_start = _at(10) + bucket * 6
    now = _at(10) + bucket * 12
    bar_ends = [page_start + bucket * (i + 1) for i in range(6)]

    statuses = _bar_statuses(spans, page_start, TODAY_END, now, monitor)
    worker = [_worker_status(spans, end, monitor) for end in bar_ends]

    assert statuses == expected
    assert worker == [None if status == "healthy" else status for status in expected]


# --- A history shorter than one period is scaled up to that period -------------

# A 1-hour interval, judged at 16:00 on a page from 11:00: four earlier bars
# and the bar holding now (15:00-16:00). A 15-minute history holds no whole
# bucket, so the earlier bars are not judged; a 60-minute one holds one.
SHORT_OR_WHOLE_HISTORY = [
    pytest.param(15, 0.25, ["insufficient_data"] * 4, id="15m-history"),
    pytest.param(60, 1.0, ["healthy"] * 4, id="60m-history-unchanged"),
]
# How the alert names that baseline: a rate over the 15 minutes it came from,
# or the previous hour's own count.
SHORT_OR_WHOLE_PHRASE = {
    15: "up from a rate of 4 per period over the previous 15 minutes",
    60: "up from 4 in the previous period",
}


@pytest.mark.parametrize(("history", "periods", "earlier_bars"), SHORT_OR_WHOLE_HISTORY)
def test_history_shorter_than_one_period_steady_traffic_reads_normal(
    history, periods, earlier_bars
) -> None:
    # "Same time 15 minutes ago" on a 1-hour interval: four errors an hour,
    # one in each quarter. The quarter before 15:00 holds one error; scaled
    # up to the hour that is 4, so the four errors of 15:00-16:00 are steady.
    # Compared raw (4 against 1) they read critical. A whole period (60
    # minutes) is judged as before, with the same baseline.
    monitor = _monitor(history_minutes=history)
    spans = _hourly(10, [4, 4, 4, 4, 4, 4])
    builder, analytics = _fake_clickhouse(spans)

    baseline = monitor_mod.get_previous_period_baseline(
        monitor,
        _at(15) - timedelta(minutes=history),
        _at(15),
        builder,
        analytics=analytics,
    )
    statuses, graph_builder = _run_graph(spans, _at(11), TODAY_END, _at(16), monitor)
    alerts, worker_builder = _run_worker(spans, _at(16), monitor)

    assert builder.build_historical_stats_query.call_args.kwargs["periods"] == periods
    assert baseline == 4.0
    assert alerts == []
    assert statuses == [*earlier_bars, "healthy"]
    # The bar holding now asks for exactly the worker's baseline.
    assert (
        graph_builder.build_historical_stats_query.call_args_list
        == worker_builder.build_historical_stats_query.call_args_list
    )


@pytest.mark.parametrize(("history", "periods", "earlier_bars"), SHORT_OR_WHOLE_HISTORY)
def test_history_shorter_than_one_period_fourfold_rise_reads_critical(
    history, periods, earlier_bars
) -> None:
    # Four errors an hour, then sixteen in 15:00-16:00: four times the rate.
    # Scaled up, the quarter before 15:00 says 4 an hour, so 16 is critical
    # on the graph and in the worker, exactly as for a whole period. The
    # alert names the scaled value as a rate over those 15 minutes, never as
    # a count "in the previous period".
    monitor = _monitor(history_minutes=history)
    spans = _hourly(10, [4, 4, 4, 4, 4, 16])

    statuses, graph_builder = _run_graph(spans, _at(11), TODAY_END, _at(16), monitor)
    alerts, worker_builder = _run_worker(spans, _at(16), monitor)

    assert statuses == [*earlier_bars, "critical"]
    [(alert_type, message)] = alerts
    assert alert_type == "critical"
    assert "(16.00) breached the critical threshold (greater_than 4.80)" in message
    assert message.endswith(f": {SHORT_OR_WHOLE_PHRASE[history]}.")
    worker_calls = worker_builder.build_historical_stats_query.call_args_list
    assert [call.kwargs["periods"] for call in worker_calls] == [periods]
    assert graph_builder.build_historical_stats_query.call_args_list == worker_calls


@pytest.mark.parametrize(
    ("overrides", "history"),
    [
        pytest.param({"alert_frequency": 0}, timedelta(hours=1), id="zero-period"),
        pytest.param({}, timedelta(0), id="empty-history"),
    ],
)
def test_no_period_or_no_history_is_no_baseline(overrides, history) -> None:
    # A bad row (alert frequency 0) or an empty history has nothing to scale
    # by: no baseline and no query, never a ZeroDivisionError.
    monitor = _monitor(**overrides)
    builder, analytics = _fake_clickhouse(_hourly(10, [4, 4, 4, 4, 4, 4]))

    assert monitor_mod.baseline_periods(monitor, history) is None
    assert (
        monitor_mod.get_previous_period_baseline(
            monitor, _at(15) - history, _at(15), builder, analytics=analytics
        )
        is None
    )
    builder.build_historical_stats_query.assert_not_called()


# --- A monitor saved before the threshold update keeps the earlier rule ---------

# The five hours before 15:00 hold 2, 6, 2, 6 and 4 errors: mean 4, sample
# stddev 2. With critical 50% and warning 10% the earlier rule's lines are
# 4 + 2 * 1.5 = 7 and 4 + 2 * 1.1 = 6.2; the current rule's are 4 * 1.5 = 6
# and 4 * 1.1 = 4.4.
EARLIER_HISTORY = [2, 6, 2, 6, 4]


@pytest.mark.parametrize(
    ("errors_now", "band_status"),
    [
        pytest.param(6, "healthy", id="inside-the-band"),
        pytest.param(7, "warning", id="above-the-warning-line"),
        pytest.param(8, "critical", id="above-the-critical-line"),
    ],
)
@pytest.mark.parametrize(
    "page_end",
    [pytest.param(_at(16), id="page-ends-now"), pytest.param(TODAY_END, id="today")],
)
def test_earlier_rule_bar_holding_now_matches_the_workers_band(
    errors_now, band_status, page_end
) -> None:
    monitor = _monitor(
        history_minutes=300,
        threshold_rule="earlier",
        critical_threshold_value=50,
        warning_threshold_value=10,
    )
    spans = _hourly(10, [*EARLIER_HISTORY, errors_now])

    statuses, graph_builder = _run_graph(spans, _at(11), page_end, _at(16), monitor)
    alerts, worker_builder = _run_worker(spans, _at(16), monitor)

    assert statuses[-1] == band_status
    assert [alert_type for alert_type, _ in alerts] == (
        [] if band_status == "healthy" else [band_status]
    )
    # One band: the graph asks for exactly the history the worker judges
    # against, in production's hour buckets, also on a page ending tonight.
    worker_calls = worker_builder.build_earlier_band_query.call_args_list
    assert [call.kwargs["interval_kind"] for call in worker_calls] == ["hour"]
    assert graph_builder.build_earlier_band_query.call_args_list == worker_calls


def test_earlier_rule_alert_names_the_band() -> None:
    monitor = _monitor(
        history_minutes=300,
        threshold_rule="earlier",
        critical_threshold_value=50,
        warning_threshold_value=10,
    )
    spans = _hourly(10, [*EARLIER_HISTORY, 8])

    [(alert_type, message)] = _worker_alerts(spans, _at(16), monitor)

    assert alert_type == "critical"
    assert message.endswith(
        "(8.00) breached the critical threshold (greater_than 7.00) based on "
        "historical data (mean: 4.00, stddev: 2.00)."
    )
