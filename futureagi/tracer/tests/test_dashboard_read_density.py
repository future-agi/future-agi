"""Cost prediction for exact dashboard trace statements.

The predictor decides which lane a heavy widget takes before its statement
runs. An unknown scope or an unmeasured statement degrades to "no prediction",
the pre-existing inline path. A probe that cannot answer for a costable
statement does not: that read is uncosted, and an uncosted read is scheduled,
never admitted to the interactive wall on the strength of not knowing.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.core.cache import cache

from tracer.services.clickhouse.dashboard_read_density import (
    density_scope_key,
    estimated_rows_for,
    exceeds_remaining_deadline,
    observe_completed_read,
    probe_candidate_estimates,
    read_density_record,
)
from tracer.services.clickhouse.read_budget import ReadDeadline
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)


def _attribute_filtered_config(project_id=None):
    """A widget whose filter compiles to an exhaustive raw value witness."""

    return {
        "project_ids": [str(project_id or uuid.uuid4())],
        "granularity": "day",
        "time_range": {"preset": "7D"},
        "metrics": [
            {
                "id": "latency",
                "name": "latency",
                "type": "system_metric",
                "aggregation": "avg",
            }
        ],
        "filters": [
            {
                "metric_type": "custom_attribute",
                "metric_name": "final_status",
                "operator": "equal_to",
                "value": "settled",
                "attribute_type": "string",
                "canonical_filter": {
                    "column_id": "final_status",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "settled",
                    },
                },
            }
        ],
        "breakdowns": [],
        "require_versioned_snapshot": True,
    }


def _unfiltered_config():
    config = _attribute_filtered_config()
    config["filters"] = []
    return config


def _build_metric_sql(config):
    builder = DashboardQueryBuilderV2(config)
    builder._latest_state_spans_required = True
    metric = builder.metrics[0]
    return builder.build_metric_query(metric)


@pytest.fixture(autouse=True)
def _clean_density_cache():
    cache.clear()
    yield
    cache.clear()


def _result(read_bytes, query_time_ms):
    return SimpleNamespace(read_bytes=read_bytes, query_time_ms=query_time_ms)


def test_estimate_statement_is_the_candidate_cte_the_statement_embeds(settings):
    settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
    sql, _params = _build_metric_sql(_attribute_filtered_config())

    estimate_sql = DashboardQueryBuilderV2.candidate_estimate_statement(sql)

    assert estimate_sql is not None
    assert estimate_sql.startswith("EXPLAIN ESTIMATE ")
    body = estimate_sql[len("EXPLAIN ESTIMATE ") :]
    # Not a reconstruction: the estimated text is lifted out of the statement.
    assert body in sql
    assert body.startswith("SELECT")
    assert "FROM spans AS dashboard_candidate_source" in body
    # The replay half is not estimated; only identity discovery is.
    assert "dashboard_replay_source" not in body
    assert "SETTINGS" not in body


def test_statement_without_a_candidate_cte_has_nothing_to_estimate(settings):
    settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
    sql, _params = _build_metric_sql(_unfiltered_config())

    assert "dashboard_filter_candidate_identities" not in sql
    assert DashboardQueryBuilderV2.candidate_estimate_statement(sql) is None


def test_scope_key_is_stable_and_does_not_carry_project_identity():
    project_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

    key = density_scope_key(project_ids)

    assert key == density_scope_key(list(reversed(project_ids)))
    assert key != density_scope_key(project_ids[:1])
    assert all(project_id not in key for project_id in project_ids)


def test_cold_scope_predicts_nothing_and_never_routes():
    scope_key = density_scope_key([str(uuid.uuid4())])

    assert read_density_record(scope_key) is None
    assert not exceeds_remaining_deadline(
        {"EXPLAIN ESTIMATE SELECT 1": 10_000_000},
        scope_key=scope_key,
        remaining_ms=30_000,
    )


def test_completed_statements_teach_the_scope_its_density():
    scope_key = density_scope_key([str(uuid.uuid4())])

    observe_completed_read(scope_key, 1_000, _result(2_000_000, 1_000.0))
    record = read_density_record(scope_key)

    assert record.samples == 1
    assert record.bytes_per_estimated_row == pytest.approx(2_000.0)
    assert record.bytes_per_ms == pytest.approx(2_000.0)
    assert record.predict_ms(5_000) == pytest.approx(5_000.0)


def test_density_follows_the_newest_statement_without_jumping_to_it():
    scope_key = density_scope_key([str(uuid.uuid4())])

    observe_completed_read(scope_key, 1_000, _result(1_000_000, 1_000.0))
    observe_completed_read(scope_key, 1_000, _result(2_000_000, 1_000.0))
    record = read_density_record(scope_key)

    assert record.samples == 2
    # One heavy sibling moves the record toward itself, not onto it.
    assert 1_000.0 < record.bytes_per_estimated_row < 2_000.0
    assert 1_000.0 < record.bytes_per_ms < 2_000.0


def test_unprobed_statement_teaches_throughput_only():
    scope_key = density_scope_key([str(uuid.uuid4())])

    observe_completed_read(scope_key, None, _result(4_000_000, 2_000.0))
    record = read_density_record(scope_key)

    assert record.bytes_per_estimated_row == 0
    assert record.bytes_per_ms == pytest.approx(2_000.0)
    # Half a record cannot predict, so the request stays on today's path.
    assert record.predict_ms(1_000) is None


def test_unmeasured_statement_teaches_nothing():
    scope_key = density_scope_key([str(uuid.uuid4())])

    observe_completed_read(scope_key, 1_000, _result(None, 1_000.0))

    assert read_density_record(scope_key) is None


def test_probe_estimates_each_distinct_candidate_cte_once(settings):
    settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
    sql, params = _build_metric_sql(_attribute_filtered_config())
    analytics = MagicMock()
    analytics.execute_ch_query.return_value = SimpleNamespace(
        data=[{"database": "d", "table": "spans", "parts": 4, "rows": 71_998}]
    )

    estimates = probe_candidate_estimates(
        [(sql, params), (sql, params)],
        analytics=analytics,
        deadline=ReadDeadline.start(30_000),
    )

    assert analytics.execute_ch_query.call_count == 1
    estimate_sql = DashboardQueryBuilderV2.candidate_estimate_statement(sql)
    assert list(estimates.values()) == [71_998]
    assert estimated_rows_for(estimates, sql, params) == 71_998
    call = analytics.execute_ch_query.call_args
    assert call.args[0] == estimate_sql
    assert call.args[1] is params
    assert call.kwargs["settings"]["max_threads"] == 1


def test_same_filter_shape_on_different_values_is_estimated_separately(settings):
    """Identical candidate SQL over a different value scans different rows."""

    settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
    project_id = uuid.uuid4()
    settled_sql, settled_params = _build_metric_sql(
        _attribute_filtered_config(project_id)
    )
    rejected_config = _attribute_filtered_config(project_id)
    rejected_config["filters"][0]["value"] = "rejected"
    rejected_config["filters"][0]["canonical_filter"]["filter_config"][
        "filter_value"
    ] = "rejected"
    rejected_sql, rejected_params = _build_metric_sql(rejected_config)

    assert settled_sql == rejected_sql
    assert settled_params != rejected_params

    analytics = MagicMock()
    analytics.execute_ch_query.side_effect = [
        SimpleNamespace(data=[{"rows": 71_998}]),
        SimpleNamespace(data=[{"rows": 12}]),
    ]

    estimates = probe_candidate_estimates(
        [(settled_sql, settled_params), (rejected_sql, rejected_params)],
        analytics=analytics,
        deadline=ReadDeadline.start(30_000),
    )

    assert analytics.execute_ch_query.call_count == 2
    assert estimated_rows_for(estimates, settled_sql, settled_params) == 71_998
    assert estimated_rows_for(estimates, rejected_sql, rejected_params) == 12


def test_probe_failure_means_unknown_not_absent(settings):
    """A costable statement whose probe raises is recorded as uncosted.

    Leaving it out of the mapping made it indistinguishable from a statement
    that has nothing to estimate, and the gate then let it run inline: the
    production shape, one probe earlier.
    """

    settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
    sql, params = _build_metric_sql(_attribute_filtered_config())
    analytics = MagicMock()
    analytics.execute_ch_query.side_effect = RuntimeError("probe unavailable")

    estimates = probe_candidate_estimates(
        [(sql, params)],
        analytics=analytics,
        deadline=ReadDeadline.start(30_000),
    )

    assert list(estimates.values()) == [None]
    assert estimated_rows_for(estimates, sql, params) is None


def test_probe_with_no_deadline_left_is_unknown_not_absent(settings):
    settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
    sql, params = _build_metric_sql(_attribute_filtered_config())
    analytics = MagicMock()
    deadline = ReadDeadline.start(1)
    while True:
        try:
            deadline.remaining_ms()
        except Exception:  # noqa: BLE001 - the wall has expired
            break

    estimates = probe_candidate_estimates(
        [(sql, params)],
        analytics=analytics,
        deadline=deadline,
    )

    assert list(estimates.values()) == [None]
    analytics.execute_ch_query.assert_not_called()


def test_an_uncosted_statement_is_not_admitted_inline_even_on_a_cold_scope():
    """Unknown means schedule. The scope's density does not enter into it."""

    scope_key = density_scope_key([str(uuid.uuid4())])
    assert read_density_record(scope_key) is None

    assert exceeds_remaining_deadline(
        {"EXPLAIN ESTIMATE SELECT 1": None},
        scope_key=scope_key,
        remaining_ms=30_000,
    )
    # A costed sibling does not rescue the uncosted one.
    observe_completed_read(scope_key, 1_000, _result(1_000_000, 1_000.0))
    assert exceeds_remaining_deadline(
        {"EXPLAIN ESTIMATE SELECT 1": 1, "EXPLAIN ESTIMATE SELECT 2": None},
        scope_key=scope_key,
        remaining_ms=30_000,
    )


def test_statement_without_a_candidate_cte_is_never_probed(settings):
    settings.DASHBOARD_ATTR_ROLLUP_ENABLED = False
    sql, params = _build_metric_sql(_unfiltered_config())
    analytics = MagicMock()

    estimates = probe_candidate_estimates(
        [(sql, params)],
        analytics=analytics,
        deadline=ReadDeadline.start(30_000),
    )

    assert estimates == {}
    analytics.execute_ch_query.assert_not_called()


def test_prediction_is_compared_against_the_deadline_the_request_has_left():
    scope_key = density_scope_key([str(uuid.uuid4())])
    observe_completed_read(scope_key, 1_000, _result(1_000_000, 1_000.0))
    estimates = {"EXPLAIN ESTIMATE SELECT 1": 20_000}

    # 20,000 estimated rows x 1,000 B/row / 1,000 B/ms = 20,000 ms.
    assert exceeds_remaining_deadline(
        estimates, scope_key=scope_key, remaining_ms=19_999
    )
    assert not exceeds_remaining_deadline(
        estimates, scope_key=scope_key, remaining_ms=20_001
    )
