"""The exact dashboard replay states hash aggregation; other shapes stream.

Offline SQL shape only, no network. The live differential that proves both
execution strategies publish identical rows over a multi-part table is
``test_dashboard_replay_read_settings_ch25.py``.
"""

import re
import socket

import pytest

from tracer.services.clickhouse.v2.query_builders.dashboard import (
    _EXACT_REPLAY_CANDIDATE_CTE,
    DashboardQueryBuilderV2,
)

pytestmark = pytest.mark.unit

PROJECT = "11111111-1111-4111-8111-111111111111"
REQUIRED = "use_skip_indexes_if_final = 0, optimize_use_projections = 1"
_KEY_RE = re.compile(r"\boptimize_aggregation_in_order\s*=\s*(\d)")


@pytest.fixture(autouse=True)
def _offline_only(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Offline dashboard test attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def _filter(key, values):
    return {
        "metric_type": "custom_attribute",
        "metric_name": key,
        "operator": "in",
        "value": list(values),
        "attribute_type": "string",
        "source": "traces",
        "canonical_filter": {
            "column_id": key,
            "filter_config": {
                "col_type": "SPAN_ATTRIBUTE",
                "filter_type": "text",
                "filter_op": "in",
                "filter_value": list(values),
                "attribute_value_types": ["string"] * len(values),
            },
        },
    }


def _metric(name, aggregation="avg"):
    return {
        "id": name,
        "name": name,
        "type": "system_metric",
        "source": "traces",
        "aggregation": aggregation,
    }


def _config(*, granularity="day", metrics=("latency",), filters=None):
    return {
        "project_ids": [PROJECT],
        "granularity": granularity,
        "time_range": {"preset": "30D"},
        "metrics": [_metric(name) for name in metrics],
        "filters": (
            [_filter("workflow_stage", ["intake", "review"])]
            if filters is None
            else filters
        ),
        "breakdowns": [],
    }


def _settings_tail(sql):
    head, separator, tail = sql.rpartition("\nSETTINGS ")
    assert separator, "the statement must end with one SETTINGS clause"
    assert "SETTINGS" not in head, "exactly one SETTINGS clause is expected"
    return tail.strip()


def _in_order_values(sql):
    return [int(value) for value in _KEY_RE.findall(sql)]


def _build(config, *, latest_state=True):
    builder = DashboardQueryBuilderV2(config)
    sql, params = builder._build_metric_query_for_snapshot_mode(
        builder.metrics[0], latest_state=latest_state
    )
    return builder, sql, params


@pytest.mark.parametrize("granularity", ("day", "month"))
def test_exact_replay_statement_states_hash_aggregation_once(granularity):
    _, sql, _ = _build(_config(granularity=granularity))

    assert _EXACT_REPLAY_CANDIDATE_CTE in sql
    assert _settings_tail(sql) == f"{REQUIRED}, optimize_aggregation_in_order = 0"
    assert _in_order_values(sql) == [0]


def test_raw_scan_of_the_same_dashboard_keeps_streaming_aggregation():
    _, sql, _ = _build(_config(), latest_state=False)

    assert _EXACT_REPLAY_CANDIDATE_CTE not in sql
    assert _settings_tail(sql) == f"{REQUIRED}, optimize_aggregation_in_order = 1"
    assert _in_order_values(sql) == [1]


def test_final_fallback_source_keeps_streaming_aggregation():
    builder = DashboardQueryBuilderV2(_config())
    # The untouched fallback: the replay source declines the filter shape.
    builder._exact_filter_replay_source = lambda *args, **kwargs: None
    sql, _ = builder._build_metric_query_for_snapshot_mode(
        builder.metrics[0], latest_state=True
    )

    assert _EXACT_REPLAY_CANDIDATE_CTE not in sql
    assert " FINAL" in sql
    assert _settings_tail(sql) == f"{REQUIRED}, optimize_aggregation_in_order = 1"
    assert _in_order_values(sql) == [1]


def test_unfiltered_dashboard_keeps_streaming_aggregation():
    _, sql, _ = _build(_config(filters=[]))

    assert _EXACT_REPLAY_CANDIDATE_CTE not in sql
    assert _settings_tail(sql) == f"{REQUIRED}, optimize_aggregation_in_order = 1"


def test_grouped_replay_statement_carries_the_opt_out():
    """Compatible metrics share one statement; the grouped text keeps it.

    ``latency`` carries a root-only predicate the others lack, so it never
    groups with them; two plain sums share one replay source and one tail.
    """

    builder = DashboardQueryBuilderV2(_config(metrics=("cost", "tokens")))
    builder._latest_state_spans_required = True
    prepared = tuple(
        (metric, *builder.build_metric_query(metric)) for metric in builder.metrics
    )
    groups = builder.group_prepared_metric_queries(prepared)

    assert len(groups) == 1
    _, plan = groups[0]
    assert plan is not None, "two compatible replay metrics must group"
    assert len(plan.metrics) == 2
    assert _EXACT_REPLAY_CANDIDATE_CTE in plan.sql
    assert _settings_tail(plan.sql) == f"{REQUIRED}, optimize_aggregation_in_order = 0"
    assert _in_order_values(plan.sql) == [0]


def test_replay_and_plain_metrics_never_share_a_statement():
    """Different SETTINGS tails are different groups by construction."""

    builder = DashboardQueryBuilderV2(_config(metrics=("latency", "cost")))
    replay = builder._build_metric_query_for_snapshot_mode(
        builder.metrics[0], latest_state=True
    )
    plain = builder._build_metric_query_for_snapshot_mode(
        builder.metrics[1], latest_state=False
    )
    prepared = ((builder.metrics[0], *replay), (builder.metrics[1], *plain))
    groups = builder.group_prepared_metric_queries(prepared)

    assert len(groups) == 2
    assert all(plan is None for _, plan in groups)
