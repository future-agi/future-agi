import pytest

from simulate.serializers.requests.run_test import RunTestFilterSerializer
from simulate.views.run_results_v3 import (
    RunCallsV3QuerySerializer,
    RunExportV3RequestSerializer,
)
from tfc.tests.schema_default_helpers import (
    assert_blank_choice_default,
    assert_encoded_default,
    field_schema,
)


@pytest.mark.parametrize(
    ("serializer_class", "field_name"),
    [
        (RunTestFilterSerializer, "simulation_type"),
        (RunCallsV3QuerySerializer, "group_by"),
    ],
)
def test_simulation_blank_choices_match_runtime(serializer_class, field_name):
    assert_blank_choice_default(serializer_class, field_name)


def test_call_filters_document_encoded_empty_default():
    assert_encoded_default(RunCallsV3QuerySerializer, "filters", "{}")


def test_export_body_keeps_object_default():
    field = RunExportV3RequestSerializer().fields["filters"]
    schema = field_schema(field, query=False)
    assert schema["type"] == "object"
    assert schema["default"] == {}
    assert field.run_validation({}) == {}
