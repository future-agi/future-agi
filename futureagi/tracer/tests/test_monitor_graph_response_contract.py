"""Typed response contracts for saved and preview monitor graphs."""

import json
from pathlib import Path

import pytest

from tracer.serializers.monitor import UserAlertMonitorGraphResponseSerializer
from tracer.views.monitor import UserAlertMonitorView


def _repo_root():
    return Path(__file__).resolve().parents[3]


def _swagger():
    with (_repo_root() / "api_contracts" / "openapi" / "swagger.json").open() as f:
        return json.load(f)


def test_monitor_graph_response_accepts_static_runtime_envelope():
    serializer = UserAlertMonitorGraphResponseSerializer(
        data={
            "status": True,
            "result": [
                {"timestamp": "2026-08-14T12:00:00+00:00", "value": 3},
                {"timestamp": "2026-08-14T13:00:00+00:00", "value": 1.25},
            ],
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_monitor_graph_response_accepts_percentage_change_runtime_envelope():
    serializer = UserAlertMonitorGraphResponseSerializer(
        data={
            "status": True,
            "result": {
                "graph_data": [
                    {"timestamp": "2026-08-14T12:00:00+00:00", "value": 3.5}
                ],
                "alert_bar_data": [
                    {
                        "start_timestamp": "2026-08-14T12:00:00+00:00",
                        "end_timestamp": "2026-08-14T13:00:00+00:00",
                        "status": "warning",
                    }
                ],
            },
        }
    )

    assert serializer.is_valid(), serializer.errors


@pytest.mark.parametrize("missing_field", ["timestamp", "value"])
def test_monitor_graph_response_rejects_incomplete_static_points(missing_field):
    point = {"timestamp": "2026-08-14T12:00:00+00:00", "value": 3}
    del point[missing_field]
    serializer = UserAlertMonitorGraphResponseSerializer(
        data={"status": True, "result": [point]}
    )

    assert not serializer.is_valid()
    assert missing_field in serializer.errors["result"][0]


def test_monitor_graph_response_rejects_invalid_percentage_status():
    serializer = UserAlertMonitorGraphResponseSerializer(
        data={
            "status": True,
            "result": {
                "graph_data": [],
                "alert_bar_data": [
                    {
                        "start_timestamp": "2026-08-14T12:00:00+00:00",
                        "end_timestamp": "2026-08-14T13:00:00+00:00",
                        "status": "unknown",
                    }
                ],
            },
        }
    )

    assert not serializer.is_valid()
    assert "status" in serializer.errors["result"]["alert_bar_data"][0]


def test_monitor_graph_actions_use_the_exact_response_contract():
    assert (
        UserAlertMonitorView.preview_graph._swagger_auto_schema["post"]["responses"][
            200
        ]
        is UserAlertMonitorGraphResponseSerializer
    )
    assert (
        UserAlertMonitorView.graph_data._swagger_auto_schema["get"]["responses"][200]
        is UserAlertMonitorGraphResponseSerializer
    )


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/tracer/user-alerts/preview-graph/", "post"),
        ("/tracer/user-alerts/{id}/graph/", "get"),
    ],
)
def test_checked_in_swagger_has_exact_monitor_graph_responses(path, method):
    swagger = _swagger()
    operation = swagger["paths"][path][method]

    assert operation["responses"]["200"]["schema"]["$ref"] == (
        "#/definitions/UserAlertMonitorGraphResponse"
    )
    assert operation["x-runtime-response-validation"] is True

    result_schema = swagger["definitions"]["UserAlertMonitorGraphResponse"][
        "properties"
    ]["result"]
    static_schema, percentage_schema = result_schema["x-one-of"]
    assert static_schema["type"] == "array"
    assert static_schema["items"]["required"] == ["timestamp", "value"]
    assert percentage_schema["required"] == ["graph_data", "alert_bar_data"]
    assert percentage_schema["properties"]["alert_bar_data"]["items"]["properties"][
        "status"
    ]["enum"] == ["healthy", "warning", "critical", "insufficient_data"]
