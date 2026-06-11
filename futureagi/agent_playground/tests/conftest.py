"""
Conftest for agent_playground app tests.
Provides fixtures specific to agent_playground models and test data.
"""

import pytest
from django.conf import settings as django_settings
from rest_framework.test import APIClient
from rest_framework.views import APIView

from accounts.models.organization import Organization
from accounts.models.organization_membership import OrganizationMembership
from accounts.models.user import User
from accounts.models.workspace import Workspace
from agent_playground.models.choices import (
    GraphExecutionStatus,
    GraphVersionStatus,
    NodeExecutionStatus,
    NodeType,
    PortDirection,
    PortMode,
)
from agent_playground.models.edge import Edge
from agent_playground.models.execution_data import ExecutionData
from agent_playground.models.graph import Graph
from agent_playground.models.graph_dataset import GraphDataset
from agent_playground.models.graph_execution import GraphExecution
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.models.node_execution import NodeExecution
from agent_playground.models.node_template import NodeTemplate
from agent_playground.models.port import Port
from agent_playground.models.prompt_template_node import PromptTemplateNode

# Override root conftest fixtures to avoid setting CURRENT_WORKSPACE,
# which causes FieldError on models without a workspace field (GraphVersion,
# Node, Port, Edge, etc.) when BaseModelManager tries to filter by workspace.

# Store original APIView.initial for patching
_original_apiview_initial = APIView.initial
_REQUEST_INJECTION_ACTIVE = False


def _initial_with_context(view_self, request, *args, **view_kwargs):
    workspace = None
    organization = None

    ws_header = request.META.get("HTTP_X_WORKSPACE_ID")
    org_header = request.META.get("HTTP_X_ORGANIZATION_ID")
    if ws_header:
        workspace = (
            Workspace.no_workspace_objects.select_related("organization")
            .filter(id=ws_header, is_active=True)
            .first()
        )
        if workspace:
            organization = workspace.organization
    elif org_header:
        organization = Organization.objects.filter(id=org_header).first()

    request.workspace = workspace
    request.organization = organization
    if organization:
        from tfc.middleware.workspace_context import set_workspace_context

        set_workspace_context(
            workspace=workspace,
            organization=organization,
            user=getattr(request, "user", None),
        )
    return _original_apiview_initial(view_self, request, *args, **view_kwargs)


class WorkspaceAwareAPIClient(APIClient):
    """Custom APIClient that injects request.workspace and request.organization for tests."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._workspace = None
        self._organization = None
        self._patcher = None

    def set_workspace(self, workspace):
        """Set the workspace for subsequent requests."""
        self._workspace = workspace
        if workspace:
            self._organization = workspace.organization
            self.credentials(
                HTTP_X_WORKSPACE_ID=str(workspace.id),
                HTTP_X_ORGANIZATION_ID=str(workspace.organization_id),
            )
            self._start_request_injection()

    def set_organization(self, organization):
        """Set the organization for subsequent requests."""
        self._organization = organization
        if organization:
            self.credentials(HTTP_X_ORGANIZATION_ID=str(organization.id))
        self._start_request_injection()

    def _start_request_injection(self):
        """Patch APIView.initial to inject workspace and organization into requests."""
        global _REQUEST_INJECTION_ACTIVE
        if (
            _REQUEST_INJECTION_ACTIVE
            and APIView.__dict__.get("initial") is _initial_with_context
        ):
            return
        APIView.initial = _initial_with_context
        _REQUEST_INJECTION_ACTIVE = True

    def _request_with_clean_context(self, method, *args, **kwargs):
        from tfc.middleware.workspace_context import clear_workspace_context

        self._start_request_injection()
        if self._workspace is not None:
            self.credentials(
                HTTP_X_WORKSPACE_ID=str(self._workspace.id),
                HTTP_X_ORGANIZATION_ID=str(self._workspace.organization_id),
            )
        elif self._organization is not None:
            self.credentials(HTTP_X_ORGANIZATION_ID=str(self._organization.id))

        clear_workspace_context()
        try:
            return method(*args, **kwargs)
        finally:
            clear_workspace_context()

    def get(self, *args, **kwargs):
        return self._request_with_clean_context(super().get, *args, **kwargs)

    def post(self, *args, **kwargs):
        return self._request_with_clean_context(super().post, *args, **kwargs)

    def put(self, *args, **kwargs):
        return self._request_with_clean_context(super().put, *args, **kwargs)

    def patch(self, *args, **kwargs):
        return self._request_with_clean_context(super().patch, *args, **kwargs)

    def delete(self, *args, **kwargs):
        return self._request_with_clean_context(super().delete, *args, **kwargs)

    def stop_workspace_injection(self):
        """Stop the workspace injection patch."""
        global _REQUEST_INJECTION_ACTIVE
        if APIView.__dict__.get("initial") is _initial_with_context:
            APIView.initial = _original_apiview_initial
            _REQUEST_INJECTION_ACTIVE = False
        self._patcher = None


@pytest.fixture
def api_client():
    """Create an API client."""
    client = WorkspaceAwareAPIClient()
    yield client
    client.stop_workspace_injection()


@pytest.fixture
def authenticated_client(api_client, user, workspace):
    """Create an authenticated API client with workspace context."""
    api_client.force_authenticate(user=user)
    api_client.set_workspace(workspace)
    yield api_client
    api_client.stop_workspace_injection()


@pytest.fixture
def organization(db):
    """Create a test organization (no workspace context set)."""
    return Organization.objects.create(name="Test Organization")


@pytest.fixture
def user(db, organization):
    """Create a test user with organization membership."""
    user = User.objects.create_user(
        email="test@futureagi.com",
        password="testpassword123",
        name="Test User",
        organization=organization,
    )
    OrganizationMembership.no_workspace_objects.get_or_create(
        user=user,
        organization=organization,
        defaults={"is_active": True},
    )
    return user


@pytest.fixture
def workspace(db, organization, user):
    """Create a test workspace without setting CURRENT_WORKSPACE."""
    return Workspace.objects.create(
        name="Test Workspace",
        organization=organization,
        is_default=True,
        is_active=True,
        created_by=user,
    )


# =============================================================================
# Core fixtures: Graph, GraphVersion
# =============================================================================


@pytest.fixture
def graph(db, organization, workspace, user):
    """Create a basic Graph."""
    return Graph.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        name="Test Graph",
        description="A test graph",
        created_by=user,
    )


@pytest.fixture
def referenced_graph(db, organization, workspace, user):
    """Create a Graph to be referenced by subgraph nodes."""
    return Graph.no_workspace_objects.create(
        organization=organization,
        workspace=workspace,
        name="Test Referenced Graph",
        description="A reusable referenced graph",
        created_by=user,
    )


@pytest.fixture
def graph_version(db, graph):
    """Create a GraphVersion with draft status."""
    return GraphVersion.no_workspace_objects.create(
        graph=graph,
        version_number=1,
        status=GraphVersionStatus.DRAFT,
        tags=[],
    )


@pytest.fixture
def active_graph_version(db, graph):
    """Create a GraphVersion with active status."""
    return GraphVersion.no_workspace_objects.create(
        graph=graph,
        version_number=2,
        status=GraphVersionStatus.ACTIVE,
        tags=["production"],
    )


@pytest.fixture
def referenced_graph_version(db, referenced_graph):
    """Create a draft GraphVersion for the referenced graph."""
    return GraphVersion.no_workspace_objects.create(
        graph=referenced_graph,
        version_number=1,
        status=GraphVersionStatus.DRAFT,
        tags=[],
    )


@pytest.fixture
def active_referenced_graph_version(db, referenced_graph):
    """Create an active GraphVersion for the referenced graph."""
    return GraphVersion.no_workspace_objects.create(
        graph=referenced_graph,
        version_number=2,
        status=GraphVersionStatus.ACTIVE,
        tags=["production"],
    )


# =============================================================================
# Template graph fixtures
# =============================================================================


@pytest.fixture
def template_graph(db):
    """Create a template Graph (is_template=True, no org/workspace/user)."""
    return Graph.no_workspace_objects.create(
        organization=None,
        workspace=None,
        name="Test Template Graph",
        description="A system template graph",
        is_template=True,
        created_by=None,
    )


@pytest.fixture
def active_template_graph_version(db, template_graph):
    """Create an active GraphVersion for a template graph."""
    return GraphVersion.no_workspace_objects.create(
        graph=template_graph,
        version_number=1,
        status=GraphVersionStatus.ACTIVE,
        tags=[],
    )


# =============================================================================
# NodeTemplate fixtures
# =============================================================================


@pytest.fixture
def node_template(db):
    """Create a basic NodeTemplate with valid schema."""
    return NodeTemplate.no_workspace_objects.create(
        name="test_template",
        display_name="Test Template",
        description="A test node template",
        categories=["testing", "utility"],
        input_definition=[
            {"key": "input1", "data_schema": {"type": "string"}},
        ],
        output_definition=[
            {"key": "output1", "data_schema": {"type": "string"}},
        ],
        input_mode=PortMode.STRICT,
        output_mode=PortMode.STRICT,
        config_schema={
            "type": "object",
            "properties": {
                "param1": {"type": "string"},
            },
        },
    )


@pytest.fixture
def dynamic_node_template(db):
    """Create a NodeTemplate with DYNAMIC port modes."""
    return NodeTemplate.no_workspace_objects.create(
        name="dynamic_template",
        display_name="Dynamic Template",
        description="A template with dynamic ports",
        categories=["dynamic"],
        input_definition=[],
        output_definition=[],
        input_mode=PortMode.DYNAMIC,
        output_mode=PortMode.DYNAMIC,
        config_schema={},
    )


@pytest.fixture
def extensible_node_template(db):
    """Create a NodeTemplate with EXTENSIBLE port modes."""
    return NodeTemplate.no_workspace_objects.create(
        name="extensible_template",
        display_name="Extensible Template",
        description="A template with extensible ports",
        categories=["extensible"],
        input_definition=[
            {"key": "required_input", "data_schema": {"type": "string"}},
        ],
        output_definition=[
            {"key": "required_output", "data_schema": {"type": "string"}},
        ],
        input_mode=PortMode.EXTENSIBLE,
        output_mode=PortMode.EXTENSIBLE,
        config_schema={},
    )


# =============================================================================
# Node fixtures
# =============================================================================


@pytest.fixture
def node(db, graph_version, node_template):
    """Create a basic atomic Node."""
    return Node.no_workspace_objects.create(
        graph_version=graph_version,
        node_template=node_template,
        type=NodeType.ATOMIC,
        name="Test Node",
        config={},
        position={"x": 100, "y": 100},
    )


@pytest.fixture
def node_in_active_version(db, active_graph_version, node_template):
    """Create an atomic Node in an active graph version."""
    return Node.no_workspace_objects.create(
        graph_version=active_graph_version,
        node_template=node_template,
        type=NodeType.ATOMIC,
        name="Active Version Node",
        config={},
        position={"x": 100, "y": 100},
    )


@pytest.fixture
def subgraph_node(db, graph_version, active_referenced_graph_version):
    """Create a subgraph Node that references another graph."""
    return Node.no_workspace_objects.create(
        graph_version=graph_version,
        ref_graph_version=active_referenced_graph_version,
        type=NodeType.SUBGRAPH,
        name="Subgraph Node",
        config={},
        position={"x": 200, "y": 100},
    )


@pytest.fixture
def dynamic_node(db, graph_version, dynamic_node_template):
    """Create an atomic Node with a DYNAMIC template (any port key allowed)."""
    return Node.no_workspace_objects.create(
        graph_version=graph_version,
        node_template=dynamic_node_template,
        type=NodeType.ATOMIC,
        name="Dynamic Node",
        config={},
        position={"x": 400, "y": 100},
    )


# =============================================================================
# Port fixtures
# =============================================================================


@pytest.fixture
def input_port(db, node):
    """Create an input Port."""
    return Port.no_workspace_objects.create(
        node=node,
        key="input1",
        display_name="input1",
        direction=PortDirection.INPUT,
        data_schema={"type": "string"},
        required=True,
    )


@pytest.fixture
def output_port(db, node):
    """Create an output Port."""
    return Port.no_workspace_objects.create(
        node=node,
        key="output1",
        display_name="output1",
        direction=PortDirection.OUTPUT,
        data_schema={"type": "string"},
        required=True,
    )


@pytest.fixture
def second_node(db, graph_version, node_template):
    """Create a second node for edge testing."""
    return Node.no_workspace_objects.create(
        graph_version=graph_version,
        node_template=node_template,
        type=NodeType.ATOMIC,
        name="Second Node",
        config={},
        position={"x": 300, "y": 100},
    )


@pytest.fixture
def second_node_input_port(db, second_node):
    """Create an input port on the second node."""
    return Port.no_workspace_objects.create(
        node=second_node,
        key="input1",
        display_name="input1",
        direction=PortDirection.INPUT,
        data_schema={"type": "string"},
        required=True,
    )


@pytest.fixture
def second_node_output_port(db, second_node):
    """Create an output port on the second node."""
    return Port.no_workspace_objects.create(
        node=second_node,
        key="output1",
        display_name="output1",
        direction=PortDirection.OUTPUT,
        data_schema={"type": "string"},
        required=True,
    )


@pytest.fixture
def third_node(db, graph_version, node_template):
    """Create a third node for multi-node cycle tests."""
    return Node.no_workspace_objects.create(
        graph_version=graph_version,
        node_template=node_template,
        type=NodeType.ATOMIC,
        name="Third Node",
        config={},
        position={"x": 500, "y": 100},
    )


@pytest.fixture
def third_node_input_port(db, third_node):
    """Create an input port on the third node."""
    return Port.no_workspace_objects.create(
        node=third_node,
        key="input1",
        display_name="input1",
        direction=PortDirection.INPUT,
        data_schema={"type": "string"},
        required=True,
    )


@pytest.fixture
def third_node_output_port(db, third_node):
    """Create an output port on the third node."""
    return Port.no_workspace_objects.create(
        node=third_node,
        key="output1",
        display_name="output1",
        direction=PortDirection.OUTPUT,
        data_schema={"type": "string"},
        required=True,
    )


# =============================================================================
# Edge fixtures
# =============================================================================


@pytest.fixture
def edge(db, graph_version, output_port, second_node_input_port, node_connection):
    """Create a valid edge connecting two nodes (requires node_connection)."""
    return Edge.no_workspace_objects.create(
        graph_version=graph_version,
        source_port=output_port,
        target_port=second_node_input_port,
    )


# =============================================================================
# Execution fixtures
# =============================================================================


@pytest.fixture
def graph_execution(db, active_graph_version):
    """Create a GraphExecution."""
    return GraphExecution.no_workspace_objects.create(
        graph_version=active_graph_version,
        status=GraphExecutionStatus.PENDING,
        input_payload={"key": "value"},
    )


@pytest.fixture
def running_graph_execution(db, active_graph_version):
    """Create a running GraphExecution."""
    return GraphExecution.no_workspace_objects.create(
        graph_version=active_graph_version,
        status=GraphExecutionStatus.RUNNING,
        input_payload={"key": "value"},
    )


@pytest.fixture
def node_execution(db, graph_execution, node_in_active_version):
    """Create a NodeExecution."""
    return NodeExecution.no_workspace_objects.create(
        graph_execution=graph_execution,
        node=node_in_active_version,
        status=NodeExecutionStatus.PENDING,
    )


@pytest.fixture
def execution_data(db, node_execution):
    """Create ExecutionData for a node execution."""
    port = Port.no_workspace_objects.create(
        node=node_execution.node,
        key="input1",
        display_name="input1",
        direction=PortDirection.INPUT,
        data_schema={"type": "string"},
        required=True,
    )
    return ExecutionData.no_workspace_objects.create(
        node_execution=node_execution,
        port=port,
        payload="test data",
    )


# =============================================================================
# Null-workspace fixture variants
# =============================================================================


@pytest.fixture
def graph_without_workspace(db, organization, user):
    """Create a Graph with workspace=None (only organization required)."""
    return Graph.no_workspace_objects.create(
        organization=organization,
        workspace=None,
        name="No-Workspace Graph",
        description="Graph without workspace",
        created_by=user,
    )


@pytest.fixture
def graph_version_no_ws(db, graph_without_workspace):
    """Create a GraphVersion on graph_without_workspace."""
    return GraphVersion.no_workspace_objects.create(
        graph=graph_without_workspace,
        version_number=1,
        status=GraphVersionStatus.DRAFT,
        tags=[],
    )


@pytest.fixture
def node_a_no_ws(db, graph_version_no_ws, node_template):
    """First node in graph_version_no_ws."""
    return Node.no_workspace_objects.create(
        graph_version=graph_version_no_ws,
        node_template=node_template,
        type=NodeType.ATOMIC,
        name="Node A (no ws)",
        config={},
        position={"x": 100, "y": 100},
    )


@pytest.fixture
def node_a_no_ws_output(db, node_a_no_ws):
    """Output port on node_a_no_ws."""
    return Port.no_workspace_objects.create(
        node=node_a_no_ws,
        key="output1",
        display_name="output1",
        direction=PortDirection.OUTPUT,
        data_schema={"type": "string"},
        required=True,
    )


@pytest.fixture
def node_a_no_ws_input(db, node_a_no_ws):
    """Input port on node_a_no_ws."""
    return Port.no_workspace_objects.create(
        node=node_a_no_ws,
        key="input1",
        display_name="input1",
        direction=PortDirection.INPUT,
        data_schema={"type": "string"},
        required=True,
    )


@pytest.fixture
def node_b_no_ws(db, graph_version_no_ws, node_template):
    """Second node in graph_version_no_ws."""
    return Node.no_workspace_objects.create(
        graph_version=graph_version_no_ws,
        node_template=node_template,
        type=NodeType.ATOMIC,
        name="Node B (no ws)",
        config={},
        position={"x": 300, "y": 100},
    )


@pytest.fixture
def node_b_no_ws_input(db, node_b_no_ws):
    """Input port on node_b_no_ws."""
    return Port.no_workspace_objects.create(
        node=node_b_no_ws,
        key="input1",
        display_name="input1",
        direction=PortDirection.INPUT,
        data_schema={"type": "string"},
        required=True,
    )


@pytest.fixture
def node_b_no_ws_output(db, node_b_no_ws):
    """Output port on node_b_no_ws."""
    return Port.no_workspace_objects.create(
        node=node_b_no_ws,
        key="output1",
        display_name="output1",
        direction=PortDirection.OUTPUT,
        data_schema={"type": "string"},
        required=True,
    )


# =============================================================================
# Dataset fixtures
# =============================================================================


@pytest.fixture
def dataset(db, organization, workspace, user):
    """Create a Dataset linked to the test organization."""
    from model_hub.models.develop_dataset import Dataset

    return Dataset.no_workspace_objects.create(
        name="Test Dataset",
        organization=organization,
        workspace=workspace,
        user=user,
        source="graph",
        model_type="GenerativeLLM",
    )


@pytest.fixture
def graph_dataset(db, graph, dataset):
    """Link a Graph to a Dataset via GraphDataset."""
    return GraphDataset.no_workspace_objects.create(
        graph=graph,
        dataset=dataset,
    )


@pytest.fixture
def dataset_columns(db, dataset):
    """Create two columns in the dataset."""
    from model_hub.models.develop_dataset import Column

    col1 = Column.no_workspace_objects.create(
        name="input_text",
        data_type="text",
        dataset=dataset,
        source="run_prompt",
    )
    col2 = Column.no_workspace_objects.create(
        name="context",
        data_type="text",
        dataset=dataset,
        source="run_prompt",
    )
    return [col1, col2]


@pytest.fixture
def dataset_row_with_cells(db, dataset, dataset_columns):
    """Create a Row with a Cell for each column."""
    from model_hub.models.develop_dataset import Cell, Row

    row = Row.no_workspace_objects.create(dataset=dataset, order=1)
    cells = []
    for col in dataset_columns:
        cell = Cell.no_workspace_objects.create(
            dataset=dataset,
            column=col,
            row=row,
            value=f"value for {col.name}",
        )
        cells.append(cell)
    return row, cells


# =============================================================================
# PromptTemplate / PromptVersion / PromptTemplateNode fixtures
# =============================================================================


@pytest.fixture
def prompt_template(db, organization, workspace):
    """Create a PromptTemplate from model_hub."""
    from model_hub.models.run_prompt import PromptTemplate

    return PromptTemplate.no_workspace_objects.create(
        name="Test Prompt Template",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def prompt_version(db, prompt_template):
    """Create a PromptVersion linked to prompt_template."""
    from model_hub.models.run_prompt import PromptVersion

    return PromptVersion.no_workspace_objects.create(
        original_template=prompt_template,
        template_version="v1",
        prompt_config_snapshot={"messages": [{"role": "user", "content": "Hello"}]},
    )


@pytest.fixture
def other_prompt_template(db, organization, workspace):
    """Create a second PromptTemplate (for mismatch tests)."""
    from model_hub.models.run_prompt import PromptTemplate

    return PromptTemplate.no_workspace_objects.create(
        name="Other Prompt Template",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def other_prompt_version(db, other_prompt_template):
    """Create a PromptVersion for other_prompt_template."""
    from model_hub.models.run_prompt import PromptVersion

    return PromptVersion.no_workspace_objects.create(
        original_template=other_prompt_template,
        template_version="v1",
        prompt_config_snapshot=[],
    )


@pytest.fixture
def prompt_template_node(db, node, prompt_template, prompt_version):
    """Create a PromptTemplateNode linking node to prompt template/version."""
    return PromptTemplateNode.no_workspace_objects.create(
        node=node,
        prompt_template=prompt_template,
        prompt_version=prompt_version,
    )


# =============================================================================
# Additional fixtures for granular CRUD tests
# =============================================================================


@pytest.fixture
def node_connection(db, graph_version, node, second_node):
    """Create a NodeConnection between node and second_node."""
    from agent_playground.models.node_connection import NodeConnection

    return NodeConnection.no_workspace_objects.create(
        graph_version=graph_version,
        source_node=node,
        target_node=second_node,
    )


@pytest.fixture
def llm_node_template(db):
    """LLM prompt node template matching production llm_prompt definition."""
    return NodeTemplate.no_workspace_objects.create(
        name="llm_prompt",
        display_name="LLM Prompt",
        description="LLM prompt node",
        categories=["llm"],
        input_definition=[],
        output_definition=[
            {
                "key": "response",
                "data_schema": {},
                "schema_source": "prompt_version",
            }
        ],
        input_mode=PortMode.DYNAMIC,
        output_mode=PortMode.STRICT,
        config_schema={},
    )


@pytest.fixture
def draft_prompt_version(db, prompt_template):
    """A draft PromptVersion."""
    from model_hub.models.run_prompt import PromptVersion

    return PromptVersion.no_workspace_objects.create(
        original_template=prompt_template,
        template_version="v2",
        prompt_config_snapshot={
            "messages": [{"role": "user", "content": "test"}],
        },
        is_draft=True,
    )
