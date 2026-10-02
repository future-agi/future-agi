"""
Unit tests for the AgentVersion.create_snapshot() method.

After refactoring, create_snapshot() reads directly from the agent
definition model instance instead of serializing through a ModelSerializer.
These tests ensure the snapshot is correctly populated.
"""

import pytest

from simulate.models import AgentDefinition, AgentVersion


@pytest.fixture
def agent_definition(db, organization, workspace):
    """Create a fully-populated voice agent definition."""
    return AgentDefinition.objects.create(
        agent_name="Snapshot Test Agent",
        agent_type=AgentDefinition.AgentTypeChoices.VOICE,
        contact_number="+12345678901",
        inbound=True,
        description="Agent for snapshot testing",
        provider="vapi",
        api_key="test-api-key",
        assistant_id="asst_test_123",
        authentication_method="api_key",
        language="en",
        languages=["en", "es"],
        model="gpt-4",
        model_details={"temperature": 0.7},
        organization=organization,
        workspace=workspace,
    )


@pytest.mark.integration
class TestAgentVersionSnapshot:
    """Tests for AgentVersion.create_snapshot() method."""

    def test_snapshot_created_on_save(self, agent_definition):
        version = agent_definition.create_version(
            description="Test",
            commit_message="Test snapshot",
            status=AgentVersion.StatusChoices.ACTIVE,
        )
        assert version.configuration_snapshot is not None
        assert isinstance(version.configuration_snapshot, dict)
        assert len(version.configuration_snapshot) > 0

    def test_snapshot_contains_all_fields(self, agent_definition):
        version = agent_definition.create_version(
            description="Test",
            commit_message="Test snapshot",
            status=AgentVersion.StatusChoices.ACTIVE,
        )
        snapshot = version.configuration_snapshot
        expected_keys = {
            "inbound",
            "languages",
            "provider",
            "agent_name",
            "commit_message",
            "contact_number",
            "authentication_method",
            "language",
            "description",
            "assistant_id",
            "model",
            "model_details",
            "agent_type",
        }
        # All expected keys should be present (observability_enabled may
        # be excluded by exclude_none if False)
        for key in expected_keys:
            assert key in snapshot, f"Missing snapshot key: {key}"

        # Credential fields must NOT be in the snapshot (refactor invariant)
        forbidden_keys = {"api_key", "livekit_api_key", "livekit_api_secret"}
        for key in forbidden_keys:
            assert key not in snapshot, f"Credential field leaked into snapshot: {key}"

    def test_snapshot_reads_from_agent(self, agent_definition):
        version = agent_definition.create_version(
            description="Test",
            commit_message="Verify values",
            status=AgentVersion.StatusChoices.ACTIVE,
        )
        snapshot = version.configuration_snapshot
        assert snapshot["agent_name"] == "Snapshot Test Agent"
        assert snapshot["agent_type"] == "voice"
        assert snapshot["provider"] == "vapi"
        assert snapshot["assistant_id"] == "asst_test_123"
        assert snapshot["contact_number"] == "+12345678901"
        assert snapshot["inbound"] is True
        assert snapshot["languages"] == ["en", "es"]
        assert snapshot["language"] == "en"
        assert snapshot["model"] == "gpt-4"
        assert snapshot["model_details"] == {"temperature": 0.7}
        assert snapshot["commit_message"] == "Verify values"

    def test_snapshot_knowledge_base_as_string(
        self, agent_definition, organization, workspace
    ):
        from model_hub.models.develop_dataset import KnowledgeBaseFile

        kb = KnowledgeBaseFile.objects.create(
            name="Test KB",
            organization=organization,
            workspace=workspace,
        )
        agent_definition.knowledge_base = kb
        agent_definition.save()

        version = agent_definition.create_version(
            description="Test",
            commit_message="KB test",
            status=AgentVersion.StatusChoices.ACTIVE,
        )
        snapshot = version.configuration_snapshot
        assert "knowledge_base" in snapshot
        assert isinstance(snapshot["knowledge_base"], str)
        assert snapshot["knowledge_base"] == str(kb.id)

    def test_snapshot_with_null_languages(self, db, organization, workspace):
        """Regression: languages=None should not crash create_snapshot()."""
        agent = AgentDefinition.objects.create(
            agent_name="No Languages Agent",
            agent_type=AgentDefinition.AgentTypeChoices.VOICE,
            contact_number="+12345678901",
            inbound=True,
            description="Agent without languages",
            provider="vapi",
            api_key="test-api-key",
            assistant_id="asst_test_123",
            authentication_method="api_key",
            language="en",
            languages=None,
            organization=organization,
            workspace=workspace,
        )
        version = agent.create_version(
            description="Test",
            commit_message="Null languages test",
            status=AgentVersion.StatusChoices.ACTIVE,
        )
        snapshot = version.configuration_snapshot
        assert snapshot is not None
        assert snapshot.get("languages", []) == []

    def test_snapshot_observability_flag(self, agent_definition):
        # Without observability provider, flag should be False or absent
        version = agent_definition.create_version(
            description="Test",
            commit_message="No obs",
            status=AgentVersion.StatusChoices.ACTIVE,
        )
        snapshot = version.configuration_snapshot
        # observability_enabled is False by default, may be excluded by exclude_none
        obs_value = snapshot.get("observability_enabled", False)
        assert obs_value is False
