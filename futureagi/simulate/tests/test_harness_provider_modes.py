from simulate.serializers.harness_job import HarnessAgentSerializer


def test_vapi_connect_only_accepts_existing_assistant_id():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "vapi",
            "mode": "connect_only",
            "config": {"assistant_id": "assistant-123"},
            "secret_refs": {},
        }
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["mode"] == "connect_only"


def test_retell_environment_backed_accepts_repository_lifecycle():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "retell",
            "mode": "environment_backed",
            "config": {"lifecycle_manifest": "config/alk.yaml"},
            "secret_refs": {},
        }
    )
    assert serializer.is_valid(), serializer.errors


def test_environment_backed_rejects_existing_target_id():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "vapi",
            "mode": "environment_backed",
            "config": {"assistant_id": "production-agent"},
            "secret_refs": {},
        }
    )
    assert not serializer.is_valid()
    assert "config" in serializer.errors


def test_vapi_provider_import_accepts_source_id_and_safe_routes():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "vapi",
            "mode": "provider_import",
            "config": {
                "assistant_id": "assistant-123",
                "event_path": "/provider/events",
                "tool_path": "/provider/tools",
            },
            "secret_refs": {},
        }
    )
    assert serializer.is_valid(), serializer.errors


def test_retell_provider_import_requires_source_id():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "retell",
            "mode": "provider_import",
            "config": {},
            "secret_refs": {},
        }
    )
    assert not serializer.is_valid()
    assert "config" in serializer.errors


def test_provider_import_rejects_route_traversal():
    serializer = HarnessAgentSerializer(
        data={
            "connector": "vapi",
            "mode": "provider_import",
            "config": {
                "assistant_id": "assistant-123",
                "tool_path": "/provider/../admin",
            },
            "secret_refs": {},
        }
    )
    assert not serializer.is_valid()
    assert "config" in serializer.errors
