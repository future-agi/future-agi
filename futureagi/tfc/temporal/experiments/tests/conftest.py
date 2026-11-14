"""
Pytest fixtures for experiment workflow e2e tests.

Provides:
- WorkflowEnvironment (in-memory Temporal server)
- Mock node runners (llm_prompt echo runner)
- Dataset + snapshot + experiment fixtures
- Graph + ExperimentAgentConfig fixtures
- PromptTemplate + PromptVersion + ExperimentPromptConfig fixtures
- Mock for _process_row_sync (avoids real LLM calls in prompt flow)

Fixtures match the exact DB state created by the V2 create experiment API
(model_hub/views/experiments.py ExperimentsTableV2View.post).

IMPORTANT: These tests must run sequentially (not with pytest-xdist).
Run with:
    set -a && source .env.test.local && set +a
    pytest tfc/temporal/experiments/tests/ -v -p no:xdist
"""

import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from django.db import close_old_connections
from temporalio.testing import WorkflowEnvironment

from accounts.models.organization import Organization
from accounts.models.user import User
from accounts.models.workspace import Workspace
from agent_playground.models import (
    Edge,
    Graph,
    GraphVersion,
    Node,
    NodeTemplate,
    Port,
)
from agent_playground.models.node_connection import NodeConnection
from agent_playground.models.choices import (
    GraphVersionStatus,
    NodeType,
    PortDirection,
    PortMode,
)
from agent_playground.services.engine.node_runner import (
    BaseNodeRunner,
    clear_runners,
    register_runner,
)
from agent_playground.services.engine.output_sink import (
    clear_sinks,
    register_sink,
)
from agent_playground.services.engine.output_sinks.cell_sink import CellOutputSink

# =============================================================================
# LLM Prompt Mock Runner (for agent graph execution)
# =============================================================================


class LLMPromptEchoRunner(BaseNodeRunner):
    """
    Mock LLM prompt runner that echoes input as 'response' output.

    Mimics the real LLMPromptRunner which returns {"response": <text>}.
    Uses the first input value as the response.
    """

    def run(self, config, inputs, execution_context):
        first_value = next(iter(inputs.values()), "")
        return {"response": str(first_value)}


# =============================================================================
# Mock _process_row_sync (for prompt flow — avoids real LLM calls)
# =============================================================================


def _mock_process_row_sync(
    row_id,
    column_id,
    dataset_id,
    experiment_id,
    messages,
    model,
    model_config,
    output_format=None,
    run_prompt_config=None,
):
    """
    Mock replacement for _process_row_sync that writes a deterministic
    value to the cell instead of making real LLM calls.
    Returns the same dict shape as the real _process_row_sync.
    """
    from model_hub.models.choices import CellStatus
    from model_hub.models.develop_dataset import Cell

    close_old_connections()
    try:
        cell = Cell.objects.filter(
            row_id=row_id, column_id=column_id, deleted=False
        ).first()

        if cell:
            cell.value = f"mock_response_for_{model}"
            cell.status = CellStatus.PASS.value
            cell.save(update_fields=["value", "status"])

        return {
            "row_id": row_id,
            "column_id": column_id,
            "status": "COMPLETED",
        }
    finally:
        close_old_connections()


# =============================================================================
# TransactionTestCase CASCADE fix (same as agent_playground tests)
# =============================================================================


@pytest.fixture(autouse=True, scope="session")
def _patch_flush_to_cascade():
    """Patch TransactionTestCase to use TRUNCATE ... CASCADE."""
    from django.core.management import call_command
    from django.db import connections
    from django.test.testcases import TransactionTestCase

    original = TransactionTestCase._fixture_teardown

    def _fixture_teardown_with_cascade(self):
        for db_name in self._databases_names(include_mirrors=False):
            inhibit_post_migrate = self.available_apps is not None or (
                self.serialized_rollback
                and hasattr(connections[db_name], "_test_serialized_contents")
            )
            call_command(
                "flush",
                verbosity=0,
                interactive=False,
                database=db_name,
                reset_sequences=False,
                allow_cascade=True,
                inhibit_post_migrate=inhibit_post_migrate,
            )
            if self.serialized_rollback and hasattr(
                connections[db_name], "_test_serialized_contents"
            ):
                connections[db_name].creation.deserialize_db_from_string(
                    connections[db_name]._test_serialized_contents
                )

    TransactionTestCase._fixture_teardown = _fixture_teardown_with_cascade
    yield
    TransactionTestCase._fixture_teardown = original


# =============================================================================
# Temporal Environment
# =============================================================================


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def workflow_environment():
    """Start an in-memory Temporal server for the entire test session."""
    env = await WorkflowEnvironment.start_local()
    yield env
    await env.shutdown()


# =============================================================================
# Mock Runner, Sink, and _process_row_sync Registration
# =============================================================================


@pytest.fixture(autouse=True)
def register_mock_runners():
    """Register llm_prompt mock runner before each test, clear after."""
    clear_runners()
    register_runner("llm_prompt", LLMPromptEchoRunner())
    yield
    clear_runners()


@pytest.fixture(autouse=True)
def register_sinks():
    """Register cell output sink before each test, clear after."""
    clear_sinks()
    register_sink("cell", CellOutputSink())
    yield
    clear_sinks()


@pytest.fixture(autouse=True)
def mock_process_row():
    """Mock _process_row_sync to avoid real LLM calls in prompt flow tests."""
    with patch(
        "tfc.temporal.experiments.activities._process_row_sync",
        side_effect=_mock_process_row_sync,
    ):
        yield


@pytest.fixture(autouse=True)
def cleanup_db_connections():
    """Close stale DB connections after each test."""
    yield
    close_old_connections()


# =============================================================================
# Base DB Fixtures
# =============================================================================


@pytest.fixture
def organization(db):
    return Organization.objects.create(name=f"Test Org {uuid.uuid4().hex[:8]}")


@pytest.fixture
def user(db, organization):
    from accounts.models.organization_membership import OrganizationMembership
    from tfc.constants.levels import Level
    from tfc.constants.roles import OrganizationRoles

    unique = uuid.uuid4().hex[:8]
    user = User.objects.create_user(
        email=f"exp_test_{unique}@futureagi.com",
        password="testpassword123",
        name="Experiment Test User",
        organization=organization,
        organization_role=OrganizationRoles.OWNER,
    )
    # Graph.clean() checks user.can_access_organization(org), which queries
    # OrganizationMembership as the source of truth. Without this row the
    # fixture-built graphs fail validation with
    # "created_by user must belong to the same organization as the graph."
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=organization,
        defaults={
            "role": OrganizationRoles.OWNER,
            "level": Level.OWNER,
            "is_active": True,
        },
    )
    return user


@pytest.fixture
def workspace(db, organization, user):
    return Workspace.objects.create(
        name=f"Test Workspace {uuid.uuid4().hex[:8]}",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=user,
    )


# =============================================================================
# Dataset + Snapshot Fixtures
# =============================================================================


@pytest.fixture
def source_dataset(db, organization, workspace, user):
    """Create a source dataset with 2 columns (query, context) and 3 rows."""
    from model_hub.models.choices import CellStatus, SourceChoices
    from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

    dataset = Dataset.objects.create(
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source="build",
        model_type="GenerativeLLM",
    )

    col_query = Column.objects.create(
        name="query",
        data_type="text",
        source=SourceChoices.OTHERS.value,
        dataset=dataset,
    )
    col_context = Column.objects.create(
        name="context",
        data_type="text",
        source=SourceChoices.OTHERS.value,
        dataset=dataset,
    )

    rows = []
    for i in range(3):
        row = Row.objects.create(dataset=dataset, order=i)
        rows.append(row)
        Cell.objects.create(
            column=col_query,
            row=row,
            dataset=dataset,
            value=f"question_{i}",
            status=CellStatus.PASS.value,
        )
        Cell.objects.create(
            column=col_context,
            row=row,
            dataset=dataset,
            value=f"context_{i}",
            status=CellStatus.PASS.value,
        )

    dataset._test_columns = [col_query, col_context]
    dataset._test_rows = rows
    return dataset


@pytest.fixture
def snapshot_dataset(db, source_dataset):
    """
    Create a snapshot of the source dataset.

    Mimics create_dataset_snapshot from model_hub/services/dataset_snapshot.py.
    Copies columns (preserving source), rows, and cells.
    """
    from model_hub.models.choices import CellStatus, DatasetSourceChoices
    from model_hub.models.develop_dataset import Cell, Column, Dataset, Row

    snap = Dataset.objects.create(
        name=f"[Snapshot] {source_dataset.name}",
        organization=source_dataset.organization,
        workspace=source_dataset.workspace,
        source=DatasetSourceChoices.EXPERIMENT_SNAPSHOT.value,
        model_type=source_dataset.model_type,
    )

    # Copy columns (source preserved — SourceChoices.OTHERS)
    col_map = {}
    for col in source_dataset._test_columns:
        new_col = Column.objects.create(
            name=col.name,
            data_type=col.data_type,
            source=col.source,
            dataset=snap,
            source_id=str(col.id),
        )
        col_map[col.id] = new_col

    # Copy rows
    row_map = {}
    for row in source_dataset._test_rows:
        new_row = Row.objects.create(dataset=snap, order=row.order)
        row_map[row.id] = new_row

    # Copy cells
    cells = Cell.objects.filter(dataset=source_dataset, deleted=False)
    for cell in cells:
        Cell.objects.create(
            column=col_map[cell.column_id],
            row=row_map[cell.row_id],
            dataset=snap,
            value=cell.value,
            status=CellStatus.PASS.value,
        )

    snap._test_columns = list(col_map.values())
    snap._test_rows = list(row_map.values())
    return snap


# =============================================================================
# Experiment Fixtures
# =============================================================================


@pytest.fixture
def experiment(db, source_dataset, snapshot_dataset, user):
    """
    Create a V2 LLM experiment with snapshot dataset.

    Matches ExperimentsTableV2View.post() creation:
    - experiment_type="llm"
    - prompt_config=[] (deprecated, empty)
    - column=first source column (the column being evaluated)
    - snapshot_dataset linked
    """
    from model_hub.models.experiments import ExperimentsTable

    return ExperimentsTable.objects.create(
        name="Test LLM Experiment",
        dataset=source_dataset,
        snapshot_dataset=snapshot_dataset,
        column=source_dataset._test_columns[0],
        user=user,
        status="NotStarted",
        experiment_type="llm",
        prompt_config=[],
    )


@pytest.fixture
def tts_experiment(db, source_dataset, snapshot_dataset, user):
    """Create a V2 TTS experiment."""
    from model_hub.models.experiments import ExperimentsTable

    return ExperimentsTable.objects.create(
        name="Test TTS Experiment",
        dataset=source_dataset,
        snapshot_dataset=snapshot_dataset,
        column=source_dataset._test_columns[0],
        user=user,
        status="NotStarted",
        experiment_type="tts",
        prompt_config=[],
    )


@pytest.fixture
def stt_experiment(db, source_dataset, snapshot_dataset, user):
    """Create a V2 STT experiment."""
    from model_hub.models.experiments import ExperimentsTable

    return ExperimentsTable.objects.create(
        name="Test STT Experiment",
        dataset=source_dataset,
        snapshot_dataset=snapshot_dataset,
        column=source_dataset._test_columns[0],
        user=user,
        status="NotStarted",
        experiment_type="stt",
        prompt_config=[],
    )


@pytest.fixture
def image_experiment(db, source_dataset, snapshot_dataset, user):
    """Create a V2 Image experiment."""
    from model_hub.models.experiments import ExperimentsTable

    return ExperimentsTable.objects.create(
        name="Test Image Experiment",
        dataset=source_dataset,
        snapshot_dataset=snapshot_dataset,
        column=source_dataset._test_columns[0],
        user=user,
        status="NotStarted",
        experiment_type="image",
        prompt_config=[],
    )


# =============================================================================
# PromptTemplate + PromptVersion Fixtures
# =============================================================================


@pytest.fixture
def prompt_template(db, organization, workspace, user):
    """Create a PromptTemplate (used by LLM experiment prompt configs)."""
    from model_hub.models.run_prompt import PromptTemplate

    return PromptTemplate.objects.create(
        name="Test Prompt Template",
        description="A test prompt template",
        organization=organization,
        workspace=workspace,
        variable_names=["query", "context"],
        created_by=user,
    )


@pytest.fixture
def prompt_version(db, prompt_template):
    """
    Create a PromptVersion with locked messages.

    For LLM experiments, messages come from prompt_version.prompt_config_snapshot
    (not from EPC.messages which is null for LLM type).
    """
    from model_hub.models.run_prompt import PromptVersion

    return PromptVersion.objects.create(
        original_template=prompt_template,
        template_version="1",
        prompt_config_snapshot=[
            {
                "role": "system",
                "content": "You are a helpful assistant.",
            },
            {
                "role": "user",
                "content": "Answer: {{query}} with context: {{context}}",
            },
        ],
        variable_names={"query": "query", "context": "context"},
        is_default=True,
    )


# =============================================================================
# ExperimentPromptConfig Fixtures
# =============================================================================


@pytest.fixture
def experiment_prompt_config(db, experiment, prompt_template, prompt_version):
    """
    Create EDT + EPC for LLM prompt flow.

    Matches V2 API creation: one EDT + one EPC per model.
    - prompt_template/prompt_version set (LLM type)
    - messages=None (comes from prompt_version.prompt_config_snapshot)
    - EDT name = "{prompt_name}-{model_name}"
    """
    from model_hub.models.experiments import (
        ExperimentDatasetTable,
        ExperimentPromptConfig,
    )

    edt_name = "Test Prompt Template-gpt-test-model"

    edt = ExperimentDatasetTable.objects.create(
        name=edt_name,
        experiment=experiment,
        status="NotStarted",
    )

    epc = ExperimentPromptConfig.objects.create(
        experiment_dataset=edt,
        prompt_template=prompt_template,
        prompt_version=prompt_version,
        name=edt_name,
        model="gpt-test-model",
        model_display_name="GPT Test",
        model_config={},
        model_params={},
        configuration={},
        output_format="string",
        order=0,
        messages=None,  # LLM: messages come from prompt_version
    )

    return epc


@pytest.fixture
def tts_prompt_config(db, tts_experiment):
    """
    Create EDT + EPC for TTS flow.

    - prompt_template/prompt_version = None (non-LLM)
    - messages stored inline on EPC
    - output_format = "audio"
    - model_config has voice config
    """
    from model_hub.models.experiments import (
        ExperimentDatasetTable,
        ExperimentPromptConfig,
    )

    edt_name = "Standard Voice-openai/tts-1-hd"

    edt = ExperimentDatasetTable.objects.create(
        name=edt_name,
        experiment=tts_experiment,
        status="NotStarted",
    )

    epc = ExperimentPromptConfig.objects.create(
        experiment_dataset=edt,
        prompt_template=None,
        prompt_version=None,
        name=edt_name,
        model="openai/tts-1-hd",
        model_display_name=None,
        model_config={"voice": "alloy"},
        model_params={},
        configuration={},
        output_format="audio",
        order=0,
        messages=[
            {"role": "user", "content": "{{query}}"},
        ],
    )

    return epc


@pytest.fixture
def stt_prompt_config(db, stt_experiment, snapshot_dataset):
    """
    Create EDT + EPC for STT flow.

    - voice_input_column set to a snapshot column (required for STT)
    - messages stored inline
    """
    from model_hub.models.experiments import (
        ExperimentDatasetTable,
        ExperimentPromptConfig,
    )

    edt_name = "Transcribe-openai/whisper-1"

    edt = ExperimentDatasetTable.objects.create(
        name=edt_name,
        experiment=stt_experiment,
        status="NotStarted",
    )

    # STT needs voice_input_column mapped to snapshot column
    voice_col = snapshot_dataset._test_columns[0]

    epc = ExperimentPromptConfig.objects.create(
        experiment_dataset=edt,
        prompt_template=None,
        prompt_version=None,
        name=edt_name,
        model="openai/whisper-1",
        model_display_name=None,
        model_config={},
        model_params={},
        configuration={},
        output_format="string",
        order=0,
        messages=[
            {"role": "user", "content": "Transcribe the audio."},
        ],
        voice_input_column=voice_col,
    )

    return epc


@pytest.fixture
def image_prompt_config(db, image_experiment):
    """
    Create EDT + EPC for Image generation flow.

    - output_format = "image"
    - messages stored inline
    """
    from model_hub.models.experiments import (
        ExperimentDatasetTable,
        ExperimentPromptConfig,
    )

    edt_name = "Generate Image-openai/dall-e-3"

    edt = ExperimentDatasetTable.objects.create(
        name=edt_name,
        experiment=image_experiment,
        status="NotStarted",
    )

    epc = ExperimentPromptConfig.objects.create(
        experiment_dataset=edt,
        prompt_template=None,
        prompt_version=None,
        name=edt_name,
        model="openai/dall-e-3",
        model_display_name=None,
        model_config={},
        model_params={},
        configuration={},
        output_format="image",
        order=0,
        messages=[
            {"role": "user", "content": "Generate image for: {{query}}"},
        ],
    )

    return epc


# =============================================================================
# Graph Fixtures (LLM prompt nodes — for agent flow)
# =============================================================================


@pytest.fixture
def llm_prompt_template(db):
    """Create the llm_prompt NodeTemplate."""
    template, _ = NodeTemplate.no_workspace_objects.get_or_create(
        name="llm_prompt",
        defaults={
            "display_name": "LLM Prompt",
            "description": "Test LLM prompt template",
            "categories": ["testing"],
            "input_definition": [],
            "output_definition": [],
            "input_mode": PortMode.DYNAMIC,
            "output_mode": PortMode.DYNAMIC,
            "config_schema": {},
        },
    )
    return template


@pytest.fixture
def single_node_graph(db, organization, workspace, user, llm_prompt_template):
    """
    Create a graph with a single LLM node:
      Input ports: query
      Output ports: response
    """
    graph = Graph.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        name="Single LLM Graph",
        created_by=user,
    )
    version = GraphVersion.no_workspace_objects.create(
        graph=graph,
        version_number=1,
        status=GraphVersionStatus.ACTIVE,
    )
    node = Node.no_workspace_objects.create(
        graph_version=version,
        node_template=llm_prompt_template,
        type=NodeType.ATOMIC,
        name="LLM Node",
        config={},
    )
    # llm_prompt template is DYNAMIC mode: port `key` must be "custom", and
    # the user-facing name goes in `display_name` (per agent_playground Port
    # validator — see agent_playground/models/port.py).
    Port.no_workspace_objects.create(
        node=node,
        key="custom",
        display_name="query",
        direction=PortDirection.INPUT,
        data_schema={"type": "string"},
    )
    Port.no_workspace_objects.create(
        node=node,
        key="custom",
        display_name="response",
        direction=PortDirection.OUTPUT,
        data_schema={"type": "string"},
    )

    version._test_nodes = [node]
    return version


@pytest.fixture
def two_node_graph(db, organization, workspace, user, llm_prompt_template):
    """
    Create a graph with two LLM nodes in a chain:
      Summarizer(query) -> response -> Reviewer(query) -> response

    Both nodes have template_name="llm_prompt" so both get experiment columns.
    """
    graph = Graph.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        name="Two LLM Graph",
        created_by=user,
    )
    version = GraphVersion.no_workspace_objects.create(
        graph=graph,
        version_number=1,
        status=GraphVersionStatus.ACTIVE,
    )

    # Node 1: Summarizer
    node1 = Node.no_workspace_objects.create(
        graph_version=version,
        node_template=llm_prompt_template,
        type=NodeType.ATOMIC,
        name="Summarizer",
        config={},
    )
    # llm_prompt template is DYNAMIC mode: port `key` must be "custom".
    p1_in = Port.no_workspace_objects.create(
        node=node1,
        key="custom",
        display_name="query",
        direction=PortDirection.INPUT,
        data_schema={"type": "string"},
    )
    p1_out = Port.no_workspace_objects.create(
        node=node1,
        key="custom",
        display_name="response",
        direction=PortDirection.OUTPUT,
        data_schema={"type": "string"},
    )

    # Node 2: Reviewer
    node2 = Node.no_workspace_objects.create(
        graph_version=version,
        node_template=llm_prompt_template,
        type=NodeType.ATOMIC,
        name="Reviewer",
        config={},
    )
    p2_in = Port.no_workspace_objects.create(
        node=node2,
        key="custom",
        display_name="query",
        direction=PortDirection.INPUT,
        data_schema={"type": "string"},
    )
    p2_out = Port.no_workspace_objects.create(
        node=node2,
        key="custom",
        display_name="response",
        direction=PortDirection.OUTPUT,
        data_schema={"type": "string"},
    )

    # NodeConnection at the node level must exist before a port-level Edge
    # can be created (Edge._validate_node_connection_exists).
    NodeConnection.no_workspace_objects.create(
        graph_version=version,
        source_node=node1,
        target_node=node2,
    )
    # Edge: Summarizer.response -> Reviewer.query
    Edge.no_workspace_objects.create(
        graph_version=version,
        source_port=p1_out,
        target_port=p2_in,
    )

    version._test_nodes = [node1, node2]
    return version


# =============================================================================
# Experiment Agent Config Fixtures
# =============================================================================


@pytest.fixture
def experiment_agent_config_single(db, experiment, single_node_graph):
    """
    Create EDT + EAC for single-node agent graph.

    Matches V2 API: one EDT + one EAC per agent entry.
    - graph/graph_version FKs set
    - EDT linked to experiment
    """
    from model_hub.models.experiments import (
        ExperimentAgentConfig,
        ExperimentDatasetTable,
    )

    edt = ExperimentDatasetTable.objects.create(
        name="Agent: Single LLM",
        experiment=experiment,
        status="NotStarted",
    )

    eac = ExperimentAgentConfig.objects.create(
        experiment_dataset=edt,
        graph=single_node_graph.graph,
        graph_version=single_node_graph,
        name="Agent: Single LLM",
        order=0,
    )

    return eac


@pytest.fixture
def experiment_agent_config_two_node(db, experiment, two_node_graph):
    """Create EDT + EAC for two-node agent graph."""
    from model_hub.models.experiments import (
        ExperimentAgentConfig,
        ExperimentDatasetTable,
    )

    edt = ExperimentDatasetTable.objects.create(
        name="Agent: Two LLM Chain",
        experiment=experiment,
        status="NotStarted",
    )

    eac = ExperimentAgentConfig.objects.create(
        experiment_dataset=edt,
        graph=two_node_graph.graph,
        graph_version=two_node_graph,
        name="Agent: Two LLM Chain",
        order=0,
    )

    return eac
