from unittest.mock import MagicMock

from django.test import RequestFactory

from tfc.logging.sentry import _scrub_oss_telemetry_event
from tfc.telemetry.middleware import OTelContextMiddleware


def test_otel_does_not_capture_oss_request_body():
    request = RequestFactory().post(
        "/oss/register",
        data='{"users":[{"email":"secret@example.com"}]}',
        content_type="application/json",
    )
    span = MagicMock()

    OTelContextMiddleware(lambda request: None)._capture_request(span, request)

    captured_attributes = [call.args[0] for call in span.set_attribute.call_args_list]
    assert "http.request.body" not in captured_attributes


def test_sentry_scrubs_oss_request_data_and_frame_variables():
    event = {
        "request": {
            "url": "https://api.futureagi.com/oss/register",
            "data": {"users": [{"email": "secret@example.com"}]},
        },
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [{"vars": {"payload": "secret@example.com"}}]
                    }
                }
            ]
        },
    }

    scrubbed = _scrub_oss_telemetry_event(event)

    assert "data" not in scrubbed["request"]
    assert "vars" not in scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]
