import json
from pathlib import Path

import pytest

from agentcc.serializers.contracts import (
    GatewayBatchSubmitRequestSerializer,
    GatewayBudgetSetRequestSerializer,
    GatewayConfigPatchRequestSerializer,
    GatewayMCPGuardrailsUpdateRequestSerializer,
    GatewayMCPServerUpdateRequestSerializer,
    GatewayMCPToolTestRequestSerializer,
)
from tfc.utils.serializer_fields import JsonValueField


REQUESTS = [
    (
        GatewayBudgetSetRequestSerializer,
        {"level": "org_limit", "config": {"limit": 250, "on_exceed": "warn"}},
        "config",
    ),
    (
        GatewayMCPServerUpdateRequestSerializer,
        {
            "server_id": "search",
            "config": {"url": "https://mcp.example.test/mcp", "transport": "http"},
        },
        "config",
    ),
    (
        GatewayMCPGuardrailsUpdateRequestSerializer,
        {"config": {"enabled": True, "blocked_tools": ["delete"]}},
        "config",
    ),
    (
        GatewayMCPToolTestRequestSerializer,
        {"name": "search", "arguments": {"query": "hello", "limit": 5}},
        "arguments",
    ),
    (
        GatewayBatchSubmitRequestSerializer,
        {
            "requests": [
                {
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "temperature": 0.5,
                    "stream": False,
                }
            ],
            "max_concurrency": 5,
        },
        "requests",
    ),
    (
        GatewayConfigPatchRequestSerializer,
        {"routing": {"strategy": "weighted", "weights": {"primary": 100}}},
        "routing",
    ),
]

PATCH_SECTIONS = [
    "guardrails",
    "routing",
    "cache",
    "rate_limiting",
    "budgets",
    "cost_tracking",
    "ip_acl",
    "alerting",
    "privacy",
    "tool_policy",
    "mcp",
    "a2a",
    "audit",
    "model_map",
]


@pytest.mark.parametrize(("serializer_cls", "payload", "field_name"), REQUESTS)
def test_gateway_requests_accept_scalar_and_nested_values(
    serializer_cls, payload, field_name
):
    serializer = serializer_cls(data=payload)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == payload


@pytest.mark.parametrize(("serializer_cls", "payload", "field_name"), REQUESTS)
def test_gateway_request_values_are_open_json(serializer_cls, payload, field_name):
    field = serializer_cls().fields[field_name]
    if serializer_cls is GatewayBatchSubmitRequestSerializer:
        field = field.child
    assert isinstance(field.child, JsonValueField)


@pytest.mark.parametrize("section", PATCH_SECTIONS)
def test_gateway_patch_section_values_are_open_json(section):
    field = GatewayConfigPatchRequestSerializer().fields[section]
    assert isinstance(field.child, JsonValueField)


@pytest.mark.parametrize(("serializer_cls", "payload", "field_name"), REQUESTS)
def test_gateway_requests_still_require_objects(serializer_cls, payload, field_name):
    invalid = {**payload, field_name: "not an object"}
    if serializer_cls is GatewayBatchSubmitRequestSerializer:
        invalid[field_name] = ["not an object"]
    serializer = serializer_cls(data=invalid)
    assert not serializer.is_valid()
    assert field_name in serializer.errors


def test_gateway_request_defaults_are_unchanged():
    tool = GatewayMCPToolTestRequestSerializer(data={"name": "search"})
    assert tool.is_valid(), tool.errors
    assert tool.validated_data["arguments"] == {}
    batch = GatewayBatchSubmitRequestSerializer(data={"requests": [{}]})
    assert batch.is_valid(), batch.errors
    assert batch.validated_data["max_concurrency"] == 5


def test_generated_gateway_request_values_are_open_json():
    swagger_path = (
        Path(__file__).resolve().parents[3] / "api_contracts/openapi/swagger.json"
    )
    definitions = json.loads(swagger_path.read_text())["definitions"]
    for serializer_cls, _payload, field_name in REQUESTS:
        name = serializer_cls.__name__.removesuffix("Serializer")
        schema = definitions[name]["properties"][field_name]
        if serializer_cls is GatewayBatchSubmitRequestSerializer:
            schema = schema["items"]
        assert schema["additionalProperties"].get("x-json-value") is True, name
    for section in PATCH_SECTIONS:
        schema = definitions["GatewayConfigPatchRequest"]["properties"][section]
        assert schema["additionalProperties"].get("x-json-value") is True, section
