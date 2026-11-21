"""
Tests for GetHuggingFaceDatasetConfigView (get_huggingface_dataset_info).

A dataset the caller asked for that Hugging Face reports as missing, private
or not supported is a client-side problem: the caller gets a 400 with a
readable message and the backend does not log it as an error issue.
"""

from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from tfc.utils.error_codes import get_error_message

URL = "/model-hub/develops/get-huggingface-dataset-config/"


def _hf_response(status_code, body):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body
    return response


def _post_with_hf_reply(auth_client, dataset_path, hf_reply):
    with (
        patch(
            "model_hub.views.utils.hugginface.requests.get", return_value=hf_reply
        ),
        patch("model_hub.views.datasets.create.huggingface.logger") as view_logger,
        patch("model_hub.views.utils.hugginface.logger") as helper_logger,
    ):
        response = auth_client.post(
            URL, {"dataset_path": dataset_path}, format="json"
        )
    return response, view_logger, helper_logger


def _assert_not_logged_as_error(*loggers):
    for mock_logger in loggers:
        assert mock_logger.exception.call_count == 0, (
            "client-side Hugging Face failure was logged with logger.exception"
        )
        assert mock_logger.error.call_count == 0, (
            "client-side Hugging Face failure was logged at error level"
        )
        assert mock_logger.critical.call_count == 0


@pytest.mark.django_db
class TestHuggingFaceDatasetConfigClientErrors:
    def test_missing_dataset_returns_400_without_error_log(self, auth_client):
        hf_reply = _hf_response(
            404,
            {
                "error": "The dataset does not exist, or is not accessible "
                "without authentication (private or gated). Please check the "
                "spelling of the dataset name or retry with authentication."
            },
        )

        response, view_logger, helper_logger = _post_with_hf_reply(
            auth_client, "example-org/no-such-dataset", hf_reply
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        body = response.json()
        assert "dataset_info" not in str(body)
        assert any(
            isinstance(value, str) and value.strip()
            for value in _flatten(body)
        ), "400 response carries no readable message"
        _assert_not_logged_as_error(view_logger, helper_logger)

    def test_dataset_with_arbitrary_code_keeps_its_message_without_error_log(
        self, auth_client
    ):
        hf_reply = _hf_response(
            501,
            {"error": "The dataset viewer doesn't support this dataset."},
        )

        response, view_logger, helper_logger = _post_with_hf_reply(
            auth_client, "example-org/script-dataset", hf_reply
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert get_error_message("CONTAINS_ARBITRARY_CODE") in str(response.json())
        _assert_not_logged_as_error(view_logger, helper_logger)


def _flatten(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _flatten(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten(item)
    else:
        yield value
