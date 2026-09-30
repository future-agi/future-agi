"""Grouping and severity errors share the management API envelope."""

from unittest.mock import Mock

import pytest

from tracer.serializers.trace_grouping import GroupingErrorSerializer
from tracer.services.grouping.control import (
    GroupingConflict,
    GroupingControlError,
    GroupingNotFound,
)
from tracer.views.trace_grouping import _respond


@pytest.mark.parametrize(
    ("error_class", "status_code"),
    [(GroupingControlError, 400), (GroupingNotFound, 404), (GroupingConflict, 409)],
)
def test_grouping_error_uses_common_envelope(error_class, status_code):
    error = error_class("Grouping cannot be published")
    operation = Mock(side_effect=error)

    response = _respond(operation, worker_id="test-worker")

    operation.assert_called_once_with(worker_id="test-worker")
    assert response.status_code == status_code
    assert response.data["status"] is False
    assert response.data["type"]
    assert response.data["code"] == error.code
    assert response.data["detail"] == str(error)
    serializer = GroupingErrorSerializer(data=response.data)
    assert serializer.is_valid(), serializer.errors
