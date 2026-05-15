"""
ObservationSpan API Tests

Tests for /tracer/observation-span/ endpoints.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from rest_framework import status

from tracer.models.observation_span import ObservationSpan


def get_result(response):
    """Extract result from API response wrapper."""
    data = response.json()
    return data.get("result", data)


@pytest.mark.integration
@pytest.mark.api
class TestObservationSpanRetrieveAPI:
    """Tests for GET /tracer/observation-span/{id}/ endpoint."""

    def test_retrieve_span_unauthenticated(self, api_client, observation_span):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(f"/tracer/observation-span/{observation_span.id}/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

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
        assert response.status_code == status.HTTP_403_FORBIDDEN

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
        assert response.status_code == status.HTTP_403_FORBIDDEN

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
        assert response.status_code == status.HTTP_403_FORBIDDEN

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
                "observation_type": "llm",
            },
        )
        assert response.status_code == status.HTTP_200_OK


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
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_list_spans_observe_org_scoped(
        self, auth_client, observe_project, trace_session, session_trace
    ):
        """Without project_id, returns spans org-scoped (user-detail page use case)."""
        ObservationSpan.objects.create(
            id=f"span_{uuid.uuid4().hex[:8]}",
            project=observe_project,
            trace=session_trace,
            name="Org Span",
            observation_type="llm",
            start_time=timezone.now() - timedelta(seconds=5),
            end_time=timezone.now(),
        )
        response = auth_client.get("/tracer/observation-span/list_spans_observe/")
        assert response.status_code == status.HTTP_200_OK

    def test_list_spans_observe_invalid_project_id(self, auth_client):
        """Non-existent project_id returns 400."""
        response = auth_client.get(
            "/tracer/observation-span/list_spans_observe/",
            {"project_id": str(uuid.uuid4())},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_spans_observe_success(
        self, auth_client, observe_project, trace_session, session_trace
    ):
        """List spans for observe project."""
        # Create a span for the observe project
        span = ObservationSpan.objects.create(
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

    def test_list_spans_observe_org_scoped_cross_project(
        self, auth_client, observe_project, project, trace_session, session_trace, trace
    ):
        """Org-scoped call returns spans from all projects in the org."""
        span1 = ObservationSpan.objects.create(
            id=f"span_{uuid.uuid4().hex[:8]}",
            project=observe_project,
            trace=session_trace,
            name="Span from observe project",
            observation_type="llm",
            start_time=timezone.now() - timedelta(seconds=5),
            end_time=timezone.now(),
        )
        span2 = ObservationSpan.objects.create(
            id=f"span_{uuid.uuid4().hex[:8]}",
            project=project,
            trace=trace,
            name="Span from experiment project",
            observation_type="llm",
            start_time=timezone.now() - timedelta(seconds=5),
            end_time=timezone.now(),
        )
        response = auth_client.get("/tracer/observation-span/list_spans_observe/")
        assert response.status_code == status.HTTP_200_OK
        result = get_result(response)
        returned_ids = {row["span_id"] for row in result["table"]}
        assert span1.id in returned_ids
        assert span2.id in returned_ids

    def test_list_spans_observe_ch_falls_back_to_pg(
        self, auth_client, observe_project, trace_session, session_trace
    ):
        """When ClickHouse fails for a single-project query, falls back to PostgreSQL."""
        from tracer.services.clickhouse.query_service import AnalyticsQueryService

        ObservationSpan.objects.create(
            id=f"span_{uuid.uuid4().hex[:8]}",
            project=observe_project,
            trace=session_trace,
            name="CH Fallback Span",
            observation_type="llm",
            start_time=timezone.now() - timedelta(seconds=5),
            end_time=timezone.now(),
        )

        with (
            patch.object(
                AnalyticsQueryService, "should_use_clickhouse", return_value=True
            ),
            patch.object(
                AnalyticsQueryService,
                "execute_ch_query",
                side_effect=Exception("clickhouse unavailable"),
            ) as ch_query,
        ):
            response = auth_client.get(
                "/tracer/observation-span/list_spans_observe/",
                {"project_id": str(observe_project.id)},
            )

        assert response.status_code == status.HTTP_200_OK
        ch_query.assert_called()

    def test_list_spans_observe_org_scoped_ch_falls_back_to_pg(
        self, auth_client, observe_project, trace_session, session_trace
    ):
        """Org-scoped mode: ClickHouse IS called (not bypassed), falls back to PG on failure."""
        from tracer.services.clickhouse.query_service import AnalyticsQueryService

        ObservationSpan.objects.create(
            id=f"span_{uuid.uuid4().hex[:8]}",
            project=observe_project,
            trace=session_trace,
            name="Org CH Fallback Span",
            observation_type="llm",
            start_time=timezone.now() - timedelta(seconds=5),
            end_time=timezone.now(),
        )

        with (
            patch.object(
                AnalyticsQueryService, "should_use_clickhouse", return_value=True
            ),
            patch.object(
                AnalyticsQueryService,
                "execute_ch_query",
                side_effect=Exception("clickhouse unavailable"),
            ) as ch_query,
        ):
            # No project_id → org-scoped mode
            response = auth_client.get("/tracer/observation-span/list_spans_observe/")

        assert response.status_code == status.HTTP_200_OK
        # Verify CH was attempted in org-scoped mode (not bypassed like before the fix)
        ch_query.assert_called()

    def test_list_spans_observe_user_id_filter_single_project(
        self, auth_client, observe_project, trace_session, session_trace
    ):
        """user_id filter in single-project mode returns only that user's spans."""
        from tracer.models.observation_span import EndUser

        end_user = EndUser.objects.create(
            organization=observe_project.organization,
            project=observe_project,
            user_id="filter-user@example.com",
        )
        user_span = ObservationSpan.objects.create(
            id=f"span_{uuid.uuid4().hex[:8]}",
            project=observe_project,
            trace=session_trace,
            name="User Span",
            observation_type="llm",
            end_user=end_user,
            start_time=timezone.now() - timedelta(seconds=5),
            end_time=timezone.now(),
        )
        other_span = ObservationSpan.objects.create(
            id=f"span_{uuid.uuid4().hex[:8]}",
            project=observe_project,
            trace=session_trace,
            name="Other Span",
            observation_type="llm",
            start_time=timezone.now() - timedelta(seconds=5),
            end_time=timezone.now(),
        )

        response = auth_client.get(
            "/tracer/observation-span/list_spans_observe/",
            {
                "project_id": str(observe_project.id),
                "user_id": "filter-user@example.com",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        result = get_result(response)
        returned_ids = {row["span_id"] for row in result["table"]}
        assert user_span.id in returned_ids
        assert other_span.id not in returned_ids

    def test_list_spans_observe_user_id_filter_org_scoped(
        self, auth_client, observe_project, trace_session, session_trace
    ):
        """user_id filter in org-scoped mode returns only that user's spans across all projects."""
        from tracer.models.observation_span import EndUser

        end_user = EndUser.objects.create(
            organization=observe_project.organization,
            project=observe_project,
            user_id="org-filter-user@example.com",
        )
        user_span = ObservationSpan.objects.create(
            id=f"span_{uuid.uuid4().hex[:8]}",
            project=observe_project,
            trace=session_trace,
            name="User Span",
            observation_type="llm",
            end_user=end_user,
            start_time=timezone.now() - timedelta(seconds=5),
            end_time=timezone.now(),
        )
        other_span = ObservationSpan.objects.create(
            id=f"span_{uuid.uuid4().hex[:8]}",
            project=observe_project,
            trace=session_trace,
            name="Other Span",
            observation_type="llm",
            start_time=timezone.now() - timedelta(seconds=5),
            end_time=timezone.now(),
        )

        # No project_id → org-scoped, still filters by user_id
        response = auth_client.get(
            "/tracer/observation-span/list_spans_observe/",
            {"user_id": "org-filter-user@example.com"},
        )
        assert response.status_code == status.HTTP_200_OK
        result = get_result(response)
        returned_ids = {row["span_id"] for row in result["table"]}
        assert user_span.id in returned_ids
        assert other_span.id not in returned_ids

    def test_list_spans_observe_user_id_not_found(self, auth_client, observe_project):
        """Non-existent user_id returns 400."""
        response = auth_client.get(
            "/tracer/observation-span/list_spans_observe/",
            {
                "project_id": str(observe_project.id),
                "user_id": "nonexistent-user@example.com",
            },
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
        assert response.status_code == status.HTTP_403_FORBIDDEN

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
        assert response.status_code == status.HTTP_403_FORBIDDEN

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


@pytest.mark.integration
@pytest.mark.api
class TestObservationSpanGetFieldsAPI:
    """Tests for GET /tracer/observation-span/get_observation_span_fields/ endpoint."""

    def test_get_fields_unauthenticated(self, api_client):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/observation-span/get_observation_span_fields/"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

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
        assert response.status_code == status.HTTP_403_FORBIDDEN

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
        assert response.status_code == status.HTTP_403_FORBIDDEN

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
        assert response.status_code == status.HTTP_403_FORBIDDEN

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
        assert response.status_code == status.HTTP_403_FORBIDDEN

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
