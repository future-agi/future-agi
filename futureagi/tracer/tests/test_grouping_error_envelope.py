"""Grouping and severity errors share the management API envelope."""

from unittest.mock import Mock, patch
from uuid import uuid4

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


def test_grouping_conflict_logs_reason_and_attempt_without_request_secrets():
    attempt_id = uuid4()
    reason = "attach target is stale or not offered"
    operation = Mock(side_effect=GroupingConflict(reason))
    with patch("tracer.views.trace_grouping.logger") as logger:
        response = _respond(
            operation, attempt_id=attempt_id, lease_token="private-token"
        )

    assert response.status_code == 409
    (event,) = logger.warning.call_args.args
    fields = logger.warning.call_args.kwargs
    assert event == "grouping_control_conflict"
    assert fields["reason"] == reason
    assert fields["attempt_id"] == str(attempt_id)
    assert fields["failure_code"] == "grouping_conflict"
    assert fields["duration_ms"] >= 0
    assert "private-token" not in str(fields)


def test_non_conflict_error_does_not_emit_conflict_event():
    operation = Mock(side_effect=GroupingNotFound("grouping attempt was not found"))
    with patch("tracer.views.trace_grouping.logger") as logger:
        response = _respond(operation)

    assert response.status_code == 404
    logger.warning.assert_not_called()
