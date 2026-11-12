"""The public contract names credentials the money-write permission accepts."""
import pytest
from django.conf import settings

from agentcc.views.managed_credits import (
    ManagedCreditGrantView, ManagedCreditAdjustView, ManagedCreditRefundView,
)


@pytest.mark.parametrize("view", [ManagedCreditGrantView, ManagedCreditAdjustView, ManagedCreditRefundView])
def test_credit_write_security_is_not_inherited_api_key_auth(view):
    security = view.post._swagger_auto_schema["security"]
    assert security == [{"GatewayAdminBearer": []}, {"StaffBearer": []}]
    for scheme in ("GatewayAdminBearer", "StaffBearer"):
        definition = settings.SWAGGER_SETTINGS["SECURITY_DEFINITIONS"][scheme]
        assert definition["type"] == "apiKey"
        assert definition["in"] == "header"
        assert definition["name"] == "Authorization"
        assert "Bearer" in definition["description"]


def test_platform_security_requires_both_key_headers_or_a_persons_bearer():
    assert settings.SWAGGER_SETTINGS["SECURITY_REQUIREMENTS"] == [
        {"X-Api-Key": [], "X-Secret-Key": []}, {"BearerToken": []},
    ]
    definition = settings.SWAGGER_SETTINGS["SECURITY_DEFINITIONS"]["BearerToken"]
    assert (definition["type"], definition["in"], definition["name"]) == ("apiKey", "header", "Authorization")


@pytest.mark.parametrize("value", ["Credit purchase is unavailable.", {"message": "Already refunded", "refunded_usd": 1.0}])
def test_credit_error_result_preserves_its_runtime_type(value):
    from agentcc.serializers.managed_credits import ManagedCreditErrorResponseSerializer
    field = ManagedCreditErrorResponseSerializer().fields["result"]
    assert field.run_validation(value) == value
    assert field.to_representation(value) == value
    assert field.Meta.swagger_schema_fields["x-string-or-object"] is True


def test_credit_conflict_result_declares_all_runtime_properties():
    from agentcc.serializers.managed_credits import ManagedCreditErrorResponseSerializer

    schema = ManagedCreditErrorResponseSerializer().fields["result"].Meta.swagger_schema_fields
    assert schema["x-string-or-object"] is True
    assert schema["required"] == ["message"]
    assert schema["additionalProperties"] is False
    assert schema["properties"] == {
        "message": {"type": "string"},
        "amount_usd": {"type": "number"},
        "spent_usd": {"type": "number"},
        "refunded_usd": {"type": "number"},
    }


@pytest.mark.parametrize("extra", [
    {"refunded_usd": "1.000000"},
    {"amount_usd": "2.000000", "spent_usd": "1.000000"},
])
def test_credit_conflict_json_renderer_matches_declared_result_types(extra):
    import json
    from decimal import Decimal
    from rest_framework.renderers import JSONRenderer
    from agentcc.serializers.managed_credits import ManagedCreditErrorResponseSerializer
    from agentcc.views.managed_credits import ManagedCreditsApiError

    error = ManagedCreditsApiError(
        409, "already_refunded", "Already refunded",
        **{name: Decimal(value) for name, value in extra.items()},
    )
    response = ManagedCreditRefundView().handle_exception(error)
    payload = json.loads(JSONRenderer().render(response.data))
    properties = ManagedCreditErrorResponseSerializer().fields["result"].Meta.swagger_schema_fields["properties"]
    assert response.status_code == 409
    assert set(payload["result"]) == {"message", *extra}
    for name, value in payload["result"].items():
        assert name in properties
        runtime_type = "string" if isinstance(value, str) else "number" if type(value) in (int, float) else "unsupported"
        assert properties[name]["type"] == runtime_type
    for name, value in extra.items():
        assert payload["result"][name] == float(value)


def test_checkout_amount_has_numeric_schema_and_positive_lower_bound():
    from decimal import Decimal
    from agentcc.serializers.managed_credits import ManagedCreditCheckoutRequestSerializer
    from rest_framework.exceptions import ValidationError
    field = ManagedCreditCheckoutRequestSerializer().fields["amount_usd"]
    assert field.coerce_to_string is False
    assert field.min_value == Decimal("0.01")
    assert field.run_validation("25.00") == Decimal("25.00")
    with pytest.raises(ValidationError):
        field.run_validation("0.00")
