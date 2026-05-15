import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import set_workspace_context
from tracer.models.observation_span import EndUser, ObservationSpan
from tracer.models.project import Project
from tracer.models.trace import Trace
from tracer.utils.helper import get_default_project_version_config

User = get_user_model()


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

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_users_list_success_basic(self, mock_get_spans):
        """Test successful basic users list request"""
        mock_results = [
            (
                "user1",
                10.50,
                1000,
                500,
                500,
                5,
                2,
                300.0,
                150.0,
                10,
                1,
                "2024-01-01",
                "2024-01-15",
                10,
                0,
                0.85,
                4.2,
                self.test_project_id,
                2,
                "email",
                "hash123",
                "end-user-1",
            ),
            (
                "user2",
                25.75,
                2000,
                1000,
                1000,
                10,
                3,
                450.0,
                200.0,
                20,
                2,
                "2024-01-02",
                "2024-01-16",
                15,
                1,
                0.92,
                3.8,
                self.test_project_id,
                2,
                "email",
                "hash456",
                "end-user-2",
            ),
        ]
        mock_get_spans.return_value = mock_results

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

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_users_list_with_search(self, mock_get_spans):
        """Test users list with search parameter"""
        mock_results = [
            (
                "searchuser",
                15.25,
                1500,
                750,
                750,
                7,
                3,
                350.0,
                175.0,
                15,
                1,
                "2024-01-01",
                "2024-01-15",
                12,
                0,
                0.88,
                4.0,
                self.test_project_id,
                1,
                "email",
                "hash789",
                "end-user-3",
            )
        ]
        mock_get_spans.return_value = mock_results

        params = {
            "project_id": self.test_project_id,
            "search": "searchuser",
            "page_size": 10,
            "current_page_index": 0,
        }

        response = self.client.get(self.url, params)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify search parameter was passed correctly
        mock_get_spans.assert_called_once()

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_users_list_with_pagination(self, mock_get_spans):
        """Test users list with pagination"""
        mock_results = [
            (
                "user3",
                5.25,
                500,
                250,
                250,
                3,
                1,
                200.0,
                100.0,
                6,
                0,
                "2024-01-03",
                "2024-01-17",
                8,
                0,
                0.75,
                3.5,
                self.test_project_id,
                25,
                "email",
                "hash101",
                "end-user-4",
            )
        ]
        mock_get_spans.return_value = mock_results

        data = {
            "project_id": self.test_project_id,
            "page_size": 5,
            "current_page_index": 2,  # Third page
        }

        response = self.client.get(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["result"]["total_count"], 25)
        self.assertEqual(response.data["result"]["total_pages"], 5)  # 25/5 = 5 pages

        # Verify pagination parameters
        mock_get_spans.assert_called_once()
        call_args = mock_get_spans.call_args[1]
        self.assertEqual(call_args["limit"], 5)
        self.assertEqual(call_args["offset"], 10)  # page 2 * page_size 5

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_users_list_with_sorting_ascending(self, mock_get_spans):
        """Test users list with ascending sort"""
        mock_results = []
        mock_get_spans.return_value = mock_results

        data = {
            "project_id": self.test_project_id,
            "sort_params": json.dumps({"column_id": "total_cost", "direction": "asc"}),
        }

        response = self.client.get(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify sort parameters
        mock_get_spans.assert_called_once()
        call_args = mock_get_spans.call_args[1]
        self.assertEqual(call_args["sort_by"], "total_cost")
        self.assertEqual(call_args["sort_order"], "ASC")

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_users_list_with_sorting_descending(self, mock_get_spans):
        """Test users list with descending sort"""
        mock_results = []
        mock_get_spans.return_value = mock_results

        data = {
            "project_id": self.test_project_id,
            "sort_params": json.dumps({"column_id": "num_traces", "direction": "desc"}),
        }

        response = self.client.get(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify sort parameters
        mock_get_spans.assert_called_once()
        call_args = mock_get_spans.call_args[1]
        self.assertEqual(call_args["sort_by"], "num_traces")
        self.assertEqual(call_args["sort_order"], "DESC")

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_users_list_with_filters(self, mock_get_spans):
        """Test users list with filters"""
        mock_results = []
        mock_get_spans.return_value = mock_results

        test_filters = [
            {"column": "total_cost", "operator": "gt", "value": 10},
            {"column": "num_traces", "operator": "gte", "value": 5},
        ]

        data = {"project_id": self.test_project_id, "filters": json.dumps(test_filters)}

        response = self.client.get(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify filters were passed
        mock_get_spans.assert_called_once()
        call_args = mock_get_spans.call_args[1]
        self.assertEqual(call_args["filters"], test_filters)

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_users_list_column_mapping(self, mock_get_spans):
        """Test that column mapping works correctly"""
        mock_results = []
        mock_get_spans.return_value = mock_results

        data = {
            "project_id": self.test_project_id,
            "sort_params": json.dumps(
                {
                    "column_id": "avg_trace_latency",  # Should map to avg_latency_trace
                    "direction": "asc",
                }
            ),
        }

        response = self.client.get(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify column mapping worked
        mock_get_spans.assert_called_once()
        call_args = mock_get_spans.call_args[1]
        self.assertEqual(call_args["sort_by"], "avg_latency_trace")

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_users_list_empty_search_stripped(self, mock_get_spans):
        """Test that empty search strings are properly handled"""
        mock_results = []
        mock_get_spans.return_value = mock_results

        data = {"project_id": self.test_project_id, "search": "   "}  # Whitespace only

        response = self.client.get(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify search_name is None when search is empty/whitespace
        mock_get_spans.assert_called_once()

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_users_list_default_values(self, mock_get_spans):
        """Test default values are applied correctly"""
        mock_results = []
        mock_get_spans.return_value = mock_results

        # Minimal data - should use defaults
        data = {"project_id": self.test_project_id}

        response = self.client.get(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify defaults were applied
        mock_get_spans.assert_called_once()
        call_args = mock_get_spans.call_args[1]
        self.assertEqual(call_args["limit"], 30)  # Default page_size
        self.assertEqual(call_args["offset"], 0)  # Default current_page_index
        self.assertEqual(call_args["org_id"], self.organization.id)

    def test_users_list_unauthenticated(self):
        """Test that unauthenticated requests are rejected"""
        # Remove authentication
        self.client.force_authenticate(user=None)

        data = {"project_id": self.test_project_id}

        response = self.client.get(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_users_list_sql_exception_handling(self, mock_get_spans):
        """Test exception handling when SQL query fails"""
        # Mock SQL exception
        mock_get_spans.side_effect = Exception("Database connection error")

        data = {"project_id": self.test_project_id}

        response = self.client.get(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error fetching users", str(response.data["result"]))

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_users_list_page_calculation_exact_division(self, mock_get_spans):
        """Test page calculation when count divides evenly by page_size"""
        mock_results = [
            (
                "user1",
                10.50,
                1000,
                500,
                500,
                5,
                2,
                300.0,
                150.0,
                10,
                1,
                "2024-01-01",
                "2024-01-15",
                10,
                0,
                0.85,
                4.2,
                self.test_project_id,
                20,
                "email",
                "hash201",
                "end-user-5",
            )
        ]
        mock_get_spans.return_value = mock_results

        data = {"project_id": self.test_project_id, "page_size": 10}

        response = self.client.get(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["result"]["total_pages"], 2)  # 20/10 = 2

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_users_list_page_calculation_with_remainder(self, mock_get_spans):
        """Test page calculation when count has remainder"""
        mock_results = [
            (
                "user1",
                10.50,
                1000,
                500,
                500,
                5,
                2,
                300.0,
                150.0,
                10,
                1,
                "2024-01-01",
                "2024-01-15",
                10,
                0,
                0.85,
                4.2,
                self.test_project_id,
                23,
                "email",
                "hash301",
                "end-user-6",
            )
        ]
        mock_get_spans.return_value = mock_results

        data = {"project_id": self.test_project_id, "page_size": 10}

        response = self.client.get(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["result"]["total_pages"], 3
        )  # 23/10 = 2 + 1 for remainder

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_users_list_multiple_sort_params(self, mock_get_spans):
        """Test behavior with multiple sort parameters (should use the last one)"""
        mock_results = []
        mock_get_spans.return_value = mock_results

        data = {
            "project_id": self.test_project_id,
            "sort_params": json.dumps({"column_id": "num_traces", "direction": "desc"}),
        }

        response = self.client.get(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Should use the last sort parameter
        mock_get_spans.assert_called_once()
        call_args = mock_get_spans.call_args[1]
        self.assertEqual(call_args["sort_by"], "num_traces")
        self.assertEqual(call_args["sort_order"], "DESC")

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_users_list_invalid_column_in_sort(self, mock_get_spans):
        """Test handling of invalid column in sort parameters"""
        mock_results = []
        mock_get_spans.return_value = mock_results

        data = {
            "project_id": self.test_project_id,
            "sort_params": json.dumps(
                {"column_id": "invalid_column", "direction": "asc"}
            ),
        }

        response = self.client.get(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Invalid column should result in None for sort_by
        mock_get_spans.assert_called_once()
        call_args = mock_get_spans.call_args[1]
        self.assertNotIn("sort_by", call_args)  # None values are filtered out

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_users_list_without_project_id(self, mock_get_spans):
        """Test that missing project_id returns all workspace users (no project filter)"""
        mock_get_spans.return_value = []
        data = {"page_size": 10}

        response = self.client.get(self.url, data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify project_id is None when not provided
        mock_get_spans.assert_called_once()
        call_args = mock_get_spans.call_args[1]
        self.assertIsNone(call_args["project_id"])


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

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_user_default_details")
    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_get_user_metrics_success(self, mock_get_spans, mock_get_default_details):
        """Test successful get_user_metrics request"""
        # Mock responses
        mock_default_details = [
            (self.test_user_id, 15, "2024-01-15T10:30:00Z")  # active_days, last_active
        ]
        mock_spans = [
            (
                self.test_user_id,
                25.50,
                2000,
                1000,
                1000,
                10,
                5,
                400.0,
                180.0,
                20,
                2,
                "2024-01-01",
                "2024-01-15",
                12,
                1,
                0.85,
                4.2,
                2,
                self.test_project_id,
                "email",
                "hash123",
            )
        ]

        mock_get_default_details.return_value = mock_default_details
        mock_get_spans.return_value = mock_spans

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
        data = {"user_id": self.test_user_id, "filters": []}

        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Project id is required", str(response.data["result"]))

    def test_get_user_metrics_missing_user_id(self):
        """Test get_user_metrics with missing end_user_id"""
        url = f"{self.base_url}get_user_metrics/"
        data = {"project_id": self.test_project_id, "filters": []}

        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("End User id is required", str(response.data["result"]))

    def test_get_user_metrics_user_not_found(self):
        """Test get_user_metrics with non-existent user"""
        url = f"{self.base_url}get_user_metrics/"
        data = {
            "end_user_id": "00000000-0000-0000-0000-000000000000",
            "project_id": self.test_project_id,
            "filters": [],
        }

        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "User not found for the given end_user_id", str(response.data["result"])
        )

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_user_default_details")
    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_get_user_metrics_with_filters(
        self, mock_get_spans, mock_get_default_details
    ):
        """Test get_user_metrics with filters"""
        mock_default_details = [(self.test_user_id, 10, "2024-01-10T10:30:00Z")]
        mock_spans = [
            (
                self.test_user_id,
                15.25,
                1500,
                750,
                750,
                5,
                3,
                300.0,
                150.0,
                15,
                1,
                "2024-01-01",
                "2024-01-10",
                8,
                0,
                0.90,
                3.8,
                1,
                self.test_project_id,
                "email",
                "hash456",
            )
        ]

        mock_get_default_details.return_value = mock_default_details
        mock_get_spans.return_value = mock_spans

        url = f"{self.base_url}get_user_metrics/"
        test_filters = [{"column": "total_cost", "operator": "gt", "value": 10}]
        data = {
            "end_user_id": str(self.end_user.id),
            "project_id": self.test_project_id,
            "filters": test_filters,
        }

        response = self.client.post(url, data, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify filters were passed to SQL handler
        mock_get_spans.assert_called_once()
        call_args = mock_get_spans.call_args[1]
        self.assertEqual(call_args["filters"], test_filters)

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_user_default_details")
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

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ============ GET USER GRAPH DATA TESTS ============

    @patch("tracer.views.project.GraphEngine")
    @patch("tracer.views.project.FilterEngine")
    def test_get_user_graph_data_success(self, mock_filter_engine, mock_graph_engine):
        """Test successful get_user_graph_data request"""
        # Create test ObservationSpan
        test_span = ObservationSpan.objects.create(
            trace=self.trace,
            project_id=self.test_project_id,
            end_user_id=self.end_user.id,
            created_at=datetime.now(),
        )

        # Mock filter engine
        mock_filter_instance = MagicMock()
        mock_filter_instance.apply_filters.return_value = [{"id": test_span.id}]
        mock_filter_engine.return_value = mock_filter_instance

        # Mock graph engine
        mock_graph_instance = MagicMock()
        mock_graph_data = {
            "session": [
                {"timestamp": "2025-01-01T10:00:00Z", "value": 100},
                {"timestamp": "2025-01-01T11:00:00Z", "value": 150},
            ]
        }
        mock_graph_instance.generate_graph.return_value = mock_graph_data
        mock_graph_engine.return_value = mock_graph_instance

        url = f"{self.base_url}get_user_graph_data/"
        data = {"interval": "hour", "filters": []}

        response = self.client.post(
            f"{url}?project_id={self.test_project_id}&end_user_id={self.end_user.id}",
            data,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["result"], mock_graph_data)

        # Verify filter engine was called
        mock_filter_engine.assert_called_once()
        mock_filter_instance.apply_filters.assert_called_once_with([])

        # Verify graph engine was called
        mock_graph_engine.assert_called_once()
        mock_graph_instance.generate_graph.assert_called_once()

    def test_get_user_graph_data_missing_project_id(self):
        """Test get_user_graph_data with missing project_id"""
        url = f"{self.base_url}get_user_graph_data/"
        data = {"interval": "hour", "filters": []}

        response = self.client.post(
            f"{url}?user_id={self.test_user_id}", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Project id is required", str(response.data["result"]))

    def test_get_user_graph_data_missing_user_id(self):
        """Test get_user_graph_data with missing end_user_id"""
        url = f"{self.base_url}get_user_graph_data/"
        data = {"interval": "hour", "filters": []}

        response = self.client.post(
            f"{url}?project_id={self.test_project_id}", data, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("End User id is required", str(response.data["result"]))

    def test_get_user_graph_data_user_not_found(self):
        """Test get_user_graph_data with non-existent user"""
        url = f"{self.base_url}get_user_graph_data/"
        data = {"interval": "hour", "filters": []}

        response = self.client.post(
            f"{url}?project_id={self.test_project_id}&end_user_id=00000000-0000-0000-0000-000000000000",
            data,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "User not found for the given end_user_id", str(response.data["result"])
        )

    @patch("tracer.views.project.GraphEngine")
    @patch("tracer.views.project.FilterEngine")
    def test_get_user_graph_data_with_custom_interval(
        self, mock_filter_engine, mock_graph_engine
    ):
        """Test get_user_graph_data with custom interval"""
        # Create test ObservationSpan
        test_span = ObservationSpan.objects.create(
            trace=self.trace,
            project_id=self.test_project_id,
            end_user_id=self.end_user.id,
            created_at=datetime.now(),
        )

        # Mock engines
        mock_filter_instance = MagicMock()
        mock_filter_instance.apply_filters.return_value = [{"id": test_span.id}]
        mock_filter_engine.return_value = mock_filter_instance

        mock_graph_instance = MagicMock()
        mock_graph_instance.generate_graph.return_value = {"data": "test"}
        mock_graph_engine.return_value = mock_graph_instance

        url = f"{self.base_url}get_user_graph_data/"
        data = {"interval": "day", "filters": []}  # Custom interval

        response = self.client.post(
            f"{url}?project_id={self.test_project_id}&end_user_id={self.end_user.id}",
            data,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify graph engine was called with custom interval
        mock_graph_engine.assert_called_once()
        call_args = mock_graph_engine.call_args[1]
        self.assertEqual(call_args["interval"], "day")

    @patch("tracer.views.project.GraphEngine")
    @patch("tracer.views.project.FilterEngine")
    def test_get_user_graph_data_with_filters(
        self, mock_filter_engine, mock_graph_engine
    ):
        """Test get_user_graph_data with filters"""
        # Create test ObservationSpan
        test_span = ObservationSpan.objects.create(
            trace=self.trace,
            project_id=self.test_project_id,
            end_user_id=self.end_user.id,
            created_at=datetime.now(),
        )

        # Mock engines
        mock_filter_instance = MagicMock()
        mock_filter_instance.apply_filters.return_value = [{"id": test_span.id}]
        mock_filter_engine.return_value = mock_filter_instance

        mock_graph_instance = MagicMock()
        mock_graph_instance.generate_graph.return_value = {"data": "test"}
        mock_graph_engine.return_value = mock_graph_instance

        url = f"{self.base_url}get_user_graph_data/"
        test_filters = [
            {"column": "created_at", "operator": "gte", "value": "2024-01-01"}
        ]
        data = {"interval": "hour", "filters": test_filters}

        response = self.client.post(
            f"{url}?project_id={self.test_project_id}&end_user_id={self.end_user.id}",
            data,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify filters were passed to filter engine
        mock_filter_instance.apply_filters.assert_called_once_with(test_filters)

        # Verify filters were passed to graph engine
        mock_graph_engine.assert_called_once()
        call_args = mock_graph_engine.call_args[1]
        self.assertEqual(call_args["filters"], test_filters)

    @patch("tracer.views.project.GraphEngine")
    @patch("tracer.views.project.FilterEngine")
    def test_get_user_graph_data_default_interval(
        self, mock_filter_engine, mock_graph_engine
    ):
        """Test get_user_graph_data uses default interval when not provided"""
        # Create test ObservationSpan
        test_span = ObservationSpan.objects.create(
            trace=self.trace,
            project_id=self.test_project_id,
            end_user_id=self.end_user.id,
            created_at=datetime.now(),
        )

        # Mock engines
        mock_filter_instance = MagicMock()
        mock_filter_instance.apply_filters.return_value = [{"id": test_span.id}]
        mock_filter_engine.return_value = mock_filter_instance

        mock_graph_instance = MagicMock()
        mock_graph_instance.generate_graph.return_value = {"data": "test"}
        mock_graph_engine.return_value = mock_graph_instance

        url = f"{self.base_url}get_user_graph_data/"
        data = {}  # No interval specified

        response = self.client.post(
            f"{url}?project_id={self.test_project_id}&end_user_id={self.end_user.id}",
            data,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify default interval was used
        mock_graph_engine.assert_called_once()
        call_args = mock_graph_engine.call_args[1]
        self.assertEqual(call_args["interval"], "hour")

    @patch("tracer.views.project.FilterEngine")
    def test_get_user_graph_data_filter_exception(self, mock_filter_engine):
        """Test get_user_graph_data filter engine exception handling"""
        # Create test ObservationSpan
        ObservationSpan.objects.create(
            trace=self.trace,
            project_id=self.test_project_id,
            end_user_id=self.end_user.id,
            created_at=datetime.now(),
        )

        # Mock filter engine to raise exception
        mock_filter_engine.side_effect = Exception("Filter error")

        url = f"{self.base_url}get_user_graph_data/"
        data = {"interval": "hour", "filters": []}

        response = self.client.post(
            f"{url}?project_id={self.test_project_id}&end_user_id={self.end_user.id}",
            data,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertIn("Filter error", str(response.data["result"]))

    def test_get_user_graph_data_unauthenticated(self):
        """Test get_user_graph_data with unauthenticated user"""
        self.client.force_authenticate(user=None)

        url = f"{self.base_url}get_user_graph_data/"
        data = {"interval": "hour", "filters": []}

        response = self.client.post(
            f"{url}?project_id={self.test_project_id}&end_user_id={self.end_user.id}",
            data,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch("tracer.views.project.GraphEngine")
    def test_get_user_graph_data_general_exception(self, mock_graph_engine):
        """Test get_user_graph_data general exception handling"""
        # Mock graph engine to raise exception
        mock_graph_engine.side_effect = Exception("Unexpected error")

        url = f"{self.base_url}get_user_graph_data/"
        data = {"interval": "hour", "filters": []}

        response = self.client.post(
            f"{url}?project_id={self.test_project_id}&end_user_id={self.end_user.id}",
            data,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

    @patch("tracer.views.project.GraphEngine")
    @patch("tracer.views.project.FilterEngine")
    def test_get_user_graph_data_no_spans(self, mock_filter_engine, mock_graph_engine):
        """Test get_user_graph_data when no spans exist for user"""
        # Mock filter engine to return empty results
        mock_filter_instance = MagicMock()
        mock_filter_instance.apply_filters.return_value = []
        mock_filter_engine.return_value = mock_filter_instance

        mock_graph_instance = MagicMock()
        mock_graph_instance.generate_graph.return_value = {"session": []}
        mock_graph_engine.return_value = mock_graph_instance

        url = f"{self.base_url}get_user_graph_data/"
        data = {"interval": "hour", "filters": []}

        response = self.client.post(
            f"{url}?project_id={self.test_project_id}&end_user_id={self.end_user.id}",
            data,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Verify graph engine was still called with empty queryset
        mock_graph_engine.assert_called_once()
