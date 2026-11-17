"""PagerDuty Events API delivery and monitor serializer contracts."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests
from rest_framework.test import APIRequestFactory

from tracer.models.monitor import UserAlertMonitor
from tracer.serializers.monitor import (
    UserAlertMonitorDetailSerializer,
    UserAlertMonitorSerializer,
)
from tracer.utils import monitor as monitor_utils



def _caller_request(user, organization, workspace):
    # Serializers scope related fields to the caller; without a request they grant no rows.
    request = APIRequestFactory().post("/")
    request.user = user
    request.organization = organization
    request.workspace = workspace
    return request

@pytest.fixture
def pagerduty_monitor():
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Test PagerDuty monitor",
        metric_type="count_of_errors",
        metric=None,
        pagerduty_routing_key="test-pagerduty-routing-key",
    )


@pytest.mark.unit
class TestPagerDutyNotification:
    @pytest.mark.parametrize(
        ("alert_type", "severity"),
        [("critical", "critical"), ("warning", "warning"), ("other", "info")],
    )
    def test_posts_trigger_payload(self, pagerduty_monitor, alert_type, severity):
        with patch("requests.post", return_value=Mock(status_code=202)) as post:
            monitor_utils._send_pagerduty_notification(
                pagerduty_monitor, "Threshold breached", alert_type
            )
        post.assert_called_once_with(
            "https://events.pagerduty.com/v2/enqueue",
            json={
                "routing_key": "test-pagerduty-routing-key",
                "event_action": "trigger",
                "dedup_key": f"futureagi-observe:{pagerduty_monitor.id}:{alert_type}",
                "payload": {
                    "summary": "Threshold breached",
                    "severity": severity,
                    "source": "futureagi-observe",
                    "custom_details": {
                        "monitor_id": str(pagerduty_monitor.id),
                        "monitor_name": "Test PagerDuty monitor",
                        "metric": "count_of_errors",
                    },
                },
            },
            timeout=10,
            allow_redirects=False,
        )
        post.return_value.raise_for_status.assert_called_once_with()

    @pytest.mark.parametrize("routing_key", [None, ""])
    def test_skips_without_key(self, pagerduty_monitor, routing_key):
        pagerduty_monitor.pagerduty_routing_key = routing_key
        with patch("requests.post", return_value=Mock(status_code=202)) as post:
            monitor_utils._send_pagerduty_notification(
                pagerduty_monitor, "Threshold breached", "critical"
            )
        post.assert_not_called()

    def test_truncates_summary_and_includes_eval_metric(self, pagerduty_monitor):
        pagerduty_monitor.metric = "test-evaluation-id"
        with patch("requests.post", return_value=Mock(status_code=202)) as post:
            monitor_utils._send_pagerduty_notification(
                pagerduty_monitor, "x" * 1100, "critical"
            )
        payload = post.call_args.kwargs["json"]["payload"]
        assert payload["summary"] == "x" * 1024
        assert payload["custom_details"]["metric"] == "test-evaluation-id"

    def test_dedup_key_is_stable_and_scoped(self, pagerduty_monitor):
        with patch("requests.post", return_value=Mock(status_code=202)) as post:
            for message, alert_type in [
                ("first", "critical"),
                ("second", "critical"),
                ("warning", "warning"),
            ]:
                monitor_utils._send_pagerduty_notification(
                    pagerduty_monitor, message, alert_type
                )
            pagerduty_monitor.id = uuid.uuid4()
            monitor_utils._send_pagerduty_notification(
                pagerduty_monitor, "another monitor", "critical"
            )
        keys = [call.kwargs["json"]["dedup_key"] for call in post.call_args_list]
        assert keys[0] == keys[1]
        assert len({keys[0], keys[2], keys[3]}) == 3

    @pytest.mark.parametrize("failure", ["transport", "http", "redirect"])
    def test_failures_are_logged_without_secrets(self, pagerduty_monitor, failure):
        error = requests.RequestException(pagerduty_monitor.pagerduty_routing_key)
        with (
            patch("requests.post", return_value=Mock(status_code=202)) as post,
            patch.object(monitor_utils, "logger") as logger,
        ):
            if failure == "transport":
                post.side_effect = error
            elif failure == "http":
                post.return_value.raise_for_status.side_effect = error
            else:
                post.return_value.status_code = 302
            monitor_utils._send_pagerduty_notification(
                pagerduty_monitor, "test-private-message", "critical"
            )
        logger.error.assert_called_once_with(
            "monitor_pagerduty_failed", monitor_id=str(pagerduty_monitor.id)
        )
        assert pagerduty_monitor.pagerduty_routing_key not in str(logger.mock_calls)
        assert "test-private-message" not in str(logger.mock_calls)

    def test_trigger_invokes_pagerduty_after_other_channels(self, pagerduty_monitor):
        calls = Mock()
        pagerduty_monitor.pk = pagerduty_monitor.id
        # t34 locks and rechecks the live monitor before dispatching channels.
        # Keep this channel-order unit test independent of the database.
        live = Mock()
        live.values_list.return_value.first.return_value = pagerduty_monitor.pk
        with (
            patch.object(monitor_utils.transaction, "atomic"),
            patch.object(
                monitor_utils.UserAlertMonitor.objects, "select_for_update"
            ) as select_for_update,
            patch.object(monitor_utils.UserAlertMonitorLog.objects, "create"),
            patch.object(monitor_utils, "_send_alert_email", calls.email),
            patch.object(monitor_utils, "_send_slack_notification", calls.slack),
            patch.object(monitor_utils, "_send_webhook_notification", calls.webhook),
            patch.object(
                monitor_utils, "_send_pagerduty_notification", calls.pagerduty,
                create=True,
            ),
        ):
            select_for_update.return_value.filter.return_value = live
            monitor_utils._handle_alert_trigger(
                pagerduty_monitor, "Threshold breached", "critical"
            )
        select_for_update.assert_called_once_with(of=("self",))
        select_for_update.return_value.filter.assert_called_once_with(
            pk=pagerduty_monitor.pk, is_mute=False
        )
        calls.pagerduty.assert_called_once_with(
            pagerduty_monitor, "Threshold breached", "critical"
        )
        assert [call[0] for call in calls.mock_calls] == [
            "email", "slack", "webhook", "pagerduty"
        ]


@pytest.mark.django_db
class TestPagerDutyMonitorSerializer:
    def test_create_read_update_and_clear_routing_key(self, user, organization, workspace, observe_project):
        context = {"request": _caller_request(user, organization, workspace)}
        serializer = UserAlertMonitorSerializer(context=context, data={
            "organization": str(organization.id),
            "project": str(observe_project.id),
            "name": "Test PagerDuty alert",
            "metric_type": "count_of_errors",
            "threshold_operator": "greater_than",
            "threshold_type": "static",
            "critical_threshold_value": 10,
            "notification_emails": [],
            "pagerduty_routing_key": "test-pagerduty-routing-key",
        })
        assert serializer.is_valid(), serializer.errors
        monitor = serializer.save()
        monitor.refresh_from_db()
        assert monitor.pagerduty_routing_key == "test-pagerduty-routing-key"
        # Existing notification credentials are readable in both serializers.
        for serializer_class in (UserAlertMonitorSerializer, UserAlertMonitorDetailSerializer):
            assert serializer_class(monitor).data["pagerduty_routing_key"] == "test-pagerduty-routing-key"
        for key in ("test-replacement-routing-key", "", None):
            update = UserAlertMonitorSerializer(
                monitor, data={"pagerduty_routing_key": key}, partial=True, context=context
            )
            assert update.is_valid(), update.errors
            update.save()
            monitor.refresh_from_db()
            assert monitor.pagerduty_routing_key == key

    def test_existing_monitors_default_to_no_key(self, user_alert_monitor):
        assert user_alert_monitor.pagerduty_routing_key is None
        assert UserAlertMonitorSerializer(user_alert_monitor).data["pagerduty_routing_key"] is None
        field = UserAlertMonitor._meta.get_field("pagerduty_routing_key")
        assert field.null and field.blank
