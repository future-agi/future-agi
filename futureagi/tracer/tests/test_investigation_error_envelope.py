"""Internal worker errors retain their codes within the common API envelope."""

import pytest

from tracer.serializers.trace_investigation import InvestigationControlErrorSerializer
from tracer.services.trace_investigation import (
    InvestigationConflict,
    InvestigationControlError,
    InvestigationNotFound,
)
from tracer.views.trace_investigation import _error_response


@pytest.mark.parametrize(
    ("error_class", "status_code", "code"),
    [
        (InvestigationControlError, 400, "invalid_request"),
        (InvestigationNotFound, 404, "not_found"),
        (InvestigationConflict, 409, "conflict"),
    ],
)
def test_investigation_error_uses_common_envelope(error_class, status_code, code):
    response = _error_response(error_class("Investigation cannot be published"))

    assert response.status_code == status_code
    assert response.data["status"] is False
    assert response.data["type"]
    assert response.data["code"] == code
    assert response.data["detail"] == "Investigation cannot be published"
    assert response.data["message"] == response.data["detail"]
    serializer = InvestigationControlErrorSerializer(data=response.data)
    assert serializer.is_valid(), serializer.errors
