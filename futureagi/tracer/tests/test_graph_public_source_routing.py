"""Public-wire graph routing and ownership regressions, entirely offline.

Only transport/cache/metadata are mocked. Public dispatch, graph compilers,
trace candidate classification, and v2 SQL rewriting remain real. These are
SQL contracts, not ClickHouse execution or latency qualification.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.db import DatabaseError

from tracer.serializers.project import (
    ProjectGraphDataQuerySerializer,
    ProjectUsersAggregateGraphDataRequestSerializer,
)
from tracer.services.clickhouse import exact_graph_reads as exact
from tracer.services.clickhouse import graph_dispatch as dispatch
from tracer.services.clickhouse.query_builders.base import BaseQueryBuilder
from tracer.services.clickhouse.query_builders.exact_graph_predicates import (
    compile_exact_graph_row_predicates,
)
from tracer.services.clickhouse.v2.query_builders.trace_list import (
    TraceListQueryBuilderV2,
)
from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)

pytestmark = pytest.mark.unit
PROJECT = "11111111-1111-4111-8111-111111111111"
CONFIG = "22222222-2222-4222-8222-222222222222"
USER = "33333333-3333-4333-8333-333333333333"
END = datetime(2026, 9, 4)
RAW_NAMES = (
    "has_eval",
    "has_annotation",
    "annotator",
    "my_annotations",
    "user",
    "user_id",
    "user_id_type",
)
COLUMNS = [
    "time_bucket",
    "avg_latency",
    "total_tokens",
    "avg_cost",
    "traffic_count",
    "prompt_tokens",
    "completion_tokens",
    "error_rate",
]


@pytest.fixture(autouse=True, scope="session")
def _drop_legacy_ch_spans_mvs():
    yield


@pytest.fixture(autouse=True, scope="session")
def _ensure_test_score_tenant_column():
    yield


@pytest.fixture(autouse=True)
def offline(monkeypatch, settings):
    import socket

    from django.db.backends.base.base import BaseDatabaseWrapper

    def forbidden(*args, **kwargs):
        pytest.fail("offline graph regression attempted external access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(BaseDatabaseWrapper, "ensure_connection", forbidden)
    monkeypatch.setattr(exact, "get_annotation_labels_for_project", forbidden)
    monkeypatch.setattr(exact.CustomEvalConfig.objects, "filter", forbidden)
    monkeypatch.setattr(
        exact.CustomEvalConfig.no_workspace_objects, "filter", forbidden
    )
    monkeypatch.setattr(
        dispatch, "read_or_schedule_exact_snapshot", lambda *a, **k: None
    )
    settings.DASHBOARD_TRACE_REPLICA_SHARD_CLUSTER = ""


def leaf(key, value, *, kind="text", op="equals", family="SPAN_ATTRIBUTE"):
    return {
        "column_id": key,
        "filter_config": {
            "col_type": family,
            "filter_type": kind,
            "filter_op": op,
            "filter_value": value,
        },
    }


def window(days=7):
    return leaf(
        "created_at",
        [(END - timedelta(days=days)).isoformat(), END.isoformat()],
        kind="datetime",
        op="between",
        family="SYSTEM_METRIC",
    )


def public(filters, *, users=False):
    cls = (
        ProjectUsersAggregateGraphDataRequestSerializer
        if users
        else ProjectGraphDataQuerySerializer
    )
    serializer = cls(data={"project_id": PROJECT, "filters": filters})
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data["filters"]


class RecordingAnalytics:
    supports_per_query_read_settings = True

    def __init__(self):
        self.calls = []

    def execute_ch_query(self, query, params, **kwargs):
        self.calls.append((query, dict(params), kwargs))
        return SimpleNamespace(data=[], columns=COLUMNS, query_time_ms=1)


def direct(filters, observe_type):
    analytics = RecordingAnalytics()
    result = dispatch.fetch_system_metric_graph_ch(
        analytics=analytics,
        project_id=PROJECT,
        filters=public(filters),
        interval="day",
        metric_id="traffic",
        observe_type=observe_type,
    )
    assert result["query_complete"] is True
    assert len(analytics.calls) == 1
    return analytics.calls[0]


@pytest.mark.parametrize("key", RAW_NAMES)
@pytest.mark.parametrize(
    "kind,value,column",
    [
        ("text", "raw-value", "attrs_string"),
        ("number", 0.01, "attrs_number"),
        ("boolean", True, "attrs_bool"),
    ],
)
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_public_dispatch_raw_alias_compiles_map_not_native_relation(
    key,
    kind,
    value,
    column,
    observe_type,
):
    query, params, _ = direct([window(), leaf(key, value, kind=kind)], observe_type)
    assert f"{column}['{key}']" in query
    assert "FROM spans" in query
    assert "tracer_eval_logger" not in query
    assert "model_hub_score" not in query
    assert "FROM end_users" not in query
    assert "spans_hourly_rollup" not in query


@pytest.mark.parametrize("key", ["created_at", "start_time"])
@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize(
    "op,value", [("equals", "clock"), ("not_equals", "clock"), ("is_null", None)]
)
@pytest.mark.parametrize("observe_type", ["trace", "span"])
def test_public_dispatch_raw_date_is_not_date_only_rollup(
    key, days, op, value, observe_type
):
    query, params, _ = direct([window(days), leaf(key, value, op=op)], observe_type)
    assert "spans_hourly_rollup" not in query
    assert f"mapContains(attrs_string, '{key}')" in query
    if op != "is_null":
        assert f"attrs_string['{key}']" in query
    assert params["start_date"] == END - timedelta(days=days)
    assert params["end_date"] == END


@pytest.mark.parametrize("key", ["created_at", "start_time"])
@pytest.mark.parametrize(
    "op,value", [("equals", "clock"), ("not_equals", "clock"), ("is_null", None)]
)
@pytest.mark.parametrize("extra_leaf", [False, True])
def test_exact_trace_reader_preserves_raw_date_in_real_identity_classifier(
    monkeypatch,
    key,
    op,
    value,
    extra_leaf,
):
    raw = leaf(key, value, op=op)
    filters = public(
        [window(), raw] + ([leaf("company_id", "company")] if extra_leaf else [])
    )
    captured = []

    def enumerate_ids(**kwargs):
        # Exercise the real classifier at the enumeration boundary, without
        # pretending that an offline fake can execute its ClickHouse SQL.
        frozen = kwargs["filters"]
        assert raw in frozen
        assert BaseQueryBuilder.parse_time_range(frozen) == (
            END - timedelta(days=7),
            END,
        )
        builder = TraceListQueryBuilderV2(
            project_id=PROJECT,
            filters=frozen,
            bounded_internal_scan=True,
            bounded_identity_only=True,
            bounded_bulk_scan=True,
            bounded_include_filter_witnesses=False,
            bounded_global_span_witnesses=True,
        )
        query, params = builder.build_filter_identity_match_query_from_seed_rows(
            [{"trace_id": "trace-1", "start_time": END - timedelta(hours=1)}]
        )
        assert "attrs_string" in query and key in params.values()
        assert "latest_is_deleted = 0" in query
        assert "FROM spans FINAL" not in query
        captured.append((query, params))
        return ["trace-1"], 1, 1

    monkeypatch.setattr(exact, "_enumerate_exact_trace_ids", enumerate_ids)
    analytics = RecordingAnalytics()
    result = exact.read_exact_system_graph(
        analytics=analytics,
        project_id=PROJECT,
        filters=filters,
        interval="day",
        metric_id="traffic",
        observe_type="trace",
    )
    assert captured and result["query_complete"] is True
    query, params, _ = analytics.calls[0]
    assert params["graph_candidate_trace_ids"] == ("trace-1",)
    assert (
        "attrs_string" not in query
    )  # membership is classified, not reapplied to contributions


@pytest.mark.parametrize("key", RAW_NAMES)
def test_agent_graph_uses_raw_alias_map(monkeypatch, key):
    analytics = RecordingAnalytics()
    result = exact.read_exact_agent_graph(
        analytics=analytics,
        project_id=PROJECT,
        filters=public([window(), leaf(key, "raw-value")]),
    )
    assert result["query_complete"] is True
    query, params, _ = analytics.calls[0]
    assert f"attrs_string['{key}']" in query
    assert "model_hub_score" not in query and "tracer_eval_logger" not in query
    assert "FROM end_users" not in query


@pytest.mark.parametrize("family", ["SYSTEM_METRIC", "NORMAL"])
@pytest.mark.parametrize("key", ["has_eval", "has_annotation", "user_id"])
def test_native_and_legacy_relations_are_not_demoted(monkeypatch, family, key):
    monkeypatch.setattr(
        exact.CustomEvalConfig.objects,
        "filter",
        lambda **kwargs: SimpleNamespace(values_list=lambda *a, **k: [CONFIG]),
    )
    config = leaf(
        key,
        "native-user" if key == "user_id" else True,
        kind="text" if key == "user_id" else "boolean",
        family=family,
    )
    plan = compile_exact_graph_row_predicates(
        public([window(), config]),
        project_id=PROJECT,
        observe_type="span",
        annotation_label_ids=[CONFIG],
    )
    predicate = " ".join(plan.predicates)
    assert {
        "has_eval": "tracer_eval_logger",
        "has_annotation": "model_hub_score",
        "user_id": "end_users",
    }[key] in predicate
    assert "attrs_string" not in predicate and "attrs_bool" not in predicate


def test_native_datetime_complement_stays_contribution_constraint():
    complement = leaf(
        "start_time",
        "2026-09-01T12:00:00Z",
        kind="datetime",
        op="not_equals",
        family="SYSTEM_METRIC",
    )
    filters = public([window(), complement, leaf("start_time", "clock")])
    plan = compile_exact_graph_row_predicates(
        filters, project_id=PROJECT, observe_type="trace"
    )
    assert len(plan.predicates) == len(plan.contribution_predicates) == 1
    frozen = exact._frozen_trace_membership_filters(
        filters, start_date=END - timedelta(days=7), end_date=END
    )
    assert complement in frozen and filters[-1] in frozen


def eval_filters(key="eval_score", value=80):
    return public(
        [
            window(),
            leaf(
                key,
                value,
                kind="number",
                op="greater_than_or_equal",
                family="SYSTEM_METRIC",
            ),
        ],
        users=True,
    )


@pytest.mark.parametrize("table", ["tracer_eval_logger", "tracer_eval_logger_v2"])
@pytest.mark.parametrize(
    "key", ["eval_score", "bool_eval_pass_rate", "avg_output_float"]
)
def test_user_graph_eval_ownership_matches_users_list_contract(
    monkeypatch, settings, table, key
):
    settings.CH25_EVAL_LOGGER_TABLE = table
    owned = Mock(return_value=SimpleNamespace(values_list=lambda *a, **k: [CONFIG]))
    monkeypatch.setattr(exact.CustomEvalConfig.no_workspace_objects, "filter", owned)
    analytics = RecordingAnalytics()
    exact.read_exact_user_system_graph(
        analytics=analytics,
        project_id=PROJECT,
        filters=eval_filters(key),
        interval="day",
        metric_id="active_users",
    )
    owned.assert_called_once_with(project_id=PROJECT, deleted=False)
    query, params, _ = analytics.calls[0]
    assert f"FROM {table} AS eval_scan FINAL" in query
    assert "eval_scan.custom_eval_config_id IN %(user_eval_config_ids)s" in query
    assert params["user_eval_config_ids"] == (CONFIG,)
    assert (
        "eval_scan.created_at" not in query
    )  # retain full trace-linked eval semantics
    live = (
        "eval_scan.is_deleted = 0"
        if table.endswith("_v2")
        else "eval_scan._peerdb_is_deleted = 0"
    )
    assert live in query
    list_sql, list_params = UserListQueryBuilderV2(
        organization_id=USER, project_id=PROJECT
    ).build_eval_query(
        [USER],
        allowed_eval_config_ids=[CONFIG],
    )
    assert "eval_scan.custom_eval_config_id IN %(allowed_eval_config_ids)s" in list_sql
    assert list_params["allowed_eval_config_ids"] == params["user_eval_config_ids"]


def test_user_eval_empty_owned_config_set_cannot_read_other_project(monkeypatch):
    monkeypatch.setattr(
        exact.CustomEvalConfig.no_workspace_objects,
        "filter",
        lambda **kwargs: SimpleNamespace(values_list=lambda *a, **k: []),
    )
    sql, params, _ = exact._user_aggregate_source_sql(
        project_id=PROJECT,
        filters=eval_filters(value=0),
        start_date=END - timedelta(days=7),
        end_date=END,
        include_trace_ids=False,
        all_snapshot_users=True,
        started=exact.monotonic(),
    )
    eval_cte = sql.split("user_eval_metrics AS (", 1)[1].split(
        "GROUP BY ut.end_user_id", 1
    )[0]
    assert "AND 0 = 1" in eval_cte
    assert "coalesce(ue.bool_eval_pass_rate, 0)" in sql


def test_user_eval_ownership_outage_fails_before_any_graph_query(monkeypatch):
    monkeypatch.setattr(
        exact.CustomEvalConfig.no_workspace_objects,
        "filter",
        Mock(side_effect=DatabaseError("private database detail")),
    )
    analytics = RecordingAnalytics()
    with pytest.raises(exact.ExactGraphReadError, match="Evaluation metadata") as error:
        exact.read_exact_user_system_graph(
            analytics=analytics,
            project_id=PROJECT,
            filters=eval_filters(),
            interval="day",
            metric_id="active_users",
        )
    assert "private" not in str(error.value)
    assert analytics.calls == []


def test_raw_eval_score_does_not_resolve_native_eval_ownership():
    analytics = RecordingAnalytics()
    exact.read_exact_user_system_graph(
        analytics=analytics,
        project_id=PROJECT,
        filters=public([window(), leaf("eval_score", 80, kind="number")], users=True),
        interval="day",
        metric_id="active_users",
    )
    query, params, _ = analytics.calls[0]
    assert "attrs_number" in query
    assert "'eval_score'" in query or "eval_score" in params.values()
    assert "user_eval_metrics AS" not in query
