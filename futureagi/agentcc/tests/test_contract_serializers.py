import pytest

from agentcc.serializers.contracts import (
    AgentccEmptyRequestSerializer,
    AgentccErrorResponseSerializer,
    APIKeyBulkResponseSerializer,
    GatewayConfigProviderSerializer,
    GatewayHealthErrorResponseSerializer,
    GatewayMCPStatusResponseSerializer,
    GatewayProviderStatusSerializer,
    OrgConfigBulkResponseSerializer,
)
from tfc.utils.api_errors import build_error_envelope


def test_agentcc_error_serializer_declares_common_error_envelope_fields():
    fields = AgentccErrorResponseSerializer().fields

    for field_name in ("type", "code", "detail", "message", "error", "attr"):
        assert field_name in fields


def test_agentcc_error_serializer_accepts_common_error_envelope():
    serializer = AgentccErrorResponseSerializer(
        data=build_error_envelope({"name": ["Unknown field."]})
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["status"] is False
    assert serializer.validated_data["attr"] == "name"
    assert serializer.validated_data["details"] == {"name": ["Unknown field."]}


@pytest.mark.parametrize(
    "result",
    [
        "Invalid request.",
        "",
        {
            "status": "unreachable",
            "error": "Gateway offline",
            "last_health_check": "2026-01-02T03:04:15Z",
        },
    ],
)
def test_gateway_health_error_round_trips_string_or_typed_object(result):
    payload = {"status": False, "result": result}
    serializer = GatewayHealthErrorResponseSerializer(data=payload)

    assert serializer.is_valid(), serializer.errors
    assert serializer.data["result"] == result
    assert serializer.data["status"] is False


@pytest.mark.parametrize("missing", ["status", "error", "last_health_check"])
def test_gateway_health_error_requires_typed_probe_fields(missing):
    result = {
        "status": "unreachable",
        "error": "Gateway offline",
        "last_health_check": "2026-01-02T03:04:15Z",
    }
    del result[missing]
    serializer = GatewayHealthErrorResponseSerializer(
        data={"status": False, "result": result}
    )

    assert not serializer.is_valid()
    assert missing in serializer.errors["result"]


@pytest.mark.parametrize(
    "result",
    [
        {},
        [],
        1,
        False,
        None,
        {
            "status": "healthy",
            "error": "Gateway offline",
            "last_health_check": "2026-01-02T03:04:15Z",
        },
        {
            "status": "unreachable",
            "error": {},
            "last_health_check": "2026-01-02T03:04:15Z",
        },
        {
            "status": "unreachable",
            "error": "Gateway offline",
            "last_health_check": "not-a-timestamp",
        },
        {
            "status": "unreachable",
            "error": "Gateway offline",
            "last_health_check": None,
        },
    ],
)
def test_gateway_health_error_rejects_malformed_result(result):
    serializer = GatewayHealthErrorResponseSerializer(
        data={"status": False, "result": result}
    )

    assert not serializer.is_valid()
    assert "result" in serializer.errors


def test_agentcc_empty_request_serializer_rejects_non_empty_body():
    serializer = AgentccEmptyRequestSerializer(data={"unexpected": True})

    assert not serializer.is_valid()
    assert "non_field_errors" in serializer.errors


def test_gateway_mcp_status_accepts_normalized_fallback_servers():
    serializer = GatewayMCPStatusResponseSerializer(
        data={
            "status": True,
            "result": {
                "enabled": True,
                "sessions": 0,
                "tools": 0,
                "resources": 0,
                "prompts": 0,
                "servers": [{"id": "github", "status": "configured"}],
            },
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_gateway_mcp_status_accepts_empty_server_list():
    serializer = GatewayMCPStatusResponseSerializer(
        data={
            "status": True,
            "result": {
                "enabled": False,
                "sessions": 0,
                "tools": 0,
                "resources": 0,
                "prompts": 0,
                "servers": [],
            },
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_gateway_config_provider_uses_uuid_database_id():
    serializer = GatewayConfigProviderSerializer(
        data={
            "id": "36ab6a86-28ef-484e-9fa2-0aade2cde52d",
            "name": "openai",
            "display_name": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "api_format": "openai",
            "models": ["gpt-4o"],
            "is_active": True,
            "default_timeout": 60,
            "max_concurrent": 100,
            "conn_pool_size": 100,
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_gateway_provider_status_uses_gateway_provider_key_not_uuid():
    serializer = GatewayProviderStatusSerializer(
        data={
            "id": "openai",
            "name": "openai",
            "status": "healthy",
            "healthy": True,
            "circuit_state": "closed",
            "display_name": "OpenAI",
            "base_url": "https://api.openai.com/v1",
            "api_format": "custom_adapter",
            "models": ["gpt-4o"],
            "request_count": 10,
            "avg_latency": 12.5,
            "error_rate": 0.0,
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_api_key_bulk_response_is_typed():
    serializer = APIKeyBulkResponseSerializer(
        data={
            "status": True,
            "result": [
                {
                    "id": "gateway-key-1",
                    "name": "Production key",
                    "owner": "platform",
                    "key_hash": "sha256:abc",
                    "key_prefix": "sk-agentcc-a...",
                    "models": ["gpt-4o"],
                    "providers": ["openai"],
                    "metadata": {"purpose": "gateway-startup"},
                    "expires_at": None,
                }
            ],
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_api_key_bulk_response_rejects_untyped_result_object():
    serializer = APIKeyBulkResponseSerializer(
        data={
            "status": True,
            "result": {
                "id": "gateway-key-1",
                "name": "Production key",
            },
        }
    )

    assert not serializer.is_valid()
    assert "result" in serializer.errors


def test_org_config_bulk_response_is_typed_by_org_id():
    serializer = OrgConfigBulkResponseSerializer(
        data={
            "status": True,
            "result": {
                "36ab6a86-28ef-484e-9fa2-0aade2cde52d": {
                    "providers": {"openai": {"models": ["gpt-4o"]}},
                    "guardrails": {},
                    "routing": {},
                    "cache": {},
                    "rate_limiting": {},
                    "budgets": {},
                    "cost_tracking": {},
                    "ip_acl": {},
                    "alerting": {},
                    "privacy": {},
                    "tool_policy": {},
                    "mcp": {},
                    "a2a": {},
                    "audit": {},
                    "model_database": {},
                    "model_map": {},
                }
            },
        }
    )

    assert serializer.is_valid(), serializer.errors
