from unittest.mock import Mock, patch

import pytest
from structlog.testing import capture_logs

from ee.voice.exceptions import VapiApiError
from ee.voice.services.vapi_service import VapiService


def test_failed_assistant_request_retries_then_reraises():
    service = VapiService(api_key="test-key")
    failure = RuntimeError("test-provider-exception-detail")
    with (
        patch("requests.get", side_effect=failure) as request,
        patch("ee.voice.services.voice_engine.time.sleep") as sleep,
        capture_logs() as events,
        pytest.raises(RuntimeError) as raised,
    ):
        service.get_assistant("test-id")

    assert raised.value is failure
    assert sleep.call_count == request.call_count - 1 > 0
    assert str(failure) not in repr(events)
    levels = [
        event["log_level"]
        for event in events
        if event.get("error_type") == "RuntimeError"
    ]
    assert levels.count("warning") == sleep.call_count
    assert levels.count("error") == 2


def test_rejected_assistant_request_does_not_log_provider_body():
    service = VapiService(api_key="test-key")
    response = Mock(status_code=401, text="test-provider-response-detail")
    with (
        patch("requests.get", return_value=response),
        capture_logs() as events,
        pytest.raises(VapiApiError) as raised,
    ):
        service.get_assistant("test-id")

    assert raised.value.status_code == 401
    assert raised.value.response_body == response.text
    assert response.text not in repr(events)
    assert any(event.get("error_type") == "VapiApiError" for event in events)
