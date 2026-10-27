"""
Tests for the TraceToGraphView API endpoint.

POST /agent-playground/graphs/from-trace/
"""

import uuid
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from accounts.models.workspace import Workspace
from agent_playground.models.graph import Graph
from tracer.models.observation_span import ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace


@pytest.fixture
def project(db, organization, workspace):
    from model_hub.models.ai_model import AIModel

    return Project.objects.create(
        name="Test Project",
        organization=organization,
        workspace=workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )


@pytest.fixture
def trace_with_llm_spans(db, project):
    """Create a trace with two LLM spans in a parent-child relationship."""
    trace = Trace.no_workspace_objects.create(
        project=project,
        name="Agent Trace",
        input={"prompt": "Hello"},
        output={"response": "World"},
    )
    now = timezone.now()

    ObservationSpan.no_workspace_objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name="First Call",
        observation_type="llm",
        start_time=now,
        end_time=now + timedelta(seconds=1),
        input={"messages": [{"role": "user", "content": "Hello"}]},
        output={"choices": [{"message": {"content": "Hi!", "role": "assistant"}}]},
        model="gpt-4",
        model_parameters={"temperature": 0.7},
        status="OK",
        span_attributes={},
    )

    return trace


@pytest.fixture
def trace_no_llm(db, project):
    """Create a trace with only non-LLM spans."""
    trace = Trace.no_workspace_objects.create(
        project=project,
        name="Chain-only Trace",
        input={},
        output={},
    )
    now = timezone.now()

    ObservationSpan.no_workspace_objects.create(
        id=f"span_{uuid.uuid4().hex[:16]}",
        project=project,
        trace=trace,
        name="Agent",
        observation_type="chain",
        start_time=now,
        end_time=now + timedelta(seconds=1),
        input={},
        output={},
        status="OK",
        span_attributes={},
    )

    return trace


@pytest.fixture
def other_organization(db):
    from accounts.models.organization import Organization

    return Organization.objects.create(name="Other Org")


@pytest.fixture
def trace_other_org(db, other_organization):
    """Create a trace belonging to a different organization."""
    from model_hub.models.ai_model import AIModel

    other_project = Project.objects.create(
        name="Other Project",
        organization=other_organization,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type="observe",
    )
    return Trace.no_workspace_objects.create(
        project=other_project,
        name="Other Trace",
        input={},
        output={},
    )


@pytest.mark.django_db
class TestTraceToGraphAPI:
    URL = reverse("graph-from-trace")

    def test_create_graph_from_trace(
        self, authenticated_client, trace_with_llm_spans, llm_node_template
    ):
        response = authenticated_client.post(
            self.URL,
            data={"trace_id": str(trace_with_llm_spans.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        result = response.data["result"]
        assert "graph_id" in result
        assert "version_id" in result

        # Verify graph was actually created
        from agent_playground.models.graph import Graph

        graph = Graph.no_workspace_objects.get(id=result["graph_id"])
        assert "Iterate" in graph.name

    def test_missing_trace_id(self, authenticated_client):
        response = authenticated_client.post(self.URL, data={}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_nonexistent_trace(self, authenticated_client):
        response = authenticated_client.post(
            self.URL,
            data={"trace_id": str(uuid.uuid4())},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_trace_wrong_organization(self, authenticated_client, trace_other_org):
        """Should return 404 for traces belonging to a different org."""
        response = authenticated_client.post(
            self.URL,
            data={"trace_id": str(trace_other_org.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_trace_same_org_other_workspace_returns_404(
        self, authenticated_client, organization, user, llm_node_template
    ):
        """Should return 404 for same-org traces outside the active workspace."""
        from model_hub.models.ai_model import AIModel

        other_workspace = Workspace.no_workspace_objects.create(
            name="Trace Import Hidden Workspace",
            organization=organization,
            is_default=False,
            is_active=True,
            created_by=user,
        )
        hidden_project = Project.objects.create(
            name="Hidden Trace Import Project",
            organization=organization,
            workspace=other_workspace,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
        )
        hidden_trace = Trace.no_workspace_objects.create(
            project=hidden_project,
            name="Hidden Agent Trace",
            input={"prompt": "hidden"},
            output={"response": "hidden"},
        )
        now = timezone.now()
        ObservationSpan.no_workspace_objects.create(
            id=f"span_{uuid.uuid4().hex[:16]}",
            project=hidden_project,
            trace=hidden_trace,
            name="Hidden LLM",
            observation_type="llm",
            start_time=now,
            end_time=now + timedelta(seconds=1),
            input={"messages": [{"role": "user", "content": "Hidden"}]},
            output={"choices": [{"message": {"content": "Nope"}}]},
            model="gpt-4",
            model_parameters={},
            status="OK",
            span_attributes={},
        )

        response = authenticated_client.post(
            self.URL,
            data={"trace_id": str(hidden_trace.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert not Graph.no_workspace_objects.filter(
            description=f"Created from trace {hidden_trace.id}"
        ).exists()

    def test_trace_no_llm_spans(
        self, authenticated_client, trace_no_llm, llm_node_template
    ):
        response = authenticated_client.post(
            self.URL,
            data={"trace_id": str(trace_no_llm.id)},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_unauthenticated(self, api_client, trace_with_llm_spans):
        response = api_client.post(
            self.URL,
            data={"trace_id": str(trace_with_llm_spans.id)},
            format="json",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
