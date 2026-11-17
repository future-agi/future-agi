"""Group-by contracts. Live SQL proof requires the opt-in disposable CH25 fixture."""
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from model_hub.models.ai_model import AIModel
from tracer.models.project import Project
from tracer.serializers.dashboard import DashboardQuerySerializer
from tracer.services.clickhouse.query_builders.dashboard import DashboardQueryBuilder
from tracer.services.clickhouse.v2.query_builders.dashboard import DashboardQueryBuilderV2
from tracer.tests.test_dashboard_narrow_replay_ch25 import ch_client, ch_port  # noqa: F401

PROJECT = "11111111-1111-4111-8111-111111111111"
ORG = "22222222-2222-4222-8222-222222222222"
WORKSPACE = "33333333-3333-4333-8333-333333333333"


def config(key="customer.tier", kind="attribute", limit=2):
    return {
        "project_ids": [PROJECT],
        "time_range": {"custom_start": "2026-09-10T00:00:00Z", "custom_end": "2026-09-11T00:00:00Z"},
        "granularity": "hour",
        "metrics": [{"name": "latency", "type": "system_metric", "aggregation": "avg"}],
        "group_by": {"type": kind, "key": key, "limit": limit},
    }


def test_group_by_accepts_exact_opaque_key_and_normalizes_breakdown():
    key = " customer'] OR 1=1 -- "
    serializer = DashboardQuerySerializer(data=config(key))
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["group_by"]["key"] == key
    assert serializer.validated_data["breakdowns"][0]["name"] == key


@pytest.mark.parametrize("group", [
    {"type": "attribute", "key": "x", "limit": 0},
    {"type": "attribute", "key": "x", "limit": 11},
    {"type": "attribute", "key": "", "limit": 2},
    {"type": "table", "key": "x", "limit": 2},
    {"type": "tag", "key": "not-tags", "limit": 2},
])
def test_group_by_rejects_invalid_contract(group):
    payload = config()
    payload["group_by"] = group
    serializer = DashboardQuerySerializer(data=payload)
    assert not serializer.is_valid()
    assert "group_by" in serializer.errors


@pytest.mark.parametrize("builder_class", [DashboardQueryBuilder, DashboardQueryBuilderV2])
@pytest.mark.parametrize("kind,key", [("attribute", "customer.tier"), ("attribute", "key'] OR 1=1 --"), ("tag", "tag")])
@pytest.mark.parametrize("latest", [False, True])
def test_group_by_compiles_bound_scoped_top_values(builder_class, kind, key, latest):
    serializer = DashboardQuerySerializer(data=config(key, kind))
    assert serializer.is_valid(), serializer.errors
    payload = {**serializer.validated_data, "organization_id": ORG, "workspace_id": WORKSPACE}
    before = deepcopy(payload)
    builder = builder_class(payload)
    builder._latest_state_spans_required = latest
    sql, params = builder.build_metric_query(payload["metrics"][0])
    assert "dashboard_group_rows" in sql
    assert "LIMIT %(group_by_limit)s" in sql
    assert params["group_by_limit"] == 2
    assert "GROUP BY time_bucket, breakdown_value" in sql
    assert "avg(group_metric_value)" in sql
    assert "sum(value)" not in sql  # Other must not sum averages/percentiles.
    assert "project_id IN %(project_ids)s" in sql
    assert list(params["project_ids"]) == [PROJECT]
    assert "start_time >= %(start_date)s" in sql
    assert "start_time < %(end_date)s" in sql
    if kind == "attribute":
        assert key not in sql
        assert key in params.values()
    else:
        assert "arrayDistinct" in sql
    assert payload == before


@pytest.mark.parametrize("time_range,granularity", [
    ({"custom_start": "2020-01-01T00:00:00Z", "custom_end": "2026-01-01T00:00:00Z"}, "day"),
    ({"custom_start": "2026-09-11T00:00:00Z", "custom_end": "2026-09-10T00:00:00Z"}, "day"),
    ({"preset": "12M"}, "minute"),
])
def test_group_by_bounds_window_before_catalog_or_bucket_allocation(time_range, granularity):
    payload = config()
    payload.update(time_range=time_range, granularity=granularity)
    serializer = DashboardQuerySerializer(data=payload)
    assert not serializer.is_valid()
    assert "group_by" in serializer.errors


@pytest.mark.parametrize("kind", ["attribute", "tag"])
def test_group_by_latest_custom_metric_uses_raw_other_aggregation(kind):
    payload = config("customer.tier" if kind == "attribute" else "tag", kind)
    payload["metrics"] = [{"name": "score", "attribute_key": "score", "attribute_type": "number", "type": "custom_attribute", "aggregation": "p95"}]
    serializer = DashboardQuerySerializer(data=payload)
    assert serializer.is_valid(), serializer.errors
    payload = {**serializer.validated_data, "organization_id": ORG, "workspace_id": WORKSPACE}
    builder = DashboardQueryBuilderV2(payload)
    builder._latest_state_spans_required = True
    sql, params = builder.build_metric_query(payload["metrics"][0])
    assert "quantileExact(0.95)(group_metric_value)" in sql
    assert "attrs_number[%(custom_metric_attr_key)s] AS group_metric_value" in sql
    assert params["custom_metric_attr_key"] == "score"
    assert "'customer.tier'" not in sql


def test_group_by_formats_all_top_values_and_other_without_recapping(settings):
    settings.DASHBOARD_BREAKDOWN_MAX_SERIES = 1
    payload = config()
    payload["organization_id"] = ORG
    payload["workspace_id"] = WORKSPACE
    builder = DashboardQueryBuilder(payload)
    ts = datetime(2026, 9, 10, tzinfo=UTC)
    rows = [
        {"time_bucket": ts, "breakdown_value": "value:gold", "value": 10},
        {"time_bucket": ts, "breakdown_value": "value:other", "value": 20},
        {"time_bucket": ts, "breakdown_value": "other", "value": 25},
    ]
    metric = builder.format_results([(payload["metrics"][0], rows)])["metrics"][0]
    assert [s["name"] for s in metric["series"]] == ["gold", "other (value)", "other"]
    assert [s["data"][0]["value"] for s in metric["series"]] == [10, 20, 25]
    assert metric["series_truncated"] is False


@pytest.mark.parametrize("builder_class", [DashboardQueryBuilder, DashboardQueryBuilderV2])
def test_group_by_preserves_period_aggregate_query_and_row_bound(builder_class):
    serializer = DashboardQuerySerializer(data=config())
    assert serializer.is_valid(), serializer.errors
    payload = {**serializer.validated_data, "organization_id": ORG, "workspace_id": WORKSPACE}
    builder = builder_class(payload)
    bucket_only_bound = builder._group_by_row_bound()
    builder.include_period_aggregate = True
    sql, params = builder.build_metric_query(payload["metrics"][0])
    assert "GROUPING(time_bucket) AS is_period_aggregate" in sql
    assert "GROUP BY GROUPING SETS ((time_bucket, breakdown_value), (breakdown_value))" in sql
    assert "avg(group_metric_value)" in sql
    assert params["group_by_max_rows"] == bucket_only_bound + 3


def test_group_by_preserves_period_values_when_relabeling_series(settings):
    settings.DASHBOARD_BREAKDOWN_MAX_SERIES = 1
    payload = config()
    builder = DashboardQueryBuilder(payload)
    builder.include_period_aggregate = True
    ts = datetime(2026, 9, 10, tzinfo=UTC)
    rows = []
    for name, value in [("value:gold", 11), ("value:other", 21), ("other", 26)]:
        rows.extend([
            {"time_bucket": ts, "breakdown_value": name, "value": value - 1, "is_period_aggregate": 0},
            {"time_bucket": ts, "breakdown_value": name, "value": value, "is_period_aggregate": 1},
        ])
    metric = builder.format_results([(payload["metrics"][0], rows)])["metrics"][0]
    assert [s["name"] for s in metric["series"]] == ["gold", "other (value)", "other"]
    assert [s["aggregate_value"] for s in metric["series"]] == [11, 21, 26]
    assert [s["data"][0]["value"] for s in metric["series"]] == [10, 20, 25]
    assert metric["series_truncated"] is False


@pytest.mark.django_db
def test_group_by_catalog_binding_is_exact_and_scoped(workspace, organization):
    from tracer.services.dashboard_group_by import bind_dashboard_group_by

    project = Project.objects.create(name="Grouped", organization=organization, workspace=workspace, model_type=AIModel.ModelTypes.GENERATIVE_LLM, trace_type="observe")
    payload = config("key'] OR 1=1 --")
    payload.update(project_ids=[str(project.id)], organization_id=str(organization.id), workspace_id=str(workspace.id))
    executor = Mock()
    executor.execute.return_value = SimpleNamespace(data=[{"attribute_types": ["number"]}])
    result = bind_dashboard_group_by(payload, executor=executor)
    assert result["breakdowns"][0]["attribute_type"] == "number"
    sql, params = executor.execute.call_args.args
    assert payload["group_by"]["key"] not in sql
    assert params["group_by_key"] == payload["group_by"]["key"]
    assert params["organization_id"] == str(organization.id)
    assert params["workspace_id"] == str(workspace.id)
    assert params["project_ids"] == (str(project.id),)
    assert "LIMIT 1" in sql
    executor.execute.return_value = SimpleNamespace(data=[{"attribute_types": []}])
    with pytest.raises(ValueError, match="catalog"):
        bind_dashboard_group_by(payload, executor=executor)


@pytest.mark.django_db
def test_group_by_cannot_expand_requested_project_scope(workspace, organization):
    from tracer.views.dashboard import DashboardQueryScopeError, _materialize_dashboard_query_scope

    project = Project.objects.create(name="Allowed", organization=organization, workspace=workspace, model_type=AIModel.ModelTypes.GENERATIVE_LLM, trace_type="observe")
    payload = config()
    with pytest.raises(DashboardQueryScopeError):
        _materialize_dashboard_query_scope(payload, workspace, trace_metrics=payload["metrics"], dataset_metrics=[])
    payload["project_ids"] = [str(project.id)]
    scoped = _materialize_dashboard_query_scope(payload, workspace, trace_metrics=payload["metrics"], dataset_metrics=[])
    assert scoped["project_ids"] == [str(project.id)]
    assert scoped["group_by"] == payload["group_by"]


@pytest.mark.django_db
def test_group_by_foreign_workspace_rejected_before_catalog(workspace, organization, user):
    from accounts.models.workspace import Workspace
    from tracer.views.dashboard import DashboardWidgetViewSet

    foreign_workspace = Workspace.objects.create(name="Other workspace", organization=organization, created_by=user)
    foreign_project = Project.objects.create(name="Foreign", organization=organization, workspace=foreign_workspace, model_type=AIModel.ModelTypes.GENERATIVE_LLM, trace_type="observe")
    payload = config()
    payload["project_ids"] = [str(foreign_project.id)]
    with patch("tracer.services.dashboard_group_by.bind_dashboard_group_by") as bind:
        response = DashboardWidgetViewSet()._execute_ch_query_config(payload, workspace)
    assert response.status_code == 400
    bind.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize("aggregation,expected", [("avg", 35.0), ("sum", 140.0), ("count", 4), ("count_distinct", 4), ("max", 50.0), ("p95", 50.0)])
def test_group_by_live_top_n_plus_other_reaggregates_rows(ch_client, aggregation, expected):
    from tracer.services.dashboard_group_by import grouped_span_sql

    # Four gold observations win; silver and bronze form one accurately
    # weighted bucket. The foreign project and out-of-window rows never rank.
    source = """values('project_id String, start_time DateTime, tier String, amount Float64',
      ('allowed', '2026-09-10 00:00:00', 'gold', 1),
      ('allowed', '2026-09-10 00:00:00', 'gold', 2),
      ('allowed', '2026-09-10 00:00:00', 'gold', 3),
      ('allowed', '2026-09-10 00:00:00', 'gold', 4),
      ('allowed', '2026-09-10 00:00:00', 'silver', 20),
      ('allowed', '2026-09-10 00:00:00', 'silver', 30),
      ('allowed', '2026-09-10 00:00:00', 'silver', 40),
      ('allowed', '2026-09-10 00:00:00', 'bronze', 50),
      ('foreign', '2026-09-10 00:00:00', 'foreign', 999),
      ('allowed', '2026-09-09 00:00:00', 'old', 999))"""
    from tracer.services.clickhouse.query_builders.dashboard import AGGREGATIONS
    sql, params = grouped_span_sql(
        source=source, bucket="toStartOfHour(start_time)", dimension="tier",
        metric_value="amount", aggregate=AGGREGATIONS[aggregation].format(col="group_metric_value"),
        predicates=["project_id IN %(project_ids)s", "start_time >= %(start_date)s", "start_time < %(end_date)s"],
        params={"project_ids": ("allowed",), "start_date": datetime(2026, 9, 10), "end_date": datetime(2026, 9, 11)},
        limit=1, max_rows=48,
    )
    rows = ch_client.execute(sql, params)
    by_name = {row[1]: row[2] for row in rows}
    assert set(by_name) == {"value:gold", "other"}
    assert by_name["other"] == expected


@pytest.mark.integration
def test_group_by_live_tags_deduplicate_membership_and_keep_other_value(ch_client):
    from tracer.services.dashboard_group_by import grouped_span_sql

    source = """values('start_time DateTime, tags String',
      ('2026-09-10 00:00:00', '["gold","gold","other"]'),
      ('2026-09-10 00:00:00', '["gold","bronze"]'),
      ('2026-09-10 00:00:00', '["silver"]'))"""
    sql, params = grouped_span_sql(
        source=source, bucket="toStartOfHour(start_time)",
        dimension="arrayJoin(arrayDistinct(JSONExtract(tags, 'Array(String)')))",
        metric_value="1", aggregate="count()", predicates=["1"],
        params={}, limit=3, max_rows=8,
    )
    rows = ch_client.execute(sql, params)
    assert {row[1]: row[2] for row in rows} == {
        "value:gold": 2, "value:bronze": 1, "value:other": 1, "other": 1,
    }
