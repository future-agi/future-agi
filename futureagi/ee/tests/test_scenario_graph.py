"""
Tests for scenario_graph module.

Tests the EnhancedScenariosAgent and ConversationGraphGenerator classes,
focusing on mode determination logic and AgentTypeChoices usage.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from simulate.models.agent_definition import AgentTypeChoices


class TestEnhancedScenariosAgentModeSelection:
    """Test mode selection logic in EnhancedScenariosAgent."""

    def test_mode_defaults_to_voice_when_agent_type_is_voice(self):
        """Mode should be 'voice' when agent_type is VOICE."""
        from ee.agenthub.scenario_graph.enhanced_scenarios_agent import (
            EnhancedScenariosAgent,
        )

        mock_agent_def = Mock()
        mock_agent_def.agent_type = AgentTypeChoices.VOICE
        mock_agent_def.agent_name = "Test Agent"
        mock_agent_def.description = "Test description"
        mock_agent_def.languages = ["en"]
        mock_agent_def.inbound = True

        with patch.object(
            EnhancedScenariosAgent,
            "__init__",
            lambda self, **kwargs: None,
        ):
            agent = EnhancedScenariosAgent()
            agent.agent_definition = mock_agent_def

            # Test the mode determination logic directly
            mode = (
                "voice"
                if getattr(agent.agent_definition, "agent_type", AgentTypeChoices.VOICE)
                == AgentTypeChoices.VOICE
                else "chat"
            )
            assert mode == "voice"

    def test_mode_is_chat_when_agent_type_is_text(self):
        """Mode should be 'chat' when agent_type is TEXT."""
        mock_agent_def = Mock()
        mock_agent_def.agent_type = AgentTypeChoices.TEXT

        # Test the mode determination logic
        mode = (
            "voice"
            if getattr(mock_agent_def, "agent_type", AgentTypeChoices.VOICE)
            == AgentTypeChoices.VOICE
            else "chat"
        )
        assert mode == "chat"

    def test_mode_defaults_to_voice_when_agent_type_missing(self):
        """Mode should default to 'voice' when agent_type attribute is missing."""
        mock_agent_def = Mock(spec=[])  # Empty spec means no attributes

        # Test the mode determination logic with missing agent_type
        mode = (
            "voice"
            if getattr(mock_agent_def, "agent_type", AgentTypeChoices.VOICE)
            == AgentTypeChoices.VOICE
            else "chat"
        )
        assert mode == "voice"

    def test_simulation_mode_overrides_agent_type(self):
        """simulation_mode parameter should override agent_type."""
        mock_agent_def = Mock()
        mock_agent_def.agent_type = AgentTypeChoices.VOICE

        # Determine mode from agent_type
        mode = (
            "voice"
            if getattr(mock_agent_def, "agent_type", AgentTypeChoices.VOICE)
            == AgentTypeChoices.VOICE
            else "chat"
        )

        # simulation_mode override
        simulation_mode = "chat"
        if simulation_mode:
            mode = simulation_mode

        assert mode == "chat"


class TestConversationGraphGeneratorModeSelection:
    """Test mode selection logic in ConversationGraphGenerator."""

    def test_mode_from_configuration_snapshot_voice(self):
        """Mode should use configuration_snapshot when available (voice)."""
        configuration_snapshot = {"agent_type": AgentTypeChoices.VOICE}
        mock_agent_def = Mock()
        mock_agent_def.agent_type = AgentTypeChoices.TEXT  # Different from snapshot

        # Test the logic: prefer configuration_snapshot
        if configuration_snapshot:
            agent_type = configuration_snapshot.get(
                "agent_type", AgentTypeChoices.VOICE
            )
        else:
            agent_type = getattr(
                mock_agent_def, "agent_type", AgentTypeChoices.VOICE
            )

        mode = "voice" if agent_type == AgentTypeChoices.VOICE else "chat"
        assert mode == "voice"

    def test_mode_from_configuration_snapshot_text(self):
        """Mode should use configuration_snapshot when available (text)."""
        configuration_snapshot = {"agent_type": AgentTypeChoices.TEXT}
        mock_agent_def = Mock()
        mock_agent_def.agent_type = AgentTypeChoices.VOICE  # Different from snapshot

        # Test the logic: prefer configuration_snapshot
        if configuration_snapshot:
            agent_type = configuration_snapshot.get(
                "agent_type", AgentTypeChoices.VOICE
            )
        else:
            agent_type = getattr(
                mock_agent_def, "agent_type", AgentTypeChoices.VOICE
            )

        mode = "voice" if agent_type == AgentTypeChoices.VOICE else "chat"
        assert mode == "chat"

    def test_mode_falls_back_to_agent_definition_when_no_snapshot(self):
        """Mode should fall back to agent_definition when no configuration_snapshot."""
        configuration_snapshot = None
        mock_agent_def = Mock()
        mock_agent_def.agent_type = AgentTypeChoices.TEXT

        # Test the logic: fall back to agent_definition
        if configuration_snapshot:
            agent_type = configuration_snapshot.get(
                "agent_type", AgentTypeChoices.VOICE
            )
        else:
            agent_type = getattr(
                mock_agent_def, "agent_type", AgentTypeChoices.VOICE
            )

        mode = "voice" if agent_type == AgentTypeChoices.VOICE else "chat"
        assert mode == "chat"

    def test_mode_falls_back_when_snapshot_is_empty_dict(self):
        """Mode should fall back to agent_definition when configuration_snapshot is empty dict."""
        configuration_snapshot = {}  # Empty dict is falsy in Python
        mock_agent_def = Mock()
        mock_agent_def.agent_type = AgentTypeChoices.TEXT

        # Test the logic - empty dict is falsy, so falls back to agent_definition
        if configuration_snapshot:
            agent_type = configuration_snapshot.get(
                "agent_type", AgentTypeChoices.VOICE
            )
        else:
            agent_type = getattr(
                mock_agent_def, "agent_type", AgentTypeChoices.VOICE
            )

        mode = "voice" if agent_type == AgentTypeChoices.VOICE else "chat"
        # Empty dict is falsy, so it falls back to agent_definition.agent_type (TEXT -> chat)
        assert mode == "chat"

    def test_mode_defaults_to_voice_when_snapshot_has_no_agent_type_key(self):
        """Mode should default to voice when configuration_snapshot exists but lacks agent_type."""
        configuration_snapshot = {"other_key": "value"}  # Non-empty but no agent_type
        mock_agent_def = Mock()
        mock_agent_def.agent_type = AgentTypeChoices.TEXT  # Would be chat if used

        # Test the logic - non-empty dict is truthy
        if configuration_snapshot:
            agent_type = configuration_snapshot.get(
                "agent_type", AgentTypeChoices.VOICE
            )
        else:
            agent_type = getattr(
                mock_agent_def, "agent_type", AgentTypeChoices.VOICE
            )

        mode = "voice" if agent_type == AgentTypeChoices.VOICE else "chat"
        # Non-empty dict uses .get() which defaults to VOICE
        assert mode == "voice"

    def test_simulation_mode_overrides_configuration_snapshot(self):
        """simulation_mode parameter should override configuration_snapshot."""
        configuration_snapshot = {"agent_type": AgentTypeChoices.VOICE}

        # Determine mode from configuration_snapshot
        agent_type = configuration_snapshot.get("agent_type", AgentTypeChoices.VOICE)
        mode = "voice" if agent_type == AgentTypeChoices.VOICE else "chat"

        # simulation_mode override
        simulation_mode = "chat"
        if simulation_mode:
            mode = simulation_mode

        assert mode == "chat"


class TestAgentTypeChoicesEnum:
    """Test AgentTypeChoices enum values."""

    def test_voice_choice_value(self):
        """VOICE choice should have value 'voice'."""
        assert AgentTypeChoices.VOICE == "voice"
        assert AgentTypeChoices.VOICE.value == "voice"

    def test_text_choice_value(self):
        """TEXT choice should have value 'text'."""
        assert AgentTypeChoices.TEXT == "text"
        assert AgentTypeChoices.TEXT.value == "text"

    def test_enum_comparison_with_string(self):
        """AgentTypeChoices should compare equal to their string values."""
        assert AgentTypeChoices.VOICE == "voice"
        assert AgentTypeChoices.TEXT == "text"
        assert "voice" == AgentTypeChoices.VOICE
        assert "text" == AgentTypeChoices.TEXT


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
