import json
from pathlib import Path

import pytest
from rest_framework import status as http_status


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _swagger():
    with (_repo_root() / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def _operation(path, method):
    return _swagger()["paths"][path][method.lower()]


def _response_ref(operation, status_code="200"):
    return operation["responses"][status_code]["schema"]["$ref"].rsplit("/", 1)[-1]


def test_prompt_label_error_responses_use_typed_contracts():
    assert (
        _response_ref(_operation("/model-hub/prompt-labels/", "GET"), "400")
        == "ModelHubTextErrorResponse"
    )
    assert (
        _response_ref(
            _operation("/model-hub/prompt-labels/create-system-labels/", "POST"),
            "500",
        )
        == "ModelHubTextErrorResponse"
    )
    assert (
        _response_ref(
            _operation(
                "/model-hub/prompt-labels/{template_id}/{label_id}/assign-label-by-id/",
                "POST",
            ),
            "404",
        )
        == "ModelHubTextErrorResponse"
    )


@pytest.mark.django_db
def test_create_system_prompt_labels_endpoint_is_idempotent(auth_client):
    response = auth_client.post("/model-hub/prompt-labels/create-system-labels/")

    assert response.status_code == http_status.HTTP_200_OK
    result = response.json()["result"]
    assert set(result) == {"created", "count"}


@pytest.mark.django_db
def test_prompt_label_validation_errors_use_general_envelope(auth_client):
    response = auth_client.get("/model-hub/prompt-labels/get-by-name/")

    assert response.status_code == http_status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert data["status"] is False
    assert data["result"] == "'name' is required"
