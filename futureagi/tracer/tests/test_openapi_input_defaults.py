import pytest
from rest_framework import serializers

from tfc.tests.schema_default_helpers import (
    assert_blank_choice_default,
    assert_encoded_default,
    field_schema,
)
from tracer.serializers.annotation import GetTraceAnnotationSerializer
from tracer.serializers.dashboard import DashboardMetricsCatalogQuerySerializer
from tracer.serializers.project import ProjectUsersAggregateGraphDataRequestSerializer
from tracer.serializers.trace import TraceListQuerySerializer, UsersQuerySerializer


@pytest.mark.parametrize("query", [True, False], ids=["get", "post"])
@pytest.mark.parametrize("field_name", ["category", "role", "source"])
def test_catalog_blank_choices_match_runtime(field_name, query):
    assert_blank_choice_default(
        DashboardMetricsCatalogQuerySerializer, field_name, query=query
    )


@pytest.mark.parametrize(
    ("serializer_class", "field_name", "wire_default", "query"),
    [
        (DashboardMetricsCatalogQuerySerializer, "project_ids", "", True),
        (GetTraceAnnotationSerializer, "annotators", "[]", True),
        (GetTraceAnnotationSerializer, "exclude_annotators", "[]", True),
        (TraceListQuerySerializer, "trace_ids", "", True),
        (TraceListQuerySerializer, "trace_ids", "", False),
        (UsersQuerySerializer, "attribute_keys", "", True),
        (UsersQuerySerializer, "attribute_keys", "", False),
    ],
)
def test_encoded_defaults_match_omission(
    serializer_class, field_name, wire_default, query
):
    assert_encoded_default(serializer_class, field_name, wire_default, query=query)


def test_aggregate_graph_omission_is_not_an_explicit_empty_config():
    # Characterize the remaining incompatible default; neither dropping required
    # keys nor replacing its runtime value is a documentation-only correction.
    field = ProjectUsersAggregateGraphDataRequestSerializer().fields["req_data_config"]
    assert field.run_validation(serializers.empty) == {}
    with pytest.raises(serializers.ValidationError, match="id is required"):
        field.run_validation({})
    schema = field_schema(field, query=False)
    assert schema["default"] == {}
    assert schema["required"] == ["id", "type"]
