"""Analytics disconnects are best effort; auth, protocol and code faults are not."""

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
    monkeypatch.setattr(mixpanel_util, "logger", structlog.get_logger(mixpanel_util.__name__))
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
        patch.object(mixpanel_util, "_delivery_failures") as metric,
        capture_logs() as records,
    ):
        tracker.track_event("test_event", {"org_id": ["org"], "org_name": "Synthetic", "$user_id": "user"})
    assert sum(url.endswith(path) for url in calls) == 1
    assert [r["log_level"] for r in records] == ["warning"]
    assert records[0]["event"] == "mixpanel_delivery_unavailable"
    assert records[0]["outcome"] == "handled"
    metric.add.assert_called_once_with(1, {"operation": "track_event"})


@pytest.mark.parametrize("error", [ValueError("bad input"), requests.exceptions.SSLError("certificate"),
                                   requests.exceptions.ProxyError("proxy auth"), MixpanelException("invalid token")])
def test_unexpected_sdk_fault_remains_error(tracker, error):
    with patch.object(tracker.mp._consumer._session, "post", side_effect=error), capture_logs() as records:
        tracker.track_event("test_event", {"$user_id": "user"})
    assert [r["log_level"] for r in records] == ["error"]
    assert records[0]["event"] == "mixpanel_track_event_failed"


@pytest.mark.parametrize("operation", ["set_details", "update_org_details"])
def test_other_tracker_entry_points_classify_transport_only(tracker, operation):
    with capture_logs() as records:
        if operation == "set_details":
            with patch.object(tracker, "_set_details", side_effect=requests.Timeout("timeout")):
                tracker.set_details(Mock())
        else:
            with patch.object(tracker.mp, "group_set", side_effect=requests.ConnectionError("offline")):
                tracker.update_org_details("org", "Synthetic", "test")
    assert [r["log_level"] for r in records] == ["warning"]
