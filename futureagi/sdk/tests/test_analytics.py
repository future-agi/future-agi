"""
SDK Simulation Analytics API Tests

Tests for the three simulation analytics endpoints:
- GET /sdk/api/v1/simulation/metrics/
- GET /sdk/api/v1/simulation/runs/
- GET /sdk/api/v1/simulation/analytics/

These endpoints use APIKeyAuthentication (x-api-key / x-secret-key headers).
"""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone
from rest_framework import status

from accounts.models import OrgApiKey
from accounts.models.organization import Organization
from simulate.models import CallExecution, TestExecution
from simulate.models.run_test import RunTest
from simulate.models.scenarios import Scenarios

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def api_key(organization, user, db):
    """Create an API key for testing SDK endpoints."""
    api_key, _ = OrgApiKey.objects.get_or_create(
        organization=organization,
        user=user,
        defaults={
            "api_key": "test_analytics_api_key",
            "secret_key": "test_analytics_secret_key",
            "name": "Analytics Test API Key",
            "enabled": True,
            "type": "user",
        },
    )
    return api_key


@pytest.fixture
def sdk_client(api_client, api_key):
    """API client with SDK API key headers."""
    api_client.credentials(
        HTTP_X_API_KEY=api_key.api_key,
        HTTP_X_SECRET_KEY=api_key.secret_key,
    )
    return api_client


@pytest.fixture
def scenario(organization, workspace, db):
    """Create a test scenario."""
    return Scenarios.objects.create(
        name="Test Scenario",
        description="A test scenario",
        source="test source",
        scenario_type=Scenarios.ScenarioTypes.DATASET,
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def run_test(organization, workspace, db):
    """Create a test RunTest."""
    return RunTest.objects.create(
        name="test-run",
        description="Test run",
        organization=organization,
        workspace=workspace,
    )


@pytest.fixture
def test_execution(run_test, db):
    """Create a test TestExecution."""
    return TestExecution.objects.create(
        run_test=run_test,
        status="completed",
        total_calls=3,
        completed_calls=2,
        failed_calls=1,
        started_at=timezone.now(),
        completed_at=timezone.now(),
        eval_explanation_summary={"summary": "test summary data"},
        eval_explanation_summary_status="completed",
    )


@pytest.fixture
def call_execution(test_execution, scenario, db):
    """Create a test CallExecution with metrics."""
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        status="completed",
        duration_seconds=120,
        cost_cents=50,
        stt_cost_cents=10,
        llm_cost_cents=30,
        tts_cost_cents=10,
        avg_agent_latency_ms=250,
        response_time_ms=300,
        user_wpm=150.0,
        bot_wpm=140.0,
        talk_ratio=0.8,
        user_interruption_count=2,
        user_interruption_rate=0.5,
        ai_interruption_count=1,
        ai_interruption_rate=0.2,
        avg_stop_time_after_interruption_ms=500,
        overall_score=85.0,
        started_at=timezone.now(),
        completed_at=timezone.now(),
        call_summary="Test call summary",
        ended_reason="completed",
        eval_outputs={
            "eval-config-1": {
                "name": "Accuracy",
                "output": "Passed",
                "output_type": "Pass/Fail",
            },
            "eval-config-2": {
                "name": "Fluency",
                "output": 0.9,
                "output_type": "score",
            },
        },
        conversation_metrics_data={
            "total_tokens": 1500,
            "input_tokens": 800,
            "output_tokens": 700,
            "avg_latency_ms": 200.0,
            "turn_count": 10,
            "csat_score": 4.5,
        },
    )


@pytest.fixture
def second_call_execution(test_execution, scenario, db):
    """Create a second CallExecution for pagination/aggregation tests."""
    return CallExecution.objects.create(
        test_execution=test_execution,
        scenario=scenario,
        status="completed",
        duration_seconds=90,
        cost_cents=40,
        avg_agent_latency_ms=200,
        response_time_ms=280,
        user_wpm=130.0,
        bot_wpm=120.0,
        talk_ratio=0.7,
        overall_score=80.0,
        started_at=timezone.now(),
        completed_at=timezone.now(),
        eval_outputs={
            "eval-config-1": {
                "name": "Accuracy",
                "output": "Failed",
                "output_type": "Pass/Fail",
            },
        },
    )


@pytest.fixture
def another_org(db):
    """Create a second organization for cross-org tests."""
    return Organization.objects.create(name="Other Organization")


# Stub for the raw SQL that get_kpi_metrics_query returns.
# The cursor mock must expose .description and .fetchone/.fetchall.
FAKE_KPI_COLUMNS = [
    "total_calls",
    "pending_calls",
    "queued_calls",
    "failed_calls",
    "completed_calls",
    "connected_voice_calls",
    "avg_score",
    "avg_response",
    "total_duration",
    "avg_agent_latency",
    "avg_user_interruption_count",
    "avg_user_interruption_rate",
    "avg_user_wpm",
    "avg_bot_wpm",
    "avg_talk_ratio",
    "avg_ai_interruption_count",
    "avg_ai_interruption_rate",
    "avg_stop_time",
    "avg_total_tokens",
    "avg_input_tokens",
    "avg_output_tokens",
    "avg_chat_latency_ms",
    "avg_turn_count",
    "avg_csat_score",
]

FAKE_KPI_ROW = (
    3,  # total_calls
    0,  # pending_calls
    0,  # queued_calls
    1,  # failed_calls
    2,  # completed_calls
    2,  # connected_voice_calls
    82.5,  # avg_score
    290,  # avg_response
    210,  # total_duration
    225,  # avg_agent_latency
    2,  # avg_user_interruption_count
    0.5,  # avg_user_interruption_rate
    140,  # avg_user_wpm
    130,  # avg_bot_wpm
    0.75,  # avg_talk_ratio
    1,  # avg_ai_interruption_count
    0.2,  # avg_ai_interruption_rate
    500,  # avg_stop_time
    1500,  # avg_total_tokens
    800,  # avg_input_tokens
    700,  # avg_output_tokens
    200,  # avg_chat_latency_ms
    10,  # avg_turn_count
    4.5,  # avg_csat_score
)

FAKE_EVAL_ROWS = [
    ("eval-1", "Accuracy", "Pass/Fail", 50.0, None, None),
    ("eval-2", "Fluency", "score", 0.85, None, None),
]


def _build_mock_cursor(description_columns, rows, single=True):
    """Build a mock cursor context manager that returns the given data."""
    cursor = MagicMock()
    cursor.description = [(col,) for col in description_columns]
    if single:
        cursor.fetchone.return_value = rows
    else:
        cursor.fetchall.return_value = rows
    return cursor


# ---------------------------------------------------------------------------
# URL constants
# ---------------------------------------------------------------------------

METRICS_URL = "/sdk/api/v1/simulation/metrics/"
RUNS_URL = "/sdk/api/v1/simulation/runs/"
ANALYTICS_URL = "/sdk/api/v1/simulation/analytics/"


# ===========================================================================
# Authentication tests (shared across all 3 endpoints)
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
@pytest.mark.api
class TestAnalyticsAuth:
    """Authentication tests shared across all simulation analytics endpoints."""

    @pytest.mark.parametrize("url", [METRICS_URL, RUNS_URL, ANALYTICS_URL])
    def test_unauthenticated_request_is_rejected(self, api_client, url):
        """Request without any credentials returns 401 before business validation."""
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("url", [METRICS_URL, RUNS_URL, ANALYTICS_URL])
    def test_invalid_api_key_returns_401(self, api_client, url):
        """Request with invalid API key returns 401."""
        api_client.credentials(
            HTTP_X_API_KEY="invalid_key",
            HTTP_X_SECRET_KEY="invalid_secret",
        )
        response = api_client.get(url)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_valid_api_key_passes_auth_on_metrics(
        self, sdk_client, run_test, test_execution, call_execution
    ):
        """Valid API key returns 200 for metrics endpoint."""
        with patch("sdk.views.analytics.connection") as mock_conn:
            mock_cursor = _build_mock_cursor(FAKE_KPI_COLUMNS, FAKE_KPI_ROW)
            mock_conn.cursor.return_value.__enter__ = MagicMock(
                return_value=mock_cursor
            )
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            response = sdk_client.get(
                METRICS_URL,
                {"execution_id": str(test_execution.id)},
            )
        assert response.status_code == status.HTTP_200_OK

    def test_valid_api_key_passes_auth_on_runs(
        self, sdk_client, run_test, test_execution, call_execution
    ):
        """Valid API key returns 200 for runs endpoint."""
        with (
            patch("sdk.views.analytics._get_eval_configs_with_template") as mock_eval,
            patch("sdk.views.analytics._get_completed_call_executions") as mock_calls,
            patch("sdk.views.analytics._build_template_statistics") as mock_stats,
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_eval.return_value = MagicMock(filter=MagicMock(return_value=[]))
            mock_calls.return_value = []
            mock_stats.return_value = {}
            response = sdk_client.get(
                RUNS_URL,
                {"execution_id": str(test_execution.id)},
            )
        assert response.status_code == status.HTTP_200_OK

    def test_valid_api_key_passes_auth_on_analytics(
        self, sdk_client, run_test, test_execution
    ):
        """Valid API key returns 200 for analytics endpoint."""
        with (
            patch("sdk.views.analytics.connection") as mock_conn,
            patch("sdk.views.analytics._get_eval_configs_with_template") as mock_eval,
            patch("sdk.views.analytics._get_completed_call_executions") as mock_calls,
            patch("sdk.views.analytics._build_template_statistics") as mock_stats,
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_eval.return_value = MagicMock(filter=MagicMock(return_value=[]))
            mock_calls.return_value = []
            mock_stats.return_value = {}

            # Two cursor calls: one for eval metrics, one for kpi metrics
            mock_cursor_eval = _build_mock_cursor(
                [
                    "metric_id",
                    "metric_name",
                    "output_type",
                    "avg_value",
                    "choice_value",
                    "choice_count",
                ],
                FAKE_EVAL_ROWS,
                single=False,
            )
            mock_cursor_kpi = _build_mock_cursor(FAKE_KPI_COLUMNS, FAKE_KPI_ROW)

            cursors = iter([mock_cursor_eval, mock_cursor_kpi])

            def side_effect():
                cm = MagicMock()
                cm.__enter__ = MagicMock(side_effect=lambda: next(cursors))
                cm.__exit__ = MagicMock(return_value=False)
                return cm

            mock_conn.cursor = side_effect
            response = sdk_client.get(
                ANALYTICS_URL,
                {"execution_id": str(test_execution.id)},
            )
        assert response.status_code == status.HTTP_200_OK


# ===========================================================================
# Input validation tests (shared across all 3 endpoints)
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
@pytest.mark.api
class TestAnalyticsInputValidation:
    """Input validation tests for simulation analytics endpoints."""

    @pytest.mark.parametrize("url", [METRICS_URL, RUNS_URL])
    def test_no_params_returns_400(self, sdk_client, url):
        """Request without any query params returns 400."""
        response = sdk_client.get(url)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_analytics_no_params_returns_400(self, sdk_client):
        """Analytics endpoint with no params returns 400."""
        response = sdk_client.get(ANALYTICS_URL)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize("url", [METRICS_URL, RUNS_URL, ANALYTICS_URL])
    def test_invalid_uuid_execution_id_returns_400(self, sdk_client, url):
        """Invalid UUID for execution_id returns 400."""
        response = sdk_client.get(url, {"execution_id": "not-a-uuid"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.parametrize("url", [METRICS_URL, RUNS_URL])
    def test_invalid_uuid_call_execution_id_returns_400(self, sdk_client, url):
        """Invalid UUID for call_execution_id returns 400."""
        response = sdk_client.get(url, {"call_execution_id": "invalid"})
        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ===========================================================================
# Not found tests
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
@pytest.mark.api
class TestAnalyticsNotFound:
    """Not-found tests for simulation analytics endpoints."""

    @pytest.mark.parametrize("url", [METRICS_URL, RUNS_URL])
    def test_nonexistent_call_execution_id_returns_404(self, sdk_client, url):
        """Non-existent call_execution_id returns 404."""
        fake_id = str(uuid.uuid4())
        response = sdk_client.get(url, {"call_execution_id": fake_id})
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("url", [METRICS_URL, RUNS_URL, ANALYTICS_URL])
    def test_nonexistent_execution_id_returns_404(self, sdk_client, url):
        """Non-existent execution_id returns 404."""
        fake_id = str(uuid.uuid4())
        response = sdk_client.get(url, {"execution_id": fake_id})
        assert response.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.parametrize("url", [METRICS_URL, RUNS_URL])
    def test_nonexistent_run_test_name_returns_404(self, sdk_client, url):
        """Non-existent run_test_name returns 404."""
        response = sdk_client.get(url, {"run_test_name": "nonexistent-run"})
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_analytics_nonexistent_run_test_name_returns_404(self, sdk_client):
        """Analytics endpoint with non-existent run_test_name returns 404."""
        response = sdk_client.get(ANALYTICS_URL, {"run_test_name": "nonexistent-run"})
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_cross_org_execution_id_returns_404(
        self, sdk_client, another_org, workspace, db
    ):
        """Execution belonging to another org returns 404 (org isolation)."""
        other_run_test = RunTest.objects.create(
            name="other-org-run",
            organization=another_org,
            workspace=None,
        )
        other_execution = TestExecution.objects.create(
            run_test=other_run_test,
            status="completed",
        )
        response = sdk_client.get(
            METRICS_URL, {"execution_id": str(other_execution.id)}
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_cross_org_call_execution_returns_404(
        self, sdk_client, another_org, scenario, db
    ):
        """Call execution belonging to another org returns 404."""
        other_run_test = RunTest.objects.create(
            name="other-org-run",
            organization=another_org,
            workspace=None,
        )
        other_execution = TestExecution.objects.create(
            run_test=other_run_test,
            status="completed",
        )
        other_call = CallExecution.objects.create(
            test_execution=other_execution,
            scenario=scenario,
            status="completed",
        )
        response = sdk_client.get(
            METRICS_URL, {"call_execution_id": str(other_call.id)}
        )
        assert response.status_code == status.HTTP_404_NOT_FOUND


# ===========================================================================
# SimulationMetricsView — happy path
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
@pytest.mark.api
class TestSimulationMetricsView:
    """Happy path tests for GET /sdk/api/v1/simulation/metrics/."""

    def test_call_execution_id_returns_correct_structure(
        self, sdk_client, call_execution
    ):
        """call_execution_id mode returns per-call raw metrics."""
        response = sdk_client.get(
            METRICS_URL, {"call_execution_id": str(call_execution.id)}
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["status"] is True
        result = data["result"]

        assert result["call_execution_id"] == str(call_execution.id)
        assert result["execution_id"] == str(call_execution.test_execution_id)
        assert result["status"] == "completed"
        assert result["duration_seconds"] == 120

        # Latency block
        assert "latency" in result
        assert result["latency"]["avg_agent_latency_ms"] == 250
        assert result["latency"]["response_time_ms"] == 300

        # Cost block
        assert "cost" in result
        assert result["cost"]["total_cost_cents"] == 50
        assert result["cost"]["stt_cost_cents"] == 10
        assert result["cost"]["llm_cost_cents"] == 30
        assert result["cost"]["tts_cost_cents"] == 10

        # Conversation block
        assert "conversation" in result
        assert result["conversation"]["user_wpm"] == 150.0
        assert result["conversation"]["bot_wpm"] == 140.0
        assert result["conversation"]["talk_ratio"] == 0.8

        # Chat metrics
        assert "chat_metrics" in result

    def test_execution_id_returns_aggregated_metrics(
        self, sdk_client, test_execution, call_execution
    ):
        """execution_id mode returns aggregated metrics for an execution."""
        with patch("sdk.views.analytics.connection") as mock_conn:
            mock_cursor = _build_mock_cursor(FAKE_KPI_COLUMNS, FAKE_KPI_ROW)
            mock_conn.cursor.return_value.__enter__ = MagicMock(
                return_value=mock_cursor
            )
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            response = sdk_client.get(
                METRICS_URL, {"execution_id": str(test_execution.id)}
            )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data["result"]

        assert result["execution_id"] == str(test_execution.id)
        assert result["status"] == "completed"
        assert "metrics" in result

        metrics = result["metrics"]
        assert "latency" in metrics
        assert "cost" in metrics
        assert "conversation" in metrics
        assert "chat" in metrics
        assert "calls" in metrics

        assert metrics["calls"]["total"] == 3
        assert metrics["calls"]["completed"] == 2
        assert metrics["calls"]["failed"] == 1

    def test_run_test_name_returns_paginated_results(
        self, sdk_client, run_test, test_execution, call_execution
    ):
        """run_test_name mode returns paginated per-execution metrics."""
        with patch("sdk.views.analytics.connection") as mock_conn:
            mock_cursor = _build_mock_cursor(FAKE_KPI_COLUMNS, FAKE_KPI_ROW)
            mock_conn.cursor.return_value.__enter__ = MagicMock(
                return_value=mock_cursor
            )
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            response = sdk_client.get(METRICS_URL, {"run_test_name": run_test.name})

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data["result"]

        assert "total_pages" in result
        assert "current_page" in result
        assert "count" in result
        assert "results" in result
        assert result["count"] >= 1
        assert len(result["results"]) >= 1

        first = result["results"][0]
        assert "execution_id" in first
        assert "metrics" in first

    def test_execution_metrics_latency_percentiles(
        self, sdk_client, test_execution, call_execution, second_call_execution
    ):
        """Execution metrics include latency percentiles when data is available."""
        fake_percentiles = {"p50": 225.0, "p95": 248.0, "p99": 249.5}
        with (
            patch("sdk.views.analytics.connection") as mock_conn,
            patch(
                "sdk.views.analytics._compute_latency_percentiles_sql",
                return_value=fake_percentiles,
            ),
        ):
            mock_cursor = _build_mock_cursor(FAKE_KPI_COLUMNS, FAKE_KPI_ROW)
            mock_conn.cursor.return_value.__enter__ = MagicMock(
                return_value=mock_cursor
            )
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            response = sdk_client.get(
                METRICS_URL, {"execution_id": str(test_execution.id)}
            )

        assert response.status_code == status.HTTP_200_OK
        metrics = response.json()["result"]["metrics"]
        percentiles = metrics["latency"]["percentiles"]
        assert percentiles == fake_percentiles

    def test_execution_metrics_empty_kpi_row(
        self, sdk_client, test_execution, call_execution
    ):
        """When SQL returns no row, metrics is an empty dict."""
        with (
            patch("sdk.views.analytics.connection") as mock_conn,
            patch(
                "sdk.views.analytics._compute_latency_percentiles_sql",
                return_value={},
            ),
        ):
            mock_cursor = MagicMock()
            mock_cursor.description = [(col,) for col in FAKE_KPI_COLUMNS]
            mock_cursor.fetchone.return_value = None
            mock_conn.cursor.return_value.__enter__ = MagicMock(
                return_value=mock_cursor
            )
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            response = sdk_client.get(
                METRICS_URL, {"execution_id": str(test_execution.id)}
            )

        assert response.status_code == status.HTTP_200_OK
        metrics = response.json()["result"]["metrics"]
        assert metrics == {}


# ===========================================================================
# SimulationRunsView — happy path
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
@pytest.mark.api
class TestSimulationRunsView:
    """Happy path tests for GET /sdk/api/v1/simulation/runs/."""

    def test_call_execution_id_returns_detail(self, sdk_client, call_execution):
        """call_execution_id mode returns full call detail."""
        response = sdk_client.get(
            RUNS_URL, {"call_execution_id": str(call_execution.id)}
        )
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        result = data["result"]

        assert result["call_execution_id"] == str(call_execution.id)
        assert result["execution_id"] == str(call_execution.test_execution_id)
        assert result["scenario_id"] == str(call_execution.scenario_id)
        assert result["scenario_name"] == "Test Scenario"
        assert result["status"] == "completed"
        assert "eval_outputs" in result
        assert "latency" in result
        assert "cost" in result

    def test_call_execution_eval_outputs_present(self, sdk_client, call_execution):
        """Call detail includes eval_outputs with all configured evals."""
        response = sdk_client.get(
            RUNS_URL, {"call_execution_id": str(call_execution.id)}
        )
        result = response.json()["result"]
        assert "eval-config-1" in result["eval_outputs"]
        assert "eval-config-2" in result["eval_outputs"]
        assert result["eval_outputs"]["eval-config-1"]["name"] == "Accuracy"

    def test_execution_id_returns_execution_with_call_results(
        self, sdk_client, test_execution, call_execution
    ):
        """execution_id mode returns execution summary + paginated call results."""
        with (
            patch("sdk.views.analytics._get_eval_configs_with_template") as mock_eval,
            patch("sdk.views.analytics._get_completed_call_executions") as mock_calls,
            patch("sdk.views.analytics._build_template_statistics") as mock_stats,
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[{"name": "Accuracy", "avg_score": 50.0}],
            ),
        ):
            mock_eval.return_value = MagicMock(filter=MagicMock(return_value=[]))
            mock_calls.return_value = []
            mock_stats.return_value = {}

            response = sdk_client.get(
                RUNS_URL, {"execution_id": str(test_execution.id)}
            )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]

        assert result["execution_id"] == str(test_execution.id)
        assert result["status"] == "completed"
        assert "eval_results" in result

        # call_results is paginated
        assert "call_results" in result
        call_results = result["call_results"]
        assert "total_pages" in call_results
        assert "current_page" in call_results
        assert "count" in call_results
        assert "results" in call_results

    def test_execution_id_with_summary_true(
        self, sdk_client, test_execution, call_execution
    ):
        """summary=true includes eval_explanation_summary in response."""
        with (
            patch("sdk.views.analytics._get_eval_configs_with_template") as mock_eval,
            patch("sdk.views.analytics._get_completed_call_executions"),
            patch("sdk.views.analytics._build_template_statistics"),
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_eval.return_value = MagicMock(filter=MagicMock(return_value=[]))

            response = sdk_client.get(
                RUNS_URL,
                {
                    "execution_id": str(test_execution.id),
                    "summary": "true",
                },
            )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert "eval_explanation_summary" in result
        assert result["eval_explanation_summary"] == {"summary": "test summary data"}
        assert "eval_explanation_summary_status" in result
        assert result["eval_explanation_summary_status"] == "completed"

    def test_execution_id_without_summary_excludes_fields(
        self, sdk_client, test_execution, call_execution
    ):
        """summary=false (default) does not include eval_explanation_summary."""
        with (
            patch("sdk.views.analytics._get_eval_configs_with_template") as mock_eval,
            patch("sdk.views.analytics._get_completed_call_executions"),
            patch("sdk.views.analytics._build_template_statistics"),
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_eval.return_value = MagicMock(filter=MagicMock(return_value=[]))

            response = sdk_client.get(
                RUNS_URL,
                {"execution_id": str(test_execution.id)},
            )

        result = response.json()["result"]
        assert "eval_explanation_summary" not in result

    def test_run_test_name_returns_paginated_executions(
        self, sdk_client, run_test, test_execution, call_execution
    ):
        """run_test_name mode returns paginated list of executions with eval scores."""
        with (
            patch("sdk.views.analytics._get_eval_configs_with_template") as mock_eval,
            patch("sdk.views.analytics._get_completed_call_executions"),
            patch("sdk.views.analytics._build_template_statistics"),
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_eval.return_value = MagicMock(filter=MagicMock(return_value=[]))

            response = sdk_client.get(RUNS_URL, {"run_test_name": run_test.name})

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert "total_pages" in result
        assert "current_page" in result
        assert "count" in result
        assert "results" in result
        assert result["count"] >= 1

    def test_eval_name_filter_on_call_execution(self, sdk_client, call_execution):
        """eval_name filter only returns matching evals in eval_outputs."""
        response = sdk_client.get(
            RUNS_URL,
            {
                "call_execution_id": str(call_execution.id),
                "eval_name": "Accuracy",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        eval_outputs = result["eval_outputs"]
        # Only Accuracy should remain, Fluency should be filtered out
        for _config_id, data in eval_outputs.items():
            assert data["name"].lower() == "accuracy"

    def test_eval_name_filter_comma_separated(self, sdk_client, call_execution):
        """eval_name accepts comma-separated names."""
        response = sdk_client.get(
            RUNS_URL,
            {
                "call_execution_id": str(call_execution.id),
                "eval_name": "Accuracy,Fluency",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        eval_outputs = result["eval_outputs"]
        names = {d["name"] for d in eval_outputs.values()}
        assert "Accuracy" in names
        assert "Fluency" in names

    def test_eval_name_filter_no_match_returns_empty(self, sdk_client, call_execution):
        """eval_name filter with non-matching name returns empty eval_outputs."""
        response = sdk_client.get(
            RUNS_URL,
            {
                "call_execution_id": str(call_execution.id),
                "eval_name": "Nonexistent",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["eval_outputs"] == {}


# ===========================================================================
# SimulationAnalyticsView — happy path
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
@pytest.mark.api
class TestSimulationAnalyticsView:
    """Happy path tests for GET /sdk/api/v1/simulation/analytics/."""

    def _mock_connection_for_analytics(self, mock_conn):
        """Set up mock connection returning eval and kpi cursors."""
        mock_cursor_eval = _build_mock_cursor(
            [
                "metric_id",
                "metric_name",
                "output_type",
                "avg_value",
                "choice_value",
                "choice_count",
            ],
            FAKE_EVAL_ROWS,
            single=False,
        )
        mock_cursor_kpi = _build_mock_cursor(FAKE_KPI_COLUMNS, FAKE_KPI_ROW)

        cursors = iter([mock_cursor_eval, mock_cursor_kpi])

        def side_effect():
            cm = MagicMock()
            cm.__enter__ = MagicMock(side_effect=lambda: next(cursors))
            cm.__exit__ = MagicMock(return_value=False)
            return cm

        mock_conn.cursor = side_effect

    def test_execution_id_returns_correct_structure(
        self, sdk_client, test_execution, call_execution
    ):
        """execution_id mode returns analytics with expected fields."""
        with (
            patch("sdk.views.analytics.connection") as mock_conn,
            patch("sdk.views.analytics._get_eval_configs_with_template") as mock_eval,
            patch("sdk.views.analytics._get_completed_call_executions"),
            patch("sdk.views.analytics._build_template_statistics"),
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[{"name": "Accuracy", "avg_score": 50.0}],
            ),
        ):
            mock_eval.return_value = MagicMock(filter=MagicMock(return_value=[]))
            self._mock_connection_for_analytics(mock_conn)

            response = sdk_client.get(
                ANALYTICS_URL,
                {"execution_id": str(test_execution.id)},
            )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]

        assert result["execution_id"] == str(test_execution.id)
        assert result["run_test_name"] == "test-run"
        assert result["status"] == "completed"
        assert "eval_results" in result
        assert "eval_averages" in result
        assert "system_summary" in result

        # summary=true by default for analytics
        assert "eval_explanation_summary" in result
        assert "eval_explanation_summary_status" in result

    def test_execution_id_eval_averages_populated(
        self, sdk_client, test_execution, call_execution
    ):
        """eval_averages contains averaged eval metric values."""
        with (
            patch("sdk.views.analytics.connection") as mock_conn,
            patch("sdk.views.analytics._get_eval_configs_with_template") as mock_eval,
            patch("sdk.views.analytics._get_completed_call_executions"),
            patch("sdk.views.analytics._build_template_statistics"),
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_eval.return_value = MagicMock(filter=MagicMock(return_value=[]))
            self._mock_connection_for_analytics(mock_conn)

            response = sdk_client.get(
                ANALYTICS_URL,
                {"execution_id": str(test_execution.id)},
            )

        result = response.json()["result"]
        eval_averages = result["eval_averages"]
        assert "avg_accuracy" in eval_averages
        assert eval_averages["avg_accuracy"] == 50.0
        assert "avg_fluency" in eval_averages
        assert eval_averages["avg_fluency"] == 0.85

    def test_execution_id_system_summary_populated(
        self, sdk_client, test_execution, call_execution
    ):
        """system_summary contains aggregated call statistics."""
        with (
            patch("sdk.views.analytics.connection") as mock_conn,
            patch("sdk.views.analytics._get_eval_configs_with_template") as mock_eval,
            patch("sdk.views.analytics._get_completed_call_executions"),
            patch("sdk.views.analytics._build_template_statistics"),
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_eval.return_value = MagicMock(filter=MagicMock(return_value=[]))
            self._mock_connection_for_analytics(mock_conn)

            response = sdk_client.get(
                ANALYTICS_URL,
                {"execution_id": str(test_execution.id)},
            )

        system_summary = response.json()["result"]["system_summary"]
        assert system_summary["total_calls"] == 3
        assert system_summary["completed_calls"] == 2
        assert system_summary["failed_calls"] == 1
        assert system_summary["avg_score"] == 82.5
        assert system_summary["avg_response_time_ms"] == 290
        assert system_summary["total_duration_seconds"] == 210

    def test_summary_false_excludes_explanation(
        self, sdk_client, test_execution, call_execution
    ):
        """summary=false does not include eval_explanation_summary."""
        with (
            patch("sdk.views.analytics.connection") as mock_conn,
            patch("sdk.views.analytics._get_eval_configs_with_template") as mock_eval,
            patch("sdk.views.analytics._get_completed_call_executions"),
            patch("sdk.views.analytics._build_template_statistics"),
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_eval.return_value = MagicMock(filter=MagicMock(return_value=[]))
            self._mock_connection_for_analytics(mock_conn)

            response = sdk_client.get(
                ANALYTICS_URL,
                {
                    "execution_id": str(test_execution.id),
                    "summary": "false",
                },
            )

        result = response.json()["result"]
        assert "eval_explanation_summary" not in result
        assert "eval_explanation_summary_status" not in result

    def test_run_test_name_uses_latest_completed_execution(
        self, sdk_client, run_test, test_execution, call_execution
    ):
        """run_test_name mode uses the latest completed execution."""
        with (
            patch("sdk.views.analytics.connection") as mock_conn,
            patch("sdk.views.analytics._get_eval_configs_with_template") as mock_eval,
            patch("sdk.views.analytics._get_completed_call_executions"),
            patch("sdk.views.analytics._build_template_statistics"),
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_eval.return_value = MagicMock(filter=MagicMock(return_value=[]))
            self._mock_connection_for_analytics(mock_conn)

            response = sdk_client.get(
                ANALYTICS_URL,
                {"run_test_name": run_test.name},
            )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["execution_id"] == str(test_execution.id)
        assert result["run_test_name"] == run_test.name

    def test_run_test_name_no_completed_executions(self, sdk_client, run_test, db):
        """run_test_name with no completed executions returns empty analytics."""
        # Create a pending execution (not completed)
        TestExecution.objects.create(
            run_test=run_test,
            status="pending",
        )
        response = sdk_client.get(ANALYTICS_URL, {"run_test_name": run_test.name})
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["run_test_name"] == run_test.name
        assert result["eval_results"] == []
        assert result["eval_averages"] == {}
        assert result["system_summary"] == {}
        assert "message" in result

    def test_eval_name_filter_on_analytics(
        self, sdk_client, test_execution, call_execution
    ):
        """eval_name filter is passed through to eval config filtering."""
        with (
            patch("sdk.views.analytics.connection") as mock_conn,
            patch("sdk.views.analytics._get_eval_configs_with_template") as mock_eval,
            patch("sdk.views.analytics._get_completed_call_executions"),
            patch("sdk.views.analytics._build_template_statistics"),
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_qs = MagicMock()
            mock_eval.return_value = mock_qs
            self._mock_connection_for_analytics(mock_conn)

            response = sdk_client.get(
                ANALYTICS_URL,
                {
                    "execution_id": str(test_execution.id),
                    "eval_name": "Accuracy",
                },
            )

        assert response.status_code == status.HTTP_200_OK
        # Verify eval_configs.filter was called with eval_names
        mock_qs.filter.assert_called_once_with(name__in=["Accuracy"])

    def test_analytics_empty_kpi_row(self, sdk_client, test_execution, call_execution):
        """When SQL returns no row, system_summary is empty."""
        with (
            patch("sdk.views.analytics.connection") as mock_conn,
            patch("sdk.views.analytics._get_eval_configs_with_template") as mock_eval,
            patch("sdk.views.analytics._get_completed_call_executions"),
            patch("sdk.views.analytics._build_template_statistics"),
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_eval.return_value = MagicMock(filter=MagicMock(return_value=[]))

            # Eval cursor returns empty, KPI cursor returns None
            mock_cursor_eval = _build_mock_cursor(
                [
                    "metric_id",
                    "metric_name",
                    "output_type",
                    "avg_value",
                    "choice_value",
                    "choice_count",
                ],
                [],
                single=False,
            )
            mock_cursor_kpi = MagicMock()
            mock_cursor_kpi.description = [(col,) for col in FAKE_KPI_COLUMNS]
            mock_cursor_kpi.fetchone.return_value = None

            cursors = iter([mock_cursor_eval, mock_cursor_kpi])

            def side_effect():
                cm = MagicMock()
                cm.__enter__ = MagicMock(side_effect=lambda: next(cursors))
                cm.__exit__ = MagicMock(return_value=False)
                return cm

            mock_conn.cursor = side_effect

            response = sdk_client.get(
                ANALYTICS_URL,
                {"execution_id": str(test_execution.id)},
            )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["system_summary"] == {}
        assert result["eval_averages"] == {}


# ===========================================================================
# Pagination tests
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
@pytest.mark.api
class TestAnalyticsPagination:
    """Pagination tests for paginated endpoints."""

    def test_metrics_pagination_params(self, sdk_client, run_test, db, scenario):
        """Metrics run_test_name mode respects page and limit params."""
        # Create multiple executions
        for _i in range(15):
            exec_obj = TestExecution.objects.create(
                run_test=run_test,
                status="completed",
                total_calls=1,
            )
            CallExecution.objects.create(
                test_execution=exec_obj,
                scenario=scenario,
                status="completed",
            )

        with (
            patch("sdk.views.analytics.connection") as mock_conn,
            patch(
                "sdk.views.analytics._compute_latency_percentiles_sql",
                return_value={"p50": 0.0, "p95": 0.0, "p99": 0.0},
            ),
        ):
            mock_cursor = _build_mock_cursor(FAKE_KPI_COLUMNS, FAKE_KPI_ROW)
            mock_conn.cursor.return_value.__enter__ = MagicMock(
                return_value=mock_cursor
            )
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

            response = sdk_client.get(
                METRICS_URL,
                {"run_test_name": run_test.name, "page": 1, "limit": 5},
            )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["count"] == 15
        assert len(result["results"]) == 5
        assert result["total_pages"] == 3
        assert result["current_page"] == 1

    def test_runs_execution_id_call_results_pagination(
        self, sdk_client, test_execution, scenario, db
    ):
        """Runs execution_id mode paginates call_results."""
        # Create 12 call executions
        for _i in range(12):
            CallExecution.objects.create(
                test_execution=test_execution,
                scenario=scenario,
                status="completed",
            )

        with (
            patch("sdk.views.analytics._get_eval_configs_with_template") as mock_eval,
            patch("sdk.views.analytics._get_completed_call_executions"),
            patch("sdk.views.analytics._build_template_statistics"),
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_eval.return_value = MagicMock(filter=MagicMock(return_value=[]))

            response = sdk_client.get(
                RUNS_URL,
                {
                    "execution_id": str(test_execution.id),
                    "page": 1,
                    "limit": 5,
                },
            )

        assert response.status_code == status.HTTP_200_OK
        call_results = response.json()["result"]["call_results"]
        assert call_results["count"] == 12
        assert len(call_results["results"]) == 5
        assert call_results["total_pages"] == 3

    def test_runs_run_test_name_pagination(self, sdk_client, run_test, scenario, db):
        """Runs run_test_name mode paginates executions."""
        for _i in range(8):
            TestExecution.objects.create(
                run_test=run_test,
                status="completed",
            )

        with (
            patch("sdk.views.analytics._get_eval_configs_with_template") as mock_eval,
            patch("sdk.views.analytics._get_completed_call_executions"),
            patch("sdk.views.analytics._build_template_statistics"),
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_eval.return_value = MagicMock(filter=MagicMock(return_value=[]))

            response = sdk_client.get(
                RUNS_URL,
                {"run_test_name": run_test.name, "page": 2, "limit": 3},
            )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["count"] == 8
        assert result["current_page"] == 2
        assert len(result["results"]) == 3


# ===========================================================================
# Edge cases and error handling
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
@pytest.mark.api
class TestAnalyticsEdgeCases:
    """Edge case and error handling tests."""

    def test_call_execution_with_null_eval_outputs(
        self, sdk_client, test_execution, scenario, db
    ):
        """Call execution with null eval_outputs returns empty dict."""
        call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status="completed",
            eval_outputs=None,
        )
        response = sdk_client.get(RUNS_URL, {"call_execution_id": str(call.id)})
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["eval_outputs"] == {}

    def test_call_execution_with_empty_eval_outputs(
        self, sdk_client, test_execution, scenario, db
    ):
        """Call execution with empty eval_outputs returns empty dict."""
        call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status="completed",
            eval_outputs={},
        )
        response = sdk_client.get(RUNS_URL, {"call_execution_id": str(call.id)})
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["eval_outputs"] == {}

    def test_call_execution_with_null_metrics(
        self, sdk_client, test_execution, scenario, db
    ):
        """Call execution with all null metric fields still returns valid response."""
        call = CallExecution.objects.create(
            test_execution=test_execution,
            scenario=scenario,
            status="pending",
        )
        response = sdk_client.get(METRICS_URL, {"call_execution_id": str(call.id)})
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert result["latency"]["avg_agent_latency_ms"] is None
        assert result["cost"]["total_cost_cents"] is None

    def test_metrics_view_handles_internal_error(self, sdk_client, db):
        """Metrics view catches exceptions and returns 500."""
        with patch(
            "sdk.views.analytics.SimulationMetricsView._handle_execution",
            side_effect=RuntimeError("boom"),
        ):
            response = sdk_client.get(METRICS_URL, {"execution_id": str(uuid.uuid4())})
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_runs_view_handles_internal_error(self, sdk_client, db):
        """Runs view catches exceptions and returns 500."""
        with patch(
            "sdk.views.analytics.SimulationRunsView._handle_execution_detail",
            side_effect=RuntimeError("boom"),
        ):
            response = sdk_client.get(RUNS_URL, {"execution_id": str(uuid.uuid4())})
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_analytics_view_handles_internal_error(self, sdk_client, db):
        """Analytics view catches exceptions and returns 500."""
        with patch(
            "sdk.views.analytics.SimulationAnalyticsView._handle_execution",
            side_effect=RuntimeError("boom"),
        ):
            response = sdk_client.get(
                ANALYTICS_URL, {"execution_id": str(uuid.uuid4())}
            )
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    def test_priority_call_execution_over_execution_id(
        self, sdk_client, test_execution, call_execution
    ):
        """When both call_execution_id and execution_id are provided,
        call_execution_id takes priority (metrics endpoint)."""
        response = sdk_client.get(
            METRICS_URL,
            {
                "call_execution_id": str(call_execution.id),
                "execution_id": str(test_execution.id),
            },
        )
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        # call_execution_id mode returns call_execution_id field (not execution_id as top-level)
        assert "call_execution_id" in result

    def test_priority_call_execution_over_run_test_name(
        self, sdk_client, run_test, call_execution
    ):
        """When both call_execution_id and run_test_name are provided,
        call_execution_id takes priority (runs endpoint)."""
        response = sdk_client.get(
            RUNS_URL,
            {
                "call_execution_id": str(call_execution.id),
                "run_test_name": run_test.name,
            },
        )
        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert "call_execution_id" in result
        assert result["call_execution_id"] == str(call_execution.id)

    def test_analytics_choices_eval_type(
        self, sdk_client, test_execution, call_execution
    ):
        """Analytics correctly handles choices-type eval metrics."""
        choice_eval_rows = [
            ("eval-3", "Sentiment", "choices", None, "positive", 5),
            ("eval-3", "Sentiment", "choices", None, "negative", 3),
        ]

        with (
            patch("sdk.views.analytics.connection") as mock_conn,
            patch("sdk.views.analytics._get_eval_configs_with_template") as mock_eval,
            patch("sdk.views.analytics._get_completed_call_executions"),
            patch("sdk.views.analytics._build_template_statistics"),
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_eval.return_value = MagicMock(filter=MagicMock(return_value=[]))

            mock_cursor_eval = _build_mock_cursor(
                [
                    "metric_id",
                    "metric_name",
                    "output_type",
                    "avg_value",
                    "choice_value",
                    "choice_count",
                ],
                choice_eval_rows,
                single=False,
            )
            mock_cursor_kpi = _build_mock_cursor(FAKE_KPI_COLUMNS, FAKE_KPI_ROW)

            cursors = iter([mock_cursor_eval, mock_cursor_kpi])

            def side_effect():
                cm = MagicMock()
                cm.__enter__ = MagicMock(side_effect=lambda: next(cursors))
                cm.__exit__ = MagicMock(return_value=False)
                return cm

            mock_conn.cursor = side_effect

            response = sdk_client.get(
                ANALYTICS_URL,
                {"execution_id": str(test_execution.id)},
            )

        assert response.status_code == status.HTTP_200_OK
        eval_averages = response.json()["result"]["eval_averages"]
        assert "sentiment" in eval_averages
        assert eval_averages["sentiment"]["positive"] == 5
        assert eval_averages["sentiment"]["negative"] == 3


# ===========================================================================
# Additional coverage tests for gaps
# ===========================================================================


@pytest.mark.django_db
@pytest.mark.integration
@pytest.mark.api
class TestAdditionalCoverage:
    """Tests covering previously untested scenarios."""

    def test_runs_execution_id_with_eval_name_filter(
        self, sdk_client, run_test, test_execution, call_execution
    ):
        """eval_name filter works on execution_id mode — filters both
        eval summary and per-call eval_outputs."""
        with (
            patch(
                "sdk.views.analytics._get_eval_configs_with_template"
            ) as mock_configs,
            patch("sdk.views.analytics._get_completed_call_executions"),
            patch("sdk.views.analytics._build_template_statistics"),
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_qs = MagicMock()
            mock_qs.filter.return_value = mock_qs
            mock_configs.return_value = mock_qs

            response = sdk_client.get(
                RUNS_URL,
                {
                    "execution_id": str(test_execution.id),
                    "eval_name": "Accuracy",
                },
            )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]

        # eval_configs should have been filtered by name
        mock_qs.filter.assert_called_once_with(name__in=["Accuracy"])

        # per-call eval_outputs should only contain Accuracy
        for call in result["call_results"]["results"]:
            for _config_id, data in call["eval_outputs"].items():
                assert data["name"].lower() == "accuracy"

    def test_runs_execution_id_summary_serializer_includes_fields(
        self, sdk_client, run_test, test_execution, call_execution
    ):
        """When summary=true, ExecutionRunsSerializer output includes
        eval_explanation_summary added by the view."""
        with (
            patch(
                "sdk.views.analytics._get_eval_configs_with_template"
            ) as mock_configs,
            patch("sdk.views.analytics._get_completed_call_executions"),
            patch("sdk.views.analytics._build_template_statistics"),
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_configs.return_value = MagicMock(
                filter=MagicMock(return_value=MagicMock())
            )

            response = sdk_client.get(
                RUNS_URL,
                {
                    "execution_id": str(test_execution.id),
                    "summary": "true",
                },
            )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert "eval_explanation_summary" in result
        assert result["eval_explanation_summary"] == {"summary": "test summary data"}
        assert result["eval_explanation_summary_status"] == "completed"

    def test_pagination_page_2_returns_different_data(
        self, sdk_client, run_test, scenario, db
    ):
        """Page 2 returns different executions than page 1."""
        executions = []
        for i in range(3):
            executions.append(
                TestExecution.objects.create(
                    run_test=run_test,
                    status="completed",
                    total_calls=i + 1,
                    started_at=timezone.now(),
                )
            )

        with (
            patch(
                "sdk.views.analytics._get_eval_configs_with_template"
            ) as mock_configs,
            patch("sdk.views.analytics._get_completed_call_executions"),
            patch("sdk.views.analytics._build_template_statistics"),
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_configs.return_value = MagicMock(
                filter=MagicMock(return_value=MagicMock())
            )

            resp_page1 = sdk_client.get(
                RUNS_URL,
                {"run_test_name": run_test.name, "limit": 2, "page": 1},
            )
            resp_page2 = sdk_client.get(
                RUNS_URL,
                {"run_test_name": run_test.name, "limit": 2, "page": 2},
            )

        assert resp_page1.status_code == status.HTTP_200_OK
        assert resp_page2.status_code == status.HTTP_200_OK

        page1_ids = {r["execution_id"] for r in resp_page1.json()["result"]["results"]}
        page2_ids = {r["execution_id"] for r in resp_page2.json()["result"]["results"]}
        assert page1_ids.isdisjoint(page2_ids)
        assert len(page1_ids) == 2
        assert len(page2_ids) == 1

    def test_run_test_name_with_spaces(self, sdk_client, organization, workspace, db):
        """run_test_name with spaces works correctly."""
        rt = RunTest.objects.create(
            name="my agent test",
            organization=organization,
            workspace=workspace,
        )
        TestExecution.objects.create(
            run_test=rt,
            status="completed",
            total_calls=1,
            started_at=timezone.now(),
        )

        with (
            patch("sdk.views.analytics.connection") as mock_conn,
            patch(
                "sdk.views.analytics._compute_latency_percentiles_sql",
                return_value={},
            ),
        ):
            mock_cursor = _build_mock_cursor(FAKE_KPI_COLUMNS, FAKE_KPI_ROW)
            mock_conn.cursor.return_value.__enter__ = MagicMock(
                return_value=mock_cursor
            )
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

            response = sdk_client.get(
                METRICS_URL,
                {"run_test_name": "my agent test"},
            )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["result"]["count"] == 1

    def test_analytics_serializer_excludes_summary_when_false(
        self, sdk_client, test_execution
    ):
        """AnalyticsResponseSerializer.to_representation excludes summary
        fields when summary=false."""
        with (
            patch("sdk.views.analytics.connection") as mock_conn,
            patch("sdk.views.analytics._get_eval_configs_with_template") as mock_eval,
            patch("sdk.views.analytics._get_completed_call_executions"),
            patch("sdk.views.analytics._build_template_statistics"),
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_eval.return_value = MagicMock(filter=MagicMock(return_value=[]))

            mock_cursor_eval = _build_mock_cursor(
                [
                    "metric_id",
                    "metric_name",
                    "output_type",
                    "avg_value",
                    "choice_value",
                    "choice_count",
                ],
                [],
                single=False,
            )
            mock_cursor_kpi = _build_mock_cursor(FAKE_KPI_COLUMNS, FAKE_KPI_ROW)
            cursors = iter([mock_cursor_eval, mock_cursor_kpi])

            def side_effect():
                cm = MagicMock()
                cm.__enter__ = MagicMock(side_effect=lambda: next(cursors))
                cm.__exit__ = MagicMock(return_value=False)
                return cm

            mock_conn.cursor = side_effect

            response = sdk_client.get(
                ANALYTICS_URL,
                {"execution_id": str(test_execution.id), "summary": "false"},
            )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert "eval_explanation_summary" not in result
        assert "eval_explanation_summary_status" not in result

    def test_analytics_serializer_includes_summary_when_true(
        self, sdk_client, test_execution
    ):
        """AnalyticsResponseSerializer.to_representation includes summary
        fields when summary=true."""
        with (
            patch("sdk.views.analytics.connection") as mock_conn,
            patch("sdk.views.analytics._get_eval_configs_with_template") as mock_eval,
            patch("sdk.views.analytics._get_completed_call_executions"),
            patch("sdk.views.analytics._build_template_statistics"),
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_eval.return_value = MagicMock(filter=MagicMock(return_value=[]))

            mock_cursor_eval = _build_mock_cursor(
                [
                    "metric_id",
                    "metric_name",
                    "output_type",
                    "avg_value",
                    "choice_value",
                    "choice_count",
                ],
                [],
                single=False,
            )
            mock_cursor_kpi = _build_mock_cursor(FAKE_KPI_COLUMNS, FAKE_KPI_ROW)
            cursors = iter([mock_cursor_eval, mock_cursor_kpi])

            def side_effect():
                cm = MagicMock()
                cm.__enter__ = MagicMock(side_effect=lambda: next(cursors))
                cm.__exit__ = MagicMock(return_value=False)
                return cm

            mock_conn.cursor = side_effect

            response = sdk_client.get(
                ANALYTICS_URL,
                {"execution_id": str(test_execution.id), "summary": "true"},
            )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        assert "eval_explanation_summary" in result
        assert result["eval_explanation_summary"] == {"summary": "test summary data"}

    def test_execution_metrics_with_zero_calls(self, sdk_client, run_test, db):
        """execution with no CallExecution records returns empty percentiles."""
        execution = TestExecution.objects.create(
            run_test=run_test,
            status="completed",
            total_calls=0,
            started_at=timezone.now(),
        )

        with (
            patch("sdk.views.analytics.connection") as mock_conn,
            patch(
                "sdk.views.analytics._compute_latency_percentiles_sql",
                return_value={},
            ),
        ):
            mock_cursor = _build_mock_cursor(FAKE_KPI_COLUMNS, FAKE_KPI_ROW)
            mock_conn.cursor.return_value.__enter__ = MagicMock(
                return_value=mock_cursor
            )
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

            response = sdk_client.get(
                METRICS_URL,
                {"execution_id": str(execution.id)},
            )

        assert response.status_code == status.HTTP_200_OK
        metrics = response.json()["result"]["metrics"]
        # No calls with avg_agent_latency_ms → empty percentiles
        assert metrics["latency"]["percentiles"] == {}

    def test_analytics_multiple_run_tests_same_name(
        self, sdk_client, organization, workspace, scenario, db
    ):
        """When multiple RunTests share the same name, analytics uses
        the latest completed execution across all of them."""
        rt1 = RunTest.objects.create(
            name="shared-name",
            organization=organization,
            workspace=workspace,
        )
        rt2 = RunTest.objects.create(
            name="shared-name",
            organization=organization,
            workspace=workspace,
        )

        # rt1 has an older completed execution
        TestExecution.objects.create(
            run_test=rt1,
            status="completed",
            total_calls=5,
            started_at=timezone.now(),
            completed_at=timezone.now(),
        )
        # rt2 has a newer completed execution
        import datetime

        newer = TestExecution.objects.create(
            run_test=rt2,
            status="completed",
            total_calls=10,
            started_at=timezone.now(),
            completed_at=timezone.now() + datetime.timedelta(hours=1),
        )

        with (
            patch("sdk.views.analytics.connection") as mock_conn,
            patch("sdk.views.analytics._get_eval_configs_with_template") as mock_eval,
            patch("sdk.views.analytics._get_completed_call_executions"),
            patch("sdk.views.analytics._build_template_statistics"),
            patch(
                "sdk.views.analytics._calculate_final_template_summaries",
                return_value=[],
            ),
        ):
            mock_eval.return_value = MagicMock(filter=MagicMock(return_value=[]))

            mock_cursor_eval = _build_mock_cursor(
                [
                    "metric_id",
                    "metric_name",
                    "output_type",
                    "avg_value",
                    "choice_value",
                    "choice_count",
                ],
                [],
                single=False,
            )
            mock_cursor_kpi = _build_mock_cursor(FAKE_KPI_COLUMNS, FAKE_KPI_ROW)
            cursors = iter([mock_cursor_eval, mock_cursor_kpi])

            def side_effect():
                cm = MagicMock()
                cm.__enter__ = MagicMock(side_effect=lambda: next(cursors))
                cm.__exit__ = MagicMock(return_value=False)
                return cm

            mock_conn.cursor = side_effect

            response = sdk_client.get(
                ANALYTICS_URL,
                {"run_test_name": "shared-name"},
            )

        assert response.status_code == status.HTTP_200_OK
        result = response.json()["result"]
        # Should pick the newer execution (from rt2)
        assert result["execution_id"] == str(newer.id)
