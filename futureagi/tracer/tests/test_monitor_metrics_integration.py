"""Integration tests: seed real rows into ClickHouse and assert the metric
VALUES the monitor path computes (and one true end-to-end alert firing).

Unlike the SQL-string tests (which assert query shape) these prove the queries
compute the RIGHT number against real data. Each test uses a fresh project_id so
rows are isolated; the test ClickHouse is ephemeral so no cleanup is needed
(the spans table has projections, which block DELETE anyway).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from tracer.models.monitor import MonitorMetricTypeChoices
from tracer.services.clickhouse.client import get_clickhouse_client
from tracer.services.clickhouse.eval_logger_table import eval_logger_source
from tracer.services.clickhouse.v2.query_builders.monitor_metrics import (
    MonitorMetricsQueryBuilderV2,
)
from tracer.utils.monitor import _get_historical_stats, _get_metric_value

pytestmark = pytest.mark.integration

# Whole-second, tz-naive: CH DateTime64 and the builder's _parse_dt both want naive.
NOW = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
WINDOW_START = NOW - timedelta(minutes=30)
WINDOW_END = NOW + timedelta(minutes=1)

_SPAN_COLS = [
    "id", "trace_id", "project_id", "name", "observation_type", "status",
    "start_time", "created_at", "total_tokens", "latency_ms", "is_deleted",
    "trace_session_id", "provider", "attrs_string",
]


@pytest.fixture
def ch():
    return get_clickhouse_client()


@pytest.fixture
def project_id() -> str:
    return str(uuid.uuid4())


def _span(project_id: str, **kw: Any) -> Dict[str, Any]:
    row = dict(
        id=str(uuid.uuid4()), trace_id=str(uuid.uuid4()), project_id=project_id,
        name="s", observation_type="llm", status="OK", start_time=NOW,
        created_at=NOW, total_tokens=0, latency_ms=0, is_deleted=0,
        trace_session_id=None, provider="", attrs_string={},
    )
    row.update(kw)
    return row


def _seed_spans(ch, rows: List[Dict[str, Any]]) -> None:
    ch.execute(
        "INSERT INTO spans (%s) VALUES" % ",".join(_SPAN_COLS),
        [{c: r[c] for c in _SPAN_COLS} for r in rows],
    )


def _seed_evals(ch, config_id: str, evals: List[Dict[str, Any]]) -> None:
    table, _ = eval_logger_source()
    # id is part of the ReplacingMergeTree sort key — must be unique per row or
    # FINAL collapses distinct evals of the same config.
    # Shape-intersection columns only: the legacy prod table has ``deleted``,
    # v2 has ``is_deleted`` — both DEFAULT to not-deleted, so neither is
    # seeded (keeps the insert valid on a fresh CH for either shape).
    cols = [
        "id", "custom_eval_config_id", "observation_span_id", "output_float",
        "output_bool", "output_str_list", "created_at",
    ]
    rows = []
    for ev in evals:
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "custom_eval_config_id": config_id,
                "observation_span_id": ev["span_id"],
                "output_float": ev.get("output_float"),
                "output_bool": ev.get("output_bool"),
                "output_str_list": ev.get("output_str_list", "[]"),
                "created_at": ev.get("created_at", NOW),
            }
        )
    ch.execute("INSERT INTO %s (%s) VALUES" % (table, ",".join(cols)), rows)


def _monitor(project_id: str, metric_type: str, **kw: Any) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(), metric_type=metric_type, project_id=project_id,
        filters=None, metric=None, threshold_metric_value=None, alert_frequency=60,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _value(project_id: str, metric_type: str, **kw: Any) -> Optional[float]:
    return _get_metric_value(
        _monitor(project_id, metric_type, **kw), WINDOW_START, WINDOW_END
    )


# --- Scalar metric values -----------------------------------------------------


def test_count_of_errors_value(ch, project_id):
    _seed_spans(ch, [_span(project_id, status=s) for s in ["ERROR", "ERROR", "OK"]])
    assert _value(project_id, MonitorMetricTypeChoices.COUNT_OF_ERRORS) == 2


def test_span_response_time_avg(ch, project_id):
    _seed_spans(ch, [_span(project_id, latency_ms=lm) for lm in [100, 200, 300]])
    assert _value(project_id, MonitorMetricTypeChoices.SPAN_RESPONSE_TIME) == 200.0


def test_llm_response_time_only_counts_llm(ch, project_id):
    _seed_spans(
        ch,
        [
            _span(project_id, observation_type="llm", latency_ms=100),
            _span(project_id, observation_type="llm", latency_ms=200),
            _span(project_id, observation_type="tool", latency_ms=9999),
        ],
    )
    assert _value(project_id, MonitorMetricTypeChoices.LLM_RESPONSE_TIME) == 150.0


def test_token_usage_sum(ch, project_id):
    _seed_spans(ch, [_span(project_id, total_tokens=t) for t in [10, 20, 30]])
    assert _value(project_id, MonitorMetricTypeChoices.TOKEN_USAGE) == 60


def test_token_usage_empty_window_is_none(ch, project_id):
    # No spans at all -> None (no-data), which the evaluator skips (no false alert).
    assert _value(project_id, MonitorMetricTypeChoices.TOKEN_USAGE) is None


def test_token_usage_all_zero_tokens_is_none(ch, project_id):
    # Traffic but zero tokens (v2 non-Nullable) -> None, not 0.
    _seed_spans(ch, [_span(project_id, total_tokens=0) for _ in range(3)])
    assert _value(project_id, MonitorMetricTypeChoices.TOKEN_USAGE) is None


def test_error_rates_for_function_calling(ch, project_id):
    # tool spans: 1 error of 4 -> 0.25; llm span ignored.
    _seed_spans(
        ch,
        [_span(project_id, observation_type="tool", status=s) for s in ["ERROR", "OK", "OK", "OK"]]
        + [_span(project_id, observation_type="llm", status="ERROR")],
    )
    assert _value(project_id, MonitorMetricTypeChoices.ERROR_RATES_FOR_FUNCTION_CALLING) == 0.25


def test_llm_api_failure_rates(ch, project_id):
    _seed_spans(
        ch,
        [_span(project_id, observation_type="llm", status=s) for s in ["ERROR", "ERROR", "ERROR", "OK", "OK"]],
    )
    assert _value(project_id, MonitorMetricTypeChoices.LLM_API_FAILURE_RATES) == 0.6


def test_service_provider_error_free_rate(ch, project_id):
    # provider p1 has an error, p2 does not -> error-free provider rate = 1/2.
    _seed_spans(
        ch,
        [
            _span(project_id, provider="p1", status="ERROR"),
            _span(project_id, provider="p1", status="OK"),
            _span(project_id, provider="p2", status="OK"),
        ],
    )
    assert _value(project_id, MonitorMetricTypeChoices.SERVICE_PROVIDER_ERROR_RATES) == 0.5


def test_error_free_session_rates(ch, project_id):
    s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
    _seed_spans(
        ch,
        [
            _span(project_id, trace_session_id=s1, status="ERROR"),
            _span(project_id, trace_session_id=s1, status="OK"),
            _span(project_id, trace_session_id=s2, status="OK"),
        ],
    )
    # session s1 has an error, s2 does not -> 1/2 error-free.
    assert _value(project_id, MonitorMetricTypeChoices.ERROR_FREE_SESSION_RATES) == 0.5


def test_error_free_session_rates_dedups_remap_aliases(ch, project_id):
    # Two aliases of one logical session (cross-cutover straddler) must count
    # as ONE session via the trace_session_id_remap survivor map — not two.
    old_id, new_id, other = (str(uuid.uuid4()) for _ in range(3))
    ch.execute(
        "INSERT INTO trace_session_id_remap (old_id, new_id, version) VALUES",
        [{"old_id": old_id, "new_id": new_id, "version": 1}],
    )
    _seed_spans(
        ch,
        [
            _span(project_id, trace_session_id=old_id, status="ERROR"),
            _span(project_id, trace_session_id=new_id, status="OK"),
            _span(project_id, trace_session_id=other, status="OK"),
        ],
    )
    # Straddler resolves to one session (carrying the error) + one clean
    # session -> 1/2 error-free. Without dedup this would read 2/3.
    assert _value(project_id, MonitorMetricTypeChoices.ERROR_FREE_SESSION_RATES) == 0.5


# --- Filters ------------------------------------------------------------------


def test_entity_scoping_filter_is_ignored(ch, project_id):
    # session_id is not a valid monitor filter — it must be dropped, so the
    # metric counts every matching span regardless of the stray key.
    s1, s2 = str(uuid.uuid4()), str(uuid.uuid4())
    _seed_spans(
        ch,
        [_span(project_id, trace_session_id=s1, status="ERROR") for _ in range(2)]
        + [_span(project_id, trace_session_id=s2, status="ERROR") for _ in range(5)],
    )
    assert _value(project_id, MonitorMetricTypeChoices.COUNT_OF_ERRORS, filters={"session_id": [s1]}) == 7


def test_observation_type_filter(ch, project_id):
    _seed_spans(
        ch,
        [_span(project_id, observation_type="tool", status="ERROR") for _ in range(2)]
        + [_span(project_id, observation_type="llm", status="ERROR") for _ in range(4)],
    )
    val = _value(project_id, MonitorMetricTypeChoices.COUNT_OF_ERRORS, filters={"observation_type": ["tool"]})
    assert val == 2


# --- Historical stats ---------------------------------------------------------


def test_historical_stats_count_of_errors(ch, project_id):
    # Two hourly buckets: 4 errors then 2 errors -> mean 3, sample stddev sqrt(2).
    b1 = NOW - timedelta(hours=2)
    b2 = NOW - timedelta(hours=1)
    rows = [_span(project_id, status="ERROR", start_time=b1, created_at=b1) for _ in range(4)]
    rows += [_span(project_id, status="ERROR", start_time=b2, created_at=b2) for _ in range(2)]
    _seed_spans(ch, rows)
    m = _monitor(project_id, MonitorMetricTypeChoices.COUNT_OF_ERRORS, alert_frequency=60)
    mean, stddev = _get_historical_stats(m, NOW - timedelta(hours=3), NOW)
    assert mean == pytest.approx(3.0)
    assert stddev == pytest.approx(2.0 ** 0.5, rel=1e-6)


# --- Evaluation metrics (eval-logger table) -----------------------------------


def _eval_value(
    project_id, cfg, output_type, threshold_metric_value=None,
    start=None, end=None,
):
    from tracer.services.clickhouse.query_service import AnalyticsQueryService

    builder = MonitorMetricsQueryBuilderV2(
        project_id=project_id,
        eval_config_id=cfg,
        eval_output_type=output_type,
        threshold_metric_value=threshold_metric_value,
    )
    query, params = builder.build_metric_value_query(
        MonitorMetricTypeChoices.EVALUATION_METRICS, start or WINDOW_START, end or WINDOW_END
    )
    return AnalyticsQueryService().execute_ch_query(
        query, params, timeout_ms=10000
    ).data[0]["value"]


# prod uses the legacy `tracer_eval_logger`; test settings default to `_v2`. The
# two use different not-deleted predicates, so assert the value on BOTH.
@pytest.mark.parametrize(
    "eval_table", ["tracer_eval_logger", "tracer_eval_logger_v2"]
)
def test_evaluation_metrics_score_avg(ch, project_id, eval_table):
    from django.test import override_settings

    with override_settings(CH25_EVAL_LOGGER_TABLE=eval_table):
        cfg = str(uuid.uuid4())
        sp1, sp2 = _span(project_id), _span(project_id)
        _seed_spans(ch, [sp1, sp2])
        _seed_evals(
            ch,
            cfg,
            [
                {"span_id": sp1["id"], "output_float": 0.4},
                {"span_id": sp2["id"], "output_float": 0.6},
            ],
        )
        assert _eval_value(project_id, cfg, "SCORE") == pytest.approx(0.5)


@pytest.mark.parametrize(
    "eval_table", ["tracer_eval_logger", "tracer_eval_logger_v2"]
)
def test_evaluation_metrics_pass_fail_rate(ch, project_id, eval_table):
    from django.test import override_settings

    with override_settings(CH25_EVAL_LOGGER_TABLE=eval_table):
        cfg = str(uuid.uuid4())
        spans = [_span(project_id) for _ in range(3)]
        _seed_spans(ch, spans)
        _seed_evals(
            ch,
            cfg,
            [
                {"span_id": spans[0]["id"], "output_bool": 1},
                {"span_id": spans[1]["id"], "output_bool": 1},
                {"span_id": spans[2]["id"], "output_bool": 0},
            ],
        )
        # 2 of 3 "Passed" -> 0.666…
        assert _eval_value(
            project_id, cfg, "PASS_FAIL", threshold_metric_value="Passed"
        ) == pytest.approx(2 / 3)


@pytest.mark.parametrize(
    "eval_table", ["tracer_eval_logger", "tracer_eval_logger_v2"]
)
def test_evaluation_metrics_choices_rate(ch, project_id, eval_table):
    from django.test import override_settings

    with override_settings(CH25_EVAL_LOGGER_TABLE=eval_table):
        cfg = str(uuid.uuid4())
        spans = [_span(project_id) for _ in range(3)]
        _seed_spans(ch, spans)
        _seed_evals(
            ch,
            cfg,
            [
                {"span_id": spans[0]["id"], "output_str_list": '["good"]'},
                {"span_id": spans[1]["id"], "output_str_list": '["bad"]'},
                {"span_id": spans[2]["id"], "output_str_list": '["good"]'},
            ],
        )
        # 2 of 3 contain "good" -> 0.666…
        assert _eval_value(
            project_id, cfg, "CHOICES", threshold_metric_value="good"
        ) == pytest.approx(2 / 3)


# --- Trailing-window (daily) --------------------------------------------------


def test_daily_tokens_spent_uses_trailing_day(ch, project_id):
    # _get_metric_value overrides the window to a trailing 24h for daily tokens.
    old = NOW - timedelta(days=2)
    _seed_spans(
        ch,
        [_span(project_id, total_tokens=30), _span(project_id, total_tokens=30)]
        + [_span(project_id, total_tokens=100, start_time=old, created_at=old)],
    )
    # Only the two recent spans count -> 60; the 2-day-old one is excluded.
    assert _value(project_id, MonitorMetricTypeChoices.DAILY_TOKENS_SPENT) == 60


def test_historical_stats_token_usage(ch, project_id):
    b1 = NOW - timedelta(hours=2)
    b2 = NOW - timedelta(hours=1)
    _seed_spans(
        ch,
        [_span(project_id, total_tokens=10, start_time=b1, created_at=b1)]
        + [_span(project_id, total_tokens=30, start_time=b2, created_at=b2)],
    )
    m = _monitor(project_id, MonitorMetricTypeChoices.TOKEN_USAGE, alert_frequency=60)
    mean, stddev = _get_historical_stats(m, NOW - timedelta(hours=3), NOW)
    assert mean == pytest.approx(20.0)  # buckets [10, 30]
    assert stddev == pytest.approx(200.0 ** 0.5, rel=1e-6)  # sample stddev


# --- End-to-end: real monitor row -> real alert log ---------------------------


@pytest.mark.django_db
def test_end_to_end_static_alert_fires(ch, observe_project):
    from tracer.models.monitor import UserAlertMonitor, UserAlertMonitorLog
    from tracer.utils.monitor import process_monitor_task

    _seed_spans(ch, [_span(str(observe_project.id), status="ERROR") for _ in range(5)])
    monitor = UserAlertMonitor.objects.create(
        organization=observe_project.organization,
        project=observe_project,
        name="E2E error count",
        metric_type="count_of_errors",
        threshold_operator="greater_than",
        threshold_type="static",
        critical_threshold_value=0,
        alert_frequency=60,
    )
    process_monitor_task._original_func(
        str(monitor.id), (NOW + timedelta(seconds=30)).isoformat()
    )
    log = UserAlertMonitorLog.objects.get(alert=monitor)
    assert log.type == "critical"
    assert "5.00" in log.message


@pytest.mark.django_db
def test_end_to_end_percentage_change_fires(ch, observe_project):
    from tracer.models.monitor import UserAlertMonitor, UserAlertMonitorLog
    from tracer.utils.monitor import process_monitor_task

    pid = str(observe_project.id)
    # Historical (calendar-hour buckets, within the 1-week baseline): 1 then 3
    # errors -> mean 2, stddev ~1.41 -> critical threshold ~3.41.
    h1, h2 = NOW - timedelta(hours=4), NOW - timedelta(hours=3)
    _seed_spans(
        ch,
        [_span(pid, status="ERROR", start_time=h1, created_at=h1)]
        + [_span(pid, status="ERROR", start_time=h2, created_at=h2) for _ in range(3)]
        # Current window: 10 errors -> well over the band.
        + [_span(pid, status="ERROR") for _ in range(10)],
    )
    monitor = UserAlertMonitor.objects.create(
        organization=observe_project.organization,
        project=observe_project,
        name="E2E pct change",
        metric_type="count_of_errors",
        threshold_operator="greater_than",
        threshold_type="percentage_change",
        critical_threshold_value=0,
        alert_frequency=60,
    )
    process_monitor_task._original_func(
        str(monitor.id), (NOW + timedelta(seconds=30)).isoformat()
    )
    assert UserAlertMonitorLog.objects.get(alert=monitor).type == "critical"


@pytest.mark.django_db
def test_graph_percentage_change_marks_current_bucket_critical(ch, observe_project):
    from tracer.utils.monitor_graphs import get_percentage_change_metric_graph_data

    pid = str(observe_project.id)
    h1, h2 = NOW - timedelta(hours=4), NOW - timedelta(hours=3)
    _seed_spans(
        ch,
        [_span(pid, status="ERROR", start_time=h1, created_at=h1)]
        + [_span(pid, status="ERROR", start_time=h2, created_at=h2) for _ in range(3)]
        + [_span(pid, status="ERROR") for _ in range(10)],
    )
    monitor = SimpleNamespace(
        id=uuid.uuid4(), metric_type="count_of_errors", project_id=pid, filters=None,
        metric=None, threshold_metric_value=None, alert_frequency=60,
        auto_threshold_time_window=60 * 24 * 7, threshold_type="percentage_change",
        threshold_operator="greater_than", critical_threshold_value=0,
        warning_threshold_value=None,
    )
    out = get_percentage_change_metric_graph_data(monitor)
    # The alert bars use the evaluator's band; the spike bucket must be critical.
    assert any(bar["status"] == "critical" for bar in out["alert_bar_data"])


@pytest.mark.django_db
def test_end_to_end_no_data_no_alert(ch, observe_project):
    from tracer.models.monitor import UserAlertMonitor, UserAlertMonitorLog
    from tracer.utils.monitor import process_monitor_task

    monitor = UserAlertMonitor.objects.create(
        organization=observe_project.organization,
        project=observe_project,
        name="E2E quiet",
        metric_type="span_response_time",
        # less_than discriminates: a buggy 0 would fire (0 < 1), a correct
        # NULL no-data skip does not.
        threshold_operator="less_than",
        threshold_type="static",
        critical_threshold_value=1,
        alert_frequency=60,
    )
    # No spans -> avg latency is NULL -> no-data skip -> no alert.
    process_monitor_task._original_func(
        str(monitor.id), (NOW + timedelta(seconds=30)).isoformat()
    )
    assert UserAlertMonitorLog.objects.filter(alert=monitor).count() == 0


# --- Window boundary + time-series values -------------------------------------


def _run_query(query, params):
    from tracer.services.clickhouse.query_service import AnalyticsQueryService

    return AnalyticsQueryService().execute_ch_query(query, params, timeout_ms=10000).data


def test_half_open_window_excludes_end_boundary(ch, project_id):
    # A span whose start_time lands exactly at end_time belongs to the NEXT
    # window, never both (event-time half-open window).
    inside = _span(project_id, status="ERROR", start_time=NOW - timedelta(minutes=1))
    boundary = _span(project_id, status="ERROR", start_time=NOW)
    _seed_spans(ch, [inside, boundary])
    b = MonitorMetricsQueryBuilderV2(project_id=project_id)
    query, params = b.build_metric_value_query(
        MonitorMetricTypeChoices.COUNT_OF_ERRORS, NOW - timedelta(minutes=30), NOW
    )
    assert _run_query(query, params)[0]["value"] == 1
    # ...and the boundary span is picked up by the adjacent window.
    query, params = b.build_metric_value_query(
        MonitorMetricTypeChoices.COUNT_OF_ERRORS, NOW, NOW + timedelta(minutes=30)
    )
    assert _run_query(query, params)[0]["value"] == 1


def test_time_series_bucket_values(ch, project_id):
    # Two hourly buckets with 4 and 2 errors -> per-bucket values [4, 2].
    b1 = NOW - timedelta(hours=2)
    b2 = NOW - timedelta(hours=1)
    rows = [_span(project_id, status="ERROR", start_time=b1, created_at=b1) for _ in range(4)]
    rows += [_span(project_id, status="ERROR", start_time=b2, created_at=b2) for _ in range(2)]
    _seed_spans(ch, rows)
    b = MonitorMetricsQueryBuilderV2(project_id=project_id)
    query, params = b.build_time_series_query(
        MonitorMetricTypeChoices.COUNT_OF_ERRORS, NOW - timedelta(hours=3), NOW, 3600
    )
    values = [row["value"] for row in _run_query(query, params)]
    assert values == [4, 2]


def test_eval_windows_on_span_time_not_eval_time(ch, project_id):
    # THE semantic contract: the monitor window means "spans that ran in the
    # window", not "evals computed in the window" (evals run async, later).
    # A late-computed eval for an in-window span must count; an eval computed
    # now for an out-of-window span must not.
    cfg = str(uuid.uuid4())
    ws, we = NOW - timedelta(hours=3), NOW - timedelta(hours=2)
    in_win = _span(
        project_id,
        start_time=NOW - timedelta(minutes=150),
        created_at=NOW - timedelta(minutes=150),
    )
    out_win = _span(
        project_id,
        start_time=NOW - timedelta(days=3),
        created_at=NOW - timedelta(days=3),
    )
    _seed_spans(ch, [in_win, out_win])
    _seed_evals(
        ch,
        cfg,
        [
            # eval computed NOW — hours after the window closed
            {"span_id": in_win["id"], "output_float": 1.0, "created_at": NOW},
            {"span_id": out_win["id"], "output_float": 0.0, "created_at": NOW},
        ],
    )
    # Only the in-window span's eval counts, even though BOTH evals were
    # computed now (outside the window): 1.0, not 0.5 and not None.
    assert _eval_value(project_id, cfg, "SCORE", start=ws, end=we) == 1.0


def test_eval_excludes_spans_outside_window(ch, project_id):
    # A 20-day-old span's eval must NOT count toward the current 30-min
    # window (pre-fix, a 30-day lookback wrongly included it).
    cfg = str(uuid.uuid4())
    recent = _span(project_id)
    old = _span(
        project_id,
        start_time=NOW - timedelta(days=20),
        created_at=NOW - timedelta(days=20),
    )
    _seed_spans(ch, [recent, old])
    _seed_evals(
        ch,
        cfg,
        [
            {"span_id": recent["id"], "output_float": 1.0},
            {"span_id": old["id"], "output_float": 0.0, "created_at": NOW},
        ],
    )
    assert _eval_value(project_id, cfg, "SCORE") == 1.0


# --- span-attribute filter: windowed trace membership -------------------------


def test_attr_filter_is_span_scoped_and_windowed(ch, project_id):
    # Span-scoped attr filters (PG parity): only ERROR spans that THEMSELVES
    # carry the attribute count — not siblings of a matching trace — and only
    # inside the event-time window.
    t1 = str(uuid.uuid4())
    rows = [
        # attr + ERROR, in window -> the only counted span
        _span(
            project_id, trace_id=t1, status="ERROR",
            attrs_string={"llm.model_name": "gpt-4o"},
        ),
        # attr but OK -> not counted
        _span(project_id, trace_id=t1, attrs_string={"llm.model_name": "gpt-4o"}),
        # ERROR without the attr, same trace -> not counted (span scope,
        # unlike the dashboard's trace scoping)
        _span(project_id, trace_id=t1, status="ERROR"),
        # attr + ERROR but outside the window -> not counted
        _span(
            project_id, status="ERROR",
            attrs_string={"llm.model_name": "gpt-4o"},
            start_time=NOW - timedelta(days=3),
            created_at=NOW - timedelta(days=3),
        ),
    ]
    _seed_spans(ch, rows)
    from tracer.services.clickhouse.query_service import AnalyticsQueryService

    builder = MonitorMetricsQueryBuilderV2(
        project_id=project_id,
        filters={
            "span_attributes_filters": [
                {
                    "column_id": "llm.model_name",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "gpt-4o",
                    },
                }
            ]
        },
    )
    query, params = builder.build_metric_value_query(
        MonitorMetricTypeChoices.COUNT_OF_ERRORS, WINDOW_START, WINDOW_END
    )
    value = AnalyticsQueryService().execute_ch_query(
        query, params, timeout_ms=10000
    ).data[0]["value"]
    assert value == 1  # only the in-window ERROR span carrying the attr
