"""Run-setting help text says they are on by default and what turning them off does."""

import pytest

from model_hub.serializers.experiment_run_scope import (
    ExperimentLimitsSerializer,
    ExperimentSampleSerializer,
)
from model_hub.serializers.experiments import ExperimentCreateV2Serializer

REQUEST_REJECTION_HELP_TEXT = (
    "Experiment run settings are on by default. When a server turns them off, "
    "a create request that sends this field is refused (HTTP 400)."
)
METADATA_HELP_TEXT = (
    "Experiment run settings are on by default. When a server turns them off, "
    "only {} is accepted for this field, as a no-op; any other value is "
    "refused (HTTP 400)."
)
ERROR_POLICY_HELP_TEXT = (
    "How a row whose model call failed is scored. Omitted, the row gets no "
    'score and stays out of averages; "zero" scores it 0; "exclude" marks it '
    "left out of scores. Experiment run settings are on by default. When a "
    "server turns them off, a create request that sends this field is "
    "refused (HTTP 400)."
)
SHARED_SCOPE_HELP_TEXT = (
    "Experiment run settings are on by default. When a server turns them off, "
    "a create request that sends this field is refused (HTTP 400) and "
    "experiment detail responses leave it out."
)
# Token, message and cost limits are refused in this release; the API says so.
LIMITS_HELP_TEXT = (
    "Per-row limits. This release accepts only timeout_s, which is checked "
    "before and after each model call, not during one; tokens, messages and "
    "cost_usd are refused (HTTP 400). Experiment run settings are on by "
    "default. When a server turns them off, a create request that sends "
    "this field is refused (HTTP 400) and experiment detail responses leave "
    "it out."
)


@pytest.mark.parametrize(
    ("field_name", "expected_help_text"),
    (
        ("row_ids", REQUEST_REJECTION_HELP_TEXT),
        ("sample", SHARED_SCOPE_HELP_TEXT),
        ("metadata", METADATA_HELP_TEXT),
        ("error_policy", ERROR_POLICY_HELP_TEXT),
        ("limits", LIMITS_HELP_TEXT),
    ),
)
def test_scope_request_help_text_documents_enablement(
    settings, field_name, expected_help_text
):
    settings.EXPERIMENT_SCOPE_ENABLED = True

    field = ExperimentCreateV2Serializer().fields[field_name]

    assert field.help_text == expected_help_text


@pytest.mark.parametrize(
    ("serializer_class", "expected"),
    (
        (ExperimentSampleSerializer, SHARED_SCOPE_HELP_TEXT),
        (ExperimentLimitsSerializer, LIMITS_HELP_TEXT),
    ),
)
def test_scope_shared_definition_description_documents_enablement(
    serializer_class, expected
):
    assert serializer_class.Meta.swagger_schema_fields["description"] == expected
