import json
from pathlib import Path

import pytest

from integrations.serializers.contracts import (
    IntegrationConnectionListQuerySerializer,
    IntegrationEmptyRequestSerializer,
    IntegrationErrorResponseSerializer,
    IntegrationMessageResponseSerializer,
    IntegrationValidationResponseSerializer,
    SyncLogListQuerySerializer,
)
from integrations.serializers.integration_connection import (
    IntegrationConnectionDetailSerializer,
    IntegrationConnectionListSerializer,
)
from tfc.utils.api_errors import build_error_envelope


def test_integration_error_serializer_accepts_common_error_envelope():
    serializer = IntegrationErrorResponseSerializer(
        data=build_error_envelope({"display_name": ["Unknown field."]})
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["status"] is False
    assert serializer.validated_data["attr"] == "display_name"
    assert serializer.validated_data["details"] == {
        "display_name": ["Unknown field."]
    }


def test_integration_empty_request_serializer_rejects_non_empty_body():
    serializer = IntegrationEmptyRequestSerializer(data={"unexpected": True})

    assert not serializer.is_valid()
    assert "non_field_errors" in serializer.errors


def test_integration_connection_list_query_rejects_unknown_aliases():
    serializer = IntegrationConnectionListQuerySerializer(
        data={"page_number": 0, "legacyPage": 1}
    )

    assert not serializer.is_valid()
    assert serializer.errors == {"legacyPage": ["Unknown field."]}


def test_sync_log_query_validates_connection_id():
    serializer = SyncLogListQuerySerializer(data={"connection_id": "not-a-uuid"})

    assert not serializer.is_valid()
    assert "connection_id" in serializer.errors


def test_integration_validation_response_is_typed():
    serializer = IntegrationValidationResponseSerializer(
        data={
            "status": True,
            "result": {
                "valid": True,
                "projects": [{"id": "team-1", "name": "Support"}],
                "total_traces": 4,
                "viewer": {
                    "id": "user-1",
                    "name": "Kartik",
                    "email": "kartik.nvj@futureagi.com",
                },
            },
        }
    )

    assert serializer.is_valid(), serializer.errors


def test_integration_message_response_is_typed():
    serializer = IntegrationMessageResponseSerializer(
        data={"status": True, "result": {"message": "Sync triggered."}}
    )

    assert serializer.is_valid(), serializer.errors


@pytest.mark.parametrize(
    "serializer_class",
    [IntegrationConnectionListSerializer, IntegrationConnectionDetailSerializer],
)
def test_connection_response_host_url_may_be_empty(serializer_class):
    """Platforms without a host (Datadog, queues, storage) are saved with "".

    The declared response field has to accept that, or the contract says
    host_url is a non-empty URL while the API returns an empty string.
    """
    field = serializer_class().fields["host_url"]

    assert field.run_validation("") == ""
    assert field.run_validation("https://us.i.posthog.com") == (
        "https://us.i.posthog.com"
    )


@pytest.mark.parametrize(
    "definition", ["IntegrationConnectionList", "IntegrationConnectionDetail"]
)
def test_swagger_connection_host_url_allows_empty_string(definition):
    swagger_path = (
        Path(__file__).resolve().parents[3] / "api_contracts/openapi/swagger.json"
    )
    with swagger_path.open() as f:
        host_url = json.load(f)["definitions"][definition]["properties"]["host_url"]

    assert "minLength" not in host_url
