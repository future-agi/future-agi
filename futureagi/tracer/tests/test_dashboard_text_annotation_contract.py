"""Text annotation operations must mean the same thing on every dashboard path."""

from copy import deepcopy

import pytest
from rest_framework.exceptions import ValidationError

from tracer.constants.dashboard import DASHBOARD_AGGREGATIONS
from tracer.serializers.dashboard import (
    DashboardQuerySerializer,
    DashboardWidgetSerializer,
)
from tracer.services.clickhouse.query_builders.dashboard import (
    DashboardQueryBuilder,
    InvalidMetricCombinationError,
)
from tracer.services.clickhouse.query_builders.dataset_dashboard import (
    DatasetQueryBuilder,
)
from tracer.services.clickhouse.v2.property_catalog.source_adapters import (
    _annotation_definition,
)
from tracer.services.clickhouse.v2.query_builders.dashboard import (
    DashboardQueryBuilderV2,
)
from tracer.services.dashboard_metrics_catalog import _annotation_label_metric_entry
from tracer.views.dashboard import DashboardReadQuerySerializer

pytestmark = pytest.mark.unit
LABEL = "44444444-4444-4444-4444-444444444444"
COUNT_OPERATIONS = ("count", "count_distinct")


def query(aggregation, source="both"):
    return {
        "project_ids": ["11111111-1111-4111-8111-111111111111"],
        "organization_id": "22222222-2222-4222-8222-222222222222",
        "workspace_id": "33333333-3333-4333-8333-333333333333",
        "time_range": {"preset": "7D"},
        "metrics": [
            {
                "name": LABEL,
                "label_id": LABEL,
                "type": "annotation_metric",
                "source": source,
                "output_type": "text",
                "aggregation": aggregation,
            }
        ],
    }


def test_text_definition_advertises_only_real_operations():
    definition = _annotation_definition(
        {"id": LABEL, "name": "Comment", "type": "text"}
    )
    assert definition.details["allowed_aggregations"] == COUNT_OPERATIONS
    assert definition.output_type == "text"
    assert definition.primary_source == "both"


@pytest.mark.parametrize("output_type", ["text", "TEXT"])
def test_legacy_text_definition_advertises_the_same_operations(output_type):
    entry = _annotation_label_metric_entry(
        {"id": LABEL, "name": "Comment", "type": output_type}
    )
    assert entry["allowed_aggregations"] == list(COUNT_OPERATIONS)


@pytest.mark.parametrize(
    "output_type", ["numeric", "star", "categorical", "thumbs_up_down"]
)
def test_legacy_non_text_definition_keeps_its_operations(output_type):
    entry = _annotation_label_metric_entry(
        {"id": LABEL, "name": "Score", "type": output_type}
    )
    assert "allowed_aggregations" not in entry


@pytest.mark.parametrize("output_type", ["text", "TEXT"])
@pytest.mark.parametrize(
    "aggregation,expression",
    [("count", "count()"), ("count_distinct", "uniqExact(c.value)")],
)
def test_dataset_text_aggregation_uses_the_requested_operation(
    output_type, aggregation, expression
):
    config = query(aggregation, "datasets")
    config["metrics"][0]["output_type"] = output_type
    before = deepcopy(config)
    sql, _ = DatasetQueryBuilder(config).build_metric_query(config["metrics"][0])
    assert f"{expression} AS value" in sql
    assert config == before


@pytest.mark.parametrize(
    "builder_class", [DashboardQueryBuilder, DashboardQueryBuilderV2]
)
@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize(
    "aggregation,expression",
    [
        ("count", "count()"),
        (
            "count_distinct",
            "uniqExact(JSONExtract(a.value, 'text', 'Nullable(String)'))",
        ),
    ],
)
def test_trace_text_aggregation_is_not_silently_substituted(
    builder_class, grouped, aggregation, expression
):
    config = query(aggregation)
    if grouped:
        config["breakdowns"] = [
            {
                "name": LABEL,
                "label_id": LABEL,
                "type": "annotation_metric",
                "source": "both",
                "output_type": "text",
            }
        ]
    before = deepcopy(config)
    sql, _ = builder_class(config).build_metric_query(config["metrics"][0])
    assert f"{expression} AS value" in sql
    assert config == before


@pytest.mark.parametrize(
    "builder_class",
    [DashboardQueryBuilder, DashboardQueryBuilderV2, DatasetQueryBuilder],
)
@pytest.mark.parametrize(
    "aggregation", [op for op in DASHBOARD_AGGREGATIONS if op not in COUNT_OPERATIONS]
)
def test_text_builder_rejects_unsupported_operation(builder_class, aggregation):
    config = query(
        aggregation, "datasets" if builder_class is DatasetQueryBuilder else "both"
    )
    with pytest.raises(InvalidMetricCombinationError, match="[Tt]ext annotation"):
        builder_class(config).build_metric_query(config["metrics"][0])


@pytest.mark.parametrize(
    "serializer_class", [DashboardQuerySerializer, DashboardReadQuerySerializer]
)
@pytest.mark.parametrize("source", ["traces", "both", "datasets"])
@pytest.mark.parametrize("aggregation", DASHBOARD_AGGREGATIONS)
def test_text_read_and_write_validation_agree(serializer_class, source, aggregation):
    config = query(aggregation, source)
    # Tenant authority is injected by the endpoint, not caller-supplied.
    payload = {
        key: value
        for key, value in config.items()
        if key not in {"organization_id", "workspace_id"}
    }
    before = deepcopy(payload)
    serializer = serializer_class(data=payload)
    assert serializer.is_valid() is (aggregation in COUNT_OPERATIONS), serializer.errors
    if aggregation in COUNT_OPERATIONS:
        assert serializer.validated_data["metrics"][0]["aggregation"] == aggregation
        assert (
            DashboardWidgetSerializer().validate_query_config(payload)["metrics"][0][
                "aggregation"
            ]
            == aggregation
        )
    else:
        assert "aggregation" in serializer.errors["metrics"][0]
        with pytest.raises(ValidationError):
            DashboardWidgetSerializer().validate_query_config(payload)
    assert payload == before
