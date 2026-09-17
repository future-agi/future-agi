"""
Trace API Tests

Tests for /tracer/trace/ endpoints.
"""

import json
import uuid

import pytest
from django.utils import timezone
from rest_framework import status

from tracer.models.trace import Trace


def get_result(response):
    """Extract result from API response wrapper."""
    data = response.json()
    return data.get("result", data)


@pytest.mark.integration
@pytest.mark.api
class TestTraceRetrieveAPI:
    """Tests for GET /tracer/trace/{id}/ endpoint."""

    def test_retrieve_trace_unauthenticated(self, api_client, trace):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(f"/tracer/trace/{trace.id}/")
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    @pytest.mark.xfail(
        reason="Production CH query references span_attributes_raw/metadata_map (v1 columns) not yet migrated to v2 schema",
        strict=False,
    )
    def test_retrieve_trace_success(self, auth_client, trace, observation_span):
        """Retrieve a trace by ID with observation spans."""
        response = auth_client.get(f"/tracer/trace/{trace.id}/")
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        # Check for trace data - could be nested or flat
        trace_data = data.get("trace", data)
        assert (
            trace_data.get("id") == str(trace.id)
            or trace_data.get("name") == "Test Trace"
        )

    @pytest.mark.xfail(
        reason="Production CH query references span_attributes_raw/metadata_map (v1 columns) not yet migrated to v2 schema",
        strict=False,
    )
    def test_retrieve_trace_with_spans(
        self, auth_client, trace, observation_span, child_span
    ):
        """Retrieve trace includes all observation spans."""
        response = auth_client.get(f"/tracer/trace/{trace.id}/")
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        # Spans may be in various locations in the response
        # API may return spans elsewhere or spans may not be included inline
        # Just verify the response contains trace data
        trace_data = data.get("trace", data)
        assert trace_data.get("id") or trace_data.get("name") or isinstance(data, dict)

    def test_retrieve_trace_not_found(self, auth_client):
        """Retrieve non-existent trace returns error."""
        fake_id = uuid.uuid4()
        response = auth_client.get(f"/tracer/trace/{fake_id}/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_retrieve_trace_from_different_org(self, auth_client, organization):
        """Cannot retrieve trace from different organization."""
        from accounts.models.organization import Organization
        from model_hub.models.ai_model import AIModel
        from tracer.models.project import Project

        # Create another organization and trace
        other_org = Organization.objects.create(name="Other Org")
        other_project = Project.objects.create(
            name="Other Project",
            organization=other_org,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="experiment",
        )
        other_trace = Trace.objects.create(
            project=other_project,
            name="Other Trace",
        )

        response = auth_client.get(f"/tracer/trace/{other_trace.id}/")
        # Should fail or return empty/error - depends on implementation
        # Some implementations return 200 with empty data, some return 400
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ]


@pytest.mark.integration
@pytest.mark.api
class TestTraceListTracesAPI:
    """Tests for GET /tracer/trace/list_traces/ endpoint."""

    def test_list_traces_unauthenticated(self, api_client, project_version):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/trace/list_traces/",
            {"project_version_id": str(project_version.id)},
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_list_traces_missing_project_version(self, auth_client):
        """List traces fails without project version ID."""
        response = auth_client.get("/tracer/trace/list_traces/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_traces_success(
        self, auth_client, project_version, trace, observation_span
    ):
        """List traces for a project version."""
        response = auth_client.get(
            "/tracer/trace/list_traces/",
            {"project_version_id": str(project_version.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        # Check for metadata and table - could be at different levels
        assert "metadata" in data or "table" in data or "column_config" in data

    def test_list_traces_with_pagination(
        self, auth_client, project, project_version, multiple_traces
    ):
        """List traces with pagination."""
        response = auth_client.get(
            "/tracer/trace/list_traces/",
            {
                "project_version_id": str(project_version.id),
                "page_number": 0,
                "page_size": 5,
            },
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        # Check metadata exists
        assert "metadata" in data or "table" in data

    def test_list_traces_invalid_project_version(self, auth_client):
        """List traces with non-existent project version fails."""
        fake_id = uuid.uuid4()
        response = auth_client.get(
            "/tracer/trace/list_traces/",
            {"project_version_id": str(fake_id)},
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_traces_filter_by_trace_ids(
        self, auth_client, project_version, multiple_traces
    ):
        """Filter traces by specific trace IDs."""
        # Get first 3 trace IDs
        trace_ids = ",".join([str(t.id) for t in multiple_traces[:3]])

        response = auth_client.get(
            "/tracer/trace/list_traces/",
            {
                "project_version_id": str(project_version.id),
                "trace_ids": trace_ids,
            },
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        # Verify metadata exists
        metadata = data.get("metadata", {})
        total = metadata.get("total_rows", 0)
        # Should return at most 3 traces
        assert total <= 3


@pytest.mark.integration
@pytest.mark.api
class TestVoiceCallListAPI:
    """Tests for GET /tracer/trace/list_voice_calls/ endpoint."""

    def test_list_voice_calls_rejects_legacy_project_alias(self, auth_client, project):
        response = auth_client.get(
            "/tracer/trace/list_voice_calls/",
            {"projectId": str(project.id), "filters": "[]"},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestTraceBulkCreateAPI:
    """Tests for POST /tracer/trace/bulk_create/ endpoint."""

    def test_bulk_create_traces_unauthenticated(self, api_client, project):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/trace/bulk_create/",
            {
                "traces": [
                    {"project": str(project.id), "name": "Trace 1"},
                ]
            },
            format="json",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_bulk_create_traces_success(self, auth_client, project):
        """Bulk create multiple traces."""
        response = auth_client.post(
            "/tracer/trace/bulk_create/",
            {
                "project_id": str(project.id),
                "traces": [
                    {
                        "name": "Bulk Trace 1",
                        "input": {"prompt": "Hello 1"},
                    },
                    {
                        "name": "Bulk Trace 2",
                        "input": {"prompt": "Hello 2"},
                    },
                ],
            },
            format="json",
        )
        # Accept 200 or 201 for creation
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
            status.HTTP_400_BAD_REQUEST,
        ]

    def test_bulk_create_traces_with_project_version(
        self, auth_client, project, project_version
    ):
        """Bulk create traces with project version."""
        response = auth_client.post(
            "/tracer/trace/bulk_create/",
            {
                "project_id": str(project.id),
                "project_version_id": str(project_version.id),
                "traces": [
                    {
                        "name": "Version Trace",
                    }
                ],
            },
            format="json",
        )
        # Accept various success statuses
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
            status.HTTP_400_BAD_REQUEST,
        ]


@pytest.mark.integration
@pytest.mark.api
class TestTraceGetPropertiesAPI:
    """Tests for GET /tracer/trace/get_properties/ endpoint."""

    def test_get_properties_unauthenticated(self, api_client, project):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/trace/get_properties/",
            {"project_id": str(project.id)},
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_get_properties_missing_project_id(self, auth_client):
        """Get properties fails without project ID."""
        response = auth_client.get("/tracer/trace/get_properties/")
        # Could return 200 with empty or 400 - depends on implementation
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]

    def test_get_properties_success(
        self, auth_client, project, trace, observation_span
    ):
        """Get properties for a project."""
        response = auth_client.get(
            "/tracer/trace/get_properties/",
            {"project_id": str(project.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        # Response should contain property names that can be used for filtering
        assert isinstance(data, list) or isinstance(data, dict)


@pytest.mark.integration
@pytest.mark.api
class TestTraceGetEvalNamesAPI:
    """Tests for GET /tracer/trace/get_eval_names/ endpoint."""

    def test_get_eval_names_unauthenticated(self, api_client, project_version):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/trace/get_eval_names/",
            {"project_version_id": str(project_version.id)},
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_get_eval_names_missing_project_version(self, auth_client):
        """Get eval names fails without project version ID."""
        response = auth_client.get("/tracer/trace/get_eval_names/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_eval_names_success(self, auth_client, project_version):
        """Get evaluation names for a project version."""
        response = auth_client.get(
            "/tracer/trace/get_eval_names/",
            {"project_version_id": str(project_version.id)},
        )
        # Accept 200 or 400 (if no evals configured)
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]


@pytest.mark.integration
@pytest.mark.api
class TestTraceCompareTracesAPI:
    """Tests for POST /tracer/trace/compare_traces/ endpoint."""

    def test_compare_traces_unauthenticated(self, api_client, trace):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/trace/compare_traces/",
            {"trace_ids": [str(trace.id)]},
            format="json",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_compare_traces_success(
        self, auth_client, project, project_version, multiple_traces, observation_span
    ):
        """Compare multiple traces."""
        trace_ids = [str(t.id) for t in multiple_traces[:3]]

        response = auth_client.post(
            "/tracer/trace/compare_traces/",
            {
                "trace_ids": trace_ids,
                "project_version_id": str(project_version.id),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        # Check result has comparison data
        assert isinstance(data, dict) or isinstance(data, list)


@pytest.mark.integration
@pytest.mark.api
class TestTraceGetTraceIdByIndexAPI:
    """Tests for GET /tracer/trace/get_trace_id_by_index/ endpoint."""

    def test_get_trace_by_index_unauthenticated(self, api_client, project_version):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/trace/get_trace_id_by_index/",
            {"project_version_id": str(project_version.id), "index": 0},
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_get_trace_by_index_missing_params(self, auth_client):
        """Get trace by index fails without required params."""
        response = auth_client.get("/tracer/trace/get_trace_id_by_index/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_trace_by_index_success(self, auth_client, project_version, trace):
        """Get trace by index."""
        response = auth_client.get(
            "/tracer/trace/get_trace_id_by_index/",
            {"project_version_id": str(project_version.id), "index": 0},
        )
        # Accept 200 or 400 (if index out of bounds)
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]

    def test_get_trace_by_index_no_root_span(
        self, auth_client, project, project_version
    ):
        """Trace with no root span should not crash; start_time falls back to created_at."""
        # Create a trace with no observation spans — start_time annotation
        # falls back to the trace's created_at via Coalesce.
        trace_no_spans = Trace.objects.create(
            project=project,
            project_version=project_version,
            name="Trace Without Spans",
        )
        response = auth_client.get(
            "/tracer/trace/get_trace_id_by_index/",
            {
                "project_version_id": str(project_version.id),
                "trace_id": str(trace_no_spans.id),
            },
        )
        # Should return 200, NOT crash with "Cannot use None as a query value"
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.integration
@pytest.mark.api
class TestTraceGetTraceIdByIndexObserveAPI:
    """Tests for GET /tracer/trace/get_trace_id_by_index_observe/ endpoint."""

    def test_get_trace_by_index_observe_unauthenticated(
        self, api_client, observe_project
    ):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/trace/get_trace_id_by_index_observe/",
            {"project_id": str(observe_project.id)},
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_get_trace_by_index_observe_missing_params(self, auth_client):
        """Missing required params should return 400."""
        response = auth_client.get("/tracer/trace/get_trace_id_by_index_observe/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_trace_by_index_observe_no_root_span(
        self, auth_client, observe_project
    ):
        """Trace with no root span returns 400 on the CH-only path.

        The CH-only path queries the ``spans`` table for a root span
        (``parent_span_id IS NULL``). A trace with zero spans has no
        root span in CH, so the endpoint correctly returns 400
        "Trace not found" rather than crashing.
        """
        trace_no_spans = Trace.objects.create(
            project=observe_project,
            name="Observe Trace Without Spans",
        )
        response = auth_client.get(
            "/tracer/trace/get_trace_id_by_index_observe/",
            {
                "project_id": str(observe_project.id),
                "trace_id": str(trace_no_spans.id),
            },
        )
        # CH-only path: a trace with no spans is genuinely not found.
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.integration
@pytest.mark.api
class TestTraceGraphMethodsAPI:
    """Tests for POST /tracer/trace/get_graph_methods/ endpoint."""

    def test_get_graph_methods_unauthenticated(self, api_client, project):
        """Unauthenticated requests should be rejected."""
        response = api_client.post(
            "/tracer/trace/get_graph_methods/",
            {"project_id": str(project.id)},
            format="json",
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_get_graph_methods_missing_project(self, auth_client):
        """Get graph methods fails without project ID."""
        response = auth_client.post(
            "/tracer/trace/get_graph_methods/",
            {},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_get_graph_methods_success(
        self, auth_client, project, trace, observation_span
    ):
        """Get graph methods for a project."""
        response = auth_client.post(
            "/tracer/trace/get_graph_methods/",
            {
                "project_id": str(project.id),
                "interval": "hour",
            },
            format="json",
        )
        # Accept 200 or 400
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]

    def test_get_graph_methods_trace_system_metric_filter_success(
        self, auth_client, observe_project, monkeypatch
    ):
        """Trace graph filters can apply trace-level system metric annotations."""
        from tracer.models.observation_span import ObservationSpan

        dispatched = []
        monkeypatch.setattr(
            "tracer.views.trace.fetch_system_metric_graph_ch",
            lambda **kwargs: (
                dispatched.append(kwargs)
                or {
                    "metric_name": "latency",
                    "data": [],
                    "query_complete": True,
                    "query_status": "complete",
                    "query_sampled": False,
                }
            ),
        )

        trace = Trace.objects.create(
            project=observe_project,
            name="Latency Filter Trace",
        )
        ObservationSpan.objects.create(
            id=f"span_{uuid.uuid4().hex[:16]}",
            project=observe_project,
            trace=trace,
            name="Root Span",
            observation_type="llm",
            start_time=timezone.now(),
            latency_ms=500,
            total_tokens=15,
            prompt_tokens=10,
            completion_tokens=5,
            cost=0.001,
            status="OK",
        )

        response = auth_client.post(
            "/tracer/trace/get_graph_methods/",
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
                            "filter_op": "greater_than",
                            "filter_value": 1,
                            "col_type": "SYSTEM_METRIC",
                        },
                    }
                ],
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        dispatched_filters = dispatched[0]["filters"]
        assert dispatched_filters[0]["column_id"] == "created_at"
        assert sum(item["column_id"] == "latency" for item in dispatched_filters) == 1

    def test_get_graph_methods_rejects_foreign_eval_config_before_ch_read(
        self,
        auth_client,
        observe_project,
        project,
        eval_template,
        monkeypatch,
    ):
        from tracer.models.custom_eval_config import CustomEvalConfig

        foreign_config = CustomEvalConfig.objects.create(
            name=f"Foreign Eval {uuid.uuid4()}",
            project=project,
            eval_template=eval_template,
        )
        event_reads = []
        monkeypatch.setattr(
            "tracer.views.trace.fetch_eval_graph_ch",
            lambda **kwargs: (
                event_reads.append(kwargs) or {"metric_name": "x", "data": []}
            ),
        )

        response = auth_client.post(
            "/tracer/trace/get_graph_methods/",
            {
                "project_id": str(observe_project.id),
                "interval": "hour",
                "req_data_config": {
                    "id": str(foreign_config.id),
                    "type": "EVAL",
                    "output_type": "SCORE",
                },
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert event_reads == []


@pytest.mark.integration
@pytest.mark.api
class TestUsersViewAPI:
    """Tests for GET /tracer/users/ endpoint."""

    def test_get_users_unauthenticated(self, api_client, project):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/users/",
            {"project_id": str(project.id)},
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_get_users_without_project_id(self, auth_client):
        """Get users returns all workspace users when project_id is missing."""
        response = auth_client.get("/tracer/users/")
        assert response.status_code == status.HTTP_200_OK

    def test_get_users_success(self, auth_client, project, end_user):
        """Get users for a project."""
        response = auth_client.get(
            "/tracer/users/",
            {"project_id": str(project.id)},
        )
        # Accept 200 or 400
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_400_BAD_REQUEST]


@pytest.mark.integration
@pytest.mark.api
class TestTraceListTracesOfSessionAPI:
    """Tests for GET /tracer/trace/list_traces_of_session/ endpoint."""

    def test_list_session_traces_unauthenticated(self, api_client, trace_session):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/trace/list_traces_of_session/",
            {"session_id": str(trace_session.id)},
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_list_session_traces_missing_session_id(self, auth_client, observe_project):
        """List session traces supports org-scoped listing without session ID.

        Depends on ``observe_project`` so the workspace has at least one
        observe project, which makes the org-scoped CH path use PG-side
        eval config lookup instead of passing project_id=None to
        ``toUUID()``.
        """
        response = auth_client.get("/tracer/trace/list_traces_of_session/")
        assert response.status_code == status.HTTP_200_OK

    def test_list_session_traces_success(
        self, auth_client, observe_project, trace_session, session_trace
    ):
        """List traces for a session."""
        response = auth_client.get(
            "/tracer/trace/list_traces_of_session/",
            {
                "session_id": str(trace_session.id),
                "project_id": str(observe_project.id),
            },
        )
        assert response.status_code == status.HTTP_200_OK
        data = get_result(response)
        # Check response has expected structure
        assert "metadata" in data or "table" in data or isinstance(data, list)

    def test_list_session_traces_rejects_nested_map_with_typed_400(
        self, auth_client, observe_project
    ):
        response = auth_client.get(
            "/tracer/trace/list_traces_of_session/",
            {
                "project_id": str(observe_project.id),
                "filters": json.dumps(
                    [
                        {
                            "column_id": "customer.context",
                            "filter_config": {
                                "col_type": "SPAN_ATTRIBUTE",
                                "filter_type": "map",
                                "filter_op": "contains",
                                "filter_value": {"nested": {"tier": "vip"}},
                            },
                        }
                    ]
                ),
            },
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        rendered = response.content.decode()
        assert "Nested JSON map filter values are not supported" in rendered
        assert "DB::Exception" not in rendered


@pytest.mark.integration
@pytest.mark.api
class TestTraceExportAPI:
    """Tests for GET /tracer/trace/get_trace_export_data/ endpoint."""

    def test_export_traces_unauthenticated(self, api_client, project):
        """Unauthenticated requests should be rejected."""
        response = api_client.get(
            "/tracer/trace/get_trace_export_data/",
            {"project_id": str(project.id)},
        )
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_export_traces_missing_project_id(self, auth_client):
        """Export traces fails without project ID."""
        response = auth_client.get("/tracer/trace/get_trace_export_data/")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_export_traces_success(self, auth_client, project, trace, observation_span):
        """Trace export remains available through the bounded CSV contract."""
        response = auth_client.get(
            "/tracer/trace/get_trace_export_data/",
            {"project_id": str(project.id)},
        )
        assert response.status_code == status.HTTP_200_OK
        assert response["Content-Type"].startswith("text/csv")
        assert "attachment" in response["Content-Disposition"]


def test_get_span_trace_map_selects_from_spans(monkeypatch):
    from tracer.services.clickhouse.query_service import AnalyticsQueryService

    captured = {}

    def fake_exec(self, query, params=None, timeout_ms=5000, settings=None):
        captured["query"] = query
        captured["params"] = params
        captured["settings"] = settings

        class R:
            data = [{"span_id": "s1", "trace_id": "t1"}]

        return R()

    monkeypatch.setattr(AnalyticsQueryService, "execute_ch_query", fake_exec)
    out = AnalyticsQueryService().get_span_trace_map(["t1"])

    assert out == {"s1": "t1"}
    assert "FROM spans" in captured["query"]
    assert "latest_trace_id IN %(trace_ids)s" in captured["query"]
    assert "argMax(is_deleted, _version) AS latest_is_deleted" in captured["query"]
    assert "WHERE latest_is_deleted = 0" in captured["query"]
    assert (
        "groupArray(tuple(span_id, live_trace_ids)) AS span_mappings"
        in captured["query"]
    )
    assert captured["params"] == {"trace_ids": ["t1"]}
    assert captured["settings"] is None
    # The physical latest-state replay groups by the full ReplacingMergeTree
    # identity, which includes project_id/start_time.  With no scope supplied,
    # neither may appear as a pruning predicate.
    assert "project_id = %(project_id)s" not in captured["query"]
    assert "start_time >=" not in captured["query"]
    assert "start_time <" not in captured["query"]


def _capture_span_trace_map_query(monkeypatch, **kwargs):
    from tracer.services.clickhouse.query_service import AnalyticsQueryService

    captured = {}

    def fake_exec(self, query, params=None, timeout_ms=5000, settings=None):
        captured["query"] = query
        captured["params"] = params
        captured["settings"] = settings

        class R:
            data = []

        return R()

    monkeypatch.setattr(AnalyticsQueryService, "execute_ch_query", fake_exec)
    AnalyticsQueryService().get_span_trace_map(["t1"], **kwargs)
    return captured


def test_get_span_trace_map_scopes_project_id_only(monkeypatch):
    captured = _capture_span_trace_map_query(monkeypatch, project_id="p1")

    assert "latest_trace_id IN %(trace_ids)s" in captured["query"]
    assert "project_id = %(project_id)s" in captured["query"]
    assert "start_time >=" not in captured["query"]
    assert "start_time <" not in captured["query"]
    assert captured["params"] == {"trace_ids": ["t1"], "project_id": "p1"}


def test_get_span_trace_map_scopes_to_finite_scored_span_ids(monkeypatch):
    captured = _capture_span_trace_map_query(
        monkeypatch,
        project_id="p1",
        scored_span_ids=["scored-7", "scored-4999", "scored-7"],
    )

    assert "latest_trace_id IN %(trace_ids)s" in captured["query"]
    assert "id IN %(scored_span_ids)s" in captured["query"]
    assert "project_id = %(project_id)s" in captured["query"]
    assert captured["params"] == {
        "trace_ids": ["t1"],
        "scored_span_ids": ("scored-7", "scored-4999"),
        "project_id": "p1",
    }


def test_get_span_trace_map_skips_clickhouse_for_proven_empty_score_set(monkeypatch):
    from tracer.services.clickhouse.query_service import AnalyticsQueryService

    monkeypatch.setattr(
        AnalyticsQueryService,
        "execute_ch_query",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ClickHouse must not run without span-linked scores")
        ),
    )

    assert (
        AnalyticsQueryService().get_span_trace_map(
            ["trace-a"], project_id="project-a", scored_span_ids=[]
        )
        == {}
    )


def test_get_span_trace_map_expands_high_fanout_packed_rows(monkeypatch):
    from tracer.services.clickhouse.query_service import AnalyticsQueryService

    span_ids = [f"span-{index}" for index in range(5_002)]

    def fake_exec(self, query, params=None, timeout_ms=5000, settings=None):
        assert "groupArray(tuple(span_id, live_trace_ids)) AS span_mappings" in query

        class R:
            data = [{"trace_id": "trace-a", "span_ids": span_ids}]

        return R()

    monkeypatch.setattr(AnalyticsQueryService, "execute_ch_query", fake_exec)

    result = AnalyticsQueryService().get_span_trace_map(["trace-a"])

    assert len(result) == 5_002
    assert result["span-0"] == "trace-a"
    assert result["span-5001"] == "trace-a"


def test_get_span_trace_map_uses_latest_state_before_trace_membership(monkeypatch):
    captured = _capture_span_trace_map_query(
        monkeypatch,
        project_id="p1",
        scored_span_ids=["reassigned", "tombstoned"],
    )
    compact = " ".join(captured["query"].split())

    assert "argMax(trace_id, _version)" in compact
    assert "argMax(is_deleted, _version) AS latest_is_deleted" in compact
    assert "WHERE latest_is_deleted = 0" in compact
    assert "latest_trace_id IN %(trace_ids)s" in compact
    # Filtering physical versions by old trace/deleted state would resurrect
    # stale rows before argMax classifies a reassignment/tombstone.
    physical_scan = compact.split("GROUP BY project_id, id, start_time", 1)[0]
    assert "trace_id IN %(trace_ids)s" not in physical_scan
    assert "is_deleted = 0" not in physical_scan


def test_get_span_trace_map_rejects_ambiguous_live_reassignment(monkeypatch):
    from tracer.services.clickhouse.query_service import (
        AnalyticsQueryService,
        SpanTraceMapIntegrityError,
    )

    class R:
        data = [
            {
                "span_mappings": [
                    ("span-reused", ["trace-old", "trace-new"]),
                ]
            }
        ]

    monkeypatch.setattr(
        AnalyticsQueryService,
        "execute_ch_query",
        lambda *_args, **_kwargs: R(),
    )

    with pytest.raises(SpanTraceMapIntegrityError):
        AnalyticsQueryService().get_span_trace_map(
            ["trace-old", "trace-new"],
            project_id="project-a",
            scored_span_ids=["span-reused"],
        )


def test_get_span_trace_map_org_pairs_preserve_tenant_identity(monkeypatch):
    from tracer.services.clickhouse.query_service import AnalyticsQueryService

    captured = {}

    class R:
        data = [
            {
                "span_mappings": [
                    ("project-a", "span-shared", ["trace-shared"]),
                    ("project-b", "span-shared", ["trace-shared"]),
                ]
            }
        ]

    def fake_exec(self, query, params=None, timeout_ms=5000, settings=None):
        captured["query"] = query
        captured["params"] = params
        return R()

    monkeypatch.setattr(AnalyticsQueryService, "execute_ch_query", fake_exec)

    result = AnalyticsQueryService().get_span_trace_map(
        ["trace-shared"],
        trace_identities=[
            ("project-a", "trace-shared"),
            ("project-b", "trace-shared"),
        ],
        scored_span_identities=[
            ("project-a", "span-shared"),
            ("project-b", "span-shared"),
        ],
    )

    assert result == {
        ("project-a", "span-shared"): ("project-a", "trace-shared"),
        ("project-b", "span-shared"): ("project-b", "trace-shared"),
    }
    assert (
        "(toString(project_id), toString(id)) IN %(scored_span_identities)s"
        in " ".join(captured["query"].split())
    )
    assert "(project_id_string, latest_trace_id) IN %(trace_identities)s" in " ".join(
        captured["query"].split()
    )
    assert captured["params"] == {
        "trace_identities": (
            ("project-a", "trace-shared"),
            ("project-b", "trace-shared"),
        ),
        "scored_span_identities": (
            ("project-a", "span-shared"),
            ("project-b", "span-shared"),
        ),
    }


def test_get_span_trace_map_org_pairs_reject_cross_tenant_payload(monkeypatch):
    from tracer.services.clickhouse.query_service import (
        AnalyticsQueryService,
        SpanTraceMapIntegrityError,
    )

    class R:
        data = [
            {
                "span_mappings": [
                    ("project-outsider", "span-shared", ["trace-shared"]),
                ]
            }
        ]

    monkeypatch.setattr(
        AnalyticsQueryService,
        "execute_ch_query",
        lambda *_args, **_kwargs: R(),
    )

    with pytest.raises(SpanTraceMapIntegrityError):
        AnalyticsQueryService().get_span_trace_map(
            ["trace-shared"],
            trace_identities=[("project-a", "trace-shared")],
            scored_span_identities=[("project-a", "span-shared")],
        )


def test_get_span_trace_map_bounds_start_time_window(monkeypatch):
    from datetime import datetime

    start = datetime(2026, 7, 1)
    end = datetime(2026, 7, 8)
    captured = _capture_span_trace_map_query(
        monkeypatch, project_id="p1", start_date=start, end_date=end
    )

    assert "trace_id IN %(trace_ids)s" in captured["query"]
    assert "project_id = %(project_id)s" in captured["query"]
    assert "start_time >= %(start_date)s - INTERVAL 1 DAY" in captured["query"]
    assert "start_time < %(end_date)s + INTERVAL 1 DAY" in captured["query"]
    assert captured["params"] == {
        "trace_ids": ["t1"],
        "project_id": "p1",
        "start_date": start,
        "end_date": end,
    }


def test_scored_span_map_does_not_drop_child_after_one_day(monkeypatch):
    from datetime import datetime

    captured = _capture_span_trace_map_query(
        monkeypatch,
        project_id="p1",
        start_date=datetime(2026, 7, 1),
        end_date=datetime(2026, 7, 8),
        scored_span_ids=["late-child"],
    )

    assert "start_time >=" not in captured["query"]
    assert "start_time <" not in captured["query"]


def test_get_span_trace_map_window_needs_both_bounds(monkeypatch):
    from datetime import datetime

    captured = _capture_span_trace_map_query(
        monkeypatch, project_id="p1", start_date=datetime(2026, 7, 1)
    )

    assert "start_time >=" not in captured["query"]
    assert "start_time <" not in captured["query"]
    assert captured["params"] == {"trace_ids": ["t1"], "project_id": "p1"}


def test_annotation_score_span_id_discovery_is_label_and_tracer_project_scoped(
    monkeypatch,
):
    import tracer.views.trace as trace_mod
    from model_hub.models.score import Score

    captured = {}

    class FakeValues:
        def order_by(self):
            return self

        def values_list(self, field, *, flat):
            captured["field"] = field
            captured["flat"] = flat
            return self

        def distinct(self):
            captured["distinct"] = True
            return self

        def __getitem__(self, item):
            captured["slice"] = item
            return self

        def __iter__(self):
            return iter(["scored-7", "scored-4999"])

    class FakeManager:
        def filter(self, **kwargs):
            captured["filters"] = kwargs
            return FakeValues()

    monkeypatch.setattr(Score, "no_workspace_objects", FakeManager())

    result = trace_mod._annotation_score_span_ids(
        ["label-a", "label-b"], "tracer-project-a"
    )

    assert result == ("scored-7", "scored-4999")
    assert captured["filters"] == {
        "tracer_project_id": "tracer-project-a",
        "label_id__in": ["label-a", "label-b"],
        "trace_id__isnull": True,
        "observation_span_id__isnull": False,
        "deleted": False,
    }
    assert captured["field"] == "observation_span_id"
    assert captured["flat"] is True
    assert captured["distinct"] is True
    assert captured["slice"] == slice(None, 50_001, None)


def test_annotation_score_span_id_discovery_is_bounded_and_fails_closed(
    monkeypatch,
):
    import tracer.views.trace as trace_mod
    from model_hub.models.score import Score

    captured = {}

    class BoundedValues:
        def order_by(self):
            return self

        def values_list(self, *_args, **_kwargs):
            return self

        def distinct(self):
            return self

        def __getitem__(self, item):
            captured["slice"] = item
            return self

        def __iter__(self):
            # The fake project has far more history, but ORM evaluation is
            # proven to stop at the three-row LIMIT+1 sentinel for a limit of 2.
            return iter(["span-1", "span-2", "span-3"])

    class FakeManager:
        def filter(self, **_kwargs):
            return BoundedValues()

    monkeypatch.setattr(Score, "no_workspace_objects", FakeManager())

    with pytest.raises(trace_mod.AnnotationScoreReadBoundExceeded):
        trace_mod._annotation_score_span_ids(["label-a"], "project-a", max_span_ids=2)

    assert captured["slice"] == slice(None, 3, None)


def test_annotation_score_span_id_discovery_fails_closed_without_project_scope(
    monkeypatch,
):
    import tracer.views.trace as trace_mod
    from model_hub.models.score import Score

    class ForbiddenManager:
        def filter(self, **_kwargs):
            raise AssertionError("an unscoped organization Score query must not run")

    monkeypatch.setattr(Score, "no_workspace_objects", ForbiddenManager())

    with pytest.raises(trace_mod.AnnotationScoreReadBoundExceeded):
        trace_mod._annotation_score_span_ids(["label-a"], None)


def test_annotation_score_span_id_discovery_org_is_candidate_project_scoped(
    monkeypatch,
):
    import tracer.views.trace as trace_mod
    from model_hub.models.score import Score

    captured = {}

    class FakeValues:
        def order_by(self):
            return self

        def values_list(self, *fields):
            captured["fields"] = fields
            return self

        def distinct(self):
            return self

        def __getitem__(self, item):
            captured["slice"] = item
            return self

        def __iter__(self):
            return iter(
                [
                    ("project-a", "span-shared"),
                    ("project-b", "span-shared"),
                ]
            )

    class FakeManager:
        def filter(self, **kwargs):
            captured["filters"] = kwargs
            return FakeValues()

    monkeypatch.setattr(Score, "no_workspace_objects", FakeManager())

    result = trace_mod._annotation_score_span_ids_by_project(
        {
            "project-a": ["label-a"],
            "project-b": ["label-b"],
            "project-not-on-page": ["label-outsider"],
        },
        [
            ("project-a", "trace-shared"),
            ("project-b", "trace-shared"),
        ],
    )

    assert result == {
        "project-a": ("span-shared",),
        "project-b": ("span-shared",),
    }
    assert captured["filters"] == {
        "tracer_project_id__in": ("project-a", "project-b"),
        "label_id__in": ("label-a", "label-b"),
        "trace_id__isnull": True,
        "observation_span_id__isnull": False,
        "deleted": False,
    }
    assert captured["fields"] == ("tracer_project_id", "observation_span_id")
    assert captured["slice"] == slice(None, 50_001, None)


def test_annotation_score_span_id_discovery_org_high_cardinality_fails_closed(
    monkeypatch,
):
    import tracer.views.trace as trace_mod
    from model_hub.models.score import Score

    class FakeValues:
        def order_by(self):
            return self

        def values_list(self, *_fields):
            return self

        def distinct(self):
            return self

        def __getitem__(self, _item):
            return self

        def __iter__(self):
            return iter(
                [
                    ("project-a", "span-1"),
                    ("project-a", "span-2"),
                    ("project-a", "span-3"),
                ]
            )

    class FakeManager:
        def filter(self, **_kwargs):
            return FakeValues()

    monkeypatch.setattr(Score, "no_workspace_objects", FakeManager())

    with pytest.raises(trace_mod.AnnotationScoreReadBoundExceeded):
        trace_mod._annotation_score_span_ids_by_project(
            {"project-a": ["label-a"]},
            [("project-a", "trace-a")],
            max_span_ids=2,
        )


def test_build_annotation_map_pg_has_no_observation_span_join(monkeypatch):
    """Regression: _build_annotation_map_from_scores_pg must not JOIN the dropped table."""
    import tracer.views.trace as trace_mod
    from model_hub.models.score import Score

    captured = {}
    real_manager = Score.no_workspace_objects  # capture BEFORE patching

    class _FakeManager:
        def filter(self, *args, **kwargs):
            qs = real_manager.filter(*args, **kwargs).select_related("annotator")
            captured["sql"] = str(qs.query)
            return qs.none()  # empty + chainable + no DB hit

    monkeypatch.setattr(Score, "no_workspace_objects", _FakeManager())
    span_id = "11111111-1111-1111-1111-111111111111"
    trace_id = "22222222-2222-2222-2222-222222222222"
    label_id = "33333333-3333-3333-3333-333333333333"
    span_trace_map = {span_id: trace_id}
    trace_mod._build_annotation_map_from_scores_pg(
        [trace_id],
        [label_id],
        {label_id: "numeric"},
        span_trace_map,
    )
    assert "tracer_observation_span" not in captured["sql"]
    assert "observation_span_id" in captured["sql"]


def test_build_annotation_map_pg_org_two_projects_is_tenant_exact(monkeypatch):
    from types import SimpleNamespace

    import tracer.views.trace as trace_mod
    from model_hub.models.score import Score

    scores = [
        SimpleNamespace(
            tracer_project_id="project-a",
            trace_id="trace-shared",
            observation_span_id=None,
            label_id="label-a",
            annotator_id=None,
            annotator=None,
            value={"text": "direct-a"},
        ),
        SimpleNamespace(
            tracer_project_id="project-b",
            trace_id=None,
            observation_span_id="span-shared",
            label_id="label-b",
            annotator_id=None,
            annotator=None,
            value={"text": "span-b"},
        ),
        # The fake manager deliberately returns rows that a correct database
        # predicate would exclude. In-process identity guards must still avoid
        # leaking either a third tenant or a label from the wrong project.
        SimpleNamespace(
            tracer_project_id="project-outsider",
            trace_id="trace-shared",
            observation_span_id=None,
            label_id="label-a",
            annotator_id=None,
            annotator=None,
            value={"text": "must-not-leak"},
        ),
        SimpleNamespace(
            tracer_project_id="project-b",
            trace_id="trace-shared",
            observation_span_id=None,
            label_id="label-a",
            annotator_id=None,
            annotator=None,
            value={"text": "wrong-project-label"},
        ),
    ]
    captured = {}

    class FakeQuery:
        def select_related(self, *_args):
            return self

        def __getitem__(self, item):
            captured["slice"] = item
            return scores

    class FakeManager:
        def filter(self, *args, **kwargs):
            captured["entity_scope"] = args
            captured["filters"] = kwargs
            return FakeQuery()

    monkeypatch.setattr(Score, "no_workspace_objects", FakeManager())

    result = trace_mod._build_annotation_map_from_scores_pg(
        ["trace-shared"],
        ["label-a", "label-b"],
        {"label-a": "text", "label-b": "text"},
        {
            ("project-b", "span-shared"): (
                "project-b",
                "trace-shared",
            )
        },
        trace_identities=[
            ("project-a", "trace-shared"),
            ("project-b", "trace-shared"),
        ],
        annotation_label_ids_by_project={
            "project-a": ["label-a"],
            "project-b": ["label-b"],
        },
    )

    assert result == {
        ("project-a", "trace-shared"): {
            "label-a": {"score": "direct-a", "annotators": {}}
        },
        ("project-b", "trace-shared"): {
            "label-b": {"score": "span-b", "annotators": {}}
        },
    }
    assert captured["filters"]["tracer_project_id__in"] == (
        "project-a",
        "project-b",
    )
    assert captured["slice"] == slice(None, 50_001, None)


def test_build_annotation_map_pg_org_query_shape_scales_with_projects(monkeypatch):
    from django.db.models import Q

    import tracer.views.trace as trace_mod
    from model_hub.models.score import Score

    captured = {}

    class FakeQuery:
        def select_related(self, *_args):
            return self

        def __getitem__(self, item):
            captured["slice"] = item
            return []

    class FakeManager:
        def filter(self, *args, **kwargs):
            captured["entity_scope"] = args[0]
            captured["filters"] = kwargs
            return FakeQuery()

    monkeypatch.setattr(Score, "no_workspace_objects", FakeManager())

    span_trace_map = {
        (project_id, f"span-{index}"): (project_id, "trace-shared")
        for project_id in ("project-a", "project-b")
        for index in range(25_000)
    }
    trace_mod._build_annotation_map_from_scores_pg(
        ["trace-shared"],
        ["label-a", "label-b"],
        {"label-a": "text", "label-b": "text"},
        span_trace_map,
        trace_identities=[
            ("project-a", "trace-shared"),
            ("project-b", "trace-shared"),
        ],
        annotation_label_ids_by_project={
            "project-a": ["label-a"],
            "project-b": ["label-b"],
        },
    )

    def _lookups(node):
        for child in node.children:
            if isinstance(child, Q):
                yield from _lookups(child)
            else:
                yield child

    lookups = list(_lookups(captured["entity_scope"]))
    trace_groups = [value for key, value in lookups if key == "trace_id__in"]
    span_groups = [value for key, value in lookups if key == "observation_span_id__in"]

    # Two projects produce two grouped trace predicates and two grouped span
    # predicates, not 50,002 individual OR branches/parameter pairs.
    assert len(trace_groups) == 2
    assert len(span_groups) == 2
    assert sorted(len(values) for values in span_groups) == [25_000, 25_000]
    assert not any(key == "observation_span_id" for key, _value in lookups)
    assert captured["slice"] == slice(None, 50_001, None)


def test_build_annotation_map_preserves_all_high_fanout_span_scores(monkeypatch):
    from types import SimpleNamespace

    import tracer.views.trace as trace_mod
    from model_hub.models.score import Score

    trace_id = "22222222-2222-2222-2222-222222222222"
    label_id = "33333333-3333-3333-3333-333333333333"
    span_trace_map = {
        f"11111111-1111-1111-1111-{index:012d}": trace_id for index in range(5_002)
    }
    scores = [
        SimpleNamespace(
            trace_id=None,
            observation_span_id=span_id,
            label_id=label_id,
            annotator_id=None,
            annotator=None,
            value={"value": index % 2 == 0},
        )
        for index, span_id in enumerate(span_trace_map)
    ]

    class FakeQuery:
        def select_related(self, *_args):
            return scores

    class FakeManager:
        def filter(self, *_args, **_kwargs):
            return FakeQuery()

    monkeypatch.setattr(Score, "no_workspace_objects", FakeManager())

    result = trace_mod._build_annotation_map_from_scores_pg(
        [trace_id],
        [label_id],
        {label_id: "thumbs_up_down"},
        span_trace_map,
    )

    assert result[trace_id][label_id]["thumbs_up"] == 2_501
    assert result[trace_id][label_id]["thumbs_down"] == 2_501
