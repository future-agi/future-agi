"""Pin the emitted managed-credit contract, not just serializer declarations."""

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import include, path
from drf_yasg import openapi
from drf_yasg.app_settings import swagger_settings

from agentcc.auto_reload_terms import AUTO_RELOAD_CONSENT_VERSION
from agentcc.serializers.managed_credits import (
    ManagedCreditAutoReloadRequestSerializer,
    ManagedCreditCheckoutRequestSerializer,
    ManagedCreditContextErrorResponseSerializer,
    ManagedCreditEntriesQuerySerializer,
    ManagedCreditStatementQuerySerializer,
)

PREFIX = "/agentcc/managed-credits/"


@pytest.fixture(scope="module", params=["generated", "published"])
def contract(request):
    if request.param == "published":
        root = Path(__file__).resolve().parents[3]
        return json.loads((root / "api_contracts/openapi/swagger.json").read_text())
    generator = swagger_settings.DEFAULT_GENERATOR_CLASS(
        info=openapi.Info(title="Managed credit contract", default_version="v1"),
        patterns=[path("agentcc/", include("agentcc.urls_billing"))],
    )
    schema = generator.get_schema(request=None, public=True).as_odict()
    # drf-yasg factors the common route prefix into basePath for this small URLConf.
    base = schema.get("basePath", "").rstrip("/")
    schema["paths"] = {base + key: value for key, value in schema["paths"].items()}
    return schema


def definition(contract, ref):
    return contract["definitions"][ref["$ref"].removeprefix("#/definitions/")]


@pytest.mark.parametrize("action", ["grant", "adjust", "refund"])
def test_staff_writes_publish_only_staff_jwt_or_admin_bearer(contract, action):
    # The composed contract names the two Authorization bearers the staff
    # writes accept, the gateway admin credential or a signed-in staff
    # person's token, and never an organization API key.
    operation = contract["paths"][f"{PREFIX}{action}/"]["post"]
    assert operation["security"] == [{"GatewayAdminBearer": []}, {"StaffBearer": []}]
    schemes = contract["securityDefinitions"]
    for name in ("GatewayAdminBearer", "StaffBearer"):
        scheme = schemes[name]
        assert scheme["type"] == "apiKey"
        assert scheme["in"] == "header"
        assert scheme["name"] == "Authorization"
        assert "Bearer" in scheme["description"]
    assert "API keys are not accepted" in schemes["StaffBearer"]["description"]
    assert "Not a personal API key" in schemes["GatewayAdminBearer"]["description"]
    assert not any(
        name in alternative
        for alternative in operation["security"]
        for name in ("X-Api-Key", "X-Secret-Key", "BearerToken")
    )
    assert "ManagedCreditStaffSession" not in schemes
    assert "ManagedCreditStaffBearer" not in schemes


def test_other_operations_keep_the_existing_security(contract):
    # The platform requirement: both key headers together, or a person's bearer.
    assert sorted(sorted(alternative) for alternative in contract["security"]) == [
        ["BearerToken"],
        ["X-Api-Key", "X-Secret-Key"],
    ]
    for suffix, method in [("", "get"), ("checkout/", "post"), ("settings/", "put")]:
        operation = contract["paths"][PREFIX + suffix][method]
        assert operation.get("security", contract["security"]) == contract["security"]


def test_gateway_reference_is_nullable_only_at_the_summary_field(contract):
    gateway = contract["definitions"]["ManagedCreditSummary"]["properties"]["gateway"]
    assert gateway["x-nullable"] is True
    assert gateway["allOf"] == [{"$ref": "#/definitions/GatewayCreditState"}]
    for reason in [
        "unreachable",
        "syncing",
        "managed_models_disabled",
        "shipped default",
    ]:
        assert reason in gateway["description"]
    state = contract["definitions"]["GatewayCreditState"]
    assert not state.get("x-nullable")
    assert state["description"] == "The gateway's hot-path credit account in USD."


@pytest.mark.parametrize(
    "action,status,amount",
    [
        ("grant", "409", "amount_usd"),
        ("adjust", "409", "amount_usd"),
        ("refund", "400", "spent_usd"),
        ("refund", "409", "refunded_usd"),
    ],
)
def test_money_error_responses_publish_their_context_object(
    contract, action, status, amount
):
    responses = contract["paths"][f"{PREFIX}{action}/"]["post"]["responses"]
    envelope = definition(contract, responses[status]["schema"])
    result = envelope["properties"]["result"]
    assert result["type"] == "object"
    assert (
        result["x-string-or-object"] is True
    )  # other refusals at the same HTTP status
    contextual = ManagedCreditContextErrorResponseSerializer(
        data={
            "status": False,
            "result": {"message": "Refused", amount: 1.25},
        }
    )
    assert contextual.is_valid(), contextual.errors
    # 503 keeps the managed-credit error envelope every status of this API
    # publishes: a string refusal, or the same bounded object; never a context
    # object of its own.
    unavailable = definition(contract, responses["503"]["schema"])
    assert unavailable["properties"]["result"]["x-string-or-object"] is True
    assert responses["503"]["schema"] != responses[status]["schema"]
    # The shared AgentCC error envelope is not widened. The billing-only
    # contract need not reference it (every managed-credit refusal uses its own
    # envelope); the published contract must carry it, as a string result.
    from rest_framework import serializers as drf_serializers

    from agentcc.serializers.contracts import AgentccErrorResponseSerializer

    shared = AgentccErrorResponseSerializer().fields["result"]
    assert isinstance(shared, drf_serializers.CharField), type(shared)
    if (
        "AgentccErrorResponse" in contract["definitions"]
        or contract["info"]["title"] != "Managed credit contract"
    ):
        assert (
            contract["definitions"]["AgentccErrorResponse"]["properties"]["result"][
                "type"
            ]
            == "string"
        )


@pytest.mark.parametrize(
    "result,valid",
    [
        ("That reference already belongs to another entry.", True),
        ({"message": "Already refunded", "refunded_usd": 2.5}, True),
        (["not an object"], False),
        (1, False),
        (True, False),
    ],
)
def test_context_error_does_not_accept_arbitrary_json(result, valid):
    serializer = ManagedCreditContextErrorResponseSerializer(
        data={"status": False, "result": result}
    )
    assert serializer.is_valid() is valid, serializer.errors


def test_enabled_settings_publish_explicit_consent_without_a_default(contract):
    shape = contract["definitions"]["ManagedCreditAutoReloadRequest"]
    consent = shape["properties"]["consent_version"]
    assert consent["type"] == "string"
    assert consent["maxLength"] == 64
    assert "default" not in consent
    assert "consent_version" not in shape.get("required", [])  # disable needs none
    for text in [
        AUTO_RELOAD_CONSENT_VERSION,
        "EVERY enabled settings save",
        "auto_reload_consent_required",
        "auto_reload_consent_outdated",
        "charge my saved payment method",
    ]:
        assert text in consent["description"]
    response = contract["paths"][PREFIX + "settings/"]["put"]["responses"]["400"]
    assert "code" in definition(contract, response["schema"])["properties"]


@pytest.mark.parametrize("enabled", [False, True])
def test_consent_is_required_only_when_enabling(enabled):
    serializer = ManagedCreditAutoReloadRequestSerializer(
        data={"enabled": enabled, "threshold_usd": "10", "amount_usd": "25"}
    )
    assert serializer.is_valid() is not enabled
    if enabled:
        error = serializer.errors["consent_version"][0]
        assert error.code == "auto_reload_consent_required"


def test_checkout_amount_has_the_servers_numeric_bounds(contract):
    amount = contract["definitions"]["ManagedCreditCheckoutRequest"]["properties"][
        "amount_usd"
    ]
    assert amount["type"] == "number"
    assert Decimal(str(amount["minimum"])) == Decimal("0.01")
    assert Decimal(str(amount["maximum"])) == Decimal("99999999.99")


def test_entry_kind_includes_platform_usage_debit(contract):
    assert (
        "usage_debit"
        in contract["definitions"]["ManagedCreditEntry"]["properties"]["kind"]["enum"]
    )


def test_statement_month_has_a_calendar_month_pattern(contract):
    parameters = contract["paths"][PREFIX + "statement/"]["get"]["parameters"]
    month = next(param for param in parameters if param["name"] == "month")
    assert re.fullmatch(month["pattern"], "2026-10")
    for invalid in ["2026-00", "2026-13", "2026-1", "October 2026"]:
        assert not re.fullmatch(month["pattern"], invalid)


@pytest.mark.parametrize(
    "month", ["2026-00", "2026-13", "2026-1", "October 2026", "0000-01", "9999-12"]
)
def test_statement_invalid_month_keeps_its_error_message(month):
    serializer = ManagedCreditStatementQuerySerializer(data={"month": month})
    assert not serializer.is_valid()
    assert serializer.errors["month"] == ["month must be YYYY-MM"]


def test_entries_filter_accepts_platform_usage_debit():
    serializer = ManagedCreditEntriesQuerySerializer(data={"kind": "usage_debit,spend"})
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["kind"] == ["usage_debit", "spend"]


@pytest.mark.parametrize(
    "amount,valid",
    [("0", False), ("0.01", True), ("99999999.99", True), ("100000000.00", False)],
)
def test_checkout_serializer_keeps_its_existing_amount_range(amount, valid):
    serializer = ManagedCreditCheckoutRequestSerializer(
        data={
            "amount_usd": amount,
            "success_url": "http://localhost:3000/credits/success",
            "cancel_url": "http://localhost:3000/credits/cancel",
        }
    )
    assert serializer.is_valid() is valid, serializer.errors
