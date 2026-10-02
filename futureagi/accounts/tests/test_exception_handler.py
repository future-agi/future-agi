import pytest
from pydantic import BaseModel, ValidationError as PydanticValidationError
from rest_framework import serializers, status
from rest_framework.exceptions import AuthenticationFailed, ErrorDetail, PermissionDenied

from accounts.authentication import custom_exception_handler


def test_drf_validation_errors_use_management_api_envelope():
    response = custom_exception_handler(
        serializers.ValidationError({"name": ["This field is required."]}),
        context={},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["status"] is False
    assert response.data["type"] == "validation_error"
    assert response.data["code"] == "invalid"
    assert response.data["detail"] == "name: This field is required."
    assert response.data["result"] == "name: This field is required."
    assert response.data["message"] == response.data["result"]
    assert response.data["attr"] == "name"
    assert response.data["details"] == {"name": ["This field is required."]}


def test_drf_list_validation_errors_use_indexed_details():
    response = custom_exception_handler(
        serializers.ValidationError(
            [{"name": [ErrorDetail("This field is required.", code="required")]}]
        ),
        context={},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["status"] is False
    assert response.data["type"] == "validation_error"
    assert response.data["code"] == "required"
    assert response.data["detail"] == "name: This field is required."
    assert response.data["result"] == "name: This field is required."
    assert response.data["message"] == response.data["result"]
    assert response.data["attr"] == "0.name"
    assert response.data["details"] == {"0.name": ["This field is required."]}


@pytest.mark.parametrize(
    ("exc", "expected_status", "expected_code", "expected_message"),
    [
        (
            AuthenticationFailed("Invalid token"),
            status.HTTP_401_UNAUTHORIZED,
            "authentication_failed",
            "Invalid token",
        ),
        (
            PermissionDenied("Access denied to this workspace"),
            status.HTTP_403_FORBIDDEN,
            "permission_denied",
            "Access denied to this workspace",
        ),
    ],
)
def test_drf_auth_errors_use_management_api_envelope(
    exc, expected_status, expected_code, expected_message
):
    response = custom_exception_handler(exc, context={})

    assert response.status_code == expected_status
    assert response.data["status"] is False
    assert response.data["code"] == expected_code
    assert response.data["detail"] == expected_message
    assert response.data["result"] == expected_message
    assert response.data["message"] == expected_message
    assert response.data["details"] == {"detail": [expected_message]}


def test_pydantic_validation_errors_use_management_api_envelope():
    class Payload(BaseModel):
        count: int

    with pytest.raises(PydanticValidationError) as exc_info:
        Payload(count="not-a-number")

    response = custom_exception_handler(exc_info.value, context={})

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.data["status"] is False
    assert response.data["type"] == "validation_error"
    assert response.data["code"] == "int_parsing"
    assert response.data["detail"].startswith("count:")
    assert response.data["result"].startswith("count:")
    assert response.data["message"] == response.data["result"]
    assert response.data["attr"] == "count"
    assert "count" in response.data["details"]
