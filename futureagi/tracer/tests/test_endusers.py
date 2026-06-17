import json
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import set_workspace_context
from tracer.models.observation_span import EndUser
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.services.clickhouse.query_service import QueryResult
from tracer.utils.helper import get_default_project_version_config

User = get_user_model()


# Columns returned by UserListQueryBuilder.build() final SELECT (plus total_count from
# the windowed counted_rows CTE). Keep this aligned with
# tracer/services/clickhouse/query_builders/user_list.py.
_USER_LIST_COLUMNS = [
    "user_id",
    "total_cost",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "num_traces",
    "num_sessions",
    "avg_session_duration",
    "avg_trace_latency",
    "num_llm_calls",
    "num_guardrails_triggered",
    "activated_at",
    "last_active",
    "num_active_days",
    "num_traces_with_errors",
    "bool_eval_pass_rate",
    "avg_output_float",
    "project_id",
    "user_id_type",
    "user_id_hash",
    "end_user_id",
    "total_count",
]


def _user_row(
    *,
    user_id,
    total_cost,
    total_tokens,
    input_tokens,
    output_tokens,
    num_traces,
    num_sessions,
    avg_session_duration,
    avg_trace_latency,
    num_llm_calls,
    num_guardrails_triggered,
    activated_at,
    last_active,
    num_active_days,
    num_traces_with_errors,
    bool_eval_pass_rate,
    avg_output_float,
    project_id,
    user_id_type,
    user_id_hash,
    end_user_id,
    total_count,
):
    """Build a dict row matching UserListQueryBuilder.build()'s SELECT columns."""
    return {
        "user_id": user_id,
        "total_cost": total_cost,
        "total_tokens": total_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "num_traces": num_traces,
        "num_sessions": num_sessions,
        "avg_session_duration": avg_session_duration,
        "avg_trace_latency": avg_trace_latency,
        "num_llm_calls": num_llm_calls,
        "num_guardrails_triggered": num_guardrails_triggered,
        "activated_at": activated_at,
        "last_active": last_active,
        "num_active_days": num_active_days,
        "num_traces_with_errors": num_traces_with_errors,
        "bool_eval_pass_rate": bool_eval_pass_rate,
        "avg_output_float": avg_output_float,
        "project_id": project_id,
        "user_id_type": user_id_type,
        "user_id_hash": user_id_hash,
        "end_user_id": end_user_id,
        "total_count": total_count,
    }


def _make_user_list_result(rows):
    """Wrap a list of UserListQueryBuilder row dicts in a QueryResult."""
    return QueryResult(
        data=list(rows),
        row_count=len(rows),
        backend_used="clickhouse",
        query_time_ms=0.0,
        columns=list(_USER_LIST_COLUMNS),
    )


def _empty_enrichment_result():
    """Empty QueryResult used for the per-user span-attribute enrichment query."""
    return QueryResult(
        data=[],
        row_count=0,
        backend_used="clickhouse",
        query_time_ms=0.0,
        columns=[
            "end_user_id",
            "span_attributes_raw",
            "span_attr_str",
            "span_attr_num",
        ],
    )


_EXECUTE_CH_PATH = (
    "tracer.services.clickhouse.query_service.AnalyticsQueryService.execute_ch_query"
)


def _canonical_span_attr_filter(filter_op="equals", filter_value="alpha"):
    return {
        "column_id": "customer_tier",
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": "text",
            "filter_op": filter_op,
            "filter_value": filter_value,
        },
    }


@pytest.mark.integration
@pytest.mark.core_backend
class TestUsersViewAPI(APITestCase):
    """Test cases for UsersView API endpoint"""

    def setUp(self):
        """Set up test data"""
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="test@example.com", name="Test User", password="testpass123"
        )
        self.organization = Organization.objects.create(name="Test Org")

        # Associate user with organization
        self.user.organization = self.organization
        self.user.organization_role = OrganizationRoles.OWNER
        self.user.save()

        # Create workspace
        self.workspace = Workspace.objects.create(
            name="Test Workspace",
            organization=self.organization,
            is_default=True,
            created_by=self.user,
        )

        # Set workspace context for signals
        set_workspace_context(workspace=self.workspace, organization=self.organization)

        # Authenticate the client
        self.client.force_authenticate(user=self.user)

        # Patch APIView.initial to inject workspace for all requests in this test class
        from rest_framework.views import APIView

        self.original_initial = APIView.initial
        workspace = self.workspace

        def initial_with_workspace(view_self, request, *args, **view_kwargs):
            # Inject workspace before view processing
            request.workspace = workspace
            return self.original_initial(view_self, request, *args, **view_kwargs)

        self.workspace_patcher = patch.object(
            APIView, "initial", initial_with_workspace
        )
        self.workspace_patcher.start()

        # Test data
        self.test_project = Project.objects.create(
            name="Test Project",
            organization=self.organization,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            config=get_default_project_version_config(),
        )

        self.test_project_id = str(self.test_project.id)

        self.url = "/tracer/users/"

    def tearDown(self):
        self.workspace_patcher.stop()
        super().tearDown()

    @patch(_EXECUTE_CH_PATH)
    def test_users_list_success_basic(self, mock_get_spans):
        """Test successful basic users list request"""
        mock_rows = [
            _user_row(
                user_id="user1",
                total_cost=10.50,
                total_tokens=1000,
                input_tokens=500,
                output_tokens=500,
                num_traces=5,
                num_sessions=2,
                avg_session_duration=300.0,
                avg_trace_latency=150.0,
                num_llm_calls=10,
                num_guardrails_triggered=1,
                activated_at="2024-01-01",
                last_active="2024-01-15",
                num_active_days=10,
                num_traces_with_errors=0,
                bool_eval_pass_rate=0.85,
                avg_output_float=4.2,
                project_id=self.test_project_id,
                user_id_type="email",
                user_id_hash="hash123",
                end_user_id="end-user-1",
                total_count=2,
            ),
            _user_row(
                user_id="user2",
                total_cost=25.75,
                total_tokens=2000,
                input_tokens=1000,
                output_tokens=1000,
                num_traces=10,
                num_sessions=3,
                avg_session_duration=450.0,
                avg_trace_latency=200.0,
                num_llm_calls=20,
                num_guardrails_triggered=2,
                activated_at="2024-01-02",
                last_active="2024-01-16",
                num_active_days=15,
                num_traces_with_errors=1,
                bool_eval_pass_rate=0.92,
                avg_output_float=3.8,
                project_id=self.test_project_id,
                user_id_type="email",
                user_id_hash="hash456",
                end_user_id="end-user-2",
                total_count=2,
            ),
        ]
        mock_get_spans.side_effect = [
            _make_user_list_result(mock_rows),
            _empty_enrichment_result(),
        ]

        params = {
            "project_id": self.test_project_id,
            "page_size": 10,
            "current_page_index": 0,
        }

        response = self.client.get(self.url, params)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("table", response.data["result"])
        self.assertIn("total_count", response.data["result"])
        self.assertIn("total_pages", response.data["result"])

        # Check response structure
        self.assertEqual(len(response.data["result"]["table"]), 2)
        self.assertEqual(response.data["result"]["total_count"], 2)
        self.assertEqual(response.data["result"]["total_pages"], 1)

        # Check first user data
        first_user = response.data["result"]["table"][0]
        self.assertEqual(first_user["user_id"], "user1")
        self.assertEqual(first_user["total_cost"], 10.50)
        self.assertEqual(first_user["total_tokens"], 1000)

    @patch(_EXECUTE_CH_PATH)
    def test_users_list_empty_search_stripped(self, mock_get_spans):
        """Test that empty search strings are properly handled"""
        mock_get_spans.side_effect = [
            _make_user_list_result([]),
            _empty_enrichment_result(),
        ]

        data = {"project_id": self.test_project_id, "search": "   "}  # Whitespace only

        response = self.client.get(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify search_name is None when search is empty/whitespace
        mock_get_spans.assert_called_once()

    def test_users_list_unauthenticated(self):
        """Test that unauthenticated requests are rejected"""
        # Remove authentication
        self.client.force_authenticate(user=None)

        data = {"project_id": self.test_project_id}

        response = self.client.get(self.url, data)

        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    @patch(_EXECUTE_CH_PATH)
    def test_users_list_sql_exception_handling(self, mock_get_spans):
        """Test exception handling when SQL query fails"""
        # Mock ClickHouse exception
        mock_get_spans.side_effect = Exception("Database connection error")

        data = {"project_id": self.test_project_id}

        response = self.client.get(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error fetching users", str(response.data["result"]))

    @patch(_EXECUTE_CH_PATH)
    def test_users_list_page_calculation_exact_division(self, mock_get_spans):
        """Test page calculation when count divides evenly by page_size"""
        mock_rows = [
            _user_row(
                user_id="user1",
                total_cost=10.50,
                total_tokens=1000,
                input_tokens=500,
                output_tokens=500,
                num_traces=5,
                num_sessions=2,
                avg_session_duration=300.0,
                avg_trace_latency=150.0,
                num_llm_calls=10,
                num_guardrails_triggered=1,
                activated_at="2024-01-01",
                last_active="2024-01-15",
                num_active_days=10,
                num_traces_with_errors=0,
                bool_eval_pass_rate=0.85,
                avg_output_float=4.2,
                project_id=self.test_project_id,
                user_id_type="email",
                user_id_hash="hash201",
                end_user_id="end-user-5",
                total_count=20,
            )
        ]
        mock_get_spans.side_effect = [
            _make_user_list_result(mock_rows),
            _empty_enrichment_result(),
        ]

        data = {"project_id": self.test_project_id, "page_size": 10}

        response = self.client.get(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["result"]["total_pages"], 2)  # 20/10 = 2

    @patch(_EXECUTE_CH_PATH)
    def test_users_list_page_calculation_with_remainder(self, mock_get_spans):
        """Test page calculation when count has remainder"""
        mock_rows = [
            _user_row(
                user_id="user1",
                total_cost=10.50,
                total_tokens=1000,
                input_tokens=500,
                output_tokens=500,
                num_traces=5,
                num_sessions=2,
                avg_session_duration=300.0,
                avg_trace_latency=150.0,
                num_llm_calls=10,
                num_guardrails_triggered=1,
                activated_at="2024-01-01",
                last_active="2024-01-15",
                num_active_days=10,
                num_traces_with_errors=0,
                bool_eval_pass_rate=0.85,
                avg_output_float=4.2,
                project_id=self.test_project_id,
                user_id_type="email",
                user_id_hash="hash301",
                end_user_id="end-user-6",
                total_count=23,
            )
        ]
        mock_get_spans.side_effect = [
            _make_user_list_result(mock_rows),
            _empty_enrichment_result(),
        ]

        data = {"project_id": self.test_project_id, "page_size": 10}

        response = self.client.get(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["result"]["total_pages"], 3
        )  # 23/10 = 2 + 1 for remainder

    @patch(_EXECUTE_CH_PATH)
    def test_users_list_invalid_column_in_sort(self, mock_get_spans):
        """Test handling of invalid column in sort parameters"""
        mock_get_spans.side_effect = [
            _make_user_list_result([]),
            _empty_enrichment_result(),
        ]

        data = {
            "project_id": self.test_project_id,
            "sort_params": json.dumps(
                [{"column_id": "invalid_column", "direction": "asc"}]
            ),
        }

        response = self.client.get(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Invalid column should result in None for sort_by
        mock_get_spans.assert_called_once()
        call_args = mock_get_spans.call_args[1]
        self.assertNotIn("sort_by", call_args)  # None values are filtered out


@pytest.mark.integration
@pytest.mark.core_backend
class TestUserMetricsAndGraphAPI(APITestCase):
    """Test cases for User Metrics and Graph Data API endpoints"""

    def setUp(self):
        """Set up test data"""
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="test@example.com", name="Test User", password="testpass123"
        )
        self.organization = Organization.objects.create(name="Test Org")

        # Associate user with organization
        self.user.organization = self.organization
        self.user.organization_role = OrganizationRoles.OWNER
        self.user.save()

        # Create workspace
        self.workspace = Workspace.objects.create(
            name="Test Workspace",
            organization=self.organization,
            is_default=True,
            created_by=self.user,
        )

        # Set workspace context for signals
        set_workspace_context(workspace=self.workspace, organization=self.organization)

        # Authenticate the client
        self.client.force_authenticate(user=self.user)

        # Patch APIView.initial to inject workspace for all requests in this test class
        from rest_framework.views import APIView

        self.original_initial = APIView.initial
        workspace = self.workspace

        def initial_with_workspace(view_self, request, *args, **view_kwargs):
            # Inject workspace before view processing
            request.workspace = workspace
            return self.original_initial(view_self, request, *args, **view_kwargs)

        self.workspace_patcher = patch.object(
            APIView, "initial", initial_with_workspace
        )
        self.workspace_patcher.start()

        # Test data
        self.test_project = Project.objects.create(
            name="Test Project",
            organization=self.organization,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            config=get_default_project_version_config(),
        )

        self.trace = Trace.objects.create(
            name="Test Trace",
            project=self.test_project,
            input="[]",
            output="LLM RESPONSE",
        )

        self.test_project_id = str(self.test_project.id)
        self.test_user_id = "user-123"
        self.base_url = "/tracer/project/"

        # Create test EndUser
        self.end_user = EndUser.objects.create(
            user_id=self.test_user_id,
            organization=self.organization,
            project=self.test_project,
        )

    def tearDown(self):
        self.workspace_patcher.stop()
        super().tearDown()

    # ============ GET USER METRICS TESTS ============

    @patch(_EXECUTE_CH_PATH)
    def test_get_user_metrics_success(self, mock_get_spans):
        """Test successful get_user_metrics request"""
        # Post-CH25, both active_days/last_active and the spans-derived totals
        # come from a single UserListQueryBuilder row. Preserve the assertion
        # expectations from the legacy two-call mock by setting num_active_days
        # to 15 and last_active to 2024-01-15T10:30:00Z (formerly from
        # get_user_default_details) while keeping the rest from the spans tuple.
        mock_rows = [
            _user_row(
                user_id=self.test_user_id,
                total_cost=25.50,
                total_tokens=2000,
                input_tokens=1000,
                output_tokens=1000,
                num_traces=10,
                num_sessions=5,
                avg_session_duration=400.0,
                avg_trace_latency=180.0,
                num_llm_calls=20,
                num_guardrails_triggered=2,
                activated_at="2024-01-01",
                last_active="2024-01-15T10:30:00Z",
                num_active_days=15,
                num_traces_with_errors=1,
                bool_eval_pass_rate=0.85,
                avg_output_float=4.2,
                project_id=self.test_project_id,
                user_id_type="email",
                user_id_hash="hash123",
                end_user_id=str(self.end_user.id),
                total_count=1,
            )
        ]
        mock_get_spans.return_value = _make_user_list_result(mock_rows)

        url = f"{self.base_url}get_user_metrics/"
        data = {
            "end_user_id": str(self.end_user.id),
            "project_id": self.test_project_id,
            "filters": [],
        }

        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data["result"], list)
        self.assertEqual(len(response.data["result"]), 1)

        # Check response structure
        user_metrics = response.data["result"][0]
        self.assertEqual(user_metrics["user_id"], self.test_user_id)
        self.assertEqual(user_metrics["active_days"], 15)
        self.assertEqual(user_metrics["last_active"], "2024-01-15T10:30:00Z")
        self.assertEqual(user_metrics["total_cost"], 25.50)
        self.assertEqual(user_metrics["total_tokens"], 2000)
        self.assertEqual(user_metrics["avg_session_duration"], 400.0)
        self.assertEqual(user_metrics["avg_trace_latency"], 180.0)
        self.assertEqual(user_metrics["num_llm_calls"], 20)
        self.assertEqual(user_metrics["num_guardrails_triggered"], 2)
        self.assertEqual(user_metrics["num_traces_with_errors"], 1)
        self.assertEqual(user_metrics["num_sessions"], 5)

    def test_get_user_metrics_missing_project_id(self):
        """Test get_user_metrics with missing project_id"""
        url = f"{self.base_url}get_user_metrics/"
        data = {"end_user_id": str(self.end_user.id), "filters": []}

        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("project_id", str(response.data["result"]))

    def test_get_user_metrics_missing_user_id(self):
        """Test get_user_metrics with missing end_user_id"""
        url = f"{self.base_url}get_user_metrics/"
        data = {"project_id": self.test_project_id, "filters": []}

        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("end_user_id", str(response.data["result"]))

    @patch(_EXECUTE_CH_PATH)
    def test_get_user_metrics_sql_exception(self, mock_get_default_details):
        """Test get_user_metrics SQL exception handling"""
        mock_get_default_details.side_effect = Exception("Database error")

        url = f"{self.base_url}get_user_metrics/"
        data = {
            "end_user_id": str(self.end_user.id),
            "project_id": self.test_project_id,
            "filters": [],
        }

        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

    def test_get_user_metrics_unauthenticated(self):
        """Test get_user_metrics with unauthenticated user"""
        self.client.force_authenticate(user=None)

        url = f"{self.base_url}get_user_metrics/"
        data = {
            "end_user_id": str(self.end_user.id),
            "project_id": self.test_project_id,
            "filters": [],
        }

        response = self.client.post(url, data, format="json")

        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    # ============ GET USER GRAPH DATA TESTS ============
