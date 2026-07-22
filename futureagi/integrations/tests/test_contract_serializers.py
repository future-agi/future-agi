from integrations.serializers.contracts import (
    IntegrationConnectionListQuerySerializer,
    IntegrationEmptyRequestSerializer,
    IntegrationErrorResponseSerializer,
    IntegrationMessageResponseSerializer,
    IntegrationValidationResponseSerializer,
    SyncLogListQuerySerializer,
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
