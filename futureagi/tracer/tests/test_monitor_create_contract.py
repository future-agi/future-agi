"""DB-free checks of the existing producer envelope and declared create contract."""

import json
from pathlib import Path

from rest_framework.exceptions import PermissionDenied, ValidationError

from accounts.authentication import custom_exception_handler
from tfc.ee_gating import FeatureUnavailable
from tfc.utils.general_methods import GeneralMethods
from tracer.serializers.monitor import (
    UserAlertMonitorCreateResponseSerializer,
    UserAlertMonitorEntitlementResponseSerializer,
)
from tracer.views.monitor import UserAlertMonitorView


def monitor_responses():
    gm = GeneralMethods()
    responses = {
        "success": gm.success_response("Resolution monitor alert created successfully"),
        "entitlement": custom_exception_handler(
            FeatureUnavailable(
                "monitors",
                detail="Monitor count exceeds the current plan limit.",
                metadata={"resource": "monitors", "limit": 1, "current_usage": 1},
            ),
            {},
        ),
        "entitlement_unavailable": custom_exception_handler(
            FeatureUnavailable(
                "monitors",
                detail="Feature entitlement service is unavailable.",
                metadata={"resource": "monitors"},
            ),
            {},
        ),
        "forbidden": custom_exception_handler(PermissionDenied(), {}),
        "validation": gm.bad_request(
            {"notification_emails": ["Enter a valid email address."]}
        ),
        "validation_drf": custom_exception_handler(
            ValidationError(
                {"critical_threshold_value": ["A valid number is required."]}
            ),
            {},
        ),
        "server": gm.internal_server_error_response(
            "synthetic private exception; must not display"
        ),
    }
    return {
        key: {"status": response.status_code, "data": response.data}
        for key, response in responses.items()
    }


def test_monitor_success_and_entitlement_use_the_actual_declared_envelopes():
    fixtures = monitor_responses()
    declared = UserAlertMonitorView.create._swagger_auto_schema["responses"]
    assert declared[200] is UserAlertMonitorCreateResponseSerializer
    assert declared[402] is UserAlertMonitorEntitlementResponseSerializer
    assert 201 not in declared
    for name in ("success", "entitlement", "entitlement_unavailable"):
        fixture = fixtures[name]
        serializer = declared[fixture["status"]](data=fixture["data"])
        assert serializer.is_valid(), serializer.errors
    for malformed in (
        {"status": True, "result": {}},
        {"status": False, "result": "not successful"},
        {"result": "missing status"},
    ):
        assert not UserAlertMonitorCreateResponseSerializer(data=malformed).is_valid()


def test_frontend_monitor_fixtures_equal_real_producers():
    root = Path(__file__).resolve().parents[3]
    fixture = (
        root
        / "frontend/src/sections/derived-metrics/__tests__/fixtures/monitorResponses.json"
    )
    assert json.loads(fixture.read_text()) == monitor_responses()
