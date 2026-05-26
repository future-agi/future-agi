import json
from datetime import datetime
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from rest_framework.views import APIView

from accounts.models import Organization
from accounts.models.workspace import Workspace
from model_hub.models.ai_model import AIModel
from tfc.constants.roles import OrganizationRoles
from tfc.middleware.workspace_context import set_workspace_context
from tracer.models.project import Project
from tracer.utils.helper import get_default_project_version_config

User = get_user_model()


def _row(
    user_id="user1",
    total_cost=10.50,
    total_tokens=1000,
    input_tokens=400,
    output_tokens=600,
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
    project_id="00000000-0000-0000-0000-000000000000",
    count=2,
    user_id_type="email",
    user_id_hash="hash123",
    end_user_id="end-user-1",
):
    """Build a result tuple in the exact shape get_spans_by_end_users returns.

    Tuple indices match the unpacking inside `users_row_to_dict` (and
    historically `UsersView.get`): position 18 carries the total count.
    """
    return (
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
        count,
        user_id_type,
        user_id_hash,
        end_user_id,
    )


@pytest.mark.integration
@pytest.mark.core_backend
class TestUsersExportAPI(APITestCase):
    """Tests for the new UsersExportView CSV endpoint.

    Mirrors the auth + workspace-injection harness used by TestUsersViewAPI
    so request.user.organization.id and request.workspace.id are populated
    when UsersExportView.get runs.
    """

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="export-test@example.com",
            name="Export Test User",
            password="testpass123",
        )
        self.organization = Organization.objects.create(name="Export Test Org")
        self.user.organization = self.organization
        self.user.organization_role = OrganizationRoles.OWNER
        self.user.save()

        self.workspace = Workspace.objects.create(
            name="Export Test Workspace",
            organization=self.organization,
            is_default=True,
            created_by=self.user,
        )
        set_workspace_context(workspace=self.workspace, organization=self.organization)
        self.client.force_authenticate(user=self.user)

        # Inject request.workspace before the view runs. WorkspaceMiddleware
        # would normally do this; in tests we patch APIView.initial to match
        # the pattern used by TestUsersViewAPI.
        self.original_initial = APIView.initial
        workspace = self.workspace

        def initial_with_workspace(view_self, request, *args, **view_kwargs):
            request.workspace = workspace
            return self.original_initial(view_self, request, *args, **view_kwargs)

        self.workspace_patcher = patch.object(
            APIView, "initial", initial_with_workspace
        )
        self.workspace_patcher.start()

        self.test_project = Project.objects.create(
            name="Export Test Project",
            organization=self.organization,
            model_type=AIModel.ModelTypes.GENERATIVE_LLM,
            trace_type="observe",
            config=get_default_project_version_config(),
        )
        self.test_project_id = str(self.test_project.id)
        self.url = "/tracer/users/export/"

    def tearDown(self):
        self.workspace_patcher.stop()
        super().tearDown()

    # ------------------------------------------------------------------
    # Happy path
    # ------------------------------------------------------------------

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_export_returns_csv_with_headers_and_rows(self, mock_get_spans):
        mock_get_spans.return_value = [
            _row(user_id="user-a", total_cost=1.234567, total_tokens=100),
            _row(user_id="user-b", total_cost=2.5, total_tokens=200),
        ]

        response = self.client.get(self.url, {"project_id": self.test_project_id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment;", response["Content-Disposition"])

        body = response.content.decode("utf-8")
        lines = [line for line in body.splitlines() if line]
        self.assertEqual(len(lines), 3, "expected header + 2 data rows")

        # Header order is the contract the frontend relies on.
        expected_header = (
            "User ID,User ID Type,User ID Hash,First Active,Last Active,"
            "No. of Traces,No. of Sessions,Avg Session Duration (s),"
            "Total Tokens,Total Cost ($),Avg Latency / Trace (ms),"
            "No. of LLM Calls,Guardrails Triggered,Evals Pass Rate (%),"
            "Input Tokens,Output Tokens"
        )
        self.assertEqual(lines[0], expected_header)
        self.assertTrue(lines[1].startswith("user-a,"))
        self.assertTrue(lines[2].startswith("user-b,"))

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def test_export_requires_authentication(self):
        # Drop credentials and confirm the IsAuthenticated guard fires.
        self.client.force_authenticate(user=None)
        response = self.client.get(self.url)
        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    # ------------------------------------------------------------------
    # Shared-helper wiring: filters, sort, pagination
    # ------------------------------------------------------------------

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_export_passes_filters_and_sort_to_sql_handler(self, mock_get_spans):
        mock_get_spans.return_value = []
        filters = [
            {
                "column_id": "created_at",
                "filter_config": {
                    "filter_type": "datetime",
                    "filter_op": "between",
                    "filter_value": [
                        "2026-02-24T08:48:10.685Z",
                        "2026-05-25T08:48:10.685Z",
                    ],
                },
            }
        ]
        sort_params = {"column_id": "total_cost", "direction": "desc"}

        response = self.client.get(
            self.url,
            {
                "project_id": self.test_project_id,
                "filters": json.dumps(filters),
                "sort_params": json.dumps(sort_params),
                "search": " alice ",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        kwargs = mock_get_spans.call_args.kwargs
        self.assertEqual(kwargs["filters"], filters)
        self.assertEqual(kwargs["sort_by"], "total_cost")
        self.assertEqual(kwargs["sort_order"], "DESC")
        self.assertEqual(kwargs["search_name"], "alice")
        self.assertEqual(kwargs["project_id"], self.test_project_id)

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_export_skips_pagination(self, mock_get_spans):
        """Export must fetch every matching row, not a single page."""
        mock_get_spans.return_value = []
        self.client.get(
            self.url,
            {
                "project_id": self.test_project_id,
                # Pagination params on the URL should be ignored.
                "page_size": 5,
                "current_page_index": 3,
            },
        )
        kwargs = mock_get_spans.call_args.kwargs
        self.assertIsNone(kwargs["limit"])
        self.assertIsNone(kwargs["offset"])

    # ------------------------------------------------------------------
    # Cell formatting
    # ------------------------------------------------------------------

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_export_formats_datetime_and_none_cells(self, mock_get_spans):
        # last_active=None must come out as empty, activated_at=datetime must
        # be ISO-formatted (not the repr Python prints by default).
        activated = datetime(2026, 5, 25, 14, 21, 35)
        mock_get_spans.return_value = [
            _row(
                user_id="dt-user",
                activated_at=activated,
                last_active=None,
                num_llm_calls=42,
            )
        ]

        response = self.client.get(self.url, {"project_id": self.test_project_id})
        body = response.content.decode("utf-8")
        # Locate the data row (not header).
        data_row = body.splitlines()[1]
        cells = data_row.split(",")

        # Header position: First Active is index 3, Last Active index 4,
        # No. of LLM Calls is index 11.
        self.assertEqual(cells[3], activated.isoformat())
        self.assertEqual(cells[4], "")
        self.assertEqual(cells[11], "42")

    # ------------------------------------------------------------------
    # Filename composition
    # ------------------------------------------------------------------

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_export_filename_includes_project_id(self, mock_get_spans):
        mock_get_spans.return_value = []
        response = self.client.get(self.url, {"project_id": self.test_project_id})
        self.assertIn(f"users_{self.test_project_id}_", response["Content-Disposition"])

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_export_filename_defaults_to_all_when_no_project(self, mock_get_spans):
        mock_get_spans.return_value = []
        response = self.client.get(self.url)
        self.assertIn("users_all_", response["Content-Disposition"])

    # ------------------------------------------------------------------
    # Failure mode
    # ------------------------------------------------------------------

    @patch("model_hub.utils.SQL_queries.SQLQueryHandler.get_spans_by_end_users")
    def test_export_returns_400_when_sql_handler_raises(self, mock_get_spans):
        mock_get_spans.side_effect = RuntimeError("boom")
        response = self.client.get(self.url, {"project_id": self.test_project_id})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
