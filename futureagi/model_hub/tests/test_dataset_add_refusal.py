"""dataset_add_refusal maps a DATASET_ADD usage entry to the create response."""

from types import SimpleNamespace

import pytest
from rest_framework import status

from model_hub.views.utils.dataset_limit import dataset_add_refusal
from tfc.constants.api_calls import APICallStatusChoices
from tfc.utils.api_serializers import DatasetLimitCheckFailedErrorSerializer
from tfc.utils.error_codes import get_error_message


def _entry(status_value):
    return SimpleNamespace(status=status_value)


def test_unverified_limit_asks_to_retry():
    response = dataset_add_refusal(None)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    # The frontend shows this typed code's message whatever the status.
    assert response.data["code"] == "dataset_limit_check_failed"
    assert response.data["message"] == get_error_message("DATASET_LIMIT_CHECK_FAILED")
    # The body is the 503 the dataset-create endpoints declare.
    declared = DatasetLimitCheckFailedErrorSerializer(data=response.data)
    assert declared.is_valid(), declared.errors


@pytest.mark.parametrize("sdk_source", [False, True])
def test_unverified_limit_refuses_sdk_uploads_too(sdk_source):
    response = dataset_add_refusal(None, sdk_source=sdk_source)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE


def test_reached_limit_answers_429():
    response = dataset_add_refusal(_entry(APICallStatusChoices.RESOURCE_LIMIT.value))

    assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert response.data["message"] == get_error_message("DATASET_CREATE_LIMIT_REACHED")


@pytest.mark.parametrize(
    ("status_value", "sdk_source"),
    [
        (APICallStatusChoices.RESOURCE_LIMIT.value, True),
        (APICallStatusChoices.PROCESSING.value, False),
        (APICallStatusChoices.PROCESSING.value, True),
    ],
)
def test_creation_proceeds(status_value, sdk_source):
    assert dataset_add_refusal(_entry(status_value), sdk_source=sdk_source) is None
