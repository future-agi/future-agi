"""Execution smoke: monitor metrics SQL runs against the live test ClickHouse.

Every other monitor test asserts on SQL strings or mocks the CH client; the
generated SQL — notably the session-remap join composition (three-level nested
subquery + window function + GROUP BY on a full expression) run through the V2
rewriter, and the eval INNER JOIN membership — was never executed. This suite
builds the SQL through the production resolver (``get_query_builder_class``)
and executes it via ``AnalyticsQueryService().execute_ch_query`` — the exact
path ``tracer/utils/monitor.py`` / ``monitor_graphs.py`` use — against the
live test CH, on a tiny seeded dataset with known expected values.

Harness: mirrors ``test_span_reader_column_projection.py`` — connect via
``get_v2_config()``, SKIP when CH is down, seed through ``_ch_seed`` (the
production ``adapt()`` shape), tear down with synchronous ``ALTER … DELETE``.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from django.conf import settings
from django.test import override_settings

WINDOW_START = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
WINDOW_END = datetime(2026, 8, 10, 11, 0, tzinfo=UTC)
EMPTY_START = datetime(2026, 1, 1, tzinfo=UTC)
EMPTY_END = datetime(2026, 1, 2, tzinfo=UTC)
FREQ_SECONDS = 300

EVAL_CONFIG_ID = str(uuid.uuid4())

SPAN_METRICS = [
    "count_of_errors",
    "error_rates_for_function_calling",
    "error_free_session_rates",
    "service_provider_error_rates",
    "llm_api_failure_rates",
    "span_response_time",
    "llm_response_time",
    "token_usage",
    "daily_tokens_spent",
    "monthly_tokens_spent",
]

# (eval_output_type, threshold_metric_value)
EVAL_VARIANTS = [
    ("SCORE", None),
    ("PASS_FAIL", "Passed"),
    ("CHOICES", "Good"),
]

# Seed shape (all inside the window, unique per-span times for bucketing):
#   s1 llm  OK    sess_a     latency 100 tokens 100 provider openai     (eval: 1.0/pass/Good)
#   s2 tool ERROR sess_a     latency  50 tokens   0 provider openai
#   s3 llm  OK    strad_old  latency 300 tokens 200 provider anthropic  (eval: 0.5/fail/Bad)
#   s4 agent OK   strad_new  latency  10 tokens   0 provider ''
#   s5 llm  ERROR (no sess)  latency 400 tokens  50 provider anthropic
# Remap strad_old -> strad_new: both alias spans resolve to ONE session, so
# resolved sessions = {sess_a (has error), straddler (error-free)}.
EXPECTED_VALUE: dict[str, Any] = {
    "count_of_errors": 2,
    "error_rates_for_function_calling": pytest.approx(1.0),
    "error_free_session_rates": pytest.approx(0.5),
    "service_provider_error_rates": pytest.approx(0.0),
    "llm_api_failure_rates": pytest.approx(1 / 3),
    "span_response_time": pytest.approx(172.0),
    "llm_response_time": pytest.approx(800 / 3),
    "token_usage": 350,
    "daily_tokens_spent": 350,
    "monthly_tokens_spent": 350,
}
EXPECTED_EVAL_VALUE = {
    "SCORE": pytest.approx(0.75),
    "PASS_FAIL": pytest.approx(0.5),
    "CHOICES": pytest.approx(0.5),
}


def _not_nan(v: Any) -> bool:
    """NULL is fine; a float NaN leaking to the caller is not."""
    return not (isinstance(v, float) and math.isnan(v))


def _builder_cls() -> type:
    """Resolve the builder exactly the way ``tracer/utils/monitor.py`` does,
    with monitor_metrics routed to v2 so the rewriter is on the path."""
    from tracer.services.clickhouse.v2.dispatch import get_query_builder_class

    v2_cfg = {**settings.CLICKHOUSE_V2, "QUERY_TYPES_V2_ONLY": "monitor_metrics"}
    with override_settings(CLICKHOUSE_V2=v2_cfg):
        return get_query_builder_class("MONITOR_METRICS")


def _make_builder(eval_output_type: str | None = None, threshold: str | None = None):
    return _builder_cls()(
        project_id=_PROJECT_ID,
        eval_config_id=EVAL_CONFIG_ID if eval_output_type else None,
        eval_output_type=eval_output_type,
        threshold_metric_value=threshold,
    )


def _execute(sql: str, params: dict[str, Any]):
    """The production execution path (monitor.py / monitor_graphs.py)."""
    from tracer.services.clickhouse.query_service import AnalyticsQueryService

    return AnalyticsQueryService().execute_ch_query(sql, params)


# One project per module run so seeds never collide with other suites.
_PROJECT_ID = str(uuid.uuid4())


def _span(
    span_id: str,
    *,
    observation_type: str,
    status: str,
    start_time: datetime,
    latency_ms: int,
    total_tokens: int,
    provider: str,
    trace_session_id: str | None,
) -> dict[str, Any]:
    return {
        "id": span_id,
        "trace_id": str(uuid.uuid4()),
        "project_id": _PROJECT_ID,
        "org_id": str(uuid.uuid4()),
        "parent_span_id": "",
        "name": f"smoke-{span_id[:8]}",
        "observation_type": observation_type,
        "status": status,
        "start_time": start_time,
        "end_time": start_time,
        "created_at": start_time,
        "latency_ms": latency_ms,
        "total_tokens": total_tokens,
        "provider": provider,
        "trace_session_id": trace_session_id,
    }


def _seed_eval_rows(client: Any, rows: list[dict[str, Any]]) -> str:
    """Insert eval rows into the table ``eval_logger_source()`` resolves,
    writing only the columns that table actually carries (v2 vs CDC shape)."""
    from tracer.services.clickhouse.eval_logger_table import eval_logger_source

    eval_table, _ = eval_logger_source()
    table_cols = [
        r[0]
        for r in client.query(
            "SELECT name FROM system.columns "
            "WHERE database = currentDatabase() AND table = %(t)s",
            parameters={"t": eval_table},
        ).result_rows
    ]
    cols = [c for c in rows[0] if c in table_cols]
    client.insert(eval_table, [[r[c] for c in cols] for r in rows], column_names=cols)
    return eval_table


def _delete_sync(client: Any, table: str, where: str, params: dict[str, Any]) -> None:
    """Synchronous ALTER … DELETE (mutations_sync=2) — deterministic teardown."""
    client.command(
        f"ALTER TABLE {table} DELETE WHERE {where} SETTINGS mutations_sync=2",
        parameters=params,
    )


@pytest.fixture(scope="module")
def seeded():
    """Seed the tiny dataset; SKIPs when CH is down (either interface)."""
    import clickhouse_connect

    from tracer.services.clickhouse.v2 import get_v2_config
    from tracer.tests._ch_seed import seed_ch_spans

    cfg = get_v2_config()
    try:
        client = clickhouse_connect.get_client(
            host=cfg["host"],
            port=cfg["http_port"],
            username=cfg["user"],
            password=cfg["password"] or "",
            database=cfg["database"],
        )
        client.command("SELECT 1")
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"CH 25.3 (v2) not reachable ({exc!r}); integration test")
    try:
        _execute("SELECT 1", {})
    except Exception as exc:  # pragma: no cover - env-dependent
        pytest.skip(f"CH native (query-service) interface not reachable ({exc!r})")

    sess_a = str(uuid.uuid4())
    strad_old = str(uuid.uuid4())
    strad_new = str(uuid.uuid4())

    def t(minute: int) -> datetime:
        return WINDOW_START.replace(minute=minute)

    s1, s2, s3, s4, s5 = (f"smoke-span-{uuid.uuid4().hex[:12]}" for _ in range(5))
    spans = [
        _span(s1, observation_type="llm", status="OK", start_time=t(2),
              latency_ms=100, total_tokens=100, provider="openai",
              trace_session_id=sess_a),
        _span(s2, observation_type="tool", status="ERROR", start_time=t(7),
              latency_ms=50, total_tokens=0, provider="openai",
              trace_session_id=sess_a),
        _span(s3, observation_type="llm", status="OK", start_time=t(12),
              latency_ms=300, total_tokens=200, provider="anthropic",
              trace_session_id=strad_old),
        _span(s4, observation_type="agent", status="OK", start_time=t(17),
              latency_ms=10, total_tokens=0, provider="",
              trace_session_id=strad_new),
        _span(s5, observation_type="llm", status="ERROR", start_time=t(22),
              latency_ms=400, total_tokens=50, provider="anthropic",
              trace_session_id=None),
    ]
    seed_ch_spans(spans, client=client)

    # Straddler bridge: old -> new; survivor is the old id (argMin over strings).
    client.command(
        "INSERT INTO trace_session_id_remap (old_id, new_id, version) "
        "VALUES (%(o)s, %(n)s, %(v)s)",
        parameters={"o": strad_old, "n": strad_new, "v": WINDOW_START},
    )

    def eval_row(span_id: str, out_float: float, out_bool: int, choices: str) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "observation_span_id": span_id,
            "custom_eval_config_id": EVAL_CONFIG_ID,
            "output_bool": out_bool,
            "output_float": out_float,
            "output_str": None,
            "output_str_list": choices,
            "status": "completed",
            "error": 0,
            "is_deleted": 0,
            "deleted": 0,
            "created_at": WINDOW_START,
            "updated_at": WINDOW_START,
        }

    eval_table = _seed_eval_rows(
        client,
        [
            eval_row(s1, 1.0, 1, '["Good"]'),
            eval_row(s3, 0.5, 0, '["Bad"]'),
        ],
    )

    try:
        yield
    finally:
        _delete_sync(client, "spans", "project_id = %(p)s", {"p": _PROJECT_ID})
        _delete_sync(
            client, "trace_session_id_remap", "old_id = %(o)s", {"o": strad_old}
        )
        _delete_sync(
            client,
            eval_table,
            "custom_eval_config_id = %(c)s",
            {"c": EVAL_CONFIG_ID},
        )
        client.close()


@pytest.mark.integration
class TestMonitorMetricsExecutionSmoke:
    def test_dispatch_resolves_v2_builder(self, seeded):
        from tracer.services.clickhouse.v2.query_builders.monitor_metrics import (
            MonitorMetricsQueryBuilderV2,
        )

        assert _builder_cls() is MonitorMetricsQueryBuilderV2

    # ── metric value (single scalar) ─────────────────────────────────────────

    @pytest.mark.parametrize("metric_type", SPAN_METRICS)
    def test_metric_value_executes(self, seeded, metric_type: str):
        sql, params = _make_builder().build_metric_value_query(
            metric_type, WINDOW_START, WINDOW_END
        )
        result = _execute(sql, params)
        assert result.row_count == 1
        value = result.data[0]["value"]
        assert _not_nan(value)
        assert value == EXPECTED_VALUE[metric_type]

    @pytest.mark.parametrize("eval_output_type,threshold", EVAL_VARIANTS)
    def test_eval_metric_value_executes(
        self, seeded, eval_output_type: str, threshold: str | None
    ):
        sql, params = _make_builder(
            eval_output_type, threshold
        ).build_metric_value_query("evaluation_metrics", WINDOW_START, WINDOW_END)
        result = _execute(sql, params)
        assert result.row_count == 1
        value = result.data[0]["value"]
        assert _not_nan(value)
        assert value == EXPECTED_EVAL_VALUE[eval_output_type]

    # ── historical stats (mean + stddev) ─────────────────────────────────────

    @pytest.mark.parametrize("metric_type", SPAN_METRICS)
    def test_historical_stats_executes(self, seeded, metric_type: str):
        sql, params = _make_builder().build_historical_stats_query(
            metric_type, WINDOW_START, WINDOW_END, interval_kind="hour"
        )
        result = _execute(sql, params)
        assert result.row_count == 1
        row = result.data[0]
        assert set(row) == {"mean", "stddev"}
        assert _not_nan(row["mean"]) and _not_nan(row["stddev"])
        assert row["mean"] is not None  # every metric has data in the window

    def test_historical_stats_known_values(self, seeded):
        # Per-row stats: mean latency over the 5 seeded spans.
        sql, params = _make_builder().build_historical_stats_query(
            "span_response_time", WINDOW_START, WINDOW_END
        )
        row = _execute(sql, params).data[0]
        assert row["mean"] == pytest.approx(172.0)
        # Calendar-bucketed stats: one hour bucket -> (value, 0).
        sql, params = _make_builder().build_historical_stats_query(
            "count_of_errors", WINDOW_START, WINDOW_END, interval_kind="hour"
        )
        row = _execute(sql, params).data[0]
        assert row["mean"] == pytest.approx(2.0)
        assert row["stddev"] == pytest.approx(0.0)

    @pytest.mark.parametrize("eval_output_type,threshold", EVAL_VARIANTS)
    def test_eval_historical_stats_executes(
        self, seeded, eval_output_type: str, threshold: str | None
    ):
        sql, params = _make_builder(
            eval_output_type, threshold
        ).build_historical_stats_query(
            "evaluation_metrics", WINDOW_START, WINDOW_END
        )
        result = _execute(sql, params)
        assert result.row_count == 1
        row = result.data[0]
        assert row["mean"] == EXPECTED_EVAL_VALUE[eval_output_type]
        assert _not_nan(row["stddev"])

    # ── time series (timestamp + value rows) ─────────────────────────────────

    @pytest.mark.parametrize("metric_type", SPAN_METRICS)
    def test_time_series_executes(self, seeded, metric_type: str):
        sql, params = _make_builder().build_time_series_query(
            metric_type, WINDOW_START, WINDOW_END, FREQ_SECONDS
        )
        result = _execute(sql, params)
        assert result.row_count >= 1
        timestamps = []
        for row in result.data:
            assert set(row) == {"timestamp", "value"}
            assert isinstance(row["timestamp"], datetime)
            assert _not_nan(row["value"])
            ts = row["timestamp"].replace(tzinfo=UTC)
            assert WINDOW_START <= ts < WINDOW_END
            assert ts.timestamp() % FREQ_SECONDS == 0  # bucket-floored
            timestamps.append(row["timestamp"])
        assert timestamps == sorted(timestamps)

    @pytest.mark.parametrize("eval_output_type,threshold", EVAL_VARIANTS)
    def test_eval_time_series_executes(
        self, seeded, eval_output_type: str, threshold: str | None
    ):
        sql, params = _make_builder(
            eval_output_type, threshold
        ).build_time_series_query(
            "evaluation_metrics", WINDOW_START, WINDOW_END, FREQ_SECONDS
        )
        result = _execute(sql, params)
        # Two eval'd spans in different 300s buckets.
        assert result.row_count == 2
        for row in result.data:
            assert isinstance(row["timestamp"], datetime)
            assert _not_nan(row["value"])

    # ── remap composition + empty window ─────────────────────────────────────

    def test_error_free_session_rate_unifies_straddler(self, seeded):
        """The remap join collapses the straddler's old+new alias spans into
        ONE error-free session: 1 error-free of 2 resolved sessions."""
        sql, params = _make_builder().build_metric_value_query(
            "error_free_session_rates", WINDOW_START, WINDOW_END
        )
        value = _execute(sql, params).data[0]["value"]
        assert value == pytest.approx(0.5)

    @pytest.mark.parametrize("metric_type", ["token_usage", "span_response_time"])
    def test_empty_window_value_is_null(self, seeded, metric_type: str):
        """No data must read as NULL (never 0, never NaN) — a 0/NaN would
        falsely fire LESS_THAN monitors."""
        sql, params = _make_builder().build_metric_value_query(
            metric_type, EMPTY_START, EMPTY_END
        )
        result = _execute(sql, params)
        assert result.row_count == 1
        assert result.data[0]["value"] is None
