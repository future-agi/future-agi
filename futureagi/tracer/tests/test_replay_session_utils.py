import uuid
from unittest.mock import MagicMock, patch

import pytest

from model_hub.models.choices import StatusType
from tracer.utils.replay_session import (
    _build_trace_query,
    _get_transcripts_from_session_query,
    _get_transcripts_from_trace_query,
    _update_agent_definition,
    create_scenario,
    get_agent_suggestions,
    get_or_create_agent_definition,
    get_system_prompt,
    get_transcripts,
)


@pytest.mark.unit
class TestBuildTraceQuery:
    """Tests for _build_trace_query function."""

    @patch("tracer.utils.replay_session.Trace")
    def test_session_type_with_ids(self, mock_trace):
        """Should filter by session_id when replay_type is session."""
        mock_queryset = MagicMock()
        mock_trace.objects.filter.return_value = mock_queryset

        project_id = str(uuid.uuid4())
        session_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

        _build_trace_query(project_id, "session", session_ids, select_all=False)

        mock_trace.objects.filter.assert_called_once_with(project_id=project_id)
        mock_queryset.filter.assert_called_once_with(session_id__in=session_ids)

    @patch("tracer.utils.replay_session.Trace")
    def test_session_type_select_all(self, mock_trace):
        """Should filter for non-null session_id when select_all is True."""
        mock_queryset = MagicMock()
        mock_trace.objects.filter.return_value = mock_queryset

        project_id = str(uuid.uuid4())

        _build_trace_query(project_id, "session", ids=None, select_all=True)

        mock_trace.objects.filter.assert_called_once_with(project_id=project_id)
        mock_queryset.filter.assert_called_once_with(session_id__isnull=False)

    @patch("tracer.utils.replay_session.Trace")
    def test_trace_type_with_ids(self, mock_trace):
        """Should filter by trace IDs when replay_type is trace."""
        mock_queryset = MagicMock()
        mock_trace.objects.filter.return_value = mock_queryset

        project_id = str(uuid.uuid4())
        trace_ids = [str(uuid.uuid4()), str(uuid.uuid4())]

        _build_trace_query(project_id, "trace", trace_ids, select_all=False)

        mock_trace.objects.filter.assert_called_once_with(project_id=project_id)
        mock_queryset.filter.assert_called_once_with(id__in=trace_ids)

    @patch("tracer.utils.replay_session.Trace")
    def test_trace_type_select_all(self, mock_trace):
        """Should return base query when select_all is True for trace type."""
        mock_queryset = MagicMock()
        mock_trace.objects.filter.return_value = mock_queryset

        project_id = str(uuid.uuid4())

        result = _build_trace_query(project_id, "trace", ids=None, select_all=True)

        mock_trace.objects.filter.assert_called_once_with(project_id=project_id)
        assert result == mock_queryset

    def test_invalid_replay_type_raises_error(self):
        """Should raise ValueError for invalid replay_type."""
        with pytest.raises(ValueError) as exc_info:
            _build_trace_query(str(uuid.uuid4()), "invalid_type", None, False)

        assert "Invalid replay type" in str(exc_info.value)

    @patch("tracer.utils.replay_session.Trace")
    def test_empty_ids_list(self, mock_trace):
        """Should handle empty ids list."""
        mock_queryset = MagicMock()
        mock_trace.objects.filter.return_value = mock_queryset

        project_id = str(uuid.uuid4())

        _build_trace_query(project_id, "session", ids=[], select_all=False)

        mock_queryset.filter.assert_called_once_with(session_id__in=[])


@pytest.mark.unit
class TestGetSystemPrompt:
    """Tests for get_system_prompt function."""

    @patch("tracer.utils.replay_session.SQL_query_handler")
    @patch("tracer.utils.replay_session._build_trace_query")
    def test_returns_system_prompt(self, mock_build_query, mock_sql_handler):
        """Should return system prompt when found."""
        trace_id = uuid.uuid4()
        mock_queryset = MagicMock()
        mock_queryset.values_list.return_value = [trace_id]
        mock_build_query.return_value = mock_queryset

        mock_sql_handler.get_system_prompt_from_traces.return_value = (
            "You are a helpful assistant."
        )

        project_id = str(uuid.uuid4())
        result = get_system_prompt(project_id, "trace", [str(trace_id)], False)

        assert result == "You are a helpful assistant."
        mock_sql_handler.get_system_prompt_from_traces.assert_called_once()

    @patch("tracer.utils.replay_session.SQL_query_handler")
    @patch("tracer.utils.replay_session._build_trace_query")
    def test_returns_none_when_no_traces(self, mock_build_query, mock_sql_handler):
        """Should return None when no traces found."""
        mock_queryset = MagicMock()
        mock_queryset.values_list.return_value = []
        mock_build_query.return_value = mock_queryset

        project_id = str(uuid.uuid4())
        result = get_system_prompt(project_id, "trace", [], False)

        assert result is None
        mock_sql_handler.get_system_prompt_from_traces.assert_not_called()

    @patch("tracer.utils.replay_session.SQL_query_handler")
    @patch("tracer.utils.replay_session._build_trace_query")
    def test_returns_none_when_no_prompt_found(
        self, mock_build_query, mock_sql_handler
    ):
        """Should return None when SQL query returns no prompt."""
        trace_id = uuid.uuid4()
        mock_queryset = MagicMock()
        mock_queryset.values_list.return_value = [trace_id]
        mock_build_query.return_value = mock_queryset

        mock_sql_handler.get_system_prompt_from_traces.return_value = None

        project_id = str(uuid.uuid4())
        result = get_system_prompt(project_id, "session", [str(trace_id)], False)

        assert result is None


@pytest.mark.unit
class TestGetAgentSuggestions:
    """Tests for get_agent_suggestions function."""

    @patch("tracer.utils.replay_session._get_agent_definition_from_replay_sessions")
    def test_returns_existing_agent_definition(self, mock_get_agent_def_from_sessions):
        """Should return existing agent definition data when found."""
        mock_project = MagicMock()
        mock_project.name = "Test Project"
        mock_project.organization = MagicMock()

        mock_agent_def = MagicMock()
        mock_agent_def.agent_name = "Existing Agent"
        mock_agent_def.description = "Agent description"
        mock_agent_def.agent_type = "voice"
        mock_version = MagicMock()
        mock_version.version_name = "v1.0"
        mock_agent_def.latest_version = mock_version

        mock_get_agent_def_from_sessions.return_value = mock_agent_def

        exists, suggestions, agent_def = get_agent_suggestions(
            mock_project, "trace", [str(uuid.uuid4())], False
        )

        assert exists is True
        assert suggestions["agent_name"] == "Existing Agent"
        assert suggestions["agent_description"] == "Agent description"
        assert suggestions["agent_type"] == "voice"
        assert suggestions["version_name"] == "v1.0"
        assert "scenario_name" in suggestions
        assert agent_def == mock_agent_def

    @patch("tracer.utils.replay_session._get_agent_definition_from_replay_sessions")
    def test_generates_defaults_when_no_agent_definition(
        self, mock_get_agent_def_from_sessions
    ):
        """Should generate default suggestions when no agent_def exists.

        The system prompt is intentionally NOT copied into agent_description —
        for text agents it lives on AgentVersion.configuration_snapshot, and
        for voice agents on the external provider config; duplicating it into
        description pollutes the scenario card UI.
        """
        mock_project = MagicMock()
        mock_project.name = "New Project"
        mock_project.id = uuid.uuid4()

        mock_get_agent_def_from_sessions.return_value = None

        exists, suggestions, agent_def = get_agent_suggestions(
            mock_project, "trace", [str(uuid.uuid4())], False
        )

        assert exists is False
        assert "New Project" in suggestions["agent_name"]
        assert suggestions["agent_description"] == ""
        assert suggestions["agent_type"] == "text"
        assert suggestions["version_name"] is None
        assert agent_def is None

    @patch("tracer.utils.replay_session._get_agent_definition_from_replay_sessions")
    def test_handles_agent_definition_not_found(self, mock_get_agent_def_from_sessions):
        """Should generate defaults when no agent_def is found from replay sessions."""
        mock_project = MagicMock()
        mock_project.name = "Project"
        mock_project.id = uuid.uuid4()
        mock_project.organization = MagicMock()

        mock_get_agent_def_from_sessions.return_value = None

        exists, suggestions, agent_def = get_agent_suggestions(
            mock_project, "trace", [], False
        )

        assert exists is False
        assert suggestions["agent_description"] == ""
        assert agent_def is None

    @patch("tracer.utils.replay_session._get_agent_definition_from_replay_sessions")
    def test_handles_none_latest_version(self, mock_get_agent_def_from_sessions):
        """Should handle agent_def with no latest_version."""
        mock_project = MagicMock()
        mock_project.name = "Test Project"
        mock_project.organization = MagicMock()

        mock_agent_def = MagicMock()
        mock_agent_def.agent_name = "Agent"
        mock_agent_def.description = "Desc"
        mock_agent_def.agent_type = "text"
        mock_agent_def.latest_version = None

        mock_get_agent_def_from_sessions.return_value = mock_agent_def

        exists, suggestions, agent_def = get_agent_suggestions(
            mock_project, "session", [], False
        )

        assert exists is True
        assert suggestions["version_name"] is None
        assert agent_def == mock_agent_def


@pytest.mark.unit
class TestUpdateAgentDefinition:
    """Tests for _update_agent_definition function."""

    def test_updates_changed_fields(self):
        """Should update fields that have changed."""
        mock_agent_def = MagicMock()
        mock_agent_def.id = uuid.uuid4()
        mock_agent_def.agent_name = "Old Name"
        mock_agent_def.description = "Old Description"
        mock_agent_def.agent_type = "text"

        _update_agent_definition(
            mock_agent_def,
            agent_name="New Name",
            agent_description="New Description",
            agent_type="voice",
        )

        assert mock_agent_def.agent_name == "New Name"
        assert mock_agent_def.description == "New Description"
        assert mock_agent_def.agent_type == "voice"
        mock_agent_def.save.assert_called_once()
        mock_agent_def.create_version.assert_called_once()

    def test_no_update_when_no_changes(self):
        """Should not update or create version when nothing changed."""
        mock_agent_def = MagicMock()
        mock_agent_def.id = uuid.uuid4()
        mock_agent_def.agent_name = "Same Name"
        mock_agent_def.description = "Same Description"
        mock_agent_def.agent_type = "text"

        _update_agent_definition(
            mock_agent_def,
            agent_name="Same Name",
            agent_description="Same Description",
            agent_type="text",
        )

        mock_agent_def.save.assert_not_called()
        mock_agent_def.create_version.assert_not_called()

    def test_partial_update(self):
        """Should only update fields that actually changed."""
        mock_agent_def = MagicMock()
        mock_agent_def.id = uuid.uuid4()
        mock_agent_def.agent_name = "Old Name"
        mock_agent_def.description = "Same Description"
        mock_agent_def.agent_type = "text"

        _update_agent_definition(
            mock_agent_def,
            agent_name="New Name",
            agent_description="Same Description",
            agent_type="text",
        )

        assert mock_agent_def.agent_name == "New Name"
        mock_agent_def.save.assert_called_once_with(update_fields=["agent_name"])


@pytest.mark.unit
class TestGetOrCreateAgentDefinition:
    """Tests for get_or_create_agent_definition function."""

    @patch("tracer.utils.replay_session._get_agent_definition_from_replay_sessions")
    @patch("tracer.utils.replay_session._update_agent_definition")
    def test_returns_existing_and_updates(
        self, mock_update, mock_get_agent_def_from_sessions
    ):
        """Should return existing agent_def and update if needed."""
        mock_project = MagicMock()
        mock_project.organization = MagicMock()

        mock_agent_def = MagicMock()
        mock_get_agent_def_from_sessions.return_value = mock_agent_def

        result = get_or_create_agent_definition(
            mock_project, "Updated Agent", "New description", "voice"
        )

        assert result == mock_agent_def
        mock_get_agent_def_from_sessions.assert_called_once_with(mock_project)
        mock_update.assert_called_once_with(
            agent_def=mock_agent_def,
            agent_name="Updated Agent",
            agent_description="New description",
            agent_type="voice",
        )

    @patch("tracer.utils.replay_session._get_agent_definition_from_replay_sessions")
    @patch("tracer.utils.replay_session.AgentDefinition")
    def test_creates_new_when_no_existing(
        self, mock_agent_def_model, mock_get_agent_def_from_sessions
    ):
        """Should create new agent_def when none exists."""
        mock_project = MagicMock()
        mock_project.organization = MagicMock()
        mock_project.id = uuid.uuid4()

        mock_get_agent_def_from_sessions.return_value = None

        mock_new_agent_def = MagicMock()
        mock_new_agent_def.id = uuid.uuid4()
        mock_agent_def_model.objects.create.return_value = mock_new_agent_def

        result = get_or_create_agent_definition(
            mock_project, "New Agent", "Description", "text"
        )

        assert result == mock_new_agent_def
        mock_agent_def_model.objects.create.assert_called_once()
        mock_new_agent_def.create_version.assert_called_once()

    @patch("tracer.utils.replay_session._get_agent_definition_from_replay_sessions")
    @patch("tracer.utils.replay_session.AgentDefinition")
    def test_creates_new_with_correct_params(
        self, mock_agent_def_model, mock_get_agent_def_from_sessions
    ):
        """Should create agent_def with correct parameters."""
        mock_project = MagicMock()
        mock_project.organization = MagicMock()
        mock_project.id = uuid.uuid4()

        mock_get_agent_def_from_sessions.return_value = None

        mock_new_agent_def = MagicMock()
        mock_new_agent_def.id = uuid.uuid4()
        mock_agent_def_model.objects.create.return_value = mock_new_agent_def

        get_or_create_agent_definition(
            mock_project, "Agent Name", "Agent Desc", "voice"
        )

        mock_agent_def_model.objects.create.assert_called_once_with(
            agent_name="Agent Name",
            description="Agent Desc",
            agent_type="voice",
            inbound=True,
            organization=mock_project.organization,
            languages=["en"],
        )

    @patch("tracer.utils.replay_session._get_agent_definition_from_replay_sessions")
    @patch("tracer.utils.replay_session.AgentDefinition")
    def test_creates_version_after_agent_def(
        self, mock_agent_def_model, mock_get_agent_def_from_sessions
    ):
        """Should create an initial version for the new agent_def."""
        from simulate.models import AgentVersion

        mock_project = MagicMock()
        mock_project.organization = MagicMock()
        mock_project.id = uuid.uuid4()

        mock_get_agent_def_from_sessions.return_value = None

        mock_new_agent_def = MagicMock()
        mock_new_agent_def.id = uuid.uuid4()
        mock_agent_def_model.objects.create.return_value = mock_new_agent_def

        get_or_create_agent_definition(mock_project, "Agent", "Desc", "text")

        mock_new_agent_def.create_version.assert_called_once_with(
            description="Desc",
            commit_message="Initial version from replay session",
            status=AgentVersion.StatusChoices.ACTIVE,
        )


@pytest.mark.unit
class TestGetTranscripts:
    """Tests for get_transcripts function."""

    @patch("tracer.utils.replay_session._get_transcripts_from_trace_query")
    @patch("tracer.utils.replay_session._build_trace_query")
    def test_returns_transcripts_for_trace_type(
        self, mock_build_query, mock_get_trace_transcripts
    ):
        """Should return transcripts for trace replay type."""
        trace_id = str(uuid.uuid4())
        mock_get_trace_transcripts.return_value = {
            trace_id: [{"input": "Hello", "output": "Hi there"}]
        }

        project_id = str(uuid.uuid4())
        result = get_transcripts(project_id, "trace", [trace_id], False)

        assert result is not None
        assert trace_id in result
        assert result[trace_id]["replay_type"] == "trace"

    @patch("tracer.utils.replay_session._get_transcripts_from_session_query")
    @patch("tracer.utils.replay_session._build_trace_query")
    def test_returns_transcripts_for_session_type(
        self, mock_build_query, mock_get_session_transcripts
    ):
        """Should return transcripts for session replay type."""
        session_id = str(uuid.uuid4())
        mock_get_session_transcripts.return_value = {
            session_id: [
                {"input": "Hello", "output": "Hi"},
                {"input": "How are you?", "output": "I'm good"},
            ]
        }

        project_id = str(uuid.uuid4())
        result = get_transcripts(project_id, "session", [session_id], False)

        assert result is not None
        assert session_id in result
        assert result[session_id]["replay_type"] == "session"

    @patch("tracer.utils.replay_session._get_transcripts_from_trace_query")
    @patch("tracer.utils.replay_session._build_trace_query")
    def test_returns_none_when_no_transcripts(
        self, mock_build_query, mock_get_transcripts
    ):
        """Should return None when no transcripts found."""
        mock_get_transcripts.return_value = {}

        project_id = str(uuid.uuid4())
        result = get_transcripts(project_id, "trace", [], False)

        assert result is None

    def test_invalid_replay_type_raises_error(self):
        """Should raise ValueError for invalid replay_type."""
        with pytest.raises(ValueError) as exc_info:
            get_transcripts(str(uuid.uuid4()), "invalid", [], False)

        assert "Invalid replay type" in str(exc_info.value)

    @patch("tracer.utils.replay_session._get_transcripts_from_trace_query")
    @patch("tracer.utils.replay_session._build_trace_query")
    def test_transcripts_are_json_serialized(
        self, mock_build_query, mock_get_transcripts
    ):
        """Should return transcripts as JSON strings."""
        import json

        trace_id = str(uuid.uuid4())
        mock_get_transcripts.return_value = {
            trace_id: [{"input": "Test", "output": "Response"}]
        }

        project_id = str(uuid.uuid4())
        result = get_transcripts(project_id, "trace", [trace_id], False)

        # Verify the transcript is valid JSON
        transcript_json = result[trace_id]["transcript"]
        parsed = json.loads(transcript_json)
        assert parsed == [{"input": "Test", "output": "Response"}]


@pytest.mark.unit
class TestGetTranscriptsFromTraceQuery:
    """Tests for _get_transcripts_from_trace_query function."""

    def test_returns_dict_with_trace_ids_as_keys(self):
        """Should return dict with trace IDs as keys."""
        trace_id_1 = uuid.uuid4()
        trace_id_2 = uuid.uuid4()

        mock_queryset = MagicMock()
        mock_annotated = MagicMock()
        mock_queryset.annotate.return_value = mock_annotated
        mock_annotated.values.return_value = [
            {
                "id": trace_id_1,
                "input": "Input 1",
                "output": "Output 1",
                "has_simulator_call_execution_id": False,
            },
            {
                "id": trace_id_2,
                "input": "Input 2",
                "output": "Output 2",
                "has_simulator_call_execution_id": False,
            },
        ]

        result = _get_transcripts_from_trace_query(mock_queryset)

        assert str(trace_id_1) in result
        assert str(trace_id_2) in result
        assert result[str(trace_id_1)] == [{"input": "Input 1", "output": "Output 1"}]
        assert result[str(trace_id_2)] == [{"input": "Input 2", "output": "Output 2"}]

    def test_returns_empty_dict_for_empty_queryset(self):
        """Should return empty dict when queryset is empty."""
        mock_queryset = MagicMock()
        mock_annotate = MagicMock()
        mock_queryset.annotate.return_value = mock_annotate
        mock_annotate.values.return_value = []

        result = _get_transcripts_from_trace_query(mock_queryset)

        assert result == {}

    def test_each_trace_has_single_turn(self):
        """Should have single turn per trace."""
        trace_id = uuid.uuid4()
        mock_queryset = MagicMock()
        mock_annotate = MagicMock()
        mock_queryset.annotate.return_value = mock_annotate
        mock_annotate.values.return_value = [
            {
                "id": trace_id,
                "input": "Hello",
                "output": "Hi",
                "has_simulator_call_execution_id": False,
            }
        ]

        result = _get_transcripts_from_trace_query(mock_queryset)

        assert len(result[str(trace_id)]) == 1


@pytest.mark.unit
class TestGetTranscriptsFromSessionQuery:
    """Tests for _get_transcripts_from_session_query function."""

    def test_groups_traces_by_session_id(self):
        """Should group traces by session_id."""
        session_id = uuid.uuid4()

        mock_queryset = MagicMock()
        mock_annotate = MagicMock()
        mock_order = MagicMock()

        mock_queryset.annotate.return_value = mock_annotate
        mock_annotate.order_by.return_value = mock_order
        mock_order.values.return_value = [
            {
                "session_id": session_id,
                "input": "Turn 1 input",
                "output": "Turn 1 output",
                "has_simulator_call_execution_id": False,
            },
            {
                "session_id": session_id,
                "input": "Turn 2 input",
                "output": "Turn 2 output",
                "has_simulator_call_execution_id": False,
            },
        ]

        result = _get_transcripts_from_session_query(mock_queryset)

        assert str(session_id) in result
        assert len(result[str(session_id)]) == 2

    def test_handles_multiple_sessions(self):
        """Should handle multiple sessions correctly."""
        session_id_1 = uuid.uuid4()
        session_id_2 = uuid.uuid4()

        mock_queryset = MagicMock()
        mock_annotate = MagicMock()
        mock_order = MagicMock()

        mock_queryset.annotate.return_value = mock_annotate
        mock_annotate.order_by.return_value = mock_order
        mock_order.values.return_value = [
            {
                "session_id": session_id_1,
                "input": "S1 Input",
                "output": "S1 Output",
                "has_simulator_call_execution_id": False,
            },
            {
                "session_id": session_id_2,
                "input": "S2 Input",
                "output": "S2 Output",
                "has_simulator_call_execution_id": False,
            },
        ]

        result = _get_transcripts_from_session_query(mock_queryset)

        assert len(result) == 2
        assert str(session_id_1) in result
        assert str(session_id_2) in result

    def test_returns_empty_dict_for_empty_queryset(self):
        """Should return empty dict when queryset is empty."""
        mock_queryset = MagicMock()
        mock_annotate = MagicMock()
        mock_order = MagicMock()

        mock_queryset.annotate.return_value = mock_annotate
        mock_annotate.order_by.return_value = mock_order
        mock_order.values.return_value = []

        result = _get_transcripts_from_session_query(mock_queryset)

        assert result == {}

    def test_orders_by_span_start_time(self):
        """Should order traces by span_start_time."""
        mock_queryset = MagicMock()
        mock_annotate = MagicMock()
        mock_order = MagicMock()

        mock_queryset.annotate.return_value = mock_annotate
        mock_annotate.order_by.return_value = mock_order
        mock_order.values.return_value = []

        _get_transcripts_from_session_query(mock_queryset)

        mock_annotate.order_by.assert_called_once_with("span_start_time")


@pytest.mark.unit
class TestCreateScenario:
    """Tests for create_scenario function."""

    @patch("tracer.utils.replay_session.Scenarios")
    def test_creates_scenario_with_correct_fields(self, mock_scenarios):
        """Should create scenario with correct field values."""
        mock_project = MagicMock()
        mock_project.id = uuid.uuid4()
        mock_project.organization = MagicMock()
        mock_project.workspace = MagicMock()

        mock_agent_def = MagicMock()
        mock_agent_def.agent_name = "Test Agent"

        mock_scenario = MagicMock()
        mock_scenarios.objects.create.return_value = mock_scenario

        result = create_scenario(
            mock_project,
            mock_agent_def,
            "Test Scenario",
            "Scenario description",
        )

        assert result == mock_scenario
        mock_scenarios.objects.create.assert_called_once()

        call_kwargs = mock_scenarios.objects.create.call_args[1]
        assert call_kwargs["name"] == "Test Scenario"
        assert call_kwargs["description"] == "Scenario description"
        assert call_kwargs["source"] == "Session Replay"
        assert call_kwargs["organization"] == mock_project.organization
        assert call_kwargs["workspace"] == mock_project.workspace
        assert call_kwargs["agent_definition"] == mock_agent_def

    @patch("tracer.utils.replay_session.Scenarios")
    def test_sets_processing_status(self, mock_scenarios):
        """Should set status to PROCESSING."""
        mock_project = MagicMock()
        mock_agent_def = MagicMock()

        create_scenario(mock_project, mock_agent_def, "Name", "Desc")

        call_kwargs = mock_scenarios.objects.create.call_args[1]
        assert call_kwargs["status"] == StatusType.PROCESSING.value

    @patch("tracer.utils.replay_session.Scenarios")
    def test_sets_graph_scenario_type(self, mock_scenarios):
        """Should set scenario_type to GRAPH."""
        mock_project = MagicMock()
        mock_agent_def = MagicMock()

        mock_scenarios.ScenarioTypes.GRAPH = "graph"

        create_scenario(mock_project, mock_agent_def, "Name", "Desc")

        call_kwargs = mock_scenarios.objects.create.call_args[1]
        assert call_kwargs["scenario_type"] == mock_scenarios.ScenarioTypes.GRAPH

    @patch("tracer.utils.replay_session.Scenarios")
    def test_includes_project_id_in_metadata(self, mock_scenarios):
        """Should include project_id in metadata."""
        mock_project = MagicMock()
        mock_project.id = uuid.uuid4()
        mock_agent_def = MagicMock()

        create_scenario(mock_project, mock_agent_def, "Name", "Desc")

        call_kwargs = mock_scenarios.objects.create.call_args[1]
        assert call_kwargs["metadata"]["project_id"] == str(mock_project.id)
        assert call_kwargs["metadata"]["created_from"] == "replay_session"

    @patch("tracer.utils.replay_session.Scenarios")
    def test_uses_fallback_description_when_empty(self, mock_scenarios):
        """Should use fallback description when agent_description is empty."""
        mock_project = MagicMock()
        mock_agent_def = MagicMock()
        mock_agent_def.agent_name = "My Agent"

        create_scenario(mock_project, mock_agent_def, "Name", "")

        call_kwargs = mock_scenarios.objects.create.call_args[1]
        assert (
            "Generated from replay session for My Agent" in call_kwargs["description"]
        )

    @patch("tracer.utils.replay_session.Scenarios")
    def test_uses_provided_description(self, mock_scenarios):
        """Should use provided description when not empty."""
        mock_project = MagicMock()
        mock_agent_def = MagicMock()

        create_scenario(mock_project, mock_agent_def, "Name", "Custom description")

        call_kwargs = mock_scenarios.objects.create.call_args[1]
        assert call_kwargs["description"] == "Custom description"
