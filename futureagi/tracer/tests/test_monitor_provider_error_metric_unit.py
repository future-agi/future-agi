"""Provider error metrics count failing providers, not error-free providers.

Exercise the real SQL builders without seeding or executing a ClickHouse query.
For a fully offline run, the harness must set FI_SKIP_CH25_SCHEMA_APPLY=1 before
pytest startup; module fixtures below bypass the remaining CH schema fixtures.
"""

from datetime import UTC, datetime

import pytest

from tracer.models.monitor import MonitorMetricTypeChoices
from tracer.services.clickhouse.query_builders.monitor_metrics import (
    MonitorMetricsQueryBuilder,
)
from tracer.services.clickhouse.v2.query_builders.monitor_metrics import (
    MonitorMetricsQueryBuilderV2,
)

pytestmark = pytest.mark.unit

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
START = datetime(2026, 8, 10, 10, tzinfo=UTC)
END = datetime(2026, 8, 10, 11, tzinfo=UTC)
FREQUENCY_SECONDS = 300


@pytest.fixture(scope="module", autouse=True)
def _drop_legacy_ch_spans_mvs() -> None:
    """Override the root CH-mutating fixture for this SQL-only module."""


@pytest.fixture(scope="module", autouse=True)
def _ensure_test_score_tenant_column() -> None:
    """Override the root CH schema fixture; no tables are needed here."""


@pytest.fixture(
    params=(MonitorMetricsQueryBuilder, MonitorMetricsQueryBuilderV2),
    ids=("base", "v2"),
)
def builder(request) -> MonitorMetricsQueryBuilder:
    return request.param(
        project_id=PROJECT_ID,
        filters={"observation_type": "llm"},
    )


def _query(
    builder: MonitorMetricsQueryBuilder,
    mode: str,
    metric_type: str = MonitorMetricTypeChoices.SERVICE_PROVIDER_ERROR_RATES,
) -> tuple[str, dict]:
    if mode == "value":
        sql, params = builder.build_metric_value_query(metric_type, START, END)
    elif mode == "historical":
        sql, params = builder.build_historical_stats_query(metric_type, START, END)
    else:
        assert mode == "series"
        sql, params = builder.build_time_series_query(
            metric_type, START, END, FREQUENCY_SECONDS
        )
    return " ".join(sql.split()), params


def test_provider_value_counts_providers_with_any_error(builder) -> None:
    sql, _ = _query(builder, "value")

    # One ERROR among any number of successful spans makes that provider fail.
    # The denominator is distinct providers, not spans or only failed providers.
    assert (
        "CASE WHEN uniq(provider) = 0 THEN NULL "
        "ELSE uniqIf(provider, error_count > 0) / uniq(provider) END AS value"
    ) in sql
    assert "SELECT provider, countIf(status = 'ERROR') AS error_count" in sql
    assert "GROUP BY provider" in sql
    assert "uniqIf(provider, error_count = 0)" not in sql


def test_provider_historical_indicator_is_one_for_errors(builder) -> None:
    sql, _ = _query(builder, "historical")

    # All failing providers => mean 1; all successful providers => mean 0.
    assert (
        "CASE WHEN countIf(status = 'ERROR') > 0 THEN 1.0 ELSE 0.0 END AS is_error"
    ) in sql
    assert "ifNotFinite(avg(is_error), NULL) AS mean" in sql
    assert "ifNotFinite(stddevPop(is_error), NULL) AS stddev" in sql
    assert "GROUP BY provider" in sql
    assert "is_error_free" not in sql


def test_provider_series_counts_errors_within_each_bucket(builder) -> None:
    sql, params = _query(builder, "series")

    assert (
        "CASE WHEN uniq(provider) = 0 THEN 0 "
        "ELSE uniqIf(provider, error_count > 0) / uniq(provider) END AS value"
    ) in sql
    assert (
        "toDateTime(intDiv(toUInt32(start_time), %(freq_seconds)s) "
        "* %(freq_seconds)s) AS timestamp, "
        "provider, countIf(status = 'ERROR') AS error_count"
    ) in sql
    assert (
        "GROUP BY timestamp, provider ) GROUP BY timestamp ORDER BY timestamp"
    ) in sql
    assert params["freq_seconds"] == FREQUENCY_SECONDS
    assert "uniqIf(provider, error_count = 0)" not in sql


@pytest.mark.parametrize("mode", ("value", "historical", "series"))
def test_provider_population_scope_and_window_are_preserved(builder, mode) -> None:
    sql, params = _query(builder, mode)

    assert "FROM spans" in sql
    assert "project_id = %(project_id)s" in sql
    assert "is_deleted = 0" in sql
    assert "observation_type = %(mf_obs_type)s" in sql
    assert "provider != ''" in sql
    assert "provider IS NOT NULL" not in sql
    assert "start_time >= %(start_time)s AND start_time < %(end_time)s" in sql
    assert "created_at >= %(start_time)s - INTERVAL 1 DAY" in sql
    assert "tracer_eval_logger" not in sql
    assert params["project_id"] == PROJECT_ID
    assert params["mf_obs_type"] == "llm"
    assert params["start_time"] == START.replace(tzinfo=None)
    assert params["end_time"] == END.replace(tzinfo=None)
    assert params["start_date"] == params["start_time"]


@pytest.mark.parametrize("mode", ("value", "historical", "series"))
def test_session_metric_still_counts_error_free_sessions(builder, mode) -> None:
    sql, _ = _query(builder, mode, MonitorMetricTypeChoices.ERROR_FREE_SESSION_RATES)

    if mode == "historical":
        assert (
            "CASE WHEN countIf(rs.status = 'ERROR') > 0 "
            "THEN 0.0 ELSE 1.0 END AS is_error_free"
        ) in sql
        assert "ifNotFinite(avg(is_error_free), NULL) AS mean" in sql
        assert "ifNotFinite(stddevPop(is_error_free), NULL) AS stddev" in sql
    else:
        assert (
            "uniqIf(trace_session_id, error_count = 0) / uniq(trace_session_id)"
        ) in sql
        assert "uniqIf(trace_session_id, error_count > 0)" not in sql


# --- Monitors saved before the update keep the share with no errors ------------


@pytest.fixture(
    params=(MonitorMetricsQueryBuilder, MonitorMetricsQueryBuilderV2),
    ids=("base", "v2"),
)
def earlier_builder(request) -> MonitorMetricsQueryBuilder:
    return request.param(
        project_id=PROJECT_ID,
        filters={"observation_type": "llm"},
        provider_error_free=True,
    )


@pytest.mark.parametrize("mode", ("value", "historical", "series"))
def test_earlier_rows_count_error_free_providers(earlier_builder, mode) -> None:
    sql, _ = _query(earlier_builder, mode)

    if mode == "historical":
        assert (
            "CASE WHEN countIf(status = 'ERROR') > 0 "
            "THEN 0.0 ELSE 1.0 END AS is_error_free"
        ) in sql
        assert "ifNotFinite(avg(is_error_free), NULL) AS mean" in sql
        assert "ifNotFinite(stddevPop(is_error_free), NULL) AS stddev" in sql
    else:
        assert "uniqIf(provider, error_count = 0) / uniq(provider)" in sql
        assert "uniqIf(provider, error_count > 0)" not in sql


def test_earlier_provider_band_is_its_historical_query_rewritten_once(
    earlier_builder,
) -> None:
    # A rate metric's band is its per-row historical stats. On the v2 builder
    # every build method rewrites its SQL, and the band's SQL is rewritten once.
    provider = MonitorMetricTypeChoices.SERVICE_PROVIDER_ERROR_RATES
    band = earlier_builder.build_earlier_band_query(
        provider, START, END, interval_kind="hour"
    )

    assert band == earlier_builder.build_historical_stats_query(provider, START, END)
