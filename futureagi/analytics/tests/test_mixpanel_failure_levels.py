"""Analytics disconnects are best effort; auth, protocol and code faults are not."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import pytest
import requests
import structlog
from mixpanel import MixpanelException
from structlog.testing import capture_logs

from analytics import mixpanel_util


@pytest.fixture
def tracker(monkeypatch):
    monkeypatch.setenv("MIX_PANEL_TOKEN", "synthetic-token")
    monkeypatch.setattr(
        mixpanel_util, "logger", structlog.get_logger(mixpanel_util.__name__)
    )
    return mixpanel_util.MixpanelTracker()


@pytest.mark.parametrize("path", ["groups", "engage", "track"])
@pytest.mark.parametrize("error_type", [requests.ConnectionError, requests.Timeout])
def test_sdk_transport_failure_is_warning_without_retry(tracker, path, error_type):
    calls = []

    def post(url, **kwargs):
        calls.append(url)
        assert kwargs["timeout"] == 5
        if url.endswith(path):
            raise error_type("synthetic disconnect")
        response = Mock()
        response.json.return_value = {"status": 1}
        return response

    with (
        patch.object(tracker.mp._consumer._session, "post", side_effect=post),
        patch.object(mixpanel_util, "_delivery_failures", create=True) as metric,
        capture_logs() as records,
    ):
        tracker.track_event(
            "test_event",
            {"org_id": ["org"], "org_name": "Synthetic", "$user_id": "user"},
        )
    assert sum(url.endswith(path) for url in calls) == 1
    assert [r["log_level"] for r in records] == ["warning"]
    assert records[0]["event"] == "mixpanel_delivery_unavailable"
    assert records[0]["outcome"] == "handled"
    metric.add.assert_called_once_with(1, {"operation": "track_event"})


@pytest.mark.parametrize(
    "error",
    [
        ValueError("bad input"),
        requests.exceptions.SSLError("certificate"),
        requests.exceptions.ProxyError("proxy auth"),
        MixpanelException("invalid token"),
        # The SDK reading a response without its "status" field is a contract
        # fault, not a server error; a Mixpanel 5xx surfaces as RetryError.
        KeyError("status"),
    ],
)
def test_unexpected_sdk_fault_remains_error(tracker, error):
    with (
        patch.object(tracker.mp._consumer._session, "post", side_effect=error),
        capture_logs() as records,
    ):
        tracker.track_event("test_event", {"$user_id": "user"})
    assert [r["log_level"] for r in records] == ["error"]
    assert records[0]["event"] == "mixpanel_track_event_failed"


@pytest.mark.parametrize("operation", ["set_details", "update_org_details"])
def test_other_tracker_entry_points_classify_transport_only(tracker, operation):
    with capture_logs() as records:
        if operation == "set_details":
            with patch.object(
                tracker, "_set_details", side_effect=requests.Timeout("timeout")
            ):
                tracker.set_details(Mock())
        else:
            with patch.object(
                tracker.mp, "group_set", side_effect=requests.ConnectionError("offline")
            ):
                tracker.update_org_details("org", "Synthetic", "test")
    assert [r["log_level"] for r in records] == ["warning"]


class _UnavailableMixpanel(BaseHTTPRequestHandler):
    """A stand-in Mixpanel API that answers every request with HTTP 503."""

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = b'{"error": "synthetic unavailable"}'
        self.send_response(503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def unavailable_mixpanel():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _UnavailableMixpanel)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_mixpanel_server_error_is_warning_without_retry(tracker, unavailable_mixpanel):
    consumer = tracker.mp._consumer
    # Production reaches https://api.mixpanel.com through the SDK's own retry
    # adapter, which turns an exhausted 5xx into RetryError. Mount that same
    # adapter for the plain-http stand-in so the test exercises the real path.
    session = consumer._session
    session.mount("http://", session.get_adapter("https://api.mixpanel.com"))
    consumer._endpoints = {
        endpoint: f"{unavailable_mixpanel}/{endpoint}"
        for endpoint in consumer._endpoints
    }
    with (
        patch.object(mixpanel_util, "_delivery_failures", create=True) as metric,
        capture_logs() as records,
    ):
        tracker.track_event("test_event", {"$user_id": "user"})
        tracker.update_org_details("org", "Synthetic", "free")
    assert [r["log_level"] for r in records] == ["warning", "warning"]
    assert {r["event"] for r in records} == {"mixpanel_delivery_unavailable"}
    assert {r["reason"] for r in records} == {"server_unavailable"}
    assert {r["error_type"] for r in records} == {"RetryError"}
    assert [c.args for c in metric.add.call_args_list] == [
        (1, {"operation": "track_event"}),
        (1, {"operation": "update_org_details"}),
    ]
