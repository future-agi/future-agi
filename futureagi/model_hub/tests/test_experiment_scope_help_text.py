"""F05 request help text documents the independent enablement gate."""

import pytest

from model_hub.serializers.experiment_run_scope import (
    ExperimentLimitsSerializer,
    ExperimentSampleSerializer,
)
from model_hub.serializers.experiments import ExperimentCreateV2Serializer

REQUEST_REJECTION_HELP_TEXT = (
    "Experiment scoping is disabled by default. When disabled on the server, "
    "this field is rejected as an unknown field (HTTP 400) in create requests."
)
METADATA_HELP_TEXT = (
    "Experiment scoping is disabled by default. When disabled on the server, "
    "only {} is accepted for this field as a no-op; any other value is rejected "
    "as an unknown field (HTTP 400) in create requests."
)
SHARED_SCOPE_HELP_TEXT = (
    "Experiment scoping is disabled by default. When disabled on the server, "
    "this field is rejected as an unknown field (HTTP 400) in create requests "
    "and omitted from experiment detail responses."
)


@pytest.mark.parametrize(
    ("field_name", "expected_help_text"),
    (
        ("row_ids", REQUEST_REJECTION_HELP_TEXT),
        ("sample", SHARED_SCOPE_HELP_TEXT),
        ("metadata", METADATA_HELP_TEXT),
        ("error_policy", REQUEST_REJECTION_HELP_TEXT),
        ("limits", SHARED_SCOPE_HELP_TEXT),
    ),
)
def test_scope_request_help_text_documents_enablement(
    settings, field_name, expected_help_text
):
    settings.EXPERIMENT_SCOPE_ENABLED = True

    field = ExperimentCreateV2Serializer().fields[field_name]

    assert field.help_text == expected_help_text


@pytest.mark.parametrize(
    "serializer_class", (ExperimentSampleSerializer, ExperimentLimitsSerializer)
)
def test_scope_shared_definition_description_documents_enablement(serializer_class):
    assert (
        serializer_class.Meta.swagger_schema_fields["description"]
        == SHARED_SCOPE_HELP_TEXT
    )
