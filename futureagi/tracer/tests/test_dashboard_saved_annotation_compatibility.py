"""Replay base-era annotation widgets without relaxing widget writes."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tracer.serializers.dashboard import DashboardQuerySerializer
from tracer.services.clickhouse.query_builders.dashboard import DashboardQueryBuilder
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)
from tracer.tests.test_dashboard_annotation_grouping import config_for
from tracer.views.dashboard import DashboardReadQuerySerializer, DashboardWidgetViewSet


def saved_payload(kind):
    config = config_for("text" if kind == "text" else "numeric", grouped=False)
    for key in ("organization_id", "workspace_id"):
        config.pop(key)
    config["metrics"][0]["aggregation"] = "avg"
    if kind != "text":
        config["breakdowns"] = [{"name": "model", "type": "system_metric"}]
    if kind == "mixed":
        config["metrics"].append(
            {"name": "latency", "type": "system_metric", "aggregation": "avg"}
        )
    return config


@pytest.mark.parametrize("kind", ["text", "numeric", "mixed"])
def test_saved_annotation_read_replays_without_changing_write_contract(kind):
    payload = saved_payload(kind)
    before = deepcopy(payload)
    assert not DashboardQuerySerializer(data=payload).is_valid()
    reader = DashboardReadQuerySerializer(data=payload)
    assert reader.is_valid(), reader.errors
    config = reader.validated_data
    builder = DashboardQueryBuilderV2(config)
    builder._latest_state_spans_required = True
    annotation_sql, _ = builder.build_metric_query(config["metrics"][0])
    assert "AS breakdown_value" not in annotation_sql
    if kind == "text":
        assert "count() AS value" in annotation_sql
    if kind == "mixed":
        system_sql, _ = builder.build_metric_query(config["metrics"][1])
        assert "model AS breakdown_value" in system_sql
        assert "avg(latency_ms) AS value" in system_sql.replace("s.", "")
    assert payload == before


@pytest.mark.parametrize("kind", ["text", "numeric", "mixed"])
@pytest.mark.parametrize("validated", [False, True])
def test_saved_widget_execution_reaches_scope_authorization(kind, validated):
    class ScopeReached(Exception):
        pass

    payload = saved_payload(kind)
    if validated:
        reader = DashboardReadQuerySerializer(data=payload)
        assert reader.is_valid(), reader.errors
        payload = reader.validated_data
    before = deepcopy(payload)
    with patch(
        "tracer.views.dashboard._materialize_dashboard_query_scope",
        side_effect=ScopeReached,
    ) as scope:
        with pytest.raises(ScopeReached):
            DashboardWidgetViewSet()._execute_ch_query_config(
                payload, SimpleNamespace(id="workspace")
            )
    assert scope.call_count == 1
    assert payload == before


@pytest.mark.parametrize(
    "builder_class", [DashboardQueryBuilder, DashboardQueryBuilderV2]
)
def test_saved_text_with_missing_type_keeps_base_count_after_label_lookup(
    builder_class,
):
    config = config_for("text", grouped=False)
    config["legacy_annotation_compatibility"] = True
    metric = config["metrics"][0]
    metric.pop("output_type")
    metric["aggregation"] = "avg"
    before = deepcopy(config)
    with patch(
        "model_hub.models.develop_annotations.AnnotationsLabels.objects"
    ) as labels:
        labels.filter.return_value.values_list.return_value.first.return_value = "text"
        builder = builder_class(config)
        sql, _ = builder.build_metric_query(metric)
    assert "count() AS value" in sql
    assert builder.metric_info(metric)["aggregation"] == "count"
    assert config == before


def test_internal_compatibility_marker_cannot_be_submitted_by_a_client():
    payload = saved_payload("text")
    payload["legacy_annotation_compatibility"] = True
    for serializer_class in (DashboardQuerySerializer, DashboardReadQuerySerializer):
        serializer = serializer_class(data=payload)
        assert not serializer.is_valid()
        assert "legacy_annotation_compatibility" in serializer.errors
