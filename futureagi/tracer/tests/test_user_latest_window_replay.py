"""Replacement/window contracts for finite Users reads (no database writes)."""

import re
from datetime import UTC, datetime, timedelta

import pytest

from tracer.services.clickhouse.query_builders.user_list import UserListQueryBuilder
from tracer.services.users_list_manager import _users_attr_enrichment_query

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
USER = "22222222-2222-4222-8222-222222222222"
START = datetime(2026, 9, 1, 12, 15, 0, 123456, tzinfo=UTC)
END = START + timedelta(microseconds=1)


def cte(sql, name):
    start = re.search(r"\b" + name + r" AS \(", sql).end()
    depth = 1
    for end in range(start, len(sql)):
        depth += (sql[end] == "(") - (sql[end] == ")")
        if depth == 0:
            return " ".join(sql[start:end].split())
    raise AssertionError("unclosed CTE")


def builder(workspace=False):
    return UserListQueryBuilder(
        organization_id=PROJECT,
        **({"project_ids": [PROJECT]} if workspace else {"project_id": PROJECT}),
        filters=[
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [START.isoformat(), END.isoformat()],
                },
            }
        ],
        candidate_end_user_ids=[USER],
        candidate_scan_end_user_ids=[USER],
        candidate_end_user_id_map={USER: USER},
        limit=25,
        offset=0,
    )


def assert_window_replay(sql, params, latest_cte="latest_candidate_spans"):
    latest = cte(sql, latest_cte)
    scan = latest.split("PREWHERE", 1)[1].split("GROUP BY", 1)[0]
    # Exact mutable timestamp predicates belong after version collapse. The
    # partition and complete replacement identity still bound the replay.
    assert "start_time >=" not in scan
    assert "start_time <" not in scan
    assert "latest_is_deleted = 0" not in scan
    assert "end_user_id IN" not in scan
    assert "toStartOfHour(start_time)" in scan
    assert "candidate_span_identities" in scan
    assert "argMax(start_time, _version) AS latest_start_time" in latest
    assert "latest_start_time >= fromUnixTimestamp64Micro(" in sql
    assert "latest_start_time < fromUnixTimestamp64Micro(" in sql
    assert set(re.findall(r"%\((\w+)\)s", sql)) <= params.keys()


@pytest.mark.parametrize("workspace", [True, False])
def test_candidate_usage_replays_every_version_before_window(workspace):
    sql, params = builder(workspace).build_candidate_page_query()
    assert_window_replay(sql, params)
    usage = cte(sql, "exact_usage")
    assert "latest_is_deleted = 0" in usage
    assert "latest_start_time >=" in usage
    assert params["user_window_end_us"] - params["user_window_start_us"] == 1


@pytest.mark.parametrize("workspace", [True, False])
@pytest.mark.parametrize(
    "metric",
    [
        "num_sessions",
        "avg_session_duration",
        "avg_trace_latency",
        "num_llm_calls",
        "num_guardrails_triggered",
        "num_active_days",
        "num_traces_with_errors",
    ],
)
def test_every_requested_metric_applies_latest_window(workspace, metric):
    queries = builder(workspace).build_requested_page_metric_queries([USER], {metric})
    assert len(queries) == 1
    sql, params, fields = queries[0]
    assert fields == (metric,)
    assert_window_replay(sql, params)
    assert params["user_window_end_us"] - params["user_window_start_us"] == 1
    if metric in {"num_sessions", "avg_session_duration"}:
        resolved = cte(sql, "resolved_candidate_spans")
        assert "latest_start_time >=" in resolved
        assert "latest_start_time <" in resolved
        assert "trace_session_id_remap FINAL" in sql


@pytest.mark.parametrize("workspace", [True, False])
def test_typed_attribute_membership_preserves_microsecond_window(workspace):
    sql, params = _users_attr_enrichment_query(
        **({"project_ids": [PROJECT]} if workspace else {"project_id": PROJECT}),
        attribute_keys=["company_id", "metadata", "score"],
        start_date=START,
        end_date=END,
        candidate_end_user_id_map={USER: USER},
    )
    params.update(eu_ids=[USER], eu_scan_ids=[USER])
    assert_window_replay(sql, params, "latest_candidate_attribute_values")
    assert params["attr_end_us"] - params["attr_start_us"] == 1
    assert "groupUniqArray" in sql
    assert "GROUP BY end_user_id, attribute_key" in sql


def test_unwindowed_attribute_read_does_not_invent_window():
    sql, params = _users_attr_enrichment_query(
        project_id=PROJECT, attribute_keys=["key"]
    )
    assert "attr_start_us" not in params
    assert "latest_start_time >=" not in sql
