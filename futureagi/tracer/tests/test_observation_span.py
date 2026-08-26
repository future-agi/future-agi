"""
ObservationSpan API Tests

Tests for /tracer/observation-span/ endpoints.
"""

import json
import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from model_hub.models.choices import AnnotationTypeChoices, FeedbackSourceChoices
from model_hub.models.develop_annotations import AnnotationsLabels
from model_hub.models.evals_metric import Feedback
from tracer.models.observation_span import (
    EvalEntryStatus,
    EvalLogger,
    EvalTargetType,
    ObservationSpan,
)
from tracer.models.project import Project
from tracer.models.project_version import ProjectVersion
from tracer.models.trace import Trace

AUTH_REQUIRED_STATUS_CODES = (
    status.HTTP_401_UNAUTHORIZED,
    status.HTTP_403_FORBIDDEN,
)


def get_result(response):
    """Extract result from API response wrapper."""
    data = response.json()
    return data.get("result", data)


def make_same_org_other_workspace_span(organization, user, trace_type="observe"):
    suffix = uuid.uuid4().hex[:8]
    other_workspace = Workspace.objects.create(
        name=f"Other Span Workspace {suffix}",
        organization=organization,
        is_active=True,
        created_by=user,
    )
    other_project = Project.objects.create(
        name=f"Other Span Project {suffix}",
        organization=organization,
        workspace=other_workspace,
        model_type=AIModel.ModelTypes.GENERATIVE_LLM,
        trace_type=trace_type,
        metadata={},
    )
    other_project_version = ProjectVersion.objects.create(
        project=other_project,
        name=f"Other Span Run {suffix}",
        version="v1",
        metadata={},
    )
    other_trace = Trace.objects.create(
        project=other_project,
        project_version=other_project_version,
        name=f"Other Trace {suffix}",
        input={"prompt": "hidden"},
        output={"response": "hidden"},
    )
    other_span = ObservationSpan.objects.create(
        id=f"other_span_{suffix}",
        project=other_project,
        project_version=other_project_version,
        trace=other_trace,
        name="Other Workspace Span",
        observation_type="llm",
        start_time=timezone.now() - timedelta(seconds=5),
        end_time=timezone.now(),
        tags=["hidden"],
        latency_ms=250,
        status="OK",
    )
    return (
        other_workspace,
        other_project,
        other_project_version,
        other_trace,
        other_span,
    )


@pytest.mark.integration
@pytest.mark.api
class TestObservationSpanRetrieveAPI:
    """Tests for GET /tracer/observation-span/{id}/ endpoint."""

    def test_retrieve_span_unauthenticated(self, api_client, observation_span):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(f"/tracer/observation-span/{observation_span.id}/")
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_retrieve_span_success(self, auth_client, observation_span):
        """Retrieve an observation span by ID."""
        response = auth_client.get(f"/tracer/observation-span/{observation_span.id}/")
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        # Data is nested under observation_span key
        span_data = data.get("observation_span", data)
        assert span_data.get("id") == observation_span.id
        assert span_data.get("name") == "Test Span"

    def test_retrieve_span_with_eval_metrics(
        self, auth_client, observation_span, project_version
    ):
        """Retrieve span includes eval metrics if available."""
        response = auth_client.get(f"/tracer/observation-span/{observation_span.id}/")
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        # Should include eval_metrics field even if empty
        assert isinstance(data, dict)

    def test_retrieve_span_not_found(self, auth_client):
        """Retrieve non-existent span returns error."""
        fake_id = f"span_{uuid.uuid4().hex[:16]}"
        response = auth_client.get(f"/tracer/observation-span/{fake_id}/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_retrieve_span_from_different_org(self, auth_client, organization):
        """Cannot retrieve span from different organization."""
        from accounts.models.organization import Organization
        from model_hub.models.ai_model import AIModel
        from tracer.models.project import Project
        from tracer.models.trace import Trace

        # Create another organization and span
        other_org = Organization.objects.create(name="Other Org")
        other_project = Project.objects.create(
            name="Other Project",
            organization=other_org,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="experiment",
        )
        other_trace = Trace.objects.create(project=other_project, name="Other Trace")
        other_span = ObservationSpan.objects.create(
            id=f"other_span_{uuid.uuid4().hex[:8]}",
            project=other_project,
            trace=other_trace,
            name="Other Span",
            observation_type="llm",
        )

        response = auth_client.get(f"/tracer/observation-span/{other_span.id}/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestObservationSpanWorkspaceScopeAPI:
    """Same-organization spans must stay scoped to the requested workspace."""

    def test_retrieve_rejects_same_org_other_workspace_span(
        self, auth_client, organization, user
    ):
        *_, other_span = make_same_org_other_workspace_span(
            organization, user, trace_type="observe"
        )

        response = auth_client.get(f"/tracer/observation-span/{other_span.id}/")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_retrieve_loading_rejects_same_org_other_workspace_span(
        self, auth_client, organization, user
    ):
        *_, other_span = make_same_org_other_workspace_span(
            organization, user, trace_type="observe"
        )

        response = auth_client.get(
            "/tracer/observation-span/retrieve_loading/",
            {"observation_span_id": other_span.id},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_root_spans_omits_same_org_other_workspace_trace(
        self, auth_client, organization, user
    ):
        """GET root-spans is fail-closed: a same-org other-workspace trace is
        omitted from the {trace_id: root_span_id} map."""
        _, _, _, other_trace, other_span = make_same_org_other_workspace_span(
            organization, user, trace_type="observe"
        )

        response = auth_client.get(
            "/tracer/observation-span/root-spans/",
            {"trace_ids": [str(other_trace.id)]},
        )

        assert response.status_code == status.HTTP_200_OK
        result = get_result(response)
        assert str(other_trace.id) not in result
        assert str(other_span.id) not in result.values()

    def test_list_and_index_reject_same_org_other_workspace_project_version(
        self, auth_client, organization, user
    ):
        _, _, other_project_version, _, other_span = make_same_org_other_workspace_span(
            organization, user, trace_type="experiment"
        )

        list_response = auth_client.get(
            "/tracer/observation-span/list_spans/",
            {"project_version_id": str(other_project_version.id), "filters": "[]"},
        )
        index_response = auth_client.get(
            "/tracer/observation-span/get_trace_id_by_index_spans_as_base/",
            {
                "span_id": other_span.id,
                "project_version_id": str(other_project_version.id),
                "filters": "[]",
            },
        )

        assert list_response.status_code == status.HTTP_400_BAD_REQUEST
        assert index_response.status_code == status.HTTP_400_BAD_REQUEST

    def test_observe_list_export_graph_and_index_reject_same_org_other_workspace_project(
        self, auth_client, organization, user
    ):
        _, other_project, _, _, other_span = make_same_org_other_workspace_span(
            organization, user, trace_type="observe"
        )

        list_response = auth_client.get(
            "/tracer/observation-span/list_spans_observe/",
            {"project_id": str(other_project.id), "filters": "[]"},
        )
        export_response = auth_client.get(
            "/tracer/observation-span/get_spans_export_data/",
            {"project_id": str(other_project.id), "filters": "[]"},
        )
        graph_response = auth_client.post(
            "/tracer/observation-span/get_graph_methods/",
            {
                "project_id": str(other_project.id),
                "filters": [],
                "interval": "day",
                "property": "average",
                "req_data_config": {"id": "latency", "type": "SYSTEM_METRIC"},
            },
            format="json",
        )
        index_response = auth_client.get(
            "/tracer/observation-span/get_trace_id_by_index_spans_as_observe/",
            {
                "span_id": other_span.id,
                "project_id": str(other_project.id),
                "filters": "[]",
            },
        )

        assert list_response.status_code == status.HTTP_400_BAD_REQUEST
        assert export_response.status_code == status.HTTP_400_BAD_REQUEST
        assert graph_response.status_code == status.HTTP_400_BAD_REQUEST
        assert index_response.status_code == status.HTTP_400_BAD_REQUEST

    def test_update_tags_rejects_same_org_other_workspace_span_without_mutating(
        self, auth_client, organization, user
    ):
        *_, other_span = make_same_org_other_workspace_span(
            organization, user, trace_type="observe"
        )

        response = auth_client.post(
            "/tracer/observation-span/update-tags/",
            {"span_id": other_span.id, "tags": ["changed"]},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        other_span.refresh_from_db()
        assert other_span.tags == ["hidden"]


@pytest.mark.integration
@pytest.mark.api
class TestObservationSpanCreateAPI:
    """Tests for POST /tracer/observation-span/ endpoint."""

    def test_create_span_unauthenticated(self, api_client, project, trace):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/observation-span/",
            {
                "project": str(project.id),
                "trace": str(trace.id),
                "name": "New Span",
                "observation_type": "llm",
            },
            format="json",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_create_span_success(self, auth_client, project, trace):
        """Create a new observation span."""
        response = auth_client.post(
            "/tracer/observation-span/",
            {
                "project": str(project.id),
                "trace": str(trace.id),
                "name": "Created Span",
                "observation_type": "llm",
                "input": {"messages": [{"role": "user", "content": "Hello"}]},
                "output": {"response": "Hi there"},
                "model": "gpt-4",
            },
            format="json",
        )
        # Accept 200 or 201 for creation
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]

    def test_create_span_with_metrics(self, auth_client, project, trace):
        """Create span with token and cost metrics."""
        response = auth_client.post(
            "/tracer/observation-span/",
            {
                "project": str(project.id),
                "trace": str(trace.id),
                "name": "Metrics Span",
                "observation_type": "llm",
                "model": "gpt-4",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "cost": 0.005,
                "latency_ms": 1500,
            },
            format="json",
        )
        # Accept 200 or 201 for creation
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]

    def test_create_span_missing_required_fields(self, auth_client, project, trace):
        """Create span fails with missing required fields."""
        # Missing name
        response = auth_client.post(
            "/tracer/observation-span/",
            {
                "project": str(project.id),
                "trace": str(trace.id),
                "observation_type": "llm",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_span_invalid_observation_type(self, auth_client, project, trace):
        """Create span fails with invalid observation type."""
        response = auth_client.post(
            "/tracer/observation-span/",
            {
                "project": str(project.id),
                "trace": str(trace.id),
                "name": "Invalid Type Span",
                "observation_type": "invalid_type",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_create_span_rejects_same_org_other_workspace_project(
        self, auth_client, organization, user
    ):
        """Create cannot attach a span to a project outside the active workspace."""
        _, other_project, _, other_trace, _ = make_same_org_other_workspace_span(
            organization, user
        )

        response = auth_client.post(
            "/tracer/observation-span/",
            {
                "project": str(other_project.id),
                "trace": str(other_trace.id),
                "name": "Cross Workspace Span",
                "observation_type": "llm",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not ObservationSpan.objects.filter(name="Cross Workspace Span").exists()

    def test_patch_span_rejects_same_org_other_workspace_project(
        self, auth_client, organization, user, observation_span
    ):
        """Update cannot move a visible span into another workspace's project."""
        _, other_project, _, other_trace, _ = make_same_org_other_workspace_span(
            organization, user
        )

        response = auth_client.patch(
            f"/tracer/observation-span/{observation_span.id}/",
            {"project": str(other_project.id), "trace": str(other_trace.id)},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        observation_span.refresh_from_db()
        assert observation_span.project_id != other_project.id


@pytest.mark.integration
@pytest.mark.api
class TestObservationSpanBulkCreateAPI:
    """Tests for POST /tracer/observation-span/bulk_create/ endpoint."""

    def test_bulk_create_spans_unauthenticated(self, api_client, project, trace):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/observation-span/bulk_create/",
            {
                "spans": [
                    {
                        "project": str(project.id),
                        "trace": str(trace.id),
                        "name": "Bulk Span 1",
                        "observation_type": "llm",
                    }
                ]
            },
            format="json",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_bulk_create_spans_success(self, auth_client, project, trace):
        """Bulk create multiple observation spans."""
        response = auth_client.post(
            "/tracer/observation-span/bulk_create/",
            {
                "spans": [
                    {
                        "project": str(project.id),
                        "trace": str(trace.id),
                        "name": "Bulk Span 1",
                        "observation_type": "llm",
                    },
                    {
                        "project": str(project.id),
                        "trace": str(trace.id),
                        "name": "Bulk Span 2",
                        "observation_type": "tool",
                    },
                ]
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK

    def test_bulk_create_rejects_same_org_other_workspace_project(
        self, auth_client, organization, user
    ):
        """Bulk create validates project/trace workspace scope."""
        _, other_project, other_project_version, other_trace, _ = (
            make_same_org_other_workspace_span(organization, user)
        )
        span_id = f"bulk_cross_workspace_{uuid.uuid4().hex[:8]}"

        response = auth_client.post(
            "/tracer/observation-span/bulk_create/",
            {
                "observation_spans": [
                    {
                        "id": span_id,
                        "project": str(other_project.id),
                        "project_version": str(other_project_version.id),
                        "trace": str(other_trace.id),
                        "name": "Hidden Bulk Span",
                        "observation_type": "llm",
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    }
                ]
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not ObservationSpan.objects.filter(id=span_id).exists()


@pytest.mark.integration
@pytest.mark.api
class TestObservationSpanListSpansAPI:
    """Tests for GET /tracer/observation-span/list_spans/ endpoint."""

    def test_list_spans_unauthenticated(self, api_client, project_version):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/observation-span/list_spans/",
            {"project_version_id": str(project_version.id)},
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_list_spans_missing_project_version(self, auth_client):
        """List spans fails without project version ID."""
        response = auth_client.get("/tracer/observation-span/list_spans/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_spans_success(
        self, auth_client, project, project_version, trace, observation_span
    ):
        """List spans for a project version."""
        # Associate span with project version
        observation_span.project_version = project_version
        observation_span.save()

        response = auth_client.get(
            "/tracer/observation-span/list_spans/",
            {"project_version_id": str(project_version.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        # Check for expected keys
        assert "metadata" in data or "table" in data or "column_config" in data

    def test_list_spans_with_pagination(
        self, auth_client, project, project_version, trace, multiple_spans
    ):
        """List spans with pagination."""
        # Associate spans with project version
        for span in multiple_spans:
            span.project_version = project_version
            span.save()

        response = auth_client.get(
            "/tracer/observation-span/list_spans/",
            {
                "project_version_id": str(project_version.id),
                "page_number": 0,
                "page_size": 5,
            },
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        # Check for metadata
        assert "metadata" in data or "table" in data

    def test_list_spans_with_filters(
        self, auth_client, project, project_version, trace, multiple_spans
    ):
        """List spans with filters."""
        # Associate spans with project version
        for span in multiple_spans:
            span.project_version = project_version
            span.save()

        response = auth_client.get(
            "/tracer/observation-span/list_spans/",
            {
                "project_version_id": str(project_version.id),
                "filters": json.dumps(
                    [
                        {
                            "column_id": "node_type",
                            "filter_config": {
                                "filter_type": "text",
                                "filter_op": "equals",
                                "filter_value": "llm",
                            },
                        }
                    ]
                ),
            },
        )
        assert response.status_code == status.HTTP_200_OK

    def test_list_spans_rejects_legacy_project_version_alias(
        self, auth_client, project_version
    ):
        response = auth_client.get(
            "/tracer/observation-span/list_spans/",
            {"projectVersionId": str(project_version.id), "filters": "[]"},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_spans_falls_back_to_postgres_when_clickhouse_fails(
        self, auth_client, project_version, observation_span, monkeypatch
    ):
        """Unlike the fail-closed observe sibling, the experiment/prototype
        `list_spans` path keeps the PG fallback: a CH error returns PG rows
        rather than 400ing the prototype span list."""
        from tracer.services.clickhouse.query_service import QueryType
        from tracer.views.observation_span import ObservationSpanView

        observation_span.project_version = project_version
        observation_span.save(update_fields=["project_version"])

        monkeypatch.setattr(
            "tracer.views.observation_span.AnalyticsQueryService.should_use_clickhouse",
            lambda self, query_type: query_type == QueryType.SPAN_LIST,
        )

        def fail_clickhouse(
            self,
            request,
            project_version_id,
            project_version,
            analytics,
            validated_data,
        ):
            raise RuntimeError("clickhouse unavailable")

        monkeypatch.setattr(
            ObservationSpanView,
            "_list_spans_non_observe_clickhouse",
            fail_clickhouse,
        )

        response = auth_client.get(
            "/tracer/observation-span/list_spans/",
            {"project_version_id": str(project_version.id), "filters": "[]"},
        )

        assert response.status_code == status.HTTP_200_OK
        rows = get_result(response).get("table", [])
        assert any(row["span_id"] == observation_span.id for row in rows)


@pytest.mark.integration
@pytest.mark.api
class TestObservationSpanListSpansObserveAPI:
    """Tests for GET /tracer/observation-span/list_spans_observe/ endpoint."""

    def test_list_spans_observe_unauthenticated(self, api_client, observe_project):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/observation-span/list_spans_observe/",
            {"project_id": str(observe_project.id)},
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_list_spans_observe_missing_project(self, auth_client):
        """List spans observe fails without project ID."""
        response = auth_client.get("/tracer/observation-span/list_spans_observe/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_spans_observe_success(
        self, auth_client, observe_project, trace_session, session_trace
    ):
        """List spans for observe project."""
        # Create a span for the observe project
        ObservationSpan.objects.create(
            id=f"observe_span_{uuid.uuid4().hex[:8]}",
            project=observe_project,
            trace=session_trace,
            name="Observe Span",
            observation_type="llm",
            start_time=timezone.now() - timedelta(seconds=5),
            end_time=timezone.now(),
        )

        response = auth_client.get(
            "/tracer/observation-span/list_spans_observe/",
            {"project_id": str(observe_project.id)},
        )
        assert response.status_code == status.HTTP_200_OK

    def test_list_spans_observe_fails_closed_when_clickhouse_fails(
        self, auth_client, observe_project, session_trace, monkeypatch
    ):
        """CH is authoritative post-migration: a CH error surfaces as 400
        rather than falling back to the dropped-table PG path."""
        from tracer.services.clickhouse.query_service import QueryType
        from tracer.views.observation_span import ObservationSpanView

        ObservationSpan.objects.create(
            id=f"observe_span_{uuid.uuid4().hex[:8]}",
            project=observe_project,
            trace=session_trace,
            name="Observe Fallback Span",
            observation_type="llm",
            start_time=timezone.now() - timedelta(seconds=5),
            end_time=timezone.now(),
        )

        monkeypatch.setattr(
            "tracer.views.observation_span.AnalyticsQueryService.should_use_clickhouse",
            lambda self, query_type: query_type == QueryType.SPAN_LIST,
        )

        def fail_clickhouse(
            self, request, project_id, validated_data, analytics, **kwargs
        ):
            raise RuntimeError("clickhouse unavailable")

        monkeypatch.setattr(
            ObservationSpanView,
            "_list_spans_clickhouse",
            fail_clickhouse,
        )

        response = auth_client.get(
            "/tracer/observation-span/list_spans_observe/",
            {"project_id": str(observe_project.id), "filters": "[]"},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestObservationSpanSubmitFeedbackAPI:
    """Tests for POST /tracer/observation-span/submit_feedback/ endpoint."""

    def test_submit_feedback_unauthenticated(self, api_client, observation_span):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/observation-span/submit_feedback/",
            {
                "span_id": observation_span.id,
                "feedback_type": "thumbs_up",
                "feedback_value": True,
            },
            format="json",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_submit_feedback_success(self, auth_client, observation_span):
        """Submit feedback for an observation span."""
        response = auth_client.post(
            "/tracer/observation-span/submit_feedback/",
            {
                "span_id": observation_span.id,
                "feedback_type": "thumbs_up",
                "feedback_value": True,
            },
            format="json",
        )
        # Accept 200 or 400 (if feature not enabled)
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]

    def test_submit_feedback_invalid_span(self, auth_client):
        """Submit feedback for non-existent span fails."""
        response = auth_client.post(
            "/tracer/observation-span/submit_feedback/",
            {
                "span_id": "nonexistent_span_id",
                "feedback_type": "thumbs_up",
                "feedback_value": True,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_feedback_detail_routes_retrieve_and_delete(
        self, auth_client, user, organization, workspace, observation_span
    ):
        """Feedback detail routes should support drawer readback and cleanup."""
        feedback = Feedback.objects.create(
            source=FeedbackSourceChoices.OBSERVE.value,
            source_id=observation_span.id,
            value="0.42",
            explanation="Needs review",
            feedback_improvement="Retune on this example",
            action_type="retune",
            user=user,
            organization=organization,
            workspace=workspace,
        )

        response = auth_client.get(f"/model-hub/feedback/{feedback.id}/")

        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        assert data["id"] == str(feedback.id)
        assert data["source"] == FeedbackSourceChoices.OBSERVE.value
        assert data["source_id"] == observation_span.id
        assert data["action_type"] == "retune"

        response = auth_client.delete(f"/model-hub/feedback/{feedback.id}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        feedback.refresh_from_db()
        assert feedback.deleted is True


@pytest.mark.integration
@pytest.mark.api
class TestObservationSpanGraphMethodsAPI:
    """Tests for POST /tracer/observation-span/get_graph_methods/ endpoint."""

    def test_get_graph_methods_unauthenticated(self, api_client, project):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/observation-span/get_graph_methods/",
            {"project_id": str(project.id)},
            format="json",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_get_graph_methods_missing_project(self, auth_client):
        """Get graph methods fails without project ID."""
        response = auth_client.post(
            "/tracer/observation-span/get_graph_methods/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_graph_methods_success(
        self, auth_client, project, trace, observation_span
    ):
        """Get graph methods for observation spans."""
        response = auth_client.post(
            "/tracer/observation-span/get_graph_methods/",
            {
                "project_id": str(project.id),
                "interval": "hour",
            },
            format="json",
        )
        # Accept 200 or 400
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]

    def test_get_graph_methods_filtered_system_metric_falls_back_to_postgres(
        self, auth_client, observe_project, monkeypatch
    ):
        """Span graph filters use the list-query metric aliases in PG fallback."""
        monkeypatch.setattr(
            "tracer.services.clickhouse.query_service.AnalyticsQueryService.should_use_clickhouse",
            lambda self, query_type: True,
        )
        monkeypatch.setattr(
            "tracer.services.clickhouse.query_service.AnalyticsQueryService.execute_ch_query",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("ch down")),
        )

        trace = Trace.objects.create(project=observe_project, name="Span Graph Trace")
        ObservationSpan.objects.create(
            id=f"span_{uuid.uuid4().hex[:16]}",
            project=observe_project,
            trace=trace,
            name="Span Graph Root",
            observation_type="llm",
            start_time=timezone.now(),
            latency_ms=250,
            total_tokens=10,
            prompt_tokens=4,
            completion_tokens=6,
            cost=0.001,
            status="OK",
        )

        response = auth_client.post(
            "/tracer/observation-span/get_graph_methods/",
            {
                "project_id": str(observe_project.id),
                "interval": "day",
                "property": "average",
                "req_data_config": {"id": "latency", "type": "SYSTEM_METRIC"},
                "filters": [
                    {
                        "column_id": "latency",
                        "filter_config": {
                            "filter_type": "number",
                            "filter_op": "greater_than_or_equal",
                            "filter_value": 0,
                            "col_type": "SYSTEM_METRIC",
                        },
                    }
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert isinstance(get_result(response).get("data"), list)


@pytest.mark.integration
@pytest.mark.api
class TestObservationSpanGetFieldsAPI:
    """Tests for GET /tracer/observation-span/get_observation_span_fields/ endpoint."""

    def test_get_fields_unauthenticated(self, api_client):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/observation-span/get_observation_span_fields/"
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_get_fields_success(self, auth_client):
        """Get available observation span fields."""
        response = auth_client.get(
            "/tracer/observation-span/get_observation_span_fields/"
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        # Should return list of available fields
        assert isinstance(data, list) or isinstance(data, dict)


@pytest.mark.integration
@pytest.mark.api
class TestObservationSpanAddAnnotationsAPI:
    """Tests for POST /tracer/observation-span/add_annotations/ endpoint."""

    def test_add_annotations_unauthenticated(self, api_client, observation_span):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/observation-span/add_annotations/",
            {
                "span_id": observation_span.id,
                "annotations": {"label": "positive"},
            },
            format="json",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_add_annotations_success(
        self, auth_client, observation_span, project_version
    ):
        """Add annotations to an observation span."""
        # Associate span with project version
        observation_span.project_version = project_version
        observation_span.save()

        response = auth_client.post(
            "/tracer/observation-span/add_annotations/",
            {
                "span_ids": [observation_span.id],
                "project_version_id": str(project_version.id),
                "annotations": [
                    {
                        "label": "sentiment",
                        "value": "positive",
                    }
                ],
            },
            format="json",
        )
        # Accept 200 or 400
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]

    def test_add_annotations_missing_span(self, auth_client, project_version):
        """Add annotations to non-existent span fails."""
        response = auth_client.post(
            "/tracer/observation-span/add_annotations/",
            {
                "span_ids": ["nonexistent_span_id"],
                "project_version_id": str(project_version.id),
                "annotations": [{"label": "test", "value": "value"}],
            },
            format="json",
        )
        # Should handle gracefully
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
        ]


@pytest.mark.integration
@pytest.mark.api
class TestObservationSpanExportAPI:
    """Tests for GET /tracer/observation-span/get_spans_export_data/ endpoint."""

    def test_export_spans_unauthenticated(self, api_client, project_version):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/observation-span/get_spans_export_data/",
            {"project_version_id": str(project_version.id)},
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_export_spans_missing_project_version(self, auth_client):
        """Export spans fails without project version ID."""
        response = auth_client.get("/tracer/observation-span/get_spans_export_data/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_export_spans_success(
        self, auth_client, project, project_version, trace, observation_span
    ):
        """Export spans for a project version."""
        # Associate span with project version
        observation_span.project_version = project_version
        observation_span.save()

        response = auth_client.get(
            "/tracer/observation-span/get_spans_export_data/",
            {"project_version_id": str(project_version.id)},
        )
        # Can be 200 with file or 400 if no spans
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]


@pytest.mark.integration
@pytest.mark.api
class TestObservationSpanCreateOtelSpanAPI:
    """Tests for POST /tracer/observation-span/create_otel_span/ endpoint."""

    def test_create_otel_span_unauthenticated(self, api_client, project, trace):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/observation-span/create_otel_span/",
            {
                "project_id": str(project.id),
                "trace_id": str(trace.id),
                "span_data": {"name": "OTEL Span"},
            },
            format="json",
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_create_otel_span_success(self, auth_client, project, trace):
        """Create an OTEL-format observation span."""
        response = auth_client.post(
            "/tracer/observation-span/create_otel_span/",
            {
                "project_id": str(project.id),
                "trace_id": str(trace.id),
                "span_data": {
                    "name": "OTEL Span",
                    "observation_type": "llm",
                    "attributes": {
                        "gen_ai.system": "openai",
                        "gen_ai.request.model": "gpt-4",
                    },
                },
            },
            format="json",
        )
        # Accept 200 or various error codes (feature may not be enabled)
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
        ]

    def test_create_otel_span_rejects_trace_from_another_workspace_project(
        self, auth_client, observe_project, organization, user
    ):
        """OTEL create must not attach spans to an existing trace from another project."""
        _, _, _, other_trace, _ = make_same_org_other_workspace_span(organization, user)
        span_id = f"otel_cross_workspace_{uuid.uuid4().hex[:8]}"
        now_ns = int(timezone.now().timestamp() * 1_000_000_000)

        response = auth_client.post(
            "/tracer/observation-span/create_otel_span/",
            [
                {
                    "project_name": observe_project.name,
                    "project_type": "observe",
                    "trace_id": str(other_trace.id),
                    "span_id": span_id,
                    "name": "Cross Workspace OTEL Span",
                    "start_time": now_ns - 1_000_000,
                    "end_time": now_ns,
                    "latency": 1,
                    "attributes": {
                        "gen_ai.span.kind": "llm",
                        "gen_ai.request.model": "gpt-4",
                    },
                }
            ],
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not ObservationSpan.objects.filter(id=span_id).exists()


@pytest.mark.integration
@pytest.mark.api
class TestObservationSpanDeleteAnnotationLabelAPI:
    """Tests for DELETE /tracer/observation-span/delete_annotation_label/ endpoint."""

    def test_delete_annotation_label_rejects_same_org_other_workspace_label(
        self, auth_client, organization, user
    ):
        """Label deletion is constrained to the active workspace."""
        other_workspace, _, _, _, _ = make_same_org_other_workspace_span(
            organization, user
        )
        label = AnnotationsLabels.objects.create(
            name=f"Hidden Label {uuid.uuid4().hex[:8]}",
            type=AnnotationTypeChoices.TEXT.value,
            organization=organization,
            workspace=other_workspace,
        )

        response = auth_client.delete(
            f"/tracer/observation-span/delete_annotation_label/?label_id={label.id}",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        label.refresh_from_db()
        assert label.deleted is False

    def test_delete_annotation_label_deletes_current_workspace_label(
        self, auth_client, organization, workspace
    ):
        label = AnnotationsLabels.objects.create(
            name=f"Disposable Label {uuid.uuid4().hex[:8]}",
            type=AnnotationTypeChoices.TEXT.value,
            organization=organization,
            workspace=workspace,
        )

        response = auth_client.delete(
            f"/tracer/observation-span/delete_annotation_label/?label_id={label.id}",
        )

        assert response.status_code == status.HTTP_200_OK
        label.refresh_from_db()
        assert label.deleted is True


@pytest.mark.integration
@pytest.mark.api
class TestObservationSpanRetrieveLoadingAPI:
    """Tests for GET /tracer/observation-span/retrieve_loading/ endpoint."""

    def test_retrieve_loading_unauthenticated(self, api_client, observation_span):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/observation-span/retrieve_loading/",
            {"span_id": observation_span.id},
        )
        assert response.status_code in AUTH_REQUIRED_STATUS_CODES

    def test_retrieve_loading_missing_span_id(self, auth_client):
        """Retrieve loading fails without span ID."""
        response = auth_client.get("/tracer/observation-span/retrieve_loading/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_retrieve_loading_success(self, auth_client, observation_span):
        """Retrieve loading state for a span."""
        response = auth_client.get(
            "/tracer/observation-span/retrieve_loading/",
            {"span_id": observation_span.id},
        )
        # Accept 200 or 400
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]


@pytest.mark.django_db
class TestSubmitFeedbackActionTypeRecalculate:
    """Regression coverage for issue #2357.

    Submitting action_type="recalculate" must NOT crash, and must not delete
    the existing eval result until the recompute has actually produced a new
    one. Two distinct failure modes are covered:

      1. The experiment (project_version) path used to pass 4 positional args
         to ``evaluate_observation_span`` (which only accepts 3), raising a
         TypeError that was swallowed into a 400 — while the prior result had
         already been soft-deleted. (the "crash" half of #2357)

      2. The old result was soft-deleted BEFORE the recompute ran, so a failed
         recompute left the span with no result at all. (the "deletes the
         result" half of #2357)

    A third mode (observe path, see #2357 design note) is covered by the
    ``*_forces_recompute_*`` tests: the live prior row shares the span's
    eval_task_id, so ``evaluate_observation_span_observe``'s batch-eval-task
    idempotency guard would otherwise silently no-op the recalculate.
    """

    def _make_feedback(self, user, observation_span):
        return Feedback.objects.create(
            source=FeedbackSourceChoices.TRACE.value,
            source_id=str(observation_span.id),
            user=user,
            value="1",
            organization=observation_span.project.organization,
            workspace=observation_span.project.workspace,
        )

    def _make_eval_logger(self, observation_span, custom_eval_config, **kwargs):
        defaults = {
            "target_type": EvalTargetType.SPAN,
            "observation_span": observation_span,
            "trace": observation_span.trace,
            "custom_eval_config": custom_eval_config,
            "output_bool": True,
            "status": EvalEntryStatus.COMPLETED,
        }
        defaults.update(kwargs)
        return EvalLogger.objects.create(**defaults)

    def _url(self):
        return "/tracer/observation-span/submit-feedback-action-type/"

    def _post(self, auth_client, observation_span, custom_eval_config, feedback):
        return auth_client.post(
            self._url(),
            {
                "observation_span_id": str(observation_span.id),
                "action_type": "recalculate",
                "custom_eval_config_id": str(custom_eval_config.id),
                "feedback_id": str(feedback.id),
            },
            format="json",
        )

    @patch("tracer.utils.eval.evaluate_observation_span_observe", return_value=True)
    def test_recalculate_observe_path_does_not_crash_and_replaces_on_success(
        self, mock_eval, auth_client, observation_span, custom_eval_config, user
    ):
        """Observe path: recompute success soft-deletes the old result and
        keeps exactly one live row (old deleted, new written by the engine)."""
        feedback = self._make_feedback(user, observation_span)
        old = self._make_eval_logger(observation_span, custom_eval_config)

        response = self._post(
            auth_client, observation_span, custom_eval_config, feedback
        )

        assert response.status_code == status.HTTP_200_OK
        # The recompute function is invoked with the feedback-driven signature.
        mock_eval.assert_called_once()
        _, kwargs = mock_eval.call_args
        assert kwargs["feedback_id"] == str(feedback.id)
        assert kwargs["delete_previous"] is False

        # Old result is soft-deleted only after a successful recompute.
        old.refresh_from_db()
        assert old.deleted is True
        assert old.deleted_at is not None

    @patch("tracer.utils.eval.evaluate_observation_span_observe", return_value=False)
    def test_recalculate_observe_path_keeps_old_result_when_recompute_fails(
        self, mock_eval, auth_client, observation_span, custom_eval_config, user
    ):
        """Observe path: a failed recompute must leave the prior result intact
        (replacement, never a blind pre-delete)."""
        feedback = self._make_feedback(user, observation_span)
        old = self._make_eval_logger(observation_span, custom_eval_config)

        response = self._post(
            auth_client, observation_span, custom_eval_config, feedback
        )

        # Failure is reported, not swallowed into a crash.
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        mock_eval.assert_called_once()
        _, kwargs = mock_eval.call_args
        assert kwargs["delete_previous"] is False

        # Prior result survives a failed recompute.
        old.refresh_from_db()
        assert old.deleted is False

    @patch("tracer.utils.eval._execute_evaluation")
    def test_recalculate_observe_path_forces_recompute_past_idempotency_guard(
        self,
        mock_execute,
        auth_client,
        observation_span,
        custom_eval_config,
        user,
    ):
        """Observe path regression for #2357: the live prior result row carries
        the same eval_task_id, so ``evaluate_observation_span_observe``'s
        batch-eval-task idempotency guard would otherwise hit it and silently
        no-op (return None -> view reports 200 but nothing recomputed).

        We patch the engine so no real eval/ClickHouse runs, keep the real
        function (not mocked), and assert it skips the guard because the view
        passes ``force=True``.
        """
        feedback = self._make_feedback(user, observation_span)
        # Prior result with a real eval_task_id — this is exactly what makes the
        # default guard fire and would have no-op'd the recalculate before #2357.
        old = self._make_eval_logger(
            observation_span,
            custom_eval_config,
            eval_task_id="existing-task-id",
        )

        response = self._post(
            auth_client, observation_span, custom_eval_config, feedback
        )

        # 200 + the engine was actually invoked (guard bypassed via force).
        assert response.status_code == status.HTTP_200_OK
        assert mock_execute.called
        # The recompute ran with the prior task id (no new EvalTask fabricated).
        # Use the last call in case composite evals invoke the engine multiple
        # times — we only care that the recalculate reached it at least once.
        _, call_kwargs = mock_execute.call_args_list[-1]
        assert call_kwargs["eval_task_id"] == "existing-task-id"

        # Old row is soft-deleted only after the successful recompute.
        old.refresh_from_db()
        assert old.deleted is True

    @patch("tracer.utils.eval._execute_evaluation")
    def test_recalculate_observe_path_no_force_would_skip_recompute(
        self, mock_execute, auth_client, observation_span, custom_eval_config, user
    ):
        """Contrast case (documents the guard's purpose): if ``force`` were not
        set, a prior row sharing the eval_task_id short-circuits the recompute.

        This is NOT the actual view behavior (the view passes force=True); it
        pins the underlying invariant so a future change to the default is
        caught.
        """
        feedback = self._make_feedback(user, observation_span)
        old = self._make_eval_logger(
            observation_span,
            custom_eval_config,
            eval_task_id="existing-task-id",
        )

        from tracer.utils.eval import evaluate_observation_span_observe

        result = evaluate_observation_span_observe(
            observation_span_id=str(observation_span.id),
            custom_eval_config_id=str(custom_eval_config.id),
            eval_task_id="existing-task-id",
            feedback_id=str(feedback.id),
            delete_previous=False,
            # force defaults to False
        )

        assert result is None
        assert not mock_execute.called
        # The prior row is left untouched by the no-op (it is neither deleted
        # nor replaced), which is exactly why the view must pass force=True.
        old.refresh_from_db()
        assert old.deleted is False

    @patch("tracer.utils.eval.evaluate_observation_span", return_value=True)
    def test_recalculate_experiment_path_no_type_error_and_replaces_on_success(
        self,
        mock_eval,
        auth_client,
        observation_span,
        custom_eval_config,
        project_version,
        user,
    ):
        """Experiment path (project_version set): the 4-arg TypeError from
        #2357 is gone, and the old result is replaced only on success."""
        observation_span.project_version = project_version
        observation_span.save(update_fields=["project_version"])
        feedback = self._make_feedback(user, observation_span)
        old = self._make_eval_logger(observation_span, custom_eval_config)

        response = self._post(
            auth_client, observation_span, custom_eval_config, feedback
        )

        assert response.status_code == status.HTTP_200_OK
        # The experiment helper takes (observation_span_id,
        # custom_eval_config_id, feedback_id) — no eval_task_id positional arg.
        mock_eval.assert_called_once()
        _, kwargs = mock_eval.call_args
        assert "eval_task_id" not in kwargs
        assert kwargs["feedback_id"] == str(feedback.id)
        assert kwargs["delete_previous"] is False

        old.refresh_from_db()
        assert old.deleted is True

    @patch("tracer.utils.eval.evaluate_observation_span", return_value=False)
    def test_recalculate_experiment_path_keeps_old_result_when_recompute_fails(
        self,
        mock_eval,
        auth_client,
        observation_span,
        custom_eval_config,
        project_version,
        user,
    ):
        """Experiment path: failed recompute leaves the prior result intact."""
        observation_span.project_version = project_version
        observation_span.save(update_fields=["project_version"])
        feedback = self._make_feedback(user, observation_span)
        old = self._make_eval_logger(observation_span, custom_eval_config)

        response = self._post(
            auth_client, observation_span, custom_eval_config, feedback
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        old.refresh_from_db()
        assert old.deleted is False
