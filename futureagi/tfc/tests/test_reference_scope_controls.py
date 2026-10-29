"""Workspace controls for referenced resources and integration endpoint validation."""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.exceptions import ValidationError

from accounts.models.workspace import Workspace
from agent_playground.models.choices import GraphVersionStatus, NodeType
from agent_playground.models.graph import Graph
from agent_playground.models.graph_version import GraphVersion
from agent_playground.models.node import Node
from agent_playground.models.port import Port
from model_hub.models.develop_dataset import KnowledgeBaseFile
from simulate.models.persona import Persona
from tfc.middleware.workspace_context import clear_workspace_context
from tfc.utils.tests.two_tenants import make_tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def scopes(settings):
    settings.FUTURE_AGI_LICENSE_URL = "http://127.0.0.1:9"
    a, b = make_tenant("A"), make_tenant("B")
    workspace = Workspace.no_workspace_objects.create(
        organization=a.organization,
        name="Second",
        is_default=False,
        is_active=True,
        created_by=a.user,
    )
    return (
        a,
        SimpleNamespace(organization=a.organization, workspace=workspace, user=a.user),
        b,
    )


@pytest.mark.parametrize("other_index", [1, 2])
def test_persona_field_follows_request(scopes, other_index):
    from simulate.serializers.requests.scenarios import ScenarioCreateRequestSerializer

    a, other = scopes[0], scopes[other_index]
    rows = []
    for scope in (a, other):
        clear_workspace_context()
        persona = Persona.no_workspace_objects.create(
            organization=scope.organization,
            workspace=scope.workspace,
            name="Persona",
            persona_type=Persona.PersonaType.WORKSPACE,
        )
        rows.append(persona)
    # Partial validation uses the public serializer pipeline, including field
    # validators, without depending on a new private validator's existence.
    for persona, allowed in ((rows[0], True), (rows[1], False)):
        serializer = ScenarioCreateRequestSerializer(
            data={"personas": [str(persona.pk)]}, partial=True,
            context={"request": a},
        )
        serializer.is_valid()
        if allowed:
            assert "personas" not in serializer.errors
        else:
            assert "Persona not found or not accessible." in str(serializer.errors.get("personas"))


def graph_version(scope):
    clear_workspace_context()
    graph = Graph.no_workspace_objects.create(
        organization=scope.organization,
        workspace=scope.workspace,
        created_by=scope.user,
        name="Graph",
    )
    return GraphVersion.no_workspace_objects.create(
        graph=graph, version_number=1, status=GraphVersionStatus.ACTIVE, tags=[]
    )


@pytest.mark.parametrize("other_index", [1, 2])
def test_graph_reference_version_and_port_scope(scopes, other_index):
    from agent_playground.utils.version_content import create_node, create_port

    a = scopes[0]
    own, other = graph_version(a), graph_version(scopes[other_index])
    ports = []
    for version in (own, other):
        node = Node(graph_version=version, type=NodeType.SUBGRAPH, name="Node")
        node.save(skip_validation=True)
        port = Port(node=node, key="result", display_name="Output", direction="output")
        port.save(skip_validation=True)
        ports.append(port)
    destination = graph_version(a)
    def add_node(ref):
        return create_node(destination, {
            "id": uuid.uuid4(), "type": NodeType.SUBGRAPH, "name": f"Reference {uuid.uuid4()}",
            "ref_graph_version_id": str(ref.pk),
        }, skip_validation=True)
    own_node = add_node(own)
    assert own_node.ref_graph_version_id == own.pk
    def add_port(ref):
        return create_port(own_node, {
            "key": f"out_{uuid.uuid4().hex[:6]}", "display_name": "Output",
            "direction": "output", "ref_port_id": str(ref.pk),
        }, skip_validation=True)
    assert add_port(ports[0]).ref_port_id == ports[0].pk
    with pytest.raises(ValidationError):
        add_node(other)
    with pytest.raises(ValidationError):
        add_port(ports[1])


@pytest.mark.parametrize("other_index", [1, 2])
def test_kb_search_validates_before_embedding_work(scopes, other_index):
    from ai_tools.base import ToolContext
    from ai_tools.tools.web.kb_search import KBSearchInput, KBSearchTool

    a = scopes[0]
    ids = []
    for scope in (a, scopes[other_index]):
        clear_workspace_context()
        ids.append(
            KnowledgeBaseFile.no_workspace_objects.create(
                organization=scope.organization,
                workspace=scope.workspace,
                name="Reference",
            ).pk
        )
    context = ToolContext(
        user=a.user, organization=a.organization, workspace=a.workspace
    )
    with patch(
        "agentic_eval.core.embeddings.embedding_manager.EmbeddingManager"
    ) as manager:
        manager.return_value.retrieve_rag_based_examples.return_value = []
        own = KBSearchTool().execute(
            KBSearchInput(query="query", kb_id=str(ids[0])), context
        )
        assert not own.is_error
        manager.reset_mock()
        for kb_id in (ids[1], uuid.uuid4()):
            refused = KBSearchTool().execute(
                KBSearchInput(query="query", kb_id=str(kb_id)), context
            )
            assert refused.is_error and refused.error_code == "NOT_FOUND"
        manager.assert_not_called()


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1", "http://localhost", "http://[::1]", "http://169.254.169.254"],
)
def test_integration_endpoint_validation_precedes_service_calls(url, scopes):
    from integrations.views.integration_connection import IntegrationConnectionViewSet

    a = scopes[0]
    request = SimpleNamespace(
        validated_data={"host_url": url, "platform": "langfuse",
                        "public_key": "synthetic", "secret_key": "synthetic"},
        organization=a.organization, workspace=a.workspace, user=a.user,
    )
    handler = IntegrationConnectionViewSet.create
    while hasattr(handler, "__wrapped__"):
        handler = handler.__wrapped__
    with (
        patch("integrations.views.integration_connection.ensure_services_loaded"),
        patch("integrations.views.integration_connection.get_integration_service") as service,
    ):
        service.return_value.validate_credentials.return_value = {
            "valid": False, "error": "Invalid credentials.",
        }
        response = handler(IntegrationConnectionViewSet(), request)
        assert response.status_code == 400
        assert "This host URL cannot be used for an integration." in str(response.data)
        service.assert_not_called()
        request.validated_data["host_url"] = "https://integration.example.com"
        control = handler(IntegrationConnectionViewSet(), request)
        assert control.status_code == 400 and "Invalid credentials." in str(control.data)
        service.return_value.validate_credentials.assert_called_once()


def test_integration_host_change_revalidates_stored_credentials(scopes):
    from integrations.views.integration_connection import IntegrationConnectionViewSet

    a = scopes[0]
    instance = MagicMock(
        platform="langfuse",
        host_url="https://before.example.com",
        ca_certificate=None,
        encrypted_credentials=b"fixture",
        status="active",
    )
    request = SimpleNamespace(
        validated_data={"host_url": "https://after.example.com"},
        organization=a.organization,
        workspace=a.workspace,
        user=a.user,
    )
    view = IntegrationConnectionViewSet()
    view.get_object = MagicMock(return_value=instance)
    # Exercise the endpoint body with already-validated request data; no network calls.
    handler = IntegrationConnectionViewSet.partial_update
    while hasattr(handler, "__wrapped__"):
        handler = handler.__wrapped__
    with (
        patch(
            "integrations.views.integration_connection.CredentialManager"
        ) as credentials,
        patch(
            "integrations.views.integration_connection.get_integration_service"
        ) as service,
        patch("integrations.views.integration_connection.ensure_services_loaded"),
    ):
        credentials.decrypt.return_value = {
            "public_key": "synthetic-public",
            "secret_key": "synthetic-secret",
        }
        service.return_value.validate_credentials.return_value = {
            "valid": False,
            "error": "Invalid credentials.",
        }
        response = handler(view, request)
    assert response.status_code == 400
    service.return_value.validate_credentials.assert_called_once()
    instance.save.assert_not_called()
