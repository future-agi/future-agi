import json
from pathlib import Path

import pytest
from rest_framework import status

from tracer.models.monitor import UserAlertMonitor


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _swagger():
    with (_repo_root() / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def _operation(path, method):
    return _swagger()["paths"][path][method.lower()]


def _body_ref(operation):
    body = next(
        parameter
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "body"
    )
    return body["schema"]["$ref"].rsplit("/", 1)[-1]


def _response_ref(operation, status_code="200"):
    schema = operation["responses"][status_code]["schema"]
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    return schema["type"]


def test_monitor_duplicate_has_explicit_contract():
    operation = _operation("/tracer/user-alerts/duplicate/", "POST")

    assert _body_ref(operation) == "UserAlertMonitorDuplicate"
    assert _response_ref(operation) == "UserAlertMonitorDuplicateResponse"


def test_monitor_metric_options_has_explicit_contract():
    operation = _operation("/tracer/user-alerts/metric-options/", "GET")

    assert _response_ref(operation) == "UserAlertMonitorMetricOptionsResponse"


# --- The threshold rule a monitor is saved with --------------------------------
#
# threshold_rule says how a monitor's thresholds are judged: "earlier" for one
# saved before the threshold update and not edited since, "current" otherwise.
# The server sets it. The list and the detail return it so the page can say so.

FORM_PERCENTAGES = {"critical_threshold_value": 400.0, "warning_threshold_value": 300.0}
MONITOR_URL = "/tracer/user-alerts/{}/"
ALERTS_API_URL = "/tracer/alerts/metric/{}/"


def _result(response):
    body = response.json()
    return body.get("result", body)


def _rule(monitor_id) -> str:
    return UserAlertMonitor._base_manager.get(pk=monitor_id).threshold_rule


@pytest.mark.django_db
class TestMonitorThresholdRule:
    def test_create_saves_the_current_rule_whatever_the_body_says(
        self, auth_client, observe_project
    ):
        response = auth_client.post(
            "/tracer/user-alerts/",
            {
                "project": str(observe_project.id),
                "name": "Errors up",
                "metric_type": "count_of_errors",
                "threshold_operator": "greater_than",
                "threshold_type": "percentage_change",
                **FORM_PERCENTAGES,
                "alert_frequency": 60,
                "threshold_rule": "earlier",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _rule(UserAlertMonitor.objects.get(name="Errors up").pk) == "current"

    def test_alerts_api_create_saves_the_current_rule(
        self, monkeypatch, auth_client, observe_project
    ):
        monkeypatch.setattr(
            "tracer.views.alerts.check_ee_can_create", lambda *args, **kwargs: None
        )
        response = auth_client.post(
            "/tracer/alerts/",
            {
                "kind": "metric",
                "name": "Errors up",
                "project": str(observe_project.id),
                "metric_type": "count_of_errors",
                "threshold_type": "percentage_change",
                "threshold_operator": "greater_than",
                **FORM_PERCENTAGES,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        result = _result(response)
        assert result["threshold_rule"] == "current"
        assert _rule(result["id"]) == "current"

    @pytest.mark.parametrize("url", [MONITOR_URL, ALERTS_API_URL])
    def test_a_rename_keeps_the_earlier_rule(
        self, auth_client, production_alert_monitor, url
    ):
        monitor = production_alert_monitor(**FORM_PERCENTAGES)

        response = auth_client.patch(
            url.format(monitor.id), {"name": "Renamed"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _result(response)["threshold_rule"] == "earlier"
        assert _rule(monitor.id) == "earlier"

    @pytest.mark.parametrize(
        "body",
        [
            {"notification_emails": ["oncall@example.com"]},
            {"slack_notes": "Page the on-call"},
            {"filters": {"observation_type": ["llm"]}},
            {"is_mute": True},
            # Values sent unchanged are no edit.
            {"critical_threshold_value": 400, "warning_threshold_value": 300},
        ],
        ids=["emails", "slack-notes", "filters", "mute", "unchanged-thresholds"],
    )
    def test_saves_that_change_no_threshold_keep_the_earlier_rule(
        self, auth_client, production_alert_monitor, body
    ):
        monitor = production_alert_monitor(**FORM_PERCENTAGES)

        response = auth_client.patch(
            MONITOR_URL.format(monitor.id), body, format="json"
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _rule(monitor.id) == "earlier"

    def test_the_edit_forms_full_save_with_a_new_name_keeps_the_earlier_rule(
        self, auth_client, production_alert_monitor
    ):
        # The edit form sends every field, the ones it did not change included.
        monitor = production_alert_monitor(**FORM_PERCENTAGES)
        body = {
            "project": str(monitor.project_id),
            "name": "Renamed",
            "metric_type": monitor.metric_type,
            "metric": "",
            "threshold_metric_value": "",
            "threshold_type": monitor.threshold_type,
            "threshold_operator": monitor.threshold_operator,
            "critical_threshold_value": monitor.critical_threshold_value,
            "warning_threshold_value": monitor.warning_threshold_value,
            "alert_frequency": monitor.alert_frequency,
            "auto_threshold_time_window": monitor.auto_threshold_time_window,
            # The form refuses a save with no channel; the email list is its saved value.
            "notification_emails": ["alerts@example.com"],
            "slack_webhook_url": None,
            "slack_notes": "",
            "filters": {},
            "is_mute": False,
        }

        response = auth_client.put(MONITOR_URL.format(monitor.id), body, format="json")

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _rule(monitor.id) == "earlier"

    @pytest.mark.parametrize("url", [MONITOR_URL, ALERTS_API_URL])
    @pytest.mark.parametrize(
        "body",
        [
            {"critical_threshold_value": 500},
            {"warning_threshold_value": 200},
            {
                "threshold_operator": "less_than",
                "critical_threshold_value": 50,
                "warning_threshold_value": 80,
            },
            {"alert_frequency": 15},
            {"auto_threshold_time_window": 60 * 24},
            {
                "threshold_type": "static",
                "critical_threshold_value": 10,
                "warning_threshold_value": 5,
            },
            {"metric_type": "token_usage"},
        ],
        ids=[
            "critical",
            "warning",
            "direction",
            "interval",
            "history",
            "threshold-type",
            "metric",
        ],
    )
    def test_a_threshold_change_moves_the_monitor_to_the_current_rule(
        self, auth_client, production_alert_monitor, url, body
    ):
        monitor = production_alert_monitor(**FORM_PERCENTAGES)

        response = auth_client.patch(url.format(monitor.id), body, format="json")

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _result(response)["threshold_rule"] == "current"
        assert _rule(monitor.id) == "current"

    def test_list_detail_and_alerts_api_return_the_rule(
        self,
        auth_client,
        observe_project,
        production_alert_monitor,
        user_alert_monitor,
    ):
        earlier = production_alert_monitor(**FORM_PERCENTAGES)
        expected = {str(earlier.id): "earlier", str(user_alert_monitor.id): "current"}
        project = {"project_id": str(observe_project.id)}

        table = _result(auth_client.get("/tracer/user-alerts/list_monitors/", project))
        root = _result(auth_client.get("/tracer/user-alerts/", project))
        detail = _result(auth_client.get(f"/tracer/user-alerts/{earlier.id}/details/"))
        alerts = _result(auth_client.get("/tracer/alerts/?kind=metric"))["alerts"]

        assert {row["id"]: row["threshold_rule"] for row in table["table"]} == expected
        # The list's "Uses the rule it was saved with" line needs the threshold type too.
        assert {row["id"]: row["threshold_type"] for row in table["table"]} == {
            str(earlier.id): earlier.threshold_type,
            str(user_alert_monitor.id): user_alert_monitor.threshold_type,
        }
        assert {row["id"]: row["threshold_rule"] for row in root["results"]} == expected
        assert detail["threshold_rule"] == "earlier"
        assert {row["id"]: row["threshold_rule"] for row in alerts} == expected

    def test_duplicate_copies_the_rule(self, auth_client, production_alert_monitor):
        earlier = production_alert_monitor(**FORM_PERCENTAGES)

        response = auth_client.post(
            "/tracer/user-alerts/duplicate/",
            {"id": str(earlier.id), "name": "Errors up (copy)"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _rule(_result(response)["id"]) == "earlier"

    @pytest.mark.parametrize("is_mute", [True, False])
    def test_bulk_mute_keeps_the_rule(
        self, auth_client, production_alert_monitor, is_mute
    ):
        earlier = production_alert_monitor(**FORM_PERCENTAGES)

        response = auth_client.post(
            "/tracer/user-alerts/bulk-mute/",
            {"ids": [str(earlier.id)], "is_mute": is_mute},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK, response.json()
        assert _rule(earlier.id) == "earlier"
