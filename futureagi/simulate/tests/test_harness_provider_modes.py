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
