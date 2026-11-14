"""Response bodies of routes excluded from body capture are not copied onto OTel spans."""

from unittest.mock import MagicMock

import pytest
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.test import RequestFactory

from tfc.telemetry.middleware import (
    SENSITIVE_BODY_PATH_PREFIXES,
    OTelContextMiddleware,
)

# Original routes plus one path under every configured excluded prefix, so a
# prefix added later is covered for responses as well as requests.
EXCLUDED_PATHS = sorted(
    {"/saml2_auth/complete/", "/telemetry/register/"}
    | {prefix + "example/" for prefix in SENSITIVE_BODY_PATH_PREFIXES}
)


def _attributes(span):
    return dict(call.args for call in span.set_attribute.call_args_list)


@pytest.mark.parametrize("path", EXCLUDED_PATHS)
@pytest.mark.parametrize(
    "capture_all,capture_errors", [(False, True), (True, False), (True, True)]
)
@pytest.mark.parametrize("status,kind", [(200, "json"), (400, "json"), (500, "text")])
def test_excluded_route_response_body_not_captured(
    settings, path, capture_all, capture_errors, status, kind
):
    settings.OTEL_CAPTURE_RESPONSE_BODY = capture_all
    settings.OTEL_CAPTURE_RESPONSE_BODY_ON_ERROR = capture_errors
    marker = "SYNTHETIC_PRIVATE_CAPABILITY_RESPONSE"
    response = (
        JsonResponse({"result": marker}, status=status)
        if kind == "json"
        else HttpResponse(marker, status=status, content_type="text/plain")
    )
    request = RequestFactory().get(path)
    request.user = MagicMock(is_authenticated=False)
    span = MagicMock()
    OTelContextMiddleware(lambda req: response)._capture_response(
        span, request, response, 12.5
    )
    attributes = _attributes(span)
    assert "http.response.body" not in attributes
    assert marker not in str(attributes)
    assert attributes["http.status_code"] == status
    assert attributes["http.duration_ms"] == 12.5
    assert "http.response.headers" in attributes


@pytest.mark.parametrize(
    "capture_all,capture_errors,status,expected",
    [
        (False, False, 200, False),
        (False, True, 200, False),
        (True, False, 200, True),
        (True, True, 200, True),
        (False, True, 400, True),
        (True, False, 400, True),
    ],
)
def test_ordinary_response_obeys_capture_flags(
    settings, capture_all, capture_errors, status, expected
):
    settings.OTEL_CAPTURE_RESPONSE_BODY = capture_all
    settings.OTEL_CAPTURE_RESPONSE_BODY_ON_ERROR = capture_errors
    response = JsonResponse({"result": "ordinary"}, status=status)
    request = RequestFactory().get("/health/")
    request.user = MagicMock(is_authenticated=False)
    span = MagicMock()
    OTelContextMiddleware(lambda req: response)._capture_response(
        span, request, response, 1.25
    )
    assert ("http.response.body" in _attributes(span)) is expected


@pytest.mark.parametrize(
    "path",
    ["/saml2_auth/stream/"] + [p + "stream/" for p in SENSITIVE_BODY_PATH_PREFIXES],
)
def test_excluded_route_streaming_response_not_consumed(settings, path):
    settings.OTEL_CAPTURE_RESPONSE_BODY = True
    response = StreamingHttpResponse(iter([b"SYNTHETIC_STREAM_CAPABILITY"]))
    request = RequestFactory().get(path)
    request.user = MagicMock(is_authenticated=False)
    span = MagicMock()
    OTelContextMiddleware(lambda req: response)._capture_response(
        span, request, response, 1
    )
    assert "http.response.body" not in _attributes(span)
    assert list(response.streaming_content) == [b"SYNTHETIC_STREAM_CAPABILITY"]
